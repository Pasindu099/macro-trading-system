(function () {
  const root = document.querySelector("[data-fi-root]");
  if (!root) return;

  const state = root.querySelector("[data-fi-state]");
  const pairBody = root.querySelector("[data-fi-pairs]");
  const countrySelect = root.querySelector("[data-fi-country]");
  const pairSelect = root.querySelector("[data-fi-pair]");
  const sortSelect = root.querySelector("[data-fi-sort]");
  const refresh = root.querySelector("[data-fi-refresh]");
  const fmt = new Intl.NumberFormat("en-US", { maximumFractionDigits: 2 });
  let latestPairs = [];

  async function getJson(url) {
    const response = await fetch(url, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
    return response.json();
  }

  function row(label, value, extraClass) {
    return `<div class="fi-row"><span class="fi-label">${escapeHtml(label)}</span><span class="fi-value ${extraClass || ""}">${escapeHtml(value)}</span></div>`;
  }

  function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      "\"": "&quot;",
      "'": "&#39;",
    }[char]));
  }

  function cls(value) {
    return value > 0 ? "fi-pos" : value < 0 ? "fi-neg" : "fi-muted";
  }

  async function load() {
    state.className = "fi-state";
    state.textContent = "Loading fixed-income workspace...";
    try {
      const [command, countries, pairs, quality, shock] = await Promise.all([
        getJson("/api/fixed-income/command-centre"),
        getJson("/api/fixed-income/countries"),
        getJson("/api/fixed-income/pairs"),
        getJson("/api/fixed-income/data-quality"),
        getJson("/api/fixed-income/shock/current"),
      ]);
      renderCommand(command, quality, shock);
      renderSources(quality);
      renderSelectors(countries.countries, pairs.pairs);
      latestPairs = pairs.pairs;
      await renderPairs(latestPairs);
      await renderCountry(countrySelect.value || "USD");
      await renderPair(pairSelect.value || "EURUSD");
      state.textContent = `Loaded. Differential convention: ${command.conventions.fx_differential}`;
    } catch (error) {
      state.className = "fi-state is-error";
      state.textContent = `Fixed-income data failed to load: ${error.message}`;
    }
  }

  function renderSources(quality) {
    const target = root.querySelector("[data-fi-source-quality]");
    if (!target) return;
    const ois = (quality.rate_probability_sources?.ois_and_futures || []).slice(0, 8);
    const scraped = (quality.rate_probability_sources?.third_party_scraped || []).slice(0, 8);
    const rows = [
      row("Primary rate-probability policy", quality.rate_probability_sources?.selection_policy || "Unavailable", "fi-muted"),
      ...ois.map((item) => row(`${item.bank} ${item.source}`, `${item.provenance} / ${item.delivery_source} / ${item.methodology} / ${item.freshness.status}`, item.freshness.is_stale ? "fi-neg" : "")),
      ...scraped.map((item) => row(`${item.bank} scraped`, `${item.methodology} / ${item.freshness.status}`, item.freshness.is_stale ? "fi-neg" : "fi-muted")),
    ];
    target.innerHTML = rows.join("");
  }

  function renderCommand(command, quality, shock) {
    const hawkish = command.most_hawkish_repricing?.[0];
    const dovish = command.most_dovish_repricing?.[0];
    const pairwise = command.strongest_pairwise_repricing?.[0];
    root.querySelector("[data-fi-two-year]").innerHTML = [
      row("Most hawkish 5D", hawkish ? `${hawkish.country_code} ${fmt.format(hawkish.score)} bp` : "Unavailable", hawkish?.score > 0 ? "fi-pos" : "fi-muted"),
      row("Most dovish 5D", dovish ? `${dovish.country_code} ${fmt.format(dovish.score)} bp` : "Unavailable", dovish?.score < 0 ? "fi-neg" : "fi-muted"),
      row("Largest 1D 2Y move", formatMove(command.largest_daily_2y_moves?.[0]), cls(command.largest_daily_2y_moves?.[0]?.change_bps || 0)),
      row("Largest 5D 2Y move", formatMove(command.largest_weekly_2y_moves?.[0]), cls(command.largest_weekly_2y_moves?.[0]?.change_bps || 0)),
      row("Pairwise repricing", pairwise ? `${pairwise.pair} ${fmt.format(pairwise.relative_repricing_bps)} bp` : "Unavailable", cls(pairwise?.relative_repricing_bps || 0)),
    ].join("");

    root.querySelector("[data-fi-regime]").innerHTML = Object.entries(command.curve_regime)
      .map(([country, item]) => {
        const slope = item.curve_shape.slope_2y_10y_bps;
        const shape = item.curve_shape.label;
        const movement = item.curve_movement.label;
        return row(country, `${shape} ${slope == null ? "" : fmt.format(slope) + " bp"}; ${movement}`, shape === "inverted" ? "fi-neg" : "");
      })
      .join("");

    const staleGov = Object.entries(quality.government_yields || {})
      .filter(([, item]) => item.latest && item.latest < new Date(Date.now() - 7 * 86400000).toISOString().slice(0, 10))
      .slice(0, 8);
    const staleRates = [
      ...(quality.rate_probability_sources?.ois_and_futures || []),
      ...(quality.rate_probability_sources?.third_party_scraped || []),
    ].filter((item) => item.freshness?.is_stale).slice(0, 4);
    root.querySelector("[data-fi-quality]").innerHTML = staleGov.length
      ? staleGov.map(([key, item]) => row(key, item.latest, "fi-neg")).join("")
      : [
        row("Government yields", "Fresh", "fi-pos"),
        row("OIS/proxy warnings", staleRates.length ? `${staleRates.length} stale/watch sources` : "None", staleRates.length ? "fi-neg" : "fi-pos"),
      ].join("");

    root.querySelector("[data-fi-shock]").innerHTML = [
      row("Primary", shock.classification, ""),
      row("Confidence", fmt.format(shock.confidence * 100) + "%", ""),
      row("US 2Y 1D", valueBp(shock.input_snapshot?.US_2Y_change_bps_1d), cls(shock.input_snapshot?.US_2Y_change_bps_1d || 0)),
      row("US 10Y 1D", valueBp(shock.input_snapshot?.US_10Y_change_bps_1d), cls(shock.input_snapshot?.US_10Y_change_bps_1d || 0)),
      row("Missing", shock.missing_inputs.join(", ") || "None", "fi-muted"),
      `<div class="fi-badge">${shock.language_guardrail}</div>`,
    ].join("");
  }

  function formatMove(item) {
    if (!item) return "Unavailable";
    return `${item.country_code} ${fmt.format(item.change_bps)} bp`;
  }

  function valueBp(value) {
    return value == null ? "Unavailable" : `${fmt.format(value)} bp`;
  }

  function renderSelectors(countries, pairs) {
    countrySelect.innerHTML = countries.map((c) => `<option value="${c.currency_code}">${c.currency_code} / ${c.country_code}</option>`).join("");
    pairSelect.innerHTML = pairs.map((p) => `<option value="${p.pair.replace("/", "")}">${p.pair}</option>`).join("");
  }

  async function renderPairs(pairs) {
    const enriched = await Promise.all(pairs.map(async (item) => {
      const pairCode = item.pair.replace("/", "");
      const [mispricing, confirmation] = await Promise.all([
        getJson(`/api/fixed-income/pairs/${pairCode}/mispricing?window=120`),
        getJson(`/api/fixed-income/pairs/${pairCode}/confirmation?lookback=5`),
      ]);
      const diff = item.latest_2y_differential;
      return { item, diff, mispricing, confirmation };
    }));
    enriched.sort(sortPairRows);
    const rows = enriched.map(({ item, diff, mispricing, confirmation }) => `<tr>
        <td><strong>${item.pair}</strong></td>
        <td class="${cls(confirmation.differential_change_bps || 0)}">${confirmation.differential_change_bps == null ? "Missing" : fmt.format(confirmation.differential_change_bps) + " bp"}</td>
        <td class="${cls(mispricing.residual_z_score || 0)}">${mispricing.residual_z_score == null ? "n/a" : fmt.format(mispricing.residual_z_score)}</td>
        <td>${mispricing.regression.r_squared == null ? "n/a" : fmt.format(mispricing.regression.r_squared)}</td>
        <td>${confirmation.state}</td>
        <td>${mispricing.state}</td>
        <td><strong>${mispricing.opportunity_bucket}</strong> ${fmt.format(mispricing.adjusted_opportunity_score || 0)}</td>
        <td><span class="fi-badge">${mispricing.gates.data_fresh ? "fresh" : "stale"} / ${mispricing.relationship_strength}</span></td>
      </tr>`);
    pairBody.innerHTML = rows.join("");
  }

  function sortPairRows(a, b) {
    const mode = sortSelect?.value || "reliable";
    if (mode === "reliable") {
      return (b.mispricing.adjusted_opportunity_score || 0) - (a.mispricing.adjusted_opportunity_score || 0);
    }
    if (mode === "relationship") {
      return (b.mispricing.regression.r_squared || -1) - (a.mispricing.regression.r_squared || -1);
    }
    if (mode === "momentum") {
      return Math.abs(b.diff || 0) - Math.abs(a.diff || 0);
    }
    if (mode === "confirmation") {
      return String(a.confirmation.state).localeCompare(String(b.confirmation.state));
    }
    return Math.abs(b.mispricing.residual_z_score || 0) - Math.abs(a.mispricing.residual_z_score || 0);
  }

  async function renderCountry(currency) {
    const data = await getJson(`/api/fixed-income/countries/${currency}/curve`);
    const detail = root.querySelector("[data-fi-country-detail]");
    const curves = data.historical_curves || {};
    const curve = curves.current?.curve || data.curve.curve || {};
    detail.innerHTML = [
      row("Country", data.country_code, ""),
      row("Missing maturities", data.missing_inputs.join(", ") || "None", data.missing_inputs.length ? "fi-neg" : "fi-pos"),
      row("Confidence", fmt.format(data.confidence * 100) + "%", ""),
      row("Method", data.calculation_method, "fi-muted"),
    ].join("");
    renderCountryChanges(data.maturity_changes_bps);
    drawCurveLines(root.querySelector("[data-fi-curve-chart]"), curves);
  }

  function renderCountryChanges(changes) {
    const target = root.querySelector("[data-fi-country-changes]");
    if (!target) return;
    const fiveDay = changes?.["5d"] || {};
    target.innerHTML = Object.entries(fiveDay)
      .filter(([, value]) => value != null)
      .map(([maturity, value]) => `<span class="${cls(value)}">${escapeHtml(maturity)} ${fmt.format(value)} bp</span>`)
      .join("");
  }

  async function renderPair(pairCode) {
    const [diff, mispricing, confirmation, narrative] = await Promise.all([
      getJson(`/api/fixed-income/pairs/${pairCode}/differentials?window=120`),
      getJson(`/api/fixed-income/pairs/${pairCode}/mispricing?window=120`),
      getJson(`/api/fixed-income/pairs/${pairCode}/confirmation?lookback=5`),
      getJson(`/api/fixed-income/pairs/${pairCode}/narrative?lookback=5`),
    ]);
    root.querySelector("[data-fi-pair-detail]").innerHTML = [
      row("Pair", mispricing.pair, ""),
      row("State", mispricing.state, ""),
      row("Confirmation", confirmation.state, ""),
      row("Residual Z", mispricing.residual_z_score == null ? "n/a" : fmt.format(mispricing.residual_z_score), cls(mispricing.residual_z_score || 0)),
      row("Gates", Object.entries(mispricing.gates).filter(([, v]) => !v).map(([k]) => k).join(", ") || "All passed", "fi-muted"),
    ].join("");
    root.querySelector("[data-fi-pair-narrative]").textContent = narrative.narrative || "";
    drawPairModel(root.querySelector("[data-fi-pair-chart]"), mispricing.model_series || diff.points);
  }

  function drawCurveLines(el, curves) {
    if (!window.echarts || !el) return;
    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
    const current = curves.current?.curve || {};
    const labels = Object.keys(current);
    const seriesConfig = [
      ["Current", curves.current?.curve, "#f97316"],
      ["1D ago", curves["1d"]?.curve, "#60a5fa"],
      ["5D ago", curves["5d"]?.curve, "#22c55e"],
      ["20D ago", curves["20d"]?.curve, "#a78bfa"],
    ].filter(([, curve]) => curve);
    chart.setOption({
      color: seriesConfig.map(([, , color]) => color),
      grid: { left: 54, right: 18, top: 28, bottom: 42 },
      tooltip: { trigger: "axis", valueFormatter: (value) => `${fmt.format(value)}%` },
      xAxis: { type: "category", data: labels, axisLabel: { color: "#8b95a7" } },
      yAxis: { type: "value", name, nameTextStyle: { color: "#8b95a7" }, axisLabel: { color: "#8b95a7", formatter: "{value}%" }, splitLine: { lineStyle: { color: "rgba(255,255,255,0.12)" } } },
      series: seriesConfig.map(([name, curve]) => ({ name, type: "line", smooth: true, data: labels.map((label) => curve[label]), showSymbol: true })),
    });
    chart.resize();
  }

  function drawPairModel(el, points) {
    if (!window.echarts || !el) return;
    const chart = echarts.getInstanceByDom(el) || echarts.init(el);
    const labels = points.map((p) => p.date);
    chart.setOption({
      color: ["#60a5fa", "#f97316", "#ef4444", "#94a3b8", "#94a3b8", "#64748b", "#64748b"],
      grid: [
        { left: 58, right: 18, top: 28, height: "48%" },
        { left: 58, right: 18, bottom: 42, height: "24%" },
      ],
      tooltip: { trigger: "axis" },
      xAxis: { type: "category", data: labels, axisLabel: { color: "#8b95a7" } },
      yAxis: [
        { type: "value", name: "FX", nameTextStyle: { color: "#8b95a7" }, axisLabel: { color: "#8b95a7" }, splitLine: { lineStyle: { color: "rgba(255,255,255,0.12)" } } },
        { type: "value", gridIndex: 1, name: "Residual Z", nameTextStyle: { color: "#8b95a7" }, axisLabel: { color: "#8b95a7" }, splitLine: { lineStyle: { color: "rgba(255,255,255,0.12)" } } },
      ],
      xAxis: [
        { type: "category", data: labels, axisLabel: { color: "#8b95a7" } },
        { type: "category", gridIndex: 1, data: labels, axisLabel: { color: "#8b95a7" } },
      ],
      series: [
        { name: "Actual FX", type: "line", smooth: true, showSymbol: false, data: points.map((p) => p.fx_close) },
        { name: "Rates-implied FX", type: "line", smooth: true, showSymbol: false, data: points.map((p) => p.rates_implied_estimate) },
        { name: "Residual Z", type: "line", xAxisIndex: 1, yAxisIndex: 1, smooth: true, showSymbol: false, data: points.map((p) => p.residual_z_score) },
        { name: "+1 SD", type: "line", xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, data: points.map(() => 1), lineStyle: { type: "dashed" } },
        { name: "-1 SD", type: "line", xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, data: points.map(() => -1), lineStyle: { type: "dashed" } },
        { name: "+2 SD", type: "line", xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, data: points.map(() => 2), lineStyle: { type: "dotted" } },
        { name: "-2 SD", type: "line", xAxisIndex: 1, yAxisIndex: 1, showSymbol: false, data: points.map(() => -2), lineStyle: { type: "dotted" } },
      ],
    });
    chart.resize();
  }

  countrySelect.addEventListener("change", () => renderCountry(countrySelect.value));
  pairSelect.addEventListener("change", () => renderPair(pairSelect.value));
  sortSelect?.addEventListener("change", () => renderPairs(latestPairs));
  refresh.addEventListener("click", load);
  load();
})();
