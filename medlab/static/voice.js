'use strict';
(() => {
  const app = document.getElementById('consultation-app');
  if (!app) return;
  const $ = selector => document.querySelector(selector);
  const studio = $('#voice-studio');
  if (!studio) return;
  const base = `/sessao/${encodeURIComponent(app.dataset.sessionId)}/voz`;
  const csrf = $('meta[name=csrf-token]').content;
  const recordButton = $('#voice-record-button');
  const status = $('#voice-status');
  const variant = $('#voice-variant');
  const gender = $('#voice-gender');
  const auto = $('#voice-autoplay');
  const settings = $('#voice-settings');
  const settingsToggle = $('#voice-settings-toggle');
  const storageKey = `medlab-voz-${app.dataset.sessionId}`;
  let config = null;
  let loadingConfig = true;
  let conversationBusy = false;
  let recording = null;
  let pendingPermission = false;
  let permissionGeneration = 0;
  let transcription = null;
  let playbackController = null;
  let playbackAudio = null;
  let playbackUrl = null;
  let playbackButton = null;
  let playbackGeneration = 0;
  let synthesizing = false;
  let playbackContext = null;
  let playbackCursor = 0;
  let playbackChunks = 0;
  let playbackFinished = false;
  const playbackSources = new Set();
  let preferences = {voice_id:'', gender:'', autoplay:false};
  try {
    const stored = JSON.parse(localStorage.getItem(storageKey) || '{}');
    preferences = {
      voice_id: typeof stored.voice_id === 'string' ? stored.voice_id : '',
      gender: typeof stored.gender === 'string' ? stored.gender : '',
      autoplay: stored.autoplay === true
    };
  } catch { /* Preferences are optional; audio is never stored here. */ }
  function notice(message, error = false) {
    status.textContent = message;
    status.hidden = !message;
    status.classList.toggle('voice-notice-error',error);
  }
  function settingsOpen(open) {
    settings.hidden = !open;
    settingsToggle.setAttribute('aria-expanded',String(open));
  }
  settingsToggle.addEventListener('click',() => settingsOpen(settings.hidden));
  function savePreferences() {
    preferences = {voice_id:variant.value, gender:gender.value, autoplay:auto.checked};
    try { localStorage.setItem(storageKey,JSON.stringify(preferences)); } catch { /* Optional browser storage. */ }
  }
  function canonicalGender(value) {
    const text = String(value || '').toLowerCase();
    if (['female','feminino','feminina','f','woman','mulher'].includes(text)) return 'female';
    if (['male','masculino','masculina','m','man','homem'].includes(text)) return 'male';
    return '';
  }
  function availableOrExplain() {
    if (loadingConfig) { notice('As opções de voz estão sendo verificadas. Aguarde um instante.'); return false; }
    if (!config?.available || config.supported === false) {
      notice(config?.reason || 'A voz local ainda não está habilitada nesta instalação. Você pode continuar a consulta por texto.');
      return false;
    }
    return true;
  }
  function currentVoice() { return (config?.voices || []).find(voice => voice.id === variant.value); }
  function fillVariants(preferredId = '') {
    const selectedGender = config.gender_locked ? canonicalGender(config.gender) : gender.value;
    variant.replaceChildren();
    const voices = (config.voices || []).filter(voice => canonicalGender(voice.gender) === selectedGender);
    if (!selectedGender || !voices.length) {
      variant.append(new Option(!selectedGender ? 'Escolha feminina ou masculina' : 'Nenhum timbre disponível',''));
      variant.disabled = true;
      auto.disabled = true;
      return;
    }
    voices.forEach(voice => variant.append(new Option(voice.label || voice.id,voice.id)));
    variant.value = voices.some(voice => voice.id === preferredId) ? preferredId : (voices.some(voice => voice.id === config.default_voice) ? config.default_voice : voices[0].id);
    variant.disabled = false;
    auto.disabled = false;
  }

  // Workers are created only by the explicit model activation buttons. Each
  // engine owns its pending requests; terminating it cancels a load or inference.
  class LocalEngine {
    constructor(name, defaultPath) {
      this.name = name;
      this.path = defaultPath;
      this.worker = null;
      this.state = 'idle';
      this.requests = new Map();
      this.sequence = 0;
      this.revision = 0;
      this.device = '';
    }
    render() {
      const loading = this.state === 'loading';
      const ready = this.state === 'ready';
      const button = $(`#${this.name}-model-activate`);
      button.disabled = loading || ready;
      button.textContent = loading ? 'Preparando…' : ready ? 'Pronto neste dispositivo' : this.name === 'asr' ? 'Ativar transcrição' : 'Ativar voz do paciente';
      $(`#${this.name}-model-cancel`).hidden = !loading;
      $(`#${this.name}-model-progress`).hidden = !loading;
      $(`#${this.name}-model-badge`).textContent = ready ? 'Pronto' : loading ? 'Preparando' : this.state === 'error' ? 'Tentar de novo' : 'Local';
      $(`[data-voice-engine=${this.name}]`).classList.toggle('is-ready',ready);
    }
    updateProgress(data) {
      if (this.state !== 'loading') return;
      const bar = $(`#${this.name}-progress-bar`);
      const label = $(`#${this.name}-progress-label`);
      const percent = Number.isFinite(data.progress) ? Math.min(100,Math.max(0,data.progress)) : null;
      if (percent !== null) bar.value = percent;
      else bar.removeAttribute('value');
      const downloaded = Number.isFinite(data.loaded) && data.loaded > 0 ? ` · ${(data.loaded / 1048576).toFixed(1)} MB` : '';
      label.textContent = `${data.message || 'Preparando o modelo no dispositivo.'}${percent !== null ? ` · ${Math.round(percent)}% do arquivo` : downloaded}`;
    }
    ensureWorker() {
      if (this.worker) return;
      const url = new URL(this.path,window.location.href);
      if (url.origin !== window.location.origin || !url.pathname.startsWith('/static/voice-local/')) throw new Error('O endereço do módulo de voz não é válido. A equipe precisa revisar esta instalação.');
      try { this.worker = new Worker(url.href,{type:'module',name:`medlab-${this.name}`}); }
      catch { throw new Error('O navegador bloqueou o módulo de voz local. Tente um navegador atualizado ou continue por texto.'); }
      const worker = this.worker;
      worker.addEventListener('message',event => {
        if (this.worker !== worker) return;
        const data = event.data;
        if (!data || typeof data.id !== 'string') return;
        const pending = this.requests.get(data.id);
        if (!pending) return;
        if (data.type === 'progress') { this.updateProgress(data); pending.progress?.(data); return; }
        if (data.type === 'chunk') {
          try { pending.chunk?.(data); }
          catch (error) { this.requests.delete(data.id); pending.reject(error); }
          return;
        }
        if (!['ready','result','disposed','error'].includes(data.type)) return;
        this.requests.delete(data.id);
        if (data.type === 'error') {
          const error = new Error(data.message || 'O processamento local de voz não foi concluído. Tente novamente.');
          error.code = data.code;
          pending.reject(error);
        } else pending.resolve(data);
      });
      worker.addEventListener('error',event => {
        event.preventDefault();
        if (this.worker !== worker) return;
        const message = 'Não foi possível executar a voz local neste navegador. Tente novamente ou continue por texto.';
        this.terminate(message,'error');
        notice(message,true);
      });
      worker.addEventListener('messageerror',() => {
        if (this.worker !== worker) return;
        const message = 'O navegador não conseguiu ler o resultado da voz. Tente novamente.';
        this.terminate(message,'error');
        notice(message,true);
      });
    }
    request(type, fields = {}, transfer = [], progress, chunk) {
      this.ensureWorker();
      const id = `${this.name}-${++this.sequence}`;
      return new Promise((resolve,reject) => {
        this.requests.set(id,{resolve,reject,progress,chunk});
        try { this.worker.postMessage({id,type,...fields},transfer); }
        catch (error) { this.requests.delete(id); reject(error); }
      });
    }
    async activate() {
      if (!availableOrExplain() || ['loading','ready'].includes(this.state)) return;
      const revision = ++this.revision;
      this.state = 'loading';
      this.render();
      this.updateProgress({message:'Preparando o download. Os arquivos em cache serão reutilizados.'});
      notice('');
      try {
        const result = await this.request('load');
        if (revision !== this.revision) return;
        this.device = result.device || '';
        this.state = 'ready';
        this.render();
        notice(this.name === 'asr'
          ? 'Transcrição pronta. Clique em “Ditar pergunta” quando quiser usar o microfone.'
          : 'Voz pronta. Use “Ouvir resposta” abaixo de uma fala do paciente.');
      } catch (error) {
        if (revision !== this.revision || error.name === 'AbortError') return;
        this.terminate(error.message,'error');
        notice(error.message,true);
      }
    }
    terminate(message = 'Operação de voz cancelada.', state = 'idle') {
      this.revision++;
      this.worker?.terminate();
      this.worker = null;
      for (const pending of this.requests.values()) {
        const error = new Error(message);
        error.name = state === 'error' ? 'LocalVoiceError' : 'AbortError';
        pending.reject(error);
      }
      this.requests.clear();
      this.state = state;
      this.render();
    }
    requireReady() {
      if (this.state === 'ready') return true;
      notice(this.state === 'loading'
        ? 'Aguarde o preparo do modelo local. Você pode continuar por texto enquanto isso.'
        : `Ative ${this.name === 'asr' ? 'a transcrição' : 'a voz do paciente'} acima para baixar o modelo e usar este recurso no navegador.`);
      $(`#${this.name}-model-activate`).focus();
      return false;
    }
  }
  const asr = new LocalEngine('asr','/static/voice-local/whisper-worker.js');
  const tts = new LocalEngine('tts','/static/voice-local/kokoro-worker.js');
  [asr,tts].forEach(engine => {
    $(`#${engine.name}-model-activate`).addEventListener('click',() => engine.activate());
    $(`#${engine.name}-model-cancel`).addEventListener('click',() => {
      engine.terminate();
      notice('Preparo cancelado. Os arquivos já baixados podem permanecer no cache do navegador.');
    });
  });
  async function loadConfig() {
    try {
      const response = await fetch(`${base}/config`,{headers:{Accept:'application/json'},credentials:'same-origin'});
      const data = await response.json().catch(() => { throw new Error('Não foi possível verificar as opções de voz. Atualize a página para tentar novamente.'); });
      if (!response.ok) throw new Error(data.error || 'Não foi possível verificar as opções de voz. Atualize a página para tentar novamente.');
      config = data;
      config.voices = Array.isArray(data.voices) ? data.voices.filter(voice => typeof voice.id === 'string') : [];
      if (config.provider !== 'local') { config.available = false; config.reason = 'Esta instalação ainda precisa atualizar os módulos de voz local. Você pode continuar por texto.'; }
      if (typeof Worker === 'undefined' || typeof WebAssembly === 'undefined') {
        config.available = false;
        config.reason = 'Este navegador não oferece os recursos necessários para a voz local. Use um navegador atualizado ou continue por texto.';
      }
      const lockedGender = canonicalGender(config.gender);
      if (!config.available || config.supported === false) {
        $('#voice-availability').textContent = 'Consulta por texto disponível';
        variant.replaceChildren(new Option('Voz local indisponível',''));
        variant.disabled = true;
        auto.disabled = true;
        recordButton.setAttribute('aria-disabled','true');
        $('#voice-gender-note').textContent = config.reason || 'Os módulos de voz local precisam ser preparados pela equipe. O atendimento por texto continua disponível.';
        notice($('#voice-gender-note').textContent);
        return;
      }
      for (const engine of [asr,tts]) {
        const info = config[`models_${engine.name}`] || {};
        if (typeof info.worker_url === 'string') engine.path = info.worker_url;
        const megabytes = Number(info.download_mb);
        const size = Number.isFinite(megabytes) && megabytes > 0 ? `aproximadamente ${Math.round(megabytes)} MB` : engine.name === 'asr' ? 'aproximadamente 100 MB' : 'mais de 100 MB';
        $(`#${engine.name}-model-description`).textContent = `${engine.name === 'asr' ? 'Transcrição em português com Whisper.' : 'Voz em português gerada no seu dispositivo.'} Download inicial de ${size}.`;
        engine.render();
      }
      $('#voice-local-models').hidden = false;
      recordButton.removeAttribute('aria-disabled');
      $('#voice-availability').textContent = 'Áudio local · português do Brasil';
      const needsGender = !config.gender_locked;
      $('#voice-gender-field').hidden = !needsGender;
      gender.value = config.gender_locked ? lockedGender : canonicalGender(preferences.gender);
      $('#voice-gender-note').textContent = needsGender
        ? 'O caso não informa o sexo do paciente. Escolha uma voz feminina ou masculina para esta consulta.'
        : `Voz ${lockedGender === 'female' ? 'feminina' : 'masculina'} definida pelo caso. Você pode ajustar o timbre disponível.`;
      fillVariants(preferences.voice_id);
      auto.checked = preferences.autoplay;
      if (needsGender && !gender.value) settingsOpen(true);
    } catch (error) {
      config = {available:false,reason:error instanceof TypeError ? 'A conexão falhou ao verificar as opções de voz. Atualize a página para tentar novamente; a consulta por texto continua disponível.' : error.message};
      $('#voice-availability').textContent = 'Voz indisponível no momento';
      $('#voice-gender-note').textContent = config.reason;
      variant.replaceChildren(new Option('Voz indisponível',''));
      variant.disabled = true;
      auto.disabled = true;
      recordButton.setAttribute('aria-disabled','true');
      notice(config.reason);
    } finally { loadingConfig = false; }
  }
  gender.addEventListener('change',() => { stopPlayback(); fillVariants(); savePreferences(); notice(''); });
  variant.addEventListener('change',() => { stopPlayback(); savePreferences(); notice(''); });
  auto.addEventListener('change',() => {
    savePreferences();
    if (!auto.checked) stopPlayback();
    else if (tts.state !== 'ready') notice('A reprodução automática começará depois que você ativar a voz do paciente e enviar uma nova pergunta.');
  });
  function listenLabel(button,text) {
    const label = button.querySelector('span');
    if (label) label.textContent = text;
    else button.textContent = text;
  }
  function stopPlayback() {
    playbackGeneration++;
    playbackController?.abort();
    playbackController = null;
    if (synthesizing) { synthesizing = false; tts.terminate(); }
    for (const source of playbackSources) { try { source.stop(); } catch { /* Already ended. */ } }
    playbackSources.clear();
    if (playbackContext) playbackContext.close().catch(() => {});
    playbackContext = null;
    playbackCursor = 0;
    playbackChunks = 0;
    playbackFinished = false;
    if (playbackAudio) { playbackAudio.pause(); playbackAudio.removeAttribute('src'); playbackAudio.load(); }
    playbackAudio = null;
    if (playbackUrl) URL.revokeObjectURL(playbackUrl);
    playbackUrl = null;
    if (playbackButton) {
      playbackButton.classList.remove('is-playing');
      playbackButton.removeAttribute('aria-busy');
      playbackButton.setAttribute('aria-label','Ouvir resposta do paciente');
      listenLabel(playbackButton,'Ouvir resposta');
    }
    playbackButton = null;
    $('#voice-player').hidden = true;
  }
  $('#voice-stop-playback').addEventListener('click',stopPlayback);
  function samplesWav(samples, sampleRate) {
    if (!(samples instanceof Float32Array) || !samples.length || !Number.isSafeInteger(sampleRate) || sampleRate < 8000 || sampleRate > 48000 || samples.length > sampleRate * 600) {
      throw new Error('O modelo local não retornou um áudio válido. Tente novamente.');
    }
    const output = new ArrayBuffer(44 + samples.length * 2);
    const view = new DataView(output);
    const ascii = (offset,text) => { for (let index=0; index<text.length; index++) view.setUint8(offset+index,text.charCodeAt(index)); };
    ascii(0,'RIFF'); view.setUint32(4,36+samples.length*2,true); ascii(8,'WAVE');
    ascii(12,'fmt '); view.setUint32(16,16,true); view.setUint16(20,1,true); view.setUint16(22,1,true);
    view.setUint32(24,sampleRate,true); view.setUint32(28,sampleRate*2,true); view.setUint16(32,2,true); view.setUint16(34,16,true);
    ascii(36,'data'); view.setUint32(40,samples.length*2,true);
    for (let index=0; index<samples.length; index++) {
      if (!Number.isFinite(samples[index])) throw new Error('O áudio local apresentou uma falha. Tente novamente.');
      const sample = Math.max(-1,Math.min(1,samples[index]));
      view.setInt16(44+index*2,Math.round(sample < 0 ? sample*32768 : sample*32767),true);
    }
    return new Blob([output],{type:'audio/wav'});
  }
  async function playTurn(turnId, button, automatic = false) {
    if (button === playbackButton) { stopPlayback(); return; }
    if (!availableOrExplain()) return;
    if (recording || pendingPermission || transcription) { notice('Conclua sua gravação antes de ouvir o paciente.'); return; }
    const voice = currentVoice();
    if (!voice) { settingsOpen(true); notice('Escolha uma voz para ouvir o paciente.'); gender.focus(); return; }
    if (!tts.requireReady()) return;
    stopPlayback();
    if (tts.state !== 'ready') { tts.requireReady(); return; }
    const generation = playbackGeneration;
    playbackButton = button;
    playbackController = new AbortController();
    button.classList.add('is-playing');
    button.setAttribute('aria-busy','true');
    button.setAttribute('aria-label','Cancelar áudio da resposta');
    listenLabel(button,'Gerando voz local…');
    $('#voice-player').hidden = false;
    $('#voice-player-label').textContent = 'Preparando a voz neste dispositivo…';
    notice('');
    try {
      // Resume in the click gesture so the first generated fragment can play
      // immediately. Browsers may require a fresh click for automatic playback.
      const PlaybackContext = window.AudioContext || window.webkitAudioContext;
      if (PlaybackContext) {
        playbackContext = new PlaybackContext();
        let resumeTimeout;
        try {
          await Promise.race([
            playbackContext.resume(),
            new Promise((_,reject) => { resumeTimeout = setTimeout(() => {
              const error = new Error('Clique em “Ouvir resposta” para iniciar o áudio.');
              error.name = 'NotAllowedError';
              reject(error);
            },1500); })
          ]);
        } finally { clearTimeout(resumeTimeout); }
        if (generation !== playbackGeneration) return;
      }
      // The server supplies only a persisted turn owned by this consultation.
      // No recording or generated audio is included in any HTTP request.
      const response = await fetch(`${base}/falar`, {
        method:'POST',credentials:'same-origin',signal:playbackController.signal,
        headers:{'Content-Type':'application/json','X-CSRFToken':csrf,Accept:'application/json'},
        body:JSON.stringify({turn_id:Number(turnId),voice_id:voice.id})
      });
      const data = await response.json().catch(() => { throw new Error('Não foi possível obter a resposta para reprodução. Atualize a página e tente novamente.'); });
      if (!response.ok) throw new Error(data.error || 'Não foi possível preparar esta resposta. Tente novamente.');
      if (generation !== playbackGeneration) return;
      if (typeof data.text !== 'string' || !data.text.trim() || data.voice_id !== voice.id) throw new Error('A resposta de voz não pôde ser validada. Tente novamente.');
      synthesizing = true;
      const streamed = Boolean(playbackContext);
      const result = await tts.request('synthesize',{text:data.text,voiceId:data.voice_id,stream:streamed},[],progress => {
        if (generation === playbackGeneration && !playbackChunks) $('#voice-player-label').textContent = progress.message || 'Gerando a voz neste dispositivo…';
      },chunk => {
        if (generation !== playbackGeneration || !playbackContext) return;
        if (!(chunk.samples instanceof Float32Array) || !chunk.samples.length || chunk.sampleRate !== 24000 || chunk.samples.length > 24000 * 90 || chunk.index !== playbackChunks) {
          throw new Error('A sequência do áudio local não pôde ser lida. Tente novamente.');
        }
        if (playbackContext.state !== 'running') {
          const error = new Error('Clique em “Ouvir resposta” para iniciar o áudio neste navegador.');
          error.name = 'NotAllowedError';
          throw error;
        }
        for (const sample of chunk.samples) if (!Number.isFinite(sample)) throw new Error('O áudio local apresentou uma falha. Tente novamente.');
        const buffer = playbackContext.createBuffer(1,chunk.samples.length,chunk.sampleRate);
        buffer.copyToChannel(chunk.samples,0);
        const source = playbackContext.createBufferSource();
        source.buffer = buffer;
        source.connect(playbackContext.destination);
        playbackSources.add(source);
        source.addEventListener('ended',() => {
          playbackSources.delete(source);
          source.disconnect();
          if (generation === playbackGeneration && !playbackSources.size) {
            if (playbackFinished) stopPlayback();
            else $('#voice-player-label').textContent = 'Gerando o próximo trecho neste dispositivo…';
          }
        },{once:true});
        const start = Math.max(playbackContext.currentTime + 0.04,playbackCursor);
        source.start(start);
        playbackCursor = start + buffer.duration;
        playbackChunks++;
        button.removeAttribute('aria-busy');
        button.setAttribute('aria-label','Parar áudio da resposta');
        listenLabel(button,'Parar áudio');
        $('#voice-player-label').textContent = `Ouvindo o paciente · ${voice.label || 'Português do Brasil'}`;
      });
      if (generation !== playbackGeneration) return;
      synthesizing = false;
      if (result.streamed) {
        playbackFinished = true;
        if (!playbackChunks) throw new Error('O modelo local não retornou um áudio. Tente novamente.');
        if (!playbackSources.size) stopPlayback();
        return;
      }
      playbackUrl = URL.createObjectURL(samplesWav(result.samples,result.sampleRate));
      playbackAudio = new Audio(playbackUrl);
      playbackAudio.preload = 'auto';
      playbackAudio.addEventListener('ended',() => { if (generation === playbackGeneration) stopPlayback(); },{once:true});
      playbackAudio.addEventListener('error',() => {
        if (generation === playbackGeneration) { stopPlayback(); notice('Seu navegador não conseguiu reproduzir este áudio. Você pode continuar por texto.',true); }
      },{once:true});
      await playbackAudio.play();
      if (generation !== playbackGeneration) return;
      button.removeAttribute('aria-busy');
      button.setAttribute('aria-label','Parar áudio da resposta');
      listenLabel(button,'Parar áudio');
      $('#voice-player-label').textContent = `Ouvindo o paciente · ${voice.label || 'Português do Brasil'}`;
    } catch (error) {
      if (generation !== playbackGeneration || error.name === 'AbortError') return;
      stopPlayback();
      if (error.name === 'NotAllowedError') notice(automatic ? 'O navegador pausou a reprodução automática. Clique em “Ouvir resposta” para escutar.' : 'O navegador bloqueou a reprodução. Tente novamente pelo botão “Ouvir resposta”.');
      else notice(error instanceof TypeError ? 'Não foi possível preparar o áudio. Verifique a conexão ou continue por texto.' : error.message,true);
    }
  }
  function addListenButton(node, turnId) {
    let button = node.querySelector('[data-listen-turn]');
    if (!button) {
      button = document.createElement('button');
      button.type = 'button';
      button.className = 'listen-response';
      button.dataset.listenTurn = String(turnId);
      button.setAttribute('aria-label','Ouvir resposta do paciente');
      const templateIcon = $('#voice-studio .voice-mark svg');
      if (templateIcon) { const icon = templateIcon.cloneNode(true); icon.setAttribute('width','13'); icon.setAttribute('height','13'); button.append(icon); }
      const label = document.createElement('span'); label.textContent = 'Ouvir resposta'; button.append(label);
      node.querySelector('.message-bubble')?.parentElement.append(button);
    }
    button.addEventListener('click',() => playTurn(turnId,button));
    return button;
  }
  document.querySelectorAll('.patient-message[data-turn-id]').forEach(node => addListenButton(node,node.dataset.turnId));
  app.addEventListener('medlab:turn-saved',event => {
    const {turnId,element} = event.detail;
    const button = addListenButton(element,turnId);
    if (auto.checked && config?.available && tts.state === 'ready') playTurn(turnId,button,true);
  });
  app.addEventListener('medlab:conversation-busy',event => {
    conversationBusy = event.detail.busy;
    recordButton.disabled = conversationBusy || app.dataset.voiceBusy === 'true';
    if (conversationBusy) stopPlayback();
  });
  function setRecordingBusy(value) {
    app.dataset.voiceBusy = String(value);
    recordButton.disabled = value || conversationBusy;
    $('#question').readOnly = value;
    $('#chat-form button[type=submit]').disabled = value || conversationBusy;
  }
  function releaseStream(stream) { stream?.getTracks().forEach(track => track.stop()); }
  function resetRecordingUi() {
    $('#voice-recording').hidden = true;
    $('#voice-transcribing').hidden = true;
    $('#microphone-status').textContent = 'Microfone desligado';
    recordButton.classList.remove('is-recording');
    setRecordingBusy(false);
  }
  function stopRecording(discard = false) {
    if (!recording) return;
    const item = recording;
    item.discard = discard;
    clearInterval(item.interval);
    clearTimeout(item.timeout);
    if (item.recorder.state !== 'inactive') item.recorder.stop();
    releaseStream(item.stream);
    $('#voice-recording').hidden = true;
    $('#microphone-status').textContent = discard ? 'Microfone desligado' : 'Transcrevendo localmente…';
    if (discard) { notice('Gravação descartada. O áudio permaneceu no navegador.'); resetRecordingUi(); }
  }
  $('#voice-cancel-recording').addEventListener('click',() => stopRecording(true));
  $('#voice-finish-recording').addEventListener('click',() => stopRecording(false));
  async function pcmSamples(blob) {
    const DecodeContext = window.AudioContext || window.webkitAudioContext;
    const RenderContext = window.OfflineAudioContext || window.webkitOfflineAudioContext;
    if (!DecodeContext || !RenderContext) throw new Error('Seu navegador não consegue preparar o áudio. Tente outro navegador ou continue por texto.');
    const decoder = new DecodeContext();
    let decoded;
    try { decoded = await decoder.decodeAudioData(await blob.arrayBuffer()); }
    catch { throw new Error('O navegador não conseguiu ler a gravação. Grave novamente ou continue por texto.'); }
    finally { await decoder.close().catch(() => {}); }
    const sampleRate = 16000;
    const frameCount = Math.min(Math.floor(decoded.duration * sampleRate),45 * sampleRate);
    if (frameCount < sampleRate / 5) throw new Error('A gravação ficou muito curta. Grave sua pergunta por pelo menos um segundo.');
    const renderer = new RenderContext(1,frameCount,sampleRate);
    const source = renderer.createBufferSource();
    source.buffer = decoded;
    source.connect(renderer.destination);
    source.start();
    const rendered = await renderer.startRendering();
    // Copy before transferring ownership to the worker; an AudioBuffer's own
    // backing store may not be transferable in every browser.
    return new Float32Array(rendered.getChannelData(0));
  }
  function cancelTranscription() {
    if (!transcription) return;
    transcription.cancelled = true;
    if (transcription.sent) asr.terminate();
    transcription = null;
    resetRecordingUi();
    notice('Transcrição cancelada. O áudio permaneceu no navegador.');
  }
  $('#voice-cancel-transcription').addEventListener('click',cancelTranscription);
  async function transcribe(item) {
    if (recording === item) recording = null;
    if (item.discard) return;
    const blob = new Blob(item.chunks,{type:item.mimeType});
    item.chunks = [];
    if (!blob.size) { resetRecordingUi(); notice('A gravação ficou vazia. Tente novamente.',true); return; }
    const operation = {cancelled:false,sent:false};
    transcription = operation;
    $('#voice-transcribing').hidden = false;
    notice('Transcrevendo neste dispositivo. Você poderá revisar o texto antes de enviar.');
    try {
      const audio = await pcmSamples(blob);
      if (operation.cancelled) return;
      operation.sent = true;
      const data = await asr.request('transcribe',{audio,sampleRate:16000},[audio.buffer],progress => {
        if (!operation.cancelled && progress.message) notice(progress.message);
      });
      if (operation.cancelled) return;
      if (typeof data.text !== 'string' || !data.text.trim()) throw new Error('Não identificamos uma fala. Tente novamente, perto do microfone.');
      if (data.text.length > 2000) throw new Error('A transcrição ultrapassou o limite da pergunta. Grave um trecho mais curto.');
      $('#voice-transcript').value = data.text.trim();
      $('#voice-review').hidden = false;
      $('#voice-transcript').focus();
      notice('Transcrição pronta. Revise o texto; nada foi enviado ao paciente.');
    } catch (error) {
      if (!operation.cancelled && error.name !== 'AbortError') {
        if (['NOT_READY','MODEL_LOAD_FAILED'].includes(error.code)) asr.terminate();
        notice(error.message || 'Não foi possível transcrever. Grave novamente ou continue por texto.',true);
      }
    } finally {
      if (transcription === operation) { transcription = null; resetRecordingUi(); }
    }
  }
  recordButton.addEventListener('click',async () => {
    if (!availableOrExplain() || conversationBusy || pendingPermission || recording || transcription || !asr.requireReady()) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
      notice('A gravação requer um navegador compatível e uma conexão segura. Você pode continuar por texto.',true); return;
    }
    if (!$('#voice-review').hidden) { notice('Use ou descarte a transcrição anterior antes de gravar outra pergunta.'); $('#voice-transcript').focus(); return; }
    stopPlayback();
    pendingPermission = true;
    const permissionToken = ++permissionGeneration;
    setRecordingBusy(true);
    notice('Permita o uso do microfone para gravar sua pergunta.');
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
      if (!pendingPermission || permissionToken !== permissionGeneration) { releaseStream(stream); return; }
      const types = ['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4','audio/webm'];
      const mimeType = types.find(type => MediaRecorder.isTypeSupported(type)) || '';
      const recorder = mimeType ? new MediaRecorder(stream,{mimeType,audioBitsPerSecond:96000}) : new MediaRecorder(stream);
      const item = {recorder,stream,chunks:[],mimeType:recorder.mimeType || mimeType || 'audio/webm',discard:false,started:Date.now(),interval:null,timeout:null};
      recording = item;
      recorder.addEventListener('dataavailable',event => { if (event.data.size) item.chunks.push(event.data); });
      recorder.addEventListener('stop',() => transcribe(item),{once:true});
      recorder.addEventListener('error',() => { item.discard = true; stopRecording(true); recording = null; notice('O navegador interrompeu a gravação. Tente novamente ou continue por texto.',true); });
      recorder.start(250);
      $('#voice-recording').hidden = false;
      $('#voice-recording-time').textContent = '0:00';
      $('#microphone-status').textContent = 'Microfone ligado';
      recordButton.classList.add('is-recording');
      const maxSeconds = Math.min(Number(config.max_recording_seconds) || 45,45);
      item.interval = setInterval(() => {
        const elapsed = Math.floor((Date.now()-item.started)/1000);
        $('#voice-recording-time').textContent = `${Math.floor(elapsed/60)}:${String(elapsed%60).padStart(2,'0')}`;
      },500);
      item.timeout = setTimeout(() => { if (recording === item) stopRecording(false); },maxSeconds*1000);
      notice(`Grave uma pergunta de até ${maxSeconds} segundos. O áudio será transcrito neste navegador.`);
    } catch (error) {
      releaseStream(stream);
      if (permissionToken !== permissionGeneration) return;
      if (recording) { clearInterval(recording.interval); clearTimeout(recording.timeout); recording.discard = true; recording = null; }
      resetRecordingUi();
      if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') notice('O acesso ao microfone não foi permitido. Você pode habilitá-lo nas permissões do navegador ou continuar por texto.');
      else if (error.name === 'NotFoundError') notice('Não encontramos um microfone disponível. Conecte um microfone ou continue por texto.',true);
      else notice('Não foi possível iniciar o microfone. Verifique se outro aplicativo está usando o dispositivo e tente novamente.',true);
    } finally { if (permissionToken === permissionGeneration) pendingPermission = false; }
  });
  $('#voice-discard-transcript').addEventListener('click',() => {
    $('#voice-transcript').value = '';
    $('#voice-review').hidden = true;
    notice('Transcrição descartada. Continue a consulta no seu ritmo.');
    $('#question').focus();
  });
  $('#voice-use-transcript').addEventListener('click',() => {
    const text = $('#voice-transcript').value.trim();
    if (!text) { notice('A transcrição está vazia. Escreva sua pergunta ou grave novamente.'); $('#voice-transcript').focus(); return; }
    const question = $('#question');
    const existing = question.value.trim();
    const combined = existing ? `${existing}\n${text}` : text;
    if (combined.length > question.maxLength) { notice('O texto somado à pergunta ultrapassa 2.000 caracteres. Encurte a transcrição ou ajuste a pergunta antes de continuar.',true); return; }
    question.value = combined;
    question.dispatchEvent(new Event('input',{bubbles:true}));
    $('#voice-transcript').value = '';
    $('#voice-review').hidden = true;
    notice('Texto na pergunta. Confira e clique em enviar quando estiver pronto.');
    question.focus();
  });
  app.addEventListener('medlab:tab-change',event => {
    if (event.detail.name !== 'chat') {
      stopPlayback();
      if (recording) stopRecording(true);
      cancelTranscription();
      if (pendingPermission) { pendingPermission = false; permissionGeneration++; setRecordingBusy(false); }
    }
  });
  window.addEventListener('pagehide',() => {
    pendingPermission = false;
    permissionGeneration++;
    if (recording) stopRecording(true);
    cancelTranscription();
    stopPlayback();
    asr.terminate();
    tts.terminate();
  });
  loadConfig();
})();
