'use strict';
(() => {
  const section = document.getElementById('learning-review');
  if (!section) return;
  const content = document.getElementById('learning-review-content');
  const status = document.getElementById('learning-review-status');
  const failure = document.getElementById('learning-review-error');
  const errorText = document.getElementById('learning-review-error-text');
  const retry = document.getElementById('learning-review-retry');
  const csrf = document.querySelector('meta[name=csrf-token]')?.content;
  let busy = false, loaded = false;
  const text = value => typeof value === 'string';
  const texts = value => Array.isArray(value) && value.every(text);
  const evidence = value => Array.isArray(value) && value.every(item => item && text(item.id) && text(item.text));
  const guidance = value => value && text(value.why) && text(value.nextTime)
    && texts(value.criteria) && texts(value.criterionIds) && texts(value.actionIds) && evidence(value.evidence)
    && (!Object.hasOwn(value,'exampleQuestion') || (text(value.exampleQuestion) && Array.from(value.exampleQuestion).length <= 500));
  const interviewLabels = {strength:'Ponto forte', partial:'Pode aprofundar', missed:'A investigar', uncertain:'Requer conferência'};
  const examLabels = {appropriate:'Pertinente', unnecessary:'Desnecessário', premature:'Precoce', consider:'Vale considerar', not_needed:'Sem necessidade indicada', uncertain:'Indicação a conferir'};
  const basicLabels = {complete:'Demonstrado', partial:'Parcial', not_met:'Não demonstrado', not_applicable:'Não aplicável'};
  const exemptionLabels = {source_unavailable:'Informação não disponível no cenário.',already_known:'Informação já conhecida no contexto da consulta.',context_not_relevant:'Item não pertinente a este contexto.',declined:'Preferência por não responder respeitada.',urgent_priority:'Outras prioridades exigiam atenção neste momento.',respondent_limitation:'Limitação do paciente ou informante considerada.',record_uncertain:'O registro não permite concluir com segurança.'};
  function validBasic(value) {
    return value && text(value.title) && Array.isArray(value.groups) && Array.isArray(value.criteria)
      && value.groups.every(group => group && text(group.id) && text(group.title) && texts(group.criterionIds)
        && typeof group.assessable === 'boolean' && (group.assessable ? typeof group.score === 'number'
          && Number.isFinite(group.score) && group.score >= 0 && group.score <= 100 : group.score === null))
      && value.criteria.every(item => item && text(item.id) && text(item.groupId) && text(item.title)
        && Object.hasOwn(basicLabels,item.status) && text(item.reason) && texts(item.evidenceRefs)
        && evidence(item.evidence) && evidence(item.contextEvidence)
        && (item.exemption === null || Object.hasOwn(exemptionLabels,item.exemption)));
  }
  function valid(value) {
    return value && value.version === 1 && value.status === 'educational' && text(value.summary)
      && (!Object.hasOwn(value,'basicQuestions') || validBasic(value.basicQuestions))
      && guidance(value.diagnosis) && guidance(value.plan)
      && Array.isArray(value.interview) && value.interview.every(item => item && text(item.criterionId)
        && text(item.criterion) && Object.hasOwn(interviewLabels,item.status) && texts(item.actionIds)
        && evidence(item.evidence) && text(item.why) && text(item.nextTime) && text(item.exampleQuestion))
      && Array.isArray(value.exams) && value.exams.every(item => item && text(item.examId) && text(item.name)
        && ['physical_exam','auxiliary_exam'].includes(item.type) && typeof item.requested === 'boolean'
        && typeof item.required === 'boolean' && Object.hasOwn(examLabels,item.judgement)
        && text(item.why) && text(item.nextTime));
  }
  function element(tag, className, value) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (value !== undefined) node.textContent = value;
    return node;
  }
  function paragraph(parent, title, value) {
    if (!value) return;
    const block = element('div','learning-guidance');
    block.append(element('h4','',title),element('p','',value));
    parent.append(block);
  }
  function recordedEvidence(parent, items) {
    if (!items.length) return;
    const details = element('details','learning-evidence');
    details.append(element('summary','','Ver trechos da sua consulta'));
    const list = element('ul','');
    const labels = {Q:'Pergunta',A:'Resposta do paciente',P:'Pedido de exame físico',L:'Pedido de exame complementar',D:'Conclusão',T:'Conduta'};
    for (const item of items) {
      const match = /^([QAPLDT])(\d+)$/.exec(item.id);
      const row = element('li','');
      row.append(element('strong','',match ? `${labels[match[1]]} ${match[2]}` : 'Trecho registrado'),element('p','',item.text));
      list.append(row);
    }
    details.append(list);parent.append(details);
  }
  function reasoningCard(title, item) {
    const card = element('article','learning-card');
    card.append(element('h3','',title));
    if (item.criteria.length) {
      const list = element('ul','learning-criteria');
      for (const criterion of item.criteria) list.append(element('li','',criterion));
      card.append(element('p','micro-label','CRITÉRIOS DE REFERÊNCIA'),list);
    }
    paragraph(card,'O que considerar',item.why);
    paragraph(card,'Como melhorar na próxima consulta',item.nextTime);
    if (item.exampleQuestion?.trim()) {
      const example = element('div','learning-question');
      example.append(element('span','micro-label','UMA FORMA DE ABORDAR'),element('p','',item.exampleQuestion));
      card.append(example);
    }
    recordedEvidence(card,item.evidence);
    return card;
  }
  function basicQuestions(value) {
    const review = element('section','basic-question-review');
    review.append(element('h3','learning-section-title',value.title),element('p','','Itens não aplicáveis ficam fora do cálculo; cada grupo avaliável tem o mesmo peso nesta dimensão.'));
    for (const group of value.groups) {
      const details = element('details','basic-question-group');
      const summary = element('summary','');
      summary.append(element('span','',group.title),element('strong','',group.assessable ? `${group.score}/100` : 'Não avaliado'));
      details.append(summary);
      const list = element('ol','');
      for (const item of value.criteria.filter(item => item.groupId === group.id)) {
        const row = element('li','');
        const heading = element('div','basic-criterion-heading');
        const badge = element('span','basic-criterion-status',basicLabels[item.status]);badge.dataset.status = item.status;
        heading.append(element('h4','',item.title),badge);row.append(heading,element('p','',item.reason));
        if (item.exemption) row.append(element('p','basic-criterion-exemption',exemptionLabels[item.exemption]));
        if (item.evidence.length) {
          const proof = element('div','basic-criterion-evidence');proof.append(element('span','micro-label','PERGUNTAS REGISTRADAS'));
          for (const citation of item.evidence) proof.append(element('blockquote','',citation.text));
          row.append(proof);
        }
        if (item.contextEvidence.length) {
          const context = element('div','basic-criterion-context');context.append(element('span','micro-label','CONTEXTO CONSIDERADO'));
          for (const citation of item.contextEvidence) context.append(element('p','',citation.text));
          row.append(context);
        }
        list.append(row);
      }
      details.append(list);review.append(details);
    }
    return review;
  }
  function render(value) {
    if (!valid(value)) throw new Error('O parecer chegou incompleto. Tente novamente; sua nota permanece salva.');
    const body = element('div','learning-review-body');
    body.append(element('p','learning-review-summary',value.summary));
    if (value.basicQuestions) body.append(basicQuestions(value.basicQuestions));
    if (value.interview.length) {
      body.append(element('h3','learning-section-title','Perguntas e condução da entrevista'));
      const grid = element('div','learning-interview-grid');
      for (const item of value.interview) {
        const card = element('article','learning-card');
        card.append(element('span','learning-badge',interviewLabels[item.status]),element('h4','learning-criterion',item.criterion));
        paragraph(card,'Por que isso importa',item.why);
        paragraph(card,'Na próxima consulta',item.nextTime);
        if (item.exampleQuestion) {
          const example = element('div','learning-question');
          example.append(element('span','micro-label','UMA FORMA DE PERGUNTAR'),element('p','',item.exampleQuestion));
          card.append(example);
        }
        recordedEvidence(card,item.evidence);grid.append(card);
      }
      body.append(grid);
    }
    const reasoning = element('div','learning-reasoning-grid');
    reasoning.append(reasoningCard('Seu raciocínio diagnóstico',value.diagnosis),reasoningCard('Sua conduta e orientações',value.plan));
    body.append(reasoning);
    if (value.exams.length) {
      body.append(element('h3','learning-section-title','Exames: indicação e próximo passo'));
      const list = element('div','learning-exams');
      for (const item of value.exams) {
        const card = element('article','learning-exam');
        const heading = element('div','learning-exam-heading');
        const identity = element('div','');
        identity.append(element('span','micro-label',item.type === 'physical_exam' ? 'EXAME FÍSICO' : 'EXAME COMPLEMENTAR'),element('h4','',item.name));
        heading.append(identity,element('span','learning-badge',examLabels[item.judgement]));
        card.append(heading,element('p','learning-exam-state',`${item.requested ? 'Solicitado' : 'Não solicitado'} · ${item.required ? 'Previsto na referência' : 'Sem exigência na referência'}`));
        paragraph(card,'Por quê',item.why);
        paragraph(card,'Como conduzir',item.nextTime);
        list.append(card);
      }
      body.append(list);
    }
    content.replaceChildren(body);content.hidden = false;
    loaded = true;failure.hidden = true;status.textContent = 'Orientações prontas. Sua nota permanece a mesma.';
  }
  function showFailure(message) {
    errorText.textContent = message;
    failure.hidden = false;status.textContent = '';
  }
  async function load() {
    if (busy || loaded || section.dataset.demoMode === 'true') return;
    busy = true;retry.disabled = true;failure.hidden = true;
    section.setAttribute('aria-busy','true');
    status.textContent = 'Preparando orientações sobre sua entrevista, raciocínio e exames…';
    try {
      const response = await fetch(`/api/sessoes/${encodeURIComponent(section.dataset.sessionId)}/parecer`, {
        method:'POST',credentials:'same-origin',headers:{'Accept':'application/json','Content-Type':'application/json','X-CSRFToken':csrf},body:'{}'
      });
      let payload;
      try { payload = await response.json(); }
      catch { throw new Error('Não foi possível ler o parecer. Tente novamente; sua nota permanece salva.'); }
      if (!response.ok) throw new Error(text(payload?.error) && payload.error ? payload.error : 'O parecer está indisponível agora. Tente novamente; sua nota permanece salva.');
      render(payload?.learningReview);
    } catch (error) {
      showFailure(error instanceof TypeError ? 'A conexão foi interrompida. Confira sua conexão e tente novamente; sua nota permanece salva.' : error.message);
    } finally {
      busy = false;retry.disabled = false;section.removeAttribute('aria-busy');
    }
  }
  retry.addEventListener('click',load);
  let saved;
  try { saved = JSON.parse(document.getElementById('learning-review-data').textContent); }
  catch { showFailure('Não foi possível ler o parecer salvo. Tente novamente; sua nota permanece salva.');return; }
  if (saved) {
    try { render(saved); }
    catch (error) { showFailure(error.message); }
  } else if (section.dataset.demoMode === 'true') {
    status.textContent = 'O parecer complementar por IA não é gerado no modo de demonstração.';
  } else {
    load();
  }
})();
