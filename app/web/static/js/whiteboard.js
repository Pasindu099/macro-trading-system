(() => {
  const root = document.querySelector('.wb');
  if (!root) return;
  const currency = root.dataset.currency;
  const viewport = root.querySelector('.wb-viewport');
  const world = root.querySelector('.wb-world');
  const cards = root.querySelector('.wb-cards');
  const svg = root.querySelector('.wb-links');
  const tabs = root.querySelector('.wb-tabs');
  const css = getComputedStyle(root);
  const px = name => parseFloat(css.getPropertyValue(name));
  const grid = px('--wb-grid');
  const zoomMin = px('--wb-zoom-min'), zoomMax = px('--wb-zoom-max');
  const size = (kind, axis) => px(`--wb-${kind}-${axis}`);
  const snap = value => Math.round(value / grid) * grid;
  const state = { board: null, boards: [], items: new Map(), links: [],
    x: 0, y: 0, zoom: 1, selected: null, connect: null, charts: new Map() };
  const timers = new Map();

  async function request(url, method = 'GET', data) {
    const response = await fetch(url, { method, headers: data ? { 'Content-Type': 'application/json' } : {},
      body: data ? JSON.stringify(data) : undefined });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    if (response.status === 204) return null;
    return response.json();
  }
  function debounce(key, fn) {
    clearTimeout(timers.get(key));
    timers.set(key, setTimeout(fn, px('--wb-save-delay')));
  }
  function transform() {
    world.style.transform = `translate(${state.x}px, ${state.y}px) scale(${state.zoom})`;
  }
  function point(event) {
    const rect = viewport.getBoundingClientRect();
    return { x: (event.clientX - rect.left - state.x) / state.zoom,
      y: (event.clientY - rect.top - state.y) / state.zoom };
  }
  function saveViewport() {
    if (!state.board) return;
    debounce('viewport', () => request(`/api/boards/${state.board.id}/viewport`, 'PATCH',
      { x: state.x, y: state.y, zoom: state.zoom }).catch(showError));
  }
  function showError(error) { console.error(error); }
  function select(id) {
    state.selected = id;
    cards.querySelectorAll('.wb-card').forEach(card =>
      card.classList.toggle('selected', Number(card.dataset.id) === id));
    viewport.focus();
  }
  function drawLinks() {
    svg.replaceChildren();
    for (const link of state.links) {
      const from = state.items.get(link.from_item_id), to = state.items.get(link.to_item_id);
      if (!from || !to) continue;
      const x1 = from.x + from.w, y1 = from.y + from.h / 2;
      const x2 = to.x, y2 = to.y + to.h / 2;
      const bend = Math.max(px('--wb-link-bend'), Math.abs(x2 - x1) / 2);
      const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
      path.setAttribute('d', `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`);
      svg.append(path);
    }
  }
  function updateGeometry(item) {
    const card = cards.querySelector(`[data-id="${item.id}"]`);
    if (!card) return;
    card.style.left = `${item.x}px`;
    card.style.top = `${item.y}px`;
    card.style.width = `${item.w}px`;
    card.style.height = `${item.h}px`;
    card.style.zIndex = item.z;
    drawLinks();
  }
  function saveItem(item, patch) {
    debounce(`item-${item.id}`, async () => {
      try {
        const saved = await request(`/api/boards/${state.board.id}/items/${item.id}`, 'PATCH', patch);
        Object.assign(item, saved);
      } catch (error) { showError(error); }
    });
  }
  function dragCard(event, item, resize = false) {
    event.preventDefault(); event.stopPropagation();
    select(item.id);
    const start = point(event), original = { x: item.x, y: item.y, w: item.w, h: item.h };
    const handle = event.currentTarget;
    handle.setPointerCapture(event.pointerId);
    function move(next) {
      const now = point(next), dx = now.x - start.x, dy = now.y - start.y;
      if (resize) {
        item.w = Math.max(px('--wb-min-card-width'), snap(original.w + dx));
        item.h = Math.max(px('--wb-min-card-height'), snap(original.h + dy));
      } else {
        item.x = snap(original.x + dx);
        item.y = snap(original.y + dy);
      }
      updateGeometry(item);
      if (resize) state.charts.get(item.id)?.resize();
    }
    function end() {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', end);
      handle.removeEventListener('pointercancel', end);
      saveItem(item, resize ? { w: item.w, h: item.h } : { x: item.x, y: item.y });
    }
    handle.addEventListener('pointermove', move);
    handle.addEventListener('pointerup', end);
    handle.addEventListener('pointercancel', end);
  }
  function field(parent, label, value, onInput) {
    const wrap = document.createElement('label');
    wrap.textContent = label;
    const input = document.createElement('textarea');
    input.value = value || '';
    input.addEventListener('input', () => onInput(input.value));
    wrap.append(input); parent.append(wrap);
  }
  function chartOptions(payload) {
    const series = payload.series;
    if (!series || !Object.keys(series).length) return null;
    const lines = Array.isArray(series) ? [{ name: payload.title, data: series }] :
      Object.entries(series).map(([name, data]) => ({ name, data }));
    return { animation: false, tooltip: { trigger: 'axis' }, legend: { show: lines.length > 1 },
      xAxis: { type: 'time' }, yAxis: { type: 'value' },
      series: lines.map(line => ({ name: line.name, type: 'line', showSymbol: false,
        data: line.data })) };
  }
  async function loadWidget(item, body, header) {
    const chartNode = document.createElement('div'); chartNode.className = 'wb-chart';
    body.append(chartNode);
    const params = new URLSearchParams({ currency, ...item.config });
    try {
      const data = await request(`/api/whiteboard/widget-data/${item.widget_type}?${params}`);
      if (data.status_chip) {
        const status = document.createElement('span');
        const direction = data.status_chip.toLowerCase();
        status.className = `wb-status${direction === 'bullish' || direction === 'bearish' ? ` ${direction}` : ''}`;
        status.textContent = data.status_chip;
        header.append(status);
      }
      const options = chartOptions(data);
      if (options && window.echarts) {
        const chart = echarts.init(chartNode); chart.setOption(options); state.charts.set(item.id, chart);
      } else {
        chartNode.textContent = data.title;
      }
    } catch (error) { chartNode.textContent = 'Data unavailable'; showError(error); }
  }
  function renderItem(item) {
    state.items.set(item.id, item);
    const card = document.createElement('article'); card.className = 'wb-card'; card.dataset.id = item.id;
    const header = document.createElement('div'); header.className = 'wb-card-header';
    header.textContent = item.kind === 'widget' ? (item.config.title || item.widget_type) :
      item.kind === 'note' ? 'Note' : 'Thesis';
    header.addEventListener('pointerdown', event => dragCard(event, item));
    const body = document.createElement('div'); body.className = 'wb-card-body';
    if (item.kind === 'widget') loadWidget(item, body, header);
    if (item.kind === 'note') {
      const note = document.createElement('textarea'); note.className = 'wb-note-text';
      note.value = item.config.text || ''; note.placeholder = 'Write a note';
      note.addEventListener('input', () => {
        item.config.text = note.value; saveItem(item, { config: item.config });
      });
      body.append(note);
    }
    if (item.kind === 'thesis') {
      const fields = document.createElement('div'); fields.className = 'wb-thesis-fields';
      for (const [key, label] of [['short_term', 'Short term'], ['long_run', 'Long run'],
        ['invalidation', 'Invalidation']]) {
        field(fields, label, item.config[key], value => {
          item.config[key] = value; saveItem(item, { config: item.config });
        });
      }
      body.append(fields);
    }
    const resize = document.createElement('div'); resize.className = 'wb-resize';
    resize.addEventListener('pointerdown', event => dragCard(event, item, true));
    card.addEventListener('pointerdown', () => select(item.id));
    card.addEventListener('click', async event => {
      if (!state.connect || event.target.closest('textarea')) return;
      event.stopPropagation();
      if (state.connect === 'choose') { state.connect = item.id; return; }
      if (state.connect === item.id) return;
      try {
        const link = await request(`/api/boards/${state.board.id}/links`, 'POST',
          { from_item_id: state.connect, to_item_id: item.id });
        state.links.push(link); drawLinks(); state.connect = null;
        root.querySelector('[data-action="connect"]').setAttribute('aria-pressed', 'false');
      } catch (error) { showError(error); }
    });
    card.append(header, body, resize); cards.append(card); updateGeometry(item);
  }
  async function openBoard(id) {
    state.charts.forEach(chart => chart.dispose()); state.charts.clear();
    cards.replaceChildren(); svg.replaceChildren(); state.items.clear();
    state.board = await request(`/api/boards/${id}`);
    state.links = state.board.links;
    ({ x: state.x, y: state.y, zoom: state.zoom } = state.board.viewport);
    transform();
    state.board.items.forEach(renderItem); drawLinks(); renderTabs();
  }
  function renderTabs() {
    tabs.replaceChildren();
    for (const board of state.boards) {
      const button = document.createElement('button'); button.type = 'button';
      button.className = `wb-tab${board.id === state.board?.id ? ' active' : ''}`;
      button.textContent = board.name; button.addEventListener('click', () => openBoard(board.id).catch(showError));
      tabs.append(button);
    }
  }
  async function addItem(kind, widget_type, config, where) {
    const dimension = kind === 'note' ? 'note' : kind === 'thesis' ? 'thesis' : 'default';
    const item = await request(`/api/boards/${state.board.id}/items`, 'POST', {
      kind, widget_type, config, x: snap(where.x), y: snap(where.y),
      w: snap(size(dimension, 'width')), h: snap(size(dimension, 'height')) });
    renderItem(item); select(item.id);
  }
  viewport.addEventListener('dragover', event => event.preventDefault());
  viewport.addEventListener('drop', event => {
    event.preventDefault();
    try {
      const data = JSON.parse(event.dataTransfer.getData('application/json'));
      addItem('widget', data.widget_type, data.config, point(event)).catch(showError);
    } catch (error) { showError(error); }
  });
  root.addEventListener('dragstart', event => {
    const item = event.target.closest('.wb-library-item'); if (!item) return;
    event.dataTransfer.setData('application/json', JSON.stringify({
      widget_type: item.dataset.widgetType, config: JSON.parse(item.dataset.config || '{}') }));
  });
  viewport.addEventListener('pointerdown', event => {
    if (event.target.closest('.wb-card')) return;
    select(null);
    const startX = event.clientX, startY = event.clientY, x = state.x, y = state.y;
    viewport.setPointerCapture(event.pointerId);
    function move(next) { state.x = x + next.clientX - startX; state.y = y + next.clientY - startY; transform(); }
    function end() {
      viewport.removeEventListener('pointermove', move);
      viewport.removeEventListener('pointerup', end);
      viewport.removeEventListener('pointercancel', end);
      saveViewport();
    }
    viewport.addEventListener('pointermove', move);
    viewport.addEventListener('pointerup', end);
    viewport.addEventListener('pointercancel', end);
  });
  viewport.addEventListener('wheel', event => {
    if (!event.ctrlKey) return;
    event.preventDefault();
    const before = point(event), next = Math.max(zoomMin, Math.min(zoomMax,
      state.zoom * px(event.deltaY < 0 ? '--wb-zoom-in' : '--wb-zoom-out')));
    const rect = viewport.getBoundingClientRect();
    state.zoom = next;
    state.x = event.clientX - rect.left - before.x * next;
    state.y = event.clientY - rect.top - before.y * next;
    transform(); saveViewport();
  }, { passive: false });
  viewport.addEventListener('keydown', event => {
    if (event.key !== 'Delete' || !state.selected || event.target.closest('input, textarea, [contenteditable]')) return;
    const id = state.selected;
    request(`/api/boards/${state.board.id}/items/${id}`, 'DELETE').then(() => {
      state.charts.get(id)?.dispose(); state.charts.delete(id);
      cards.querySelector(`[data-id="${id}"]`)?.remove(); state.items.delete(id);
      state.links = state.links.filter(link => link.from_item_id !== id && link.to_item_id !== id);
      select(null); drawLinks();
    }).catch(showError);
  });
  root.addEventListener('toggle', event => {
    if (!event.target.matches('.wb-library-category') || !event.target.open) return;
    root.querySelectorAll('.wb-library-category').forEach(category => {
      if (category !== event.target) category.open = false;
    });
  }, true);
  root.querySelector('[data-action="fit"]').addEventListener('click', () => {
    if (!state.items.size) { state.x = 0; state.y = 0; state.zoom = 1; }
    else {
      const items = [...state.items.values()];
      const left = Math.min(...items.map(item => item.x)), top = Math.min(...items.map(item => item.y));
      const right = Math.max(...items.map(item => item.x + item.w));
      const bottom = Math.max(...items.map(item => item.y + item.h));
      state.zoom = Math.min(zoomMax, Math.max(zoomMin,
        Math.min(viewport.clientWidth / (right - left + px('--wb-fit-padding')),
          viewport.clientHeight / (bottom - top + px('--wb-fit-padding')))));
      state.x = (viewport.clientWidth - (right - left) * state.zoom) / 2 - left * state.zoom;
      state.y = (viewport.clientHeight - (bottom - top) * state.zoom) / 2 - top * state.zoom;
    }
    transform(); saveViewport();
  });
  root.querySelector('[data-action="note"]').addEventListener('click', () =>
    addItem('note', null, { text: '' }, point({ clientX: viewport.getBoundingClientRect().left + viewport.clientWidth / 2,
      clientY: viewport.getBoundingClientRect().top + viewport.clientHeight / 2 })).catch(showError));
  root.querySelector('[data-action="thesis"]').addEventListener('click', () => {
    if ([...state.items.values()].some(item => item.kind === 'thesis')) return;
    addItem('thesis', null, {}, { x: 0, y: 0 }).catch(showError);
  });
  root.querySelector('[data-action="connect"]').addEventListener('click', event => {
    state.connect = state.connect ? null : 'choose';
    event.currentTarget.setAttribute('aria-pressed', String(Boolean(state.connect)));
  });
  root.querySelector('[data-action="new-board"]').addEventListener('click', async () => {
    const name = prompt('Board name'); if (!name?.trim()) return;
    try {
      const board = await request('/api/boards', 'POST', { currency, name: name.trim() });
      state.boards.push(board); await openBoard(board.id);
    } catch (error) { showError(error); }
  });
  request(`/whiteboard/${currency}/boards`).then(boards => {
    state.boards = boards;
    const main = boards.find(board => board.name === 'Main') || boards[0];
    return openBoard(main.id);
  }).catch(showError);
})();
