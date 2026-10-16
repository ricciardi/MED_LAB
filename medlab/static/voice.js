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
  const continuousButton = $('#voice-continuous-toggle');
  let continuousActive = false;
  let continuousState = 'idle';
  let continuousGeneration = 0;
  let continuousSequence = 0;
  let continuousTurnToken = null;
  let continuousContext = null;
  let continuousTimer = null;
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
  let playbackRevealTimer = null;
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
      this.activation = null;
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
        if (continuousActive) stopContinuous(message,true);
        notice(message,true);
      });
      worker.addEventListener('messageerror',() => {
        if (this.worker !== worker) return;
        const message = 'O navegador não conseguiu ler o resultado da voz. Tente novamente.';
        this.terminate(message,'error');
        if (continuousActive) stopContinuous(message,true);
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
      if (!availableOrExplain()) return false;
      if (this.state === 'ready') return true;
      if (this.state === 'loading') return this.activation;
      const revision = ++this.revision;
      this.state = 'loading';
      this.render();
      this.updateProgress({message:'Preparando o download. Os arquivos em cache serão reutilizados.'});
      notice('');
      this.activation = (async () => { try {
        const result = await this.request('load');
        if (revision !== this.revision) return false;
        this.device = result.device || '';
        this.state = 'ready';
        this.render();
        notice(this.name === 'asr'
          ? 'Transcrição pronta. Clique em “Ditar pergunta” quando quiser usar o microfone.'
          : 'Voz pronta. Use “Ouvir resposta” abaixo de uma fala do paciente.');
        return true;
      } catch (error) {
        if (revision !== this.revision || error.name === 'AbortError') return false;
        this.terminate(error.message,'error');
        notice(error.message,true);
        return false;
      } })();
      return this.activation;
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
      if (continuousActive) stopContinuous('Preparo cancelado. O modo contínuo foi pausado.');
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
        const size = Number.isFinite(megabytes) && megabytes > 0 ? `aproximadamente ${Math.round(megabytes)} MB` : engine.name === 'asr' ? 'aproximadamente 175 MB' : 'mais de 100 MB';
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
  gender.addEventListener('change',() => { if (continuousActive) stopContinuous('Voz alterada. Inicie a conversa contínua novamente quando quiser.'); stopPlayback(); fillVariants(); savePreferences(); });
  variant.addEventListener('change',() => { if (continuousActive) stopContinuous('Timbre alterado. Inicie a conversa contínua novamente quando quiser.'); stopPlayback(); savePreferences(); });
  auto.addEventListener('change',() => {
    savePreferences();
    if (!auto.checked && !continuousActive) stopPlayback();
    else if (tts.state !== 'ready') notice('A reprodução automática começará depois que você ativar a voz do paciente e enviar uma nova pergunta.');
  });
  function listenLabel(button,text) {
    const label = button.querySelector('span');
    if (label) label.textContent = text;
    else button.textContent = text;
  }
  function stopPlayback() {
    playbackGeneration++;
    clearTimeout(playbackRevealTimer);
    playbackRevealTimer = null;
    playbackController?.abort();
    playbackController = null;
    if (synthesizing) { synthesizing = false; tts.terminate(); }
    for (const source of playbackSources) { try { source.stop(); } catch { /* Already ended. */ } }
    playbackSources.clear();
    if (playbackContext && playbackContext !== continuousContext) playbackContext.close().catch(() => {});
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
  $('#voice-stop-playback').addEventListener('click',() => {
    if (continuousActive) stopContinuous('Conversa contínua pausada. Microfone desligado.');
    else stopPlayback();
  });
  function playbackEnded() {
    if (continuousActive && continuousTurnToken) app.dispatchEvent(new CustomEvent('medlab:voice-response-start',{detail:{token:continuousTurnToken}}));
    stopPlayback();
    if (!continuousActive || continuousState !== 'speaking') return;
    continuousTurnToken = null;
    const generation = continuousGeneration;
    // Let the last sound and room reverberation finish before opening a new
    // stream. A worker's final result alone never reopens the microphone.
    continuousTimer = setTimeout(() => {
      if (continuousActive && generation === continuousGeneration && !document.hidden) startRecording(generation);
    },350);
  }
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
    if (button === playbackButton) { if (continuousActive) stopContinuous('Conversa contínua pausada. Microfone desligado.'); else stopPlayback(); return; }
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
        playbackContext = continuousContext || new PlaybackContext();
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
      const synthesisStarted = playbackContext?.currentTime;
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
            if (playbackFinished) playbackEnded();
            else $('#voice-player-label').textContent = 'Gerando o próximo trecho neste dispositivo…';
          }
        },{once:true});
        // If the first block takes longer to generate than to speak, leave a
        // small, bounded head start for the next inference. Fast devices and
        // single-block replies retain the immediate 40 ms schedule. The text
        // reveal below follows this actual start time, not chunk arrival.
        const needsLead = playbackChunks === 0 && chunk.total > 1 &&
          playbackContext.currentTime - synthesisStarted >= buffer.duration;
        const start = Math.max(playbackContext.currentTime + (needsLead ? 0.7 : 0.04),playbackCursor);
        source.start(start);
        playbackCursor = start + buffer.duration;
        playbackChunks++;
        if (playbackChunks === 1 && continuousActive) {
          const token = continuousTurnToken;
          playbackRevealTimer = setTimeout(() => {
            if (generation === playbackGeneration && continuousActive && playbackContext?.state === 'running') app.dispatchEvent(new CustomEvent('medlab:voice-response-start',{detail:{token}}));
          },Math.max(0,(start-playbackContext.currentTime)*1000));
        }
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
        if (!playbackSources.size) playbackEnded();
        return;
      }
      playbackUrl = URL.createObjectURL(samplesWav(result.samples,result.sampleRate));
      playbackAudio = new Audio(playbackUrl);
      playbackAudio.preload = 'auto';
      playbackAudio.addEventListener('ended',() => { if (generation === playbackGeneration) playbackEnded(); },{once:true});
      playbackAudio.addEventListener('error',() => {
        if (generation === playbackGeneration) { if (continuousActive) stopContinuous('O áudio foi interrompido. O modo contínuo foi pausado.',true); else stopPlayback(); notice('Seu navegador não conseguiu reproduzir este áudio. Você pode continuar por texto.',true); }
      },{once:true});
      await playbackAudio.play();
      if (generation !== playbackGeneration) return;
      if (continuousActive) app.dispatchEvent(new CustomEvent('medlab:voice-response-start',{detail:{token:continuousTurnToken}}));
      button.removeAttribute('aria-busy');
      button.setAttribute('aria-label','Parar áudio da resposta');
      listenLabel(button,'Parar áudio');
      $('#voice-player-label').textContent = `Ouvindo o paciente · ${voice.label || 'Português do Brasil'}`;
    } catch (error) {
      if (generation !== playbackGeneration || error.name === 'AbortError') return;
      if (continuousActive) stopContinuous('O áudio não pôde ser reproduzido. O modo contínuo foi pausado.',true);
      else stopPlayback();
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
    button.addEventListener('click',() => {
      if (continuousActive) { stopContinuous('Conversa contínua pausada. Clique em “Ouvir resposta” para ouvir este trecho.'); return; }
      playTurn(turnId,button);
    });
    return button;
  }
  document.querySelectorAll('.patient-message[data-turn-id]').forEach(node => addListenButton(node,node.dataset.turnId));
  app.addEventListener('medlab:turn-saved',event => {
    const {turnId,element,voiceToken} = event.detail;
    const button = addListenButton(element,turnId);
    if (voiceToken) {
      if (continuousActive && voiceToken === continuousTurnToken) { setContinuousState('speaking'); playTurn(turnId,button,true); }
      return;
    }
    if (auto.checked && config?.available && tts.state === 'ready') playTurn(turnId,button,true);
  });
  app.addEventListener('medlab:message-error',event => {
    if (continuousActive && event.detail?.voiceToken === continuousTurnToken) stopContinuous(event.detail.message || 'A conversa foi pausada. Confira o texto antes de continuar.',true);
  });
  app.addEventListener('medlab:conversation-busy',event => {
    conversationBusy = event.detail.busy;
    recordButton.disabled = continuousActive || conversationBusy || app.dataset.voiceBusy === 'true';
    if (conversationBusy) stopPlayback();
  });
  function setRecordingBusy(value) {
    app.dataset.voiceBusy = String(value);
    recordButton.disabled = continuousActive || value || conversationBusy;
    $('#question').readOnly = continuousActive || value;
    $('#chat-form button[type=submit]').disabled = value || conversationBusy;
  }
  function setContinuousState(state) {
    continuousState = state;
    app.dataset.continuousState = state;
    continuousButton.setAttribute('aria-pressed',String(continuousActive));
    continuousButton.textContent = continuousActive ? 'Parar conversa contínua' : 'Iniciar conversa contínua';
    const labels = {idle:'Desligada',preparing:'Preparando voz e microfone…',listening:'Escutando — faça sua pergunta',transcribing:'Transcrevendo sua fala…',waiting:'Aguardando o paciente…',speaking:'Voz do paciente — microfone desligado'};
    $('#voice-continuous-state').textContent = labels[state];
    app.dispatchEvent(new CustomEvent('medlab:continuous-state',{detail:{active:continuousActive,state}}));
  }
  function stopContinuous(message = 'Conversa contínua pausada. Microfone desligado.', error = false) {
    continuousActive = false;
    continuousGeneration++;
    clearTimeout(continuousTimer);
    continuousTimer = null;
    pendingPermission = false;
    permissionGeneration++;
    if (recording) stopRecording(true);
    cancelTranscription();
    if (continuousTurnToken) app.dispatchEvent(new CustomEvent('medlab:cancel-voice-send',{detail:{token:continuousTurnToken}}));
    continuousTurnToken = null;
    for (const engine of [asr,tts]) if (engine.state === 'loading') engine.terminate();
    stopPlayback();
    continuousContext?.close().catch(() => {});
    continuousContext = null;
    resetRecordingUi();
    setContinuousState('idle');
    if (message) notice(message,error);
  }
  continuousButton.addEventListener('click',async () => {
    if (continuousActive) { stopContinuous(); return; }
    if (!availableOrExplain()) return;
    if (conversationBusy || recording || pendingPermission || transcription) { notice('Conclua a operação atual antes de iniciar a conversa contínua.'); return; }
    if ($('#question').value.trim() || !$('#voice-review').hidden) { notice('Envie ou descarte o texto que está em revisão antes de iniciar a conversa contínua.'); return; }
    if (!currentVoice()) { settingsOpen(true); notice('Escolha uma voz feminina ou masculina antes de iniciar.'); gender.focus(); return; }
    const Context = window.AudioContext || window.webkitAudioContext;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined' || !Context) { notice('O modo contínuo precisa de microfone e áudio em um navegador compatível com conexão segura.',true); return; }
    stopPlayback();
    continuousActive = true;
    const generation = ++continuousGeneration;
    setContinuousState('preparing');
    setRecordingBusy(true);
    try {
      // Unlock playback synchronously in this explicit user gesture. Keeping
      // the context alive avoids a new autoplay permission after STT/API waits.
      continuousContext = new Context();
      const context = continuousContext;
      context.addEventListener('statechange',() => {
        if (continuousActive && continuousContext === context && continuousState !== 'preparing' && context.state !== 'running') stopContinuous('O navegador suspendeu o áudio. A conversa contínua foi pausada.',true);
      });
      const resumed = continuousContext.resume();
      let timer;
      try { await Promise.race([resumed,new Promise((_,reject) => { timer = setTimeout(() => reject(new Error('O navegador não liberou o áudio. Inicie novamente ou use o modo manual.')),1500); })]); }
      finally { clearTimeout(timer); }
      if (!continuousActive || generation !== continuousGeneration) return;
      const ready = await Promise.all([asr.activate(),tts.activate()]);
      if (!continuousActive || generation !== continuousGeneration) return;
      if (ready.some(value => !value)) throw new Error('Não foi possível preparar todos os modelos. O modo contínuo foi pausado.');
      if (document.hidden) { stopContinuous('A página ficou oculta. Inicie novamente quando voltar.'); return; }
      await startRecording(generation);
    } catch (error) {
      if (generation === continuousGeneration) stopContinuous(error.message || 'Não foi possível iniciar a conversa contínua.',true);
    }
  });

  // Energy-based endpointing, not speech recognition. Short clicks and silence
  // never reach ASR. Background noise may still require the manual mode.
  class VoiceActivity {
    constructor(started) { this.started = started; this.last = started; this.lastVoice = null; this.voicedMs = 0; this.noise = .001; this.calibrationMinimum = Infinity; this.onsetFrames = []; }
    sample(rms, now) {
      const elapsed = Math.max(0,Math.min(now-this.last,100));
      this.last = now;
      if (this.lastVoice === null) {
        // Retain the first syllables while estimating the noise floor. Someone
        // may speak immediately: a later quiet frame can establish that those
        // initial frames were speech. The recording itself is never trimmed.
        this.onsetFrames.push({rms,now,elapsed});
        this.onsetFrames = this.onsetFrames.filter(frame => now-frame.now <= 1500);
        this.calibrationMinimum = Math.min(this.calibrationMinimum,rms);
        this.noise = Math.max(.0005,this.calibrationMinimum);
        if (now-this.started <= 250) return 'listening';
        const entryThreshold = Math.max(.006,this.noise*3);
        for (const frame of this.onsetFrames) {
          if (frame.rms >= entryThreshold) { this.voicedMs += frame.elapsed; this.lastVoice = frame.now; }
        }
        if (this.lastVoice !== null) this.onsetFrames = [];
      } else {
        // Hysteresis preserves quieter word endings without treating the same
        // low level as a fresh onset; noise-only captures still time out.
        const continuing = now-this.lastVoice <= 250;
        const threshold = continuing ? Math.max(.0035,this.noise*1.6) : Math.max(.006,this.noise*3);
        if (rms >= threshold) { this.voicedMs += elapsed; this.lastVoice = now; }
      }
      if (this.voicedMs >= 350 && now-this.started >= 900 && this.lastVoice !== null && now-this.lastVoice >= 1600) return 'complete';
      if (this.voicedMs < 350 && now-this.started >= 15000) return 'silent';
      return 'listening';
    }
  }
  function attachVoiceActivity(item) {
    const context = continuousContext;
    if (!context || context.state !== 'running') throw new Error('O áudio do navegador foi suspenso. Inicie a conversa contínua novamente.');
    item.vad = new VoiceActivity(Date.now());
    item.input = context.createMediaStreamSource(item.stream);
    item.analyser = context.createAnalyser();
    item.analyser.fftSize = 2048;
    item.input.connect(item.analyser); // No connection to speakers: no mic monitoring.
    const samples = new Float32Array(item.analyser.fftSize);
    item.vadInterval = setInterval(() => {
      if (recording !== item || item.stopping) return;
      if (context.state !== 'running') { stopContinuous('O áudio do navegador foi suspenso. A conversa contínua foi pausada.',true); return; }
      item.analyser.getFloatTimeDomainData(samples);
      let energy = 0;
      for (const value of samples) energy += value*value;
      const decision = item.vad.sample(Math.sqrt(energy/samples.length),Date.now());
      if (decision === 'complete') stopRecording(false);
      if (decision === 'silent') stopContinuous('Não identificamos fala. O microfone foi desligado; inicie novamente quando quiser.');
    },50);
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
    if (item.stopping) { if (discard) item.discard = true; return; }
    if (!discard && item.continuousGeneration !== null && item.vad?.voicedMs < 350) { stopContinuous('A gravação não teve fala suficiente. O microfone foi desligado.'); return; }
    item.stopping = true;
    item.discard = discard;
    clearInterval(item.interval);
    clearInterval(item.vadInterval);
    clearTimeout(item.timeout);
    item.input?.disconnect();
    item.analyser?.disconnect();
    if (item.recorder.state !== 'inactive') item.recorder.stop();
    releaseStream(item.stream);
    $('#voice-recording').hidden = true;
    $('#microphone-status').textContent = discard ? 'Microfone desligado' : 'Transcrevendo localmente…';
    if (discard) { notice('Gravação descartada. O áudio permaneceu no navegador.'); resetRecordingUi(); }
  }
  $('#voice-cancel-recording').addEventListener('click',() => { if (continuousActive) stopContinuous(); else stopRecording(true); });
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
  $('#voice-cancel-transcription').addEventListener('click',() => { if (continuousActive) stopContinuous(); else cancelTranscription(); });
  async function transcribe(item) {
    if (recording === item) recording = null;
    if (item.continuousGeneration !== null && (!continuousActive || item.continuousGeneration !== continuousGeneration)) item.discard = true;
    if (item.discard) return;
    const blob = new Blob(item.chunks,{type:item.mimeType});
    item.chunks = [];
    if (!blob.size) { if (continuousActive) stopContinuous('A gravação ficou vazia. O modo contínuo foi pausado.',true); else { resetRecordingUi(); notice('A gravação ficou vazia. Tente novamente.',true); } return; }
    const operation = {cancelled:false,sent:false};
    transcription = operation;
    $('#voice-transcribing').hidden = false;
    const continuous = item.continuousGeneration !== null;
    if (continuous) setContinuousState('transcribing');
    notice(continuous ? 'Transcrevendo localmente. A pergunta será enviada automaticamente.' : 'Transcrevendo neste dispositivo. Você poderá revisar o texto antes de enviar.');
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
      if (continuous && data.needsReview === true) {
        transcription = null;
        stopContinuous('O volume da gravação ficou muito baixo ou saturado. Confira o texto ou grave novamente; a pergunta não foi enviada.',true);
        $('#voice-transcript').value = data.text.trim();
        $('#voice-review').hidden = false;
        $('#voice-transcript').focus();
        return;
      }
      if (continuous) {
        if (!continuousActive || item.continuousGeneration !== continuousGeneration || document.hidden) return;
        const text = data.text.trim();
        if (text.length < 2) throw new Error('A transcrição ficou curta demais. Grave novamente ou use o texto.');
        $('#question').value = text;
        $('#voice-continuous-transcript').textContent = text;
        $('#voice-continuous-last').hidden = false;
        transcription = null;
        resetRecordingUi();
        continuousTurnToken = `continuous-${continuousGeneration}-${++continuousSequence}`;
        setContinuousState('waiting');
        notice('Pergunta enviada a partir da transcrição. Se houver um erro, pare e esclareça a pergunta por texto.');
        app.dispatchEvent(new CustomEvent('medlab:voice-submit',{detail:{text,token:continuousTurnToken}}));
        return;
      }
      $('#voice-transcript').value = data.text.trim();
      $('#voice-review').hidden = false;
      $('#voice-transcript').focus();
      notice('Transcrição pronta. Revise o texto; nada foi enviado ao paciente.');
    } catch (error) {
      if (!operation.cancelled && error.name !== 'AbortError') {
        if (['NOT_READY','MODEL_LOAD_FAILED'].includes(error.code)) asr.terminate();
        if (continuousActive && continuous) stopContinuous(error.message || 'A transcrição falhou. O modo contínuo foi pausado.',true);
        else notice(error.message || 'Não foi possível transcrever. Grave novamente ou continue por texto.',true);
      }
    } finally {
      if (transcription === operation) { transcription = null; resetRecordingUi(); }
    }
  }
  async function startRecording(continuousToken = null) {
    if (continuousToken !== null && (!continuousActive || continuousToken !== continuousGeneration)) return;
    if (continuousToken === null && continuousActive) return;
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
      stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:{ideal:1},echoCancellation:true,noiseSuppression:true,autoGainControl:true},video:false});
      if (!pendingPermission || permissionToken !== permissionGeneration) { releaseStream(stream); return; }
      const types = ['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4','audio/webm'];
      const mimeType = types.find(type => MediaRecorder.isTypeSupported(type)) || '';
      const recorder = mimeType ? new MediaRecorder(stream,{mimeType,audioBitsPerSecond:96000}) : new MediaRecorder(stream);
      const item = {recorder,stream,chunks:[],mimeType:recorder.mimeType || mimeType || 'audio/webm',discard:false,stopping:false,started:Date.now(),interval:null,timeout:null,continuousGeneration:continuousToken};
      recording = item;
      recorder.addEventListener('dataavailable',event => { if (event.data.size) item.chunks.push(event.data); });
      recorder.addEventListener('stop',() => transcribe(item),{once:true});
      recorder.addEventListener('error',() => {
        if (recording !== item || item.stopping) return;
        if (continuousActive && continuousToken !== null) stopContinuous('O navegador interrompeu a gravação. A conversa contínua foi pausada.',true);
        else { item.discard = true; stopRecording(true); recording = null; notice('O navegador interrompeu a gravação. Tente novamente ou continue por texto.',true); }
      });
      for (const track of stream.getTracks()) track.addEventListener('ended',() => {
        if (recording !== item || item.stopping) return;
        if (continuousActive) stopContinuous('O microfone foi desconectado ou interrompido. A conversa contínua foi pausada.',true);
        else { stopRecording(true); notice('O microfone foi interrompido. Grave novamente ou continue por texto.',true); }
      },{once:true});
      recorder.start(250);
      if (continuousToken !== null) { attachVoiceActivity(item); setContinuousState('listening'); }
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
      notice(continuousToken !== null
        ? `Faça uma pergunta e pause por um instante. Ela será enviada após cerca de 1,6 segundo de silêncio (máximo ${maxSeconds}s).`
        : `Grave uma pergunta de até ${maxSeconds} segundos. O áudio será transcrito neste navegador.`);
    } catch (error) {
      releaseStream(stream);
      if (permissionToken !== permissionGeneration) return;
      if (recording) { stopRecording(true); recording = null; }
      resetRecordingUi();
      if (continuousToken !== null && continuousActive) stopContinuous('Não foi possível iniciar o microfone. A conversa contínua foi pausada.',true);
      if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') notice('O acesso ao microfone não foi permitido. Você pode habilitá-lo nas permissões do navegador ou continuar por texto.');
      else if (error.name === 'NotFoundError') notice('Não encontramos um microfone disponível. Conecte um microfone ou continue por texto.',true);
      else notice('Não foi possível iniciar o microfone. Verifique se outro aplicativo está usando o dispositivo e tente novamente.',true);
    } finally { if (permissionToken === permissionGeneration) pendingPermission = false; }
  }
  recordButton.addEventListener('click',() => startRecording());
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
      if (continuousActive) stopContinuous('Conversa contínua pausada ao mudar de etapa.');
      stopPlayback();
      if (recording) stopRecording(true);
      cancelTranscription();
      if (pendingPermission) { pendingPermission = false; permissionGeneration++; setRecordingBusy(false); }
    }
  });
  window.addEventListener('pagehide',() => {
    if (continuousActive) stopContinuous('');
    pendingPermission = false;
    permissionGeneration++;
    if (recording) stopRecording(true);
    cancelTranscription();
    stopPlayback();
    asr.terminate();
    tts.terminate();
  });
  document.addEventListener('visibilitychange',() => {
    if (document.hidden && continuousActive) stopContinuous('A página ficou oculta. O modo contínuo foi pausado e o microfone foi desligado.');
  });
  setContinuousState('idle');
  loadConfig();
})();
