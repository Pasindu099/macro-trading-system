(function () {
  const form = document.getElementById('calendar-filters');
  if (!form) return;
  const preset = document.getElementById('calendar-preset');
  document.querySelectorAll('[data-preset]').forEach(button => button.addEventListener('click', () => {
    preset.value = button.dataset.preset;
    form.requestSubmit();
  }));
  form.querySelectorAll('input[type=date]').forEach(input => input.addEventListener('change', () => {
    preset.value = 'custom';
  }));
  form.querySelectorAll('input[name=impact], input[name=currency], input[name=surprise]')
    .forEach(input => input.addEventListener('change', () => form.requestSubmit()));
  const cookie = name => document.cookie.split('; ').find(part => part.startsWith(name + '='))?.split('=')[1];
  if (!cookie('calendar_tz_name')) {
    document.cookie = 'calendar_tz_name=' + encodeURIComponent(Intl.DateTimeFormat().resolvedOptions().timeZone) + '; path=/; SameSite=Lax';
  }
  const mode = cookie('calendar_tz_mode') || 'utc';
  document.querySelectorAll('[data-zone]').forEach(button => {
    button.setAttribute('aria-pressed', String(button.dataset.zone === mode));
    button.addEventListener('click', () => {
      document.cookie = 'calendar_tz_mode=' + button.dataset.zone + '; path=/; SameSite=Lax';
      window.location.reload();
    });
  });
})();

(function () {
  const panel = document.getElementById('calendar-detail');
  if (!panel) return;
  const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  const close = () => { panel.classList.remove('open'); document.body.classList.remove('calendar-sheet-open'); };

  function charts(root) {
    const source = root.querySelector('[data-chart-data]');
    if (!source || !window.echarts) return;
    const data = JSON.parse(source.textContent);
    const text = { color: css('--muted'), fontFamily: css('--font-mono') || 'DM Mono, monospace', fontSize: 10 };
    const axis = { type: 'category', data: data.labels, axisLabel: text, axisLine: { lineStyle: { color: css('--line') } } };
    const history = echarts.init(root.querySelector('[data-history-chart]'));
    const draw = version => history.setOption({
      animation: false, grid: { left: 42, right: 10, top: 24, bottom: 24 }, tooltip: { trigger: 'axis' },
      legend: { textStyle: text, top: 0 }, xAxis: axis, yAxis: { type: 'value', axisLabel: text, splitLine: { lineStyle: { color: css('--line') } } },
      series: [
        { name: version === 'actual' ? 'Actual (revised)' : 'Actual (first print)', type: 'bar', data: data[version], itemStyle: { color: css('--chart-actual') } },
        { name: 'Consensus', type: 'line', data: data.consensus, symbolSize: 4, lineStyle: { color: css('--text') }, itemStyle: { color: css('--text') } },
      ],
    }, true);
    draw('actual');
    root.querySelectorAll('[data-version]').forEach(button => button.addEventListener('click', () => {
      root.querySelectorAll('[data-version]').forEach(other => other.setAttribute('aria-pressed', String(other === button)));
      draw(button.dataset.version);
    }));
    echarts.init(root.querySelector('[data-sigma-chart]')).setOption({
      animation: false, grid: { left: 42, right: 10, top: 8, bottom: 20 }, tooltip: { trigger: 'axis' },
      xAxis: { ...axis, axisLabel: { show: false } }, yAxis: { type: 'value', axisLabel: text, splitLine: { show: false } },
      series: [{ name: 'Surprise σ', type: 'bar', data: data.sigma.map(value => value === null ? null :
        { value, itemStyle: { color: value >= 0 ? css('--surprise-beat') : css('--surprise-miss') } }) }],
    });
  }

  document.body.addEventListener('htmx:afterSwap', event => {
    if (event.detail.target !== panel) return;
    panel.classList.add('open');
    document.body.classList.add('calendar-sheet-open');
    panel.querySelectorAll('[data-tab]').forEach(tab => tab.addEventListener('click', () => {
      panel.querySelectorAll('[data-tab]').forEach(other => other.setAttribute('aria-selected', String(other === tab)));
      panel.querySelectorAll('[data-panel]').forEach(section => { section.hidden = section.dataset.panel !== tab.dataset.tab; });
    }));
    panel.querySelector('[data-detail-close]')?.addEventListener('click', close);
    charts(panel);
  });
  document.addEventListener('keyup', event => { if (event.key === 'Escape') close(); });
})();

(function () {
  document.addEventListener('click', event => {
    const pick = event.target.closest('.event-pick');
    if (!pick) return;
    const form = pick.closest('form');
    form.querySelector('[data-release-input]').value = pick.dataset.releaseId;
    const label = form.querySelector('[data-picked-label]');
    label.textContent = 'Linked: ' + pick.dataset.label;
    label.hidden = false;
    document.getElementById('event-picker-results').innerHTML = '';
  });
  document.querySelectorAll('form[data-confirm]').forEach(form => form.addEventListener('submit', event => {
    if (!window.confirm(form.dataset.confirm)) { event.preventDefault(); return; }
    form.querySelector('[name=confirm]').value = 'yes';
  }));
})();
