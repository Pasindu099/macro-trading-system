(() => {
  const target = document.querySelector('[data-content-url]');
  if (!target) return;
  const charts = [];
  const css = getComputedStyle(document.documentElement);
  const color = name => css.getPropertyValue(name).trim();
  function chart(node, options) {
    if (!node || !window.echarts) return;
    const instance = echarts.init(node); instance.setOption(options); charts.push(instance); return instance;
  }
  function options(series) {
    return { animation: false, color: [color('--down'), color('--accent'), color('--muted')],
      textStyle: { color: color('--muted') }, tooltip: { trigger: 'axis' },
      legend: { type: 'scroll', textStyle: { color: color('--muted') } },
      grid: { left: 50, right: 18, top: 40, bottom: 55, containLabel: true },
      xAxis: { type: 'time' }, yAxis: { type: 'value', scale: true },
      dataZoom: [{ type: 'inside' }, { type: 'slider', height: 15, bottom: 5 }], series };
  }
  function initCharts() {
    const indicator = target.querySelector('[data-indicator-points]');
    if (indicator) {
      const points = JSON.parse(indicator.textContent);
      const lines = [
        { name: 'Actual (latest revised)', type: 'line', showSymbol: false, data: points.map(p => [p.date, p.actual]) },
        { name: 'Consensus', type: 'line', showSymbol: false, data: points.map(p => [p.date, p.consensus]) },
        { name: 'First print', type: 'line', showSymbol: false, lineStyle: { type: 'dashed' }, data: points.map(p => [p.date, p.first]) }
      ];
      const actual = chart(target.querySelector('[data-indicator-chart]'), options(lines.slice(0, 2)));
      target.querySelector('[data-first-toggle]')?.addEventListener('change', event => {
        actual?.setOption(options(event.target.checked ? lines : lines.slice(0, 2)), { replaceMerge: ['series'] });
      });
      chart(target.querySelector('[data-surprise-chart]'), options([{ name: 'Surprise σ', type: 'bar',
        data: points.map(p => ({ value: [p.date, p.sigma], itemStyle: { color: color(p.sigma < 0 ? '--down' : '--accent') } })) }]));
    }
    const dataset = target.querySelector('[data-dataset-points]');
    if (dataset) {
      const payload = JSON.parse(dataset.textContent), grouped = new Map();
      for (const point of payload.rows) {
        if (point.value === null) continue;
        if (!grouped.has(point.series)) grouped.set(point.series, []);
        grouped.get(point.series).push([point.date, point.value]);
      }
      const series = Array.from(grouped, ([name, data]) => ({ name, type: payload.type,
        showSymbol: false, data: data.sort((a, b) => a[0].localeCompare(b[0])) }));
      chart(target.querySelector('[data-dataset-chart]'), options(series));
    }
  }
  async function load() {
    target.setAttribute('aria-busy', 'true');
    charts.splice(0).forEach(instance => instance.dispose());
    try {
      const response = await fetch(target.dataset.contentUrl);
      if (!response.ok || response.redirected) throw new Error('Data request failed');
      target.innerHTML = await response.text(); initCharts();
    } catch (error) {
      target.replaceChildren();
      const message = document.createElement('p'); message.className = 'data-state'; message.dataset.state = 'error';
      message.textContent = 'This view could not load. Please try again.';
      const retry = document.createElement('button'); retry.type = 'button'; retry.dataset.retry = ''; retry.textContent = 'Retry';
      target.append(message, retry);
    } finally { target.setAttribute('aria-busy', 'false'); }
  }
  target.addEventListener('click', event => { if (event.target.closest('[data-retry]')) load(); });
  window.addEventListener('resize', () => charts.forEach(instance => instance.resize()));
  load();
})();
