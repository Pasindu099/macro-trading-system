const PAIRS = ["USDJPY", "EURUSD", "GBPUSD", "AUDUSD", "USDCHF"];
const TIMEFRAMES = ["3M", "6M", "1Y", "2Y"];
const SERIES = {
  price: { label: "FX price", color: "#378ADD", dash: null, width: 2 },
  yieldDiff: { label: "Yield diff", color: "#D85A30", dash: [5, 4], width: 1.5 },
  cpiDiff: { label: "CPI diff", color: "#1D9E75", dash: [3, 3], width: 1.5 },
  rateDiff: { label: "Rate diff", color: "#7F77DD", dash: [8, 3], width: 1.5 },
  pmiDiff: { label: "PMI diff", color: "#BA7517", dash: [2, 4], width: 1.5 },
  fairValue: { label: "Fair value", color: "#7F77DD", dash: [6, 4], width: 1.5 },
};
const OVERLAY_KEYS = ["yieldDiff", "cpiDiff", "rateDiff", "pmiDiff"];
const UI_OVERLAY_KEYS = {
  yieldDiff: "yieldDiff",
  cpiDiff: "cpi",
  rateDiff: "rate",
  pmiDiff: "pmi",
};

let state = {
  pair: "USDJPY",
  tf: "3M",
  overlays: new Set(["yieldDiff"]),
  series: null,
  matrix: null,
  debounceId: null,
};

const chartIds = {
  main: "corr-main-chart",
  scatter: "corr-scatter-chart",
  matrix: "corr-matrix-chart",
  divergence: "corr-divergence-chart",
};

export function initCorrelationLab() {
  const root = document.getElementById("correlation-lab-section");
  if (!root || !window.echarts) return;
  renderShell(root);
  bindControls(root);
  loadAll(root);
}

function renderShell(root) {
  root.innerHTML = `
    <div class="section-header">
      <span class="section-label">Correlation Lab</span>
      <span class="corr-cache-badge" data-corr-cache hidden>cached data</span>
    </div>
    <div class="corr-topbar">
      <label class="corr-select-wrap">
        <span>Pair</span>
        <select data-corr-pair>
          ${PAIRS.map((pair) => `<option value="${pair}" ${pair === state.pair ? "selected" : ""}>${pair}</option>`).join("")}
        </select>
      </label>
      <div class="corr-timeframes" aria-label="Timeframe">
        ${TIMEFRAMES.map((tf) => `<button class="corr-tf-btn ${tf === state.tf ? "is-active" : ""}" type="button" data-corr-tf="${tf}">${tf}</button>`).join("")}
      </div>
      <div class="corr-overlays" aria-label="Overlays">
        ${OVERLAY_KEYS.map((key) => `
          <button class="corr-overlay-btn ${state.overlays.has(key) ? "is-active" : ""}" type="button" data-corr-overlay="${key}" style="--swatch:${SERIES[key].color}">
            <span></span>${SERIES[key].label}
          </button>
        `).join("")}
      </div>
    </div>
    <div class="corr-stat-row">
      <article class="corr-stat-card"><span>Spot</span><strong data-corr-stat="price">--</strong><small data-corr-stat="priceChange">--</small></article>
      <article class="corr-stat-card"><span>Yield Diff</span><strong data-corr-stat="yield">--</strong><small>latest differential</small></article>
      <article class="corr-stat-card"><span>Correlation</span><strong data-corr-stat="corr">--</strong><small data-corr-stat="r2">--</small></article>
      <article class="corr-stat-card"><span>Divergence</span><strong data-corr-stat="divergence">--</strong><small>spot vs fair value</small></article>
    </div>
    ${chartCard("main", "FX price + macro overlays", "Dual-axis monthly series", true)}
    <div class="corr-grid-two">
      ${chartCard("scatter", "Scatter + regression", "Price vs dominant driver", false)}
      ${chartCard("matrix", "Correlation matrix", "Pearson r, full available monthly history", false)}
    </div>
    ${chartCard("divergence", "Divergence detector", "Actual spot vs fitted fair value", true, true)}
  `;
}

function chartCard(key, title, subtitle, wide = false, ask = false) {
  return `
    <article class="corr-chart-card ${wide ? "corr-chart-card--wide" : ""}">
      <header>
        <div>
          <strong>${title}</strong>
          <small>${subtitle}</small>
        </div>
        ${ask ? '<button class="corr-ask-btn" type="button" data-corr-ask>Ask AI ↗</button>' : ""}
      </header>
      <div id="${chartIds[key]}" class="corr-chart ${key === "matrix" ? "corr-chart--matrix" : ""}"></div>
      <div class="corr-insight-box" data-corr-insight="${key}">Waiting for data.</div>
    </article>
  `;
}

