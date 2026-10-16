/* Progressive ranking refresh; the server remains authoritative for all scores. */
(() => {
  'use strict';
  const tracks = ['basic', 'clinical'];
  const boards = Object.fromEntries(tracks.map(key => [key, document.querySelector(`[data-ranking-content="${key}"]`)]));
  const personal = Object.fromEntries(tracks.map(key => [key, document.querySelector(`[data-ranking-personal="${key}"]`)]));
  if (!tracks.some(key => boards[key] || personal[key])) return;
  const endpoint = document.querySelector('script[data-ranking-url]')?.dataset.rankingUrl;
  if (!endpoint) return;
  const signatures = new Map();
  let busy = false;
  const integer = n => Number.isInteger(n) && n >= 0;
  const score = n => typeof n === 'number' && Number.isFinite(n) && n >= 0 && n <= 100;
  const average = n => n.toFixed(2).replace('.', ',');
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function validBoard(board) {
    return board && typeof board.title === 'string' && integer(board.minimum_cases) && board.minimum_cases > 0 &&
      Array.isArray(board.entries) && board.entries.length <= 15 && board.entries.every(row =>
        integer(row.rank) && row.rank > 0 && typeof row.display_name === 'string' && score(row.average_score) &&
        integer(row.distinct_cases) && row.distinct_cases >= board.minimum_cases && typeof row.is_current_user === 'boolean');
  }
  function validPersonal(value) {
    return value && integer(value.minimum_cases) && value.minimum_cases > 0 && integer(value.distinct_cases) &&
      integer(value.remaining_cases) && typeof value.eligible === 'boolean' &&
      (value.average_score === null || score(value.average_score)) &&
      (value.rank === null || (integer(value.rank) && value.rank > 0)) &&
      (!value.eligible || (value.rank !== null && value.average_score !== null && value.distinct_cases >= value.minimum_cases));
  }
  function renderBoard(key, board) {
    const signature = JSON.stringify(board);
    if (signatures.get(`board-${key}`) === signature) return;
    const content = boards[key];
    if (!board.entries.length) {
      const empty = element('div', undefined, 'ranking-empty');
      empty.append(element('h4', 'Esta trajetória está começando.'), element('p', `A classificação aparecerá quando houver participantes com pelo menos ${board.minimum_cases} casos distintos avaliados nesta categoria.`));
      content.replaceChildren(empty);
    } else {
      const table = element('table', undefined, 'ranking-table');
      table.append(element('caption', `Top 15 de ${board.title}. Média de zero a cem; casos distintos com nota válida.`, 'ranking-sr-only'));
      const head = element('thead'), headings = element('tr');
      for (const [label, style] of [['Posição','ranking-position'], ['Participante',''], ['Média','ranking-number'], ['Casos','ranking-number ranking-case-count']]) {
        const th = element('th', label, style); th.setAttribute('scope', 'col'); headings.append(th);
      }
      head.append(headings); table.append(head);
      const body = element('tbody');
      for (const entry of board.entries) {
        const row = element('tr', undefined, entry.is_current_user ? 'ranking-current' : '');
        const position = element('td', undefined, 'ranking-position');
        position.append(element('span', `${entry.rank}º`, `ranking-place${entry.rank <= 3 ? ' ranking-place-leading' : ''}`));
        const name = element('th', entry.display_name, 'ranking-name'); name.setAttribute('scope', 'row');
        if (entry.is_current_user) name.append(element('span', 'Você', 'ranking-you'));
        const points = element('td', undefined, 'ranking-number');
        points.append(element('strong', average(entry.average_score)), element('span', ' de 100', 'ranking-sr-only'));
        row.append(position, name, points, element('td', entry.distinct_cases, 'ranking-number ranking-case-count'));
        body.append(row);
      }
      table.append(body); content.replaceChildren(table);
    }
    document.querySelector(`[data-ranking-minimum="${key}"]`).textContent = `${board.minimum_cases} casos distintos`;
    document.getElementById(`ranking-title-${key}`).textContent = board.title;
    signatures.set(`board-${key}`, signature);
  }
  function renderPersonal(key, value) {
    const signature = JSON.stringify(value);
    if (signatures.get(`personal-${key}`) === signature) return;
    const card = personal[key];
    card.querySelector('[data-ranking-personal-count]').textContent = `${value.distinct_cases} ${value.distinct_cases === 1 ? 'caso distinto' : 'casos distintos'} com nota válida`;
    const points = card.querySelector('[data-ranking-personal-value]');
    if (value.eligible) {
      const rank = element('strong', `${value.rank}º `); rank.append(element('span', 'lugar'));
      points.replaceChildren(rank, element('p', `Média ${average(value.average_score)}/100`));
    } else {
      const remaining = value.remaining_cases === 1 ? 'Falta 1 caso distinto' :
        value.remaining_cases > 1 ? `Faltam ${value.remaining_cases} casos distintos` : 'Aguardando classificação';
      const progress = element('p', remaining); progress.append(element('span', ` · mínimo ${value.minimum_cases}`));
      points.replaceChildren(element('strong', 'Em formação', 'personal-ranking-forming'), progress);
    }
    signatures.set(`personal-${key}`, signature);
  }
  async function refresh() {
    if (document.hidden || busy) return;
    busy = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10000);
    try {
      const response = await fetch(endpoint, {credentials:'same-origin', cache:'no-store', signal:controller.signal, headers:{Accept:'application/json'}});
      if (!response.ok) return;
      const data = await response.json();
      if (document.hidden) return;
      for (const key of tracks) {
        if (boards[key] && validBoard(data.leaderboards?.[key])) renderBoard(key, data.leaderboards[key]);
        if (personal[key] && validPersonal(data.personal_rankings?.[key])) renderPersonal(key, data.personal_rankings[key]);
      }
    } catch (_) {
      // A failed refresh leaves the last server-rendered/accepted ranking intact.
    } finally {
      clearTimeout(timeout); busy = false;
    }
  }
  setInterval(refresh, 60000);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) refresh(); });
})();
