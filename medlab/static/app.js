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
  const formSubmit = $('#diagnosis-form button[type=submit]');
  function scrollChat() { chat.scrollTop = chat.scrollHeight; }
  function activateTab(name, focus = false) {
    $$('[data-consult-tab]').forEach(button => {
      const active = button.dataset.consultTab === name;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', String(active));
      button.tabIndex = active ? 0 : -1;
      if (active && focus) button.focus();
    });
    $$('.consult-panel').forEach(panel => { panel.hidden = panel.id !== `${name}-panel`; });
    if (name === 'chat') scrollChat();
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
  function showError(id, message) {
    const error = document.getElementById(id);
    error.textContent = message;
    error.hidden = !message;
  }
  async function api(path, data) {
    const response = await fetch(`/sessao/${encodeURIComponent(sessionId)}/${path}`, {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type':'application/json','Accept':'application/json','X-CSRFToken':csrf},
      body: JSON.stringify(data)
    });
    let payload;
    try { payload = await response.json(); }
    catch { throw new Error('Não foi possível concluir agora. Atualize a página e tente novamente.'); }
    if (!response.ok) throw new Error(payload.error || 'Não foi possível concluir. Tente novamente.');
    return payload;
  }
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
    const question = questionInput.value.trim();
    if (question.length < 2) { showError('chat-error','Escreva uma pergunta com pelo menos 2 caracteres.'); questionInput.focus(); return; }
    busy = true;
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
      const result = await api('mensagem', retryRequest);
      loading.remove();
      messageNode(result.answer,true);
      $('#turn-count').textContent = result.turn_count;
      retryRequest = null;
    } catch (error) {
      loading.remove(); doctorNode.remove();
      questionInput.value = question;
      showError('chat-error', error instanceof TypeError ? 'A conexão foi interrompida. Sua pergunta está preservada; tente enviar novamente.' : error.message);
    } finally {
      busy = false; sendButton.disabled = false; questionInput.disabled = false; formSubmit.disabled = false;
      sendButton.removeAttribute('aria-busy'); questionInput.focus(); scrollChat();
    }
  });
  questionInput.addEventListener('keydown', event => {
    if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); chatForm.requestSubmit(); }
  });
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
    if (busy) { event.preventDefault(); showError('chat-error','Aguarde a resposta do paciente antes de finalizar.'); activateTab('chat'); formSubmit.disabled = false; }
  });
  const hasServerDraft = $('#diagnosis').defaultValue.trim() || $('#treatment').defaultValue.trim();
  activateTab(hasServerDraft ? 'diagnosis' : 'chat');
  scrollChat();
})();