function bindControls(root) {
  root.querySelector("[data-corr-pair]")?.addEventListener("change", (event) => {
    state.pair = event.target.value;
    debouncedLoad(root);
  });
  root.querySelectorAll("[data-corr-tf]").forEach((button) => {
    button.addEventListener("click", () => {
      state.tf = button.dataset.corrTf || "3M";
      root.querySelectorAll("[data-corr-tf]").forEach((item) => item.classList.toggle("is-active", item === button));
      debouncedLoad(root);
    });
  });
  root.querySelectorAll("[data-corr-overlay]").forEach((button) => {
    button.addEventListener("click", () => {
      const key = button.dataset.corrOverlay;
      if (!key) return;
      if (state.overlays.has(key)) {
        state.overlays.delete(key);
      } else {
        state.overlays.add(key);
      }
      button.classList.toggle("is-active", state.overlays.has(key));
      renderMainOnly(root);
    });
  });
  root.querySelector("[data-corr-ask]")?.addEventListener("click", () => askAi(root));
}

function debouncedLoad(root) {
  window.clearTimeout(state.debounceId);
  state.debounceId = window.setTimeout(() => loadAll(root), 300);
}

async function loadAll(root) {
  setBusy(root, true);
  clearErrors(root);
  try {
    const overlays = OVERLAY_KEYS.map((key) => UI_OVERLAY_KEYS[key]).join(",");
    const params = new URLSearchParams({ pair: state.pair, tf: state.tf, overlays });
    const [seriesRes, matrixRes] = await Promise.all([
      fetch(`/api/correlation/series?${params.toString()}`, { cache: "no-store" }),
      fetch(`/api/correlation/matrix?pair=${encodeURIComponent(state.pair)}`, { cache: "no-store" }),
    ]);
    if (!seriesRes.ok) throw new Error(await fetchError(seriesRes, "Series fetch failed"));
    if (!matrixRes.ok) throw new Error(await fetchError(matrixRes, "Matrix fetch failed"));
    state.series = await seriesRes.json();
    state.matrix = await matrixRes.json();
    renderAll(root);
  } catch (error) {
    showError(root, "main", error.message);
    showError(root, "scatter", error.message);
    showError(root, "matrix", error.message);
    showError(root, "divergence", error.message);
  } finally {
    setBusy(root, false);
  }
}

function renderAll(root) {
  renderStats(root);
  renderMainChart(root);
  renderScatterChart(root);
  renderMatrixChart(root);
  renderDivergenceChart(root);
  renderInsights(root);
  const badge = root.querySelector("[data-corr-cache]");
  if (badge) badge.hidden = !state.series?.cachedData;
}

function renderMainOnly(root) {
  if (!state.series) return;
  renderMainChart(root);
  renderInsights(root);
}

function renderStats(root) {
  const stats = state.series?.stats || {};
  setText(root, '[data-corr-stat="price"]', formatNumber(stats.currentPrice, 4));
  setText(root, '[data-corr-stat="priceChange"]', `${formatSigned(stats.priceChange1d, 2)}% 1d`);
  setText(root, '[data-corr-stat="yield"]', formatSigned(stats.yieldDiff, 2));
  setText(root, '[data-corr-stat="corr"]', formatNumber(stats.correlation, 2));
  setText(root, '[data-corr-stat="r2"]', `${formatNumber(stats.rSquared, 1)}% R2`);
  setText(root, '[data-corr-stat="divergence"]', `${formatSigned(stats.divergencePct, 1)}%`);
}

function renderMainChart(root) {
  const payload = state.series;
  if (!payload) return;
  const chart = freshChart(chartIds.main);
  if (!chart) return;
  const series = [
    lineSeries("FX price", payload.price, SERIES.price, 0),
    lineSeries("Fair value", payload.fairValue, SERIES.fairValue, 0),
  ];
  OVERLAY_KEYS.forEach((key) => {
    if (state.overlays.has(key) && Array.isArray(payload[key])) {
      series.push(lineSeries(SERIES[key].label, payload[key], SERIES[key], 1));
    }
  });
  series[0].markArea = divergenceMarkArea(payload);
  chart.setOption(baseLineOptions(payload.labels, series, "FX price", "Drivers"), true);
}

function renderScatterChart(root) {
  const payload = state.series;
  if (!payload) return;
  const chart = freshChart(chartIds.scatter);
  if (!chart) return;
  const driver = dominantDriverKey();
  const xValues = payload[driver] || [];
  const points = (payload.price || []).map((price, index) => [xValues[index], price])
    .filter(([x, y]) => Number.isFinite(Number(x)) && Number.isFinite(Number(y)));
  if (points.length < 3) {
    showError(root, "scatter", "insufficient data");
    return;
  }
  const regression = regressionLine(points);
  chart.setOption({
    ...baseChartScaffold(),
    grid: chartGrid(),
    xAxis: axis("value", `${SERIES[driver]?.label || "Driver"}`),
    yAxis: axis("value", "FX price"),
    series: [
      { name: "Monthly points", type: "scatter", data: points, symbolSize: 8, itemStyle: { color: "#378ADD" } },
      { name: "Regression", type: "line", data: regression, showSymbol: false, lineStyle: { color: "#EFA027", width: 1.5 } },
    ],
  }, true);
}

