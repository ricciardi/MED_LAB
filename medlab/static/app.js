'use strict';
(() => {
  const $ = (selector, scope = document) => scope.querySelector(selector);
  const $$ = (selector, scope = document) => [...scope.querySelectorAll(selector)];
  $$('[data-dismiss-flash]').forEach(button => button.addEventListener('click', () => button.closest('.flash').remove()));
  $$('[data-toggle-password]').forEach(button => button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.togglePassword);
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.setAttribute('aria-label', show ? 'Ocultar senha' : 'Mostrar senha');
    button.setAttribute('aria-pressed', String(show));
  }));
  $$('[data-sidebar-toggle]').forEach(button => button.addEventListener('click', () => {
    const open = document.body.classList.toggle('sidebar-open');
    $$('[data-sidebar-toggle]').forEach(item => item.setAttribute('aria-expanded', String(open)));
    const trigger = $('.mobile-menu');
    if (open) { $('.side-nav a')?.focus(); }
    else { trigger?.focus(); }
  }));
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && document.body.classList.contains('sidebar-open')) {
      document.body.classList.remove('sidebar-open');
      $('.mobile-menu')?.setAttribute('aria-expanded', 'false');
      $('.mobile-menu')?.focus();
    }
    if (event.key === 'Tab' && document.body.classList.contains('sidebar-open')) {
      const focusable = $$('a, button', $('#sidebar'));
      if (event.shiftKey && document.activeElement === focusable[0]) { event.preventDefault(); focusable.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === focusable.at(-1)) { event.preventDefault(); focusable[0]?.focus(); }
    }
  });
  $$('[data-auto-submit]').forEach(select => select.addEventListener('change', () => select.form.requestSubmit()));
  $$('[data-loading-form]').forEach(form => form.addEventListener('submit', () => {
    const button = $('button', form);
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    button.textContent = 'Preparando o rascunho…';
  }));
  $$('[data-basic-evaluation-retry]').forEach(form => form.addEventListener('submit', () => {
    const button = $('button[type=submit]', form);
    button.disabled = true;
    button.setAttribute('aria-busy', 'true');
    $('[data-basic-evaluation-progress]', form).hidden = false;
  }));
  $$('[data-history-filter]').forEach(button => button.addEventListener('click', () => {
    const status = button.dataset.historyFilter;
    $$('[data-history-filter]').forEach(item => {
      const active = item === button;
      item.classList.toggle('active', active);
      item.setAttribute('aria-pressed', String(active));
    });
    let shown = 0;
    $$('[data-session-status]').forEach(card => {
      const visible = status === 'all' || status === card.dataset.sessionStatus;
      card.hidden = !visible;
      if (visible) shown++;
    });
    const empty = $('#history-empty');
    if (empty) empty.hidden = shown !== 0;
  }));
  function fieldMessage(input) {
    const isPassword = input.name === 'password' || input.name === 'password_confirm';
    const value = isPassword ? input.value : input.value.trim();
    if (input.required && !value) return 'Preencha este campo para continuar.';
    if (input.type === 'email' && value && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value)) return 'Informe um e-mail válido.';
    if (input.minLength > 0 && value.length < input.minLength) return `Use pelo menos ${input.minLength} caracteres.`;
    if (input.maxLength > 0 && value.length > input.maxLength) return `Use no máximo ${input.maxLength} caracteres.`;
    if (input.name === 'password_confirm' && value !== $('#password', input.form)?.value) return 'As senhas precisam ser iguais.';
    return '';
  }
  $$('[data-validate-form]').forEach(form => {
    form.addEventListener('submit', event => {
      let firstInvalid;
      $$('input:not([type=hidden]), textarea', form).forEach(input => {
        const message = fieldMessage(input);
        const error = document.getElementById(`${input.id}-error`);
        if (error) error.textContent = message;
        input.setAttribute('aria-invalid', String(Boolean(message)));
        if (message && error) input.setAttribute('aria-describedby', error.id);
        if (message && !firstInvalid) firstInvalid = input;
      });
      if (firstInvalid) { event.preventDefault(); firstInvalid.focus(); return; }
      const button = $('button[type=submit]', form);
      if (button) {
        button.disabled = true;
        button.setAttribute('aria-busy', 'true');
        if (form.id === 'diagnosis-form') $('#evaluation-progress').hidden = false;
      }
    });
    $$('input:not([type=hidden]), textarea', form).forEach(input => input.addEventListener('input', () => {
      if (input.getAttribute('aria-invalid') === 'true') {
        input.removeAttribute('aria-invalid');
        const error = document.getElementById(`${input.id}-error`);
        if (error) error.textContent = '';
      }
    }));
  });
  window.addEventListener('pageshow', event => {
    if (event.persisted) $$('button[aria-busy=true]').forEach(button => { button.disabled = false; button.removeAttribute('aria-busy'); });
  });
  document.addEventListener('error', event => {
    const image = event.target;
    if (!(image instanceof HTMLImageElement) || !image.closest('.exam-media-card')) return;
    const notice = document.createElement('p');
    notice.className = 'exam-media-unavailable';
    notice.textContent = 'Esta imagem está indisponível no momento. O resultado escrito permanece disponível.';
    image.parentElement.replaceWith(notice);
  }, true);
  const app = $('#consultation-app');
  if (!app) return;
  const sessionId = app.dataset.sessionId;
  const csrf = $('meta[name=csrf-token]').content;
  const patient = JSON.parse($('#patient-data').textContent);
  const chat = $('#chat-thread');
  const questionInput = $('#question');
  const chatForm = $('#chat-form');
  let busy = false;
  let retryRequest = null;
  let pendingVoiceToken = null;
  let activeVoiceSend = null;
  const pendingVoiceReplies = new Map();
  const formSubmit = $('#diagnosis-form button[type=submit]');
  function scrollChat() { chat.scrollTop = chat.scrollHeight; }
  function activateTab(name, focus = false) {
    app.dataset.activeTab = name;
    const phase = $('#consultation-phase-label');
    if (phase) phase.textContent = name === 'diagnosis' ? 'Conclusões antes da avaliação' : 'Consulta em andamento';
    $$('[data-consult-tab]').forEach(button => {
      const active = button.dataset.consultTab === name;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
      if (active && focus) button.focus();
    });
    $$('.consult-panel').forEach(panel => { panel.hidden = panel.id !== `${name}-panel`; });
    if (name === 'chat') scrollChat();
    app.dispatchEvent(new CustomEvent('medlab:tab-change', {detail:{name}}));
  }
  $$('[data-consult-tab]').forEach(button => {
    button.addEventListener('click', () => activateTab(button.dataset.consultTab));
    button.addEventListener('keydown', event => {
      const tabs = $$('[data-consult-tab]');
      const index = tabs.indexOf(button);
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); activateTab(tabs[next].dataset.consultTab, true); }
    });
  });
  $$('[data-conclude-consultation]').forEach(button => button.addEventListener('click', () => {
    if (busy) {
      showError('conclusion-status', 'Aguarde a resposta do paciente antes de avançar.');
      return;
    }
    showError('conclusion-status', '');
    activateTab('diagnosis');
    $('#diagnosis').focus({preventScroll:true});
    $('#diagnosis-panel').scrollIntoView?.({block:'start'});
  }));
  $$('[data-return-to-chat]').forEach(button => button.addEventListener('click', () => {
    activateTab('chat');
    questionInput.focus({preventScroll:true});
    $('#chat-panel').scrollIntoView?.({block:'start'});
  }));
  function showError(id, message) {
    const error = document.getElementById(id);
    error.textContent = message;
    error.hidden = !message;
  }
  async function api(path, data, signal) {
    const response = await fetch(`/sessao/${encodeURIComponent(sessionId)}/${path}`, {
      method: 'POST', credentials: 'same-origin', signal,
      headers: {'Content-Type':'application/json','Accept':'application/json','X-CSRFToken':csrf},
      body: JSON.stringify(data)
    });
    let payload;
    try { payload = await response.json(); }
    catch (error) {
      if (error.name === 'AbortError') throw error;
      throw new Error('Não foi possível concluir agora. Atualize a página e tente novamente.');
    }
    if (!response.ok) throw new Error(payload.error || 'Não foi possível concluir. Tente novamente.');
    return payload;
  }
  app.addEventListener('medlab:voice-submit', event => {
    const {text, token} = event.detail || {};
    if (busy || app.dataset.voiceBusy === 'true' || typeof token !== 'string' || typeof text !== 'string' || text.trim().length < 2 || text.length > 2000) {
      app.dispatchEvent(new CustomEvent('medlab:message-error', {detail:{voiceToken:token, message:'A pergunta não pôde ser enviada. O modo contínuo foi pausado.'}}));
      return;
    }
    questionInput.value = text.trim();
    pendingVoiceToken = token;
    chatForm.requestSubmit();
    pendingVoiceToken = null;
  });
  app.addEventListener('medlab:cancel-voice-send', event => {
    if (activeVoiceSend?.token === event.detail?.token) activeVoiceSend.controller.abort();
    revealVoiceReply(event.detail?.token);
  });
  function revealVoiceReply(token) {
    const reply = pendingVoiceReplies.get(token);
    if (!reply) return;
    const bubble = $('.message-bubble', reply.node);
    bubble.textContent = reply.text;
    bubble.removeAttribute('aria-busy');
    pendingVoiceReplies.delete(token);
    scrollChat();
  }
  app.addEventListener('medlab:voice-response-start',event => revealVoiceReply(event.detail?.token));
  app.addEventListener('medlab:message-error',event => revealVoiceReply(event.detail?.voiceToken));
  function messageNode(text, fromPatient, loading = false) {
    const wrapper = document.createElement('div');
    wrapper.className = `chat-message ${fromPatient ? 'patient-message' : 'doctor-message'}`;
    const avatar = document.createElement('span');
    avatar.className = `chat-avatar ${fromPatient ? '' : 'doctor-avatar'}`;
    avatar.textContent = fromPatient ? patient.initials : 'V';
    const content = document.createElement('div');
    const author = document.createElement('span');
    author.className = 'message-author';
    author.textContent = fromPatient ? patient.name : 'Você';
    const bubble = document.createElement('div');
    bubble.className = 'message-bubble';
    if (loading) {
      bubble.setAttribute('aria-label','O paciente está respondendo…');
      const dots = document.createElement('div'); dots.className = 'typing-dots'; dots.setAttribute('aria-hidden','true');
      for (let i = 0; i < 3; i++) dots.append(document.createElement('i'));
      bubble.append(dots);
    } else bubble.textContent = text;
    content.append(author, bubble);
    wrapper.append(...(fromPatient ? [avatar, content] : [content, avatar]));
    chat.append(wrapper);
    scrollChat();
    return wrapper;
  }
  chatForm.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy) return;
    if (app.dataset.voiceBusy === 'true') { showError('chat-error','Conclua ou descarte a gravação antes de enviar sua pergunta.'); return; }
    const question = questionInput.value.trim();
    if (question.length < 2) { showError('chat-error','Escreva uma pergunta com pelo menos 2 caracteres.'); questionInput.focus(); return; }
    const voiceToken = pendingVoiceToken;
    const controller = new AbortController();
    if (voiceToken) activeVoiceSend = {token:voiceToken, controller};
    busy = true;
    app.dispatchEvent(new CustomEvent('medlab:conversation-busy', {detail:{busy:true, voiceToken}}));
    showError('chat-error','');
    const sendButton = $('button[type=submit]', chatForm);
    sendButton.disabled = true;
    sendButton.setAttribute('aria-busy','true');
    questionInput.disabled = true;
    formSubmit.disabled = true;
    if (!retryRequest || retryRequest.question !== question) retryRequest = {question, request_id: (globalThis.crypto?.randomUUID?.() || `consulta_${Date.now()}_${Math.random().toString(36).slice(2)}`)};
    const doctorNode = messageNode(question,false);
    questionInput.value = '';
    const loading = messageNode('',true,true);
    try {
      const result = await api('mensagem', retryRequest, controller.signal);
      if (controller.signal.aborted) throw new DOMException('Envio interrompido.', 'AbortError');
      loading.remove();
      $('#chat-opening')?.remove();
      const synchronizedVoice = voiceToken && app.dataset.continuousState === 'waiting';
      const answerNode = messageNode(synchronizedVoice ? 'Preparando voz do paciente…' : result.answer,true);
      if (synchronizedVoice) {
        $('.message-bubble',answerNode).setAttribute('aria-busy','true');
        pendingVoiceReplies.set(voiceToken,{node:answerNode,text:result.answer});
      }
      if (result.turn_id !== undefined && result.turn_id !== null) {
        answerNode.dataset.turnId = String(result.turn_id);
        app.dispatchEvent(new CustomEvent('medlab:turn-saved', {detail:{turnId:result.turn_id, element:answerNode, voiceToken}}));
      } else if (voiceToken) {
        app.dispatchEvent(new CustomEvent('medlab:message-error', {detail:{voiceToken, message:'A resposta apareceu em texto, mas não ficou disponível para voz. Continue por texto.'}}));
      }
      $('#turn-count').textContent = result.turn_count;
      retryRequest = null;
    } catch (error) {
      loading.remove(); doctorNode.remove();
      questionInput.value = question;
      const message = error.name === 'AbortError'
        ? 'O modo contínuo foi interrompido. Sua pergunta está preservada. O servidor pode ter concluído o envio; confira a conversa antes de tentar novamente.'
        : error instanceof TypeError ? 'A conexão foi interrompida. Sua pergunta está preservada; tente enviar novamente.' : error.message;
      showError('chat-error', message);
      app.dispatchEvent(new CustomEvent('medlab:message-error', {detail:{voiceToken, message}}));
    } finally {
      if (activeVoiceSend?.controller === controller) activeVoiceSend = null;
      busy = false;
      app.dispatchEvent(new CustomEvent('medlab:conversation-busy', {detail:{busy:false}}));
      sendButton.disabled = false; questionInput.disabled = false; formSubmit.disabled = false;
      sendButton.removeAttribute('aria-busy');
      if (app.dataset.activeTab === 'chat') questionInput.focus();
      scrollChat();
    }
  });
  questionInput.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); chatForm.requestSubmit(); }
  });
  function renderExamMedia(gallery, media) {
    gallery.replaceChildren();
    for (const item of Array.isArray(media) ? media : []) {
      const url = new URL(item.url, window.location.origin);
      if (url.origin !== window.location.origin || !url.pathname.startsWith(`/sessao/${encodeURIComponent(sessionId)}/imagem/`)) continue;
      const element = (tag, className, content) => {
        const node = document.createElement(tag);
        node.className = className;
        if (content) node.textContent = content;
        return node;
      };
      const link = (className, content) => {
        const node = element('a', className, content);
        node.href = url.href; node.target = '_blank'; node.rel = 'noopener noreferrer';
        node.setAttribute('aria-label', `Ampliar imagem de ${item.exam_name}`);
        return node;
      };
      const figure = element('figure', 'exam-media-card');
      const heading = element('div', 'exam-media-heading');
      heading.append(element('span', '', 'IMAGEM DO EXAME'), link('', 'Ampliar ↗'));
      const imageLink = link('exam-media-image', '');
      const image = element('img', '');
      image.alt = item.caption; image.loading = 'lazy'; image.decoding = 'async'; image.src = url.href;
      if (Number.isInteger(item.width) && Number.isInteger(item.height)) { image.width = item.width; image.height = item.height; }
      imageLink.append(image);
      const caption = element('figcaption', '');
      caption.append(element('p', '', item.caption));
      const credit = element('div', 'exam-media-credit');
      if (item.credit) credit.append(element('span', '', item.credit));
      if (item.license_url) {
        const license = new URL(item.license_url);
        if (license.protocol === 'https:' && license.hostname === 'creativecommons.org') {
          const licenseLink = element('a', '', item.license_code);
          licenseLink.href = license.href; licenseLink.target = '_blank'; licenseLink.rel = 'noopener noreferrer';
          credit.append(licenseLink);
        }
      }
      credit.append(element('span', '', 'Imagem original; legenda em português.'));
      if (item.review_mode === 'machine') credit.append(element('span', '', 'Seleção e conferência por IA.'));
      caption.append(credit); figure.append(heading, imageLink, caption); gallery.append(figure);
    }
  }
  $$('[data-request-exam]').forEach(button => button.addEventListener('click', async () => {
    if (busy) { showError('exam-error','Aguarde a resposta do paciente antes de solicitar um exame.'); return; }
    const item = button.closest('.exam-item');
    button.disabled = true;
    button.setAttribute('aria-busy','true');
    const originalLabel = button.textContent;
    button.textContent = 'Solicitando…';
    showError('exam-error','');
    try {
      const result = await api('exame',{name:item.dataset.examName,type:item.dataset.examType});
      $('.exam-result p',item).textContent = result.result;
      renderExamMedia($('[data-exam-media]',item), result.media);
      $('.exam-result',item).hidden = false;
      item.classList.add('performed');
      button.textContent = 'Realizado';
      const count = $$('.exam-item.performed').length;
      $('#exam-count').textContent = count;
      $('#performed-count').textContent = count;
    } catch (error) {
      showError('exam-error',error instanceof TypeError ? 'A conexão foi interrompida. Tente solicitar o exame novamente.' : error.message);
      button.disabled = false;
      button.textContent = originalLabel;
    } finally { button.removeAttribute('aria-busy'); }
  }));
  const draftFields = ['diagnosis','differential','treatment'];
  const draftKey = `medlab-consulta-${sessionId}`;
  try {
    const draft = JSON.parse(sessionStorage.getItem(draftKey) || '{}');
    draftFields.forEach(name => { const field = document.getElementById(name); if (!field.value && typeof draft[name] === 'string') field.value = draft[name]; });
  } catch { /* Storage may be unavailable in private browsing. */ }
  draftFields.forEach(name => document.getElementById(name).addEventListener('input', () => {
    const draft = Object.fromEntries(draftFields.map(key => [key,document.getElementById(key).value]));
    try { sessionStorage.setItem(draftKey,JSON.stringify(draft)); } catch { /* The server still preserves submitted drafts. */ }
  }));
  $('#diagnosis-form').addEventListener('submit', event => {
    if (busy || app.dataset.voiceBusy === 'true') {
      event.preventDefault();
      showError('chat-error',busy ? 'Aguarde a resposta do paciente antes de finalizar.' : 'Conclua ou descarte a gravação antes de finalizar a consulta.');
      activateTab('chat'); formSubmit.disabled = false; formSubmit.removeAttribute('aria-busy'); $('#evaluation-progress').hidden = true;
    }
  });
  const hasServerDraft = $('#diagnosis').defaultValue.trim() || $('#treatment').defaultValue.trim();
  activateTab(hasServerDraft ? 'diagnosis' : 'chat');
  scrollChat();
})();