function renderMatrixChart() {
  const drivers = state.matrix?.drivers || [];
  const chart = freshChart(chartIds.matrix);
  if (!chart) return;
  chart.setOption({
    ...baseChartScaffold(),
    grid: { left: 82, right: 18, top: 18, bottom: 22 },
    xAxis: axis("value", "r", { min: -1, max: 1 }),
    yAxis: {
      type: "category",
      data: drivers.map((driver) => driver.label),
      axisLabel: tickLabel(),
      axisLine: axisLine(),
      axisTick: { show: false },
    },
    series: [{
      type: "bar",
      data: drivers.map((driver) => Number(driver.r || 0)),
      itemStyle: {
        color: (params) => (Number(params.value) >= 0 ? "#1D9E75" : "#D85A30"),
      },
      label: { show: true, position: "right", color: "#888", fontFamily: "monospace", fontSize: 11 },
    }],
  }, true);
}

function renderDivergenceChart(root) {
  const payload = state.series;
  if (!payload) return;
  const chart = freshChart(chartIds.divergence);
  if (!chart) return;
  const price = lineSeries("Actual", payload.price, SERIES.price, 0);
  price.markArea = divergenceMarkArea(payload);
  chart.setOption(baseLineOptions(payload.labels, [
    price,
    lineSeries("Fair value", payload.fairValue, SERIES.fairValue, 0),
  ], "FX price", ""), true);
}

function renderInsights(root) {
  const stats = state.series?.stats || {};
  const dominant = dominantDriver();
  const corr = Number(stats.correlation);
  const divergence = Number(stats.divergencePct);
  setInsight(root, "main", `${correlationInsight(corr)} Dominant driver: ${dominant.label || "n/a"}.`);
  setInsight(root, "scatter", `${dominant.label || "Driver"} currently has the highest absolute r in the matrix (${formatNumber(dominant.r, 2)}).`);
  setInsight(root, "matrix", `Driver ranking is led by ${dominant.label || "n/a"}, followed by ${(state.matrix?.drivers || [])[1]?.label || "n/a"}.`);
  setInsight(root, "divergence", divergenceInsight(divergence));
}

function lineSeries(name, data, style, yAxisIndex) {
  return {
    name,
    type: "line",
    smooth: true,
    showSymbol: false,
    yAxisIndex,
    data: data || [],
    lineStyle: {
      color: style.color,
      width: style.width,
      type: style.dash || "solid",
    },
    itemStyle: { color: style.color },
  };
}

function baseLineOptions(labels, series, leftName, rightName) {
  return {
    ...baseChartScaffold(),
    legend: {
      top: 0,
      textStyle: tickLabel(),
    },
    grid: chartGrid(),
    xAxis: {
      type: "category",
      data: labels || [],
      axisLabel: tickLabel(),
      axisLine: axisLine(),
      axisTick: { show: false },
    },
    yAxis: [
      axis("value", leftName),
      axis("value", rightName, { splitLine: { show: false } }),
    ],
    series,
  };
}

function baseChartScaffold() {
  return {
    backgroundColor: "transparent",
    tooltip: {
      trigger: "axis",
      backgroundColor: "#1a1a1a",
      borderColor: "rgba(255,255,255,0.15)",
      borderWidth: 1,
      textStyle: { color: "#e8e8e8", fontFamily: "monospace", fontSize: 11 },
    },
  };
}

function axis(type, name, overrides = {}) {
  return {
    type,
    name,
    scale: true,
    axisLabel: tickLabel(),
    axisLine: axisLine(),
    axisTick: { show: false },
    splitLine: { lineStyle: { color: "rgba(255,255,255,0.07)" } },
    nameTextStyle: tickLabel(),
    ...overrides,
  };
}

function chartGrid() {
  return { left: 48, right: 54, top: 44, bottom: 34, containLabel: true };
}

function tickLabel() {
  return { color: "#888", fontFamily: "monospace", fontSize: 11 };
}

function axisLine() {
  return { lineStyle: { color: "rgba(255,255,255,0.15)" } };
}

function freshChart(id) {
  const el = document.getElementById(id);
  if (!el) return null;
  const existing = window.echarts.getInstanceByDom(el);
  if (existing) existing.dispose();
  el.innerHTML = "";
  return window.echarts.init(el);
}

function divergenceMarkArea(payload) {
  const labels = payload.labels || [];
  const price = payload.price || [];
  const fair = payload.fairValue || [];
  const areas = [];
  labels.forEach((label, index) => {
    const gap = pctGap(price[index], fair[index]);
    if (Math.abs(gap || 0) > 5) {
      areas.push([{ xAxis: label }, { xAxis: labels[Math.min(index + 1, labels.length - 1)] }]);
    }
  });
  return areas.length ? {
    silent: true,
    itemStyle: { color: "rgba(239,159,39,0.12)" },
    data: areas,
  } : undefined;
}

function dominantDriver() {
  return (state.matrix?.drivers || [])[0] || {};
}

function dominantDriverKey() {
  const label = String(dominantDriver().label || "Yield diff").toLowerCase();
  if (label.includes("cpi")) return "cpiDiff";
  if (label.includes("rate")) return "rateDiff";
  if (label.includes("pmi")) return "pmiDiff";
  return "yieldDiff";
}

function correlationInsight(rValue) {
  if (!Number.isFinite(rValue)) return "Correlation is unavailable for the current sample.";
  const direction = rValue >= 0 ? "positive" : "negative";
  const abs = Math.abs(rValue);
  if (abs > 0.8) return `Strong ${direction} correlation between FX price and the selected fundamental driver.`;
  if (abs >= 0.6) return `Moderate ${direction} correlation between FX price and the selected fundamental driver.`;
  return `Weak ${direction} correlation in this sample, so price is not closely tracking the selected driver.`;
}

function divergenceInsight(value) {
  if (!Number.isFinite(value)) return "Divergence is unavailable for the current sample.";
  const side = value >= 0 ? "premium" : "discount";
  const abs = Math.abs(value);
  if (abs > 7) return `Spot is ${formatNumber(abs, 1)}% away from fair value, warning of possible overextension.`;
  if (abs >= 3) return `Spot trades at a moderate ${side} to fair value (${formatNumber(abs, 1)}%).`;
  return `Spot is near fair value, with less than 3% model divergence.`;
}

function regressionLine(points) {
  const xs = points.map(([x]) => Number(x));
  const ys = points.map(([, y]) => Number(y));
  const xMean = average(xs);
  const yMean = average(ys);
  const denom = xs.reduce((sum, x) => sum + ((x - xMean) ** 2), 0);
  if (!denom) return [];
  const slope = points.reduce((sum, [x, y]) => sum + ((Number(x) - xMean) * (Number(y) - yMean)), 0) / denom;
  const intercept = yMean - (slope * xMean);
  const minX = Math.min(...xs);
  const maxX = Math.max(...xs);
  return [[minX, intercept + slope * minX], [maxX, intercept + slope * maxX]];
}

function askAi(root) {
  const prompt = `Analyze ${state.pair} divergence. Spot is ${formatNumber(state.series?.stats?.currentPrice, 4)}, divergence is ${formatSigned(state.series?.stats?.divergencePct, 1)}%, and dominant driver is ${dominantDriver().label || "n/a"} with r=${formatNumber(dominantDriver().r, 2)}.`;
  const input = document.querySelector("[data-ai-chat-input], [data-chat-input], #ai-chat-input, textarea[name='chat']");
  if (input) {
    input.value = prompt;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
  } else if (navigator.clipboard) {
    navigator.clipboard.writeText(prompt).catch(() => {});
  }
  window.dispatchEvent(new CustomEvent("macro:prefill-chat", { detail: { prompt } }));
  setInsight(root, "divergence", `${divergenceInsight(Number(state.series?.stats?.divergencePct))} AI prompt prepared.`);
}

function showError(root, key, message) {
  const el = document.getElementById(chartIds[key]);
  if (el) el.innerHTML = `<div class="corr-error">${escapeHtml(message || "Unable to load chart.")}</div>`;
  setInsight(root, key, "Chart unavailable.");
}

function clearErrors(root) {
  root.querySelectorAll(".corr-error").forEach((el) => el.remove());
}

function setBusy(root, busy) {
  root.classList.toggle("is-loading", busy);
}

async function fetchError(response, fallback) {
  const body = await response.json().catch(() => null);
  return body?.detail || fallback;
}

function setText(root, selector, value) {
  const el = root.querySelector(selector);
  if (el) el.textContent = value;
}

function setInsight(root, key, value) {
  const el = root.querySelector(`[data-corr-insight="${key}"]`);
  if (el) el.textContent = value;
}

function pctGap(price, fair) {
  const p = Number(price);
  const f = Number(fair);
  if (!Number.isFinite(p) || !Number.isFinite(f) || !f) return null;
  return ((p - f) / f) * 100;
}

function average(values) {
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function formatNumber(value, digits) {
  const number = Number(value);
  return Number.isFinite(number) ? number.toFixed(digits) : "--";
}

function formatSigned(value, digits) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "--";
  return `${number >= 0 ? "+" : ""}${number.toFixed(digits)}`;
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
