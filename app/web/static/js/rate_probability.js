/**
 * rate_probability.js
 *
 * Handles:
 *   1. Countdown timer  (#countdown-display)
 *   2. Step dropdown    — re-fetches the meeting table
 *   3. ECharts renders  — rate path, year-end bar, policy history
 *
 * Globals injected by the template:
 *   window._rpMeetingIso  — ISO-8601 next-meeting datetime
 *   window._rpBank        — e.g. "ECB"
 *   window._rpCurrentRate — e.g. 2.00
 */

(function () {
  "use strict";

  /* ── Chart style constants (match existing dashboard) ────────────── */

  const CHART_AXIS = {
    axisLine:  { lineStyle: { color: "#2a2a2a" } },
    axisLabel: { color: "#888888", fontFamily: "'JetBrains Mono', monospace", fontSize: 11 },
    splitLine: { lineStyle: { color: "rgba(255,255,255,.08)" } },
  };

  const CHART_TOOLTIP = {
    trigger: "axis",
    backgroundColor: "#0a0a0a",
    borderColor: "#2a2a2a",
    textStyle: { color: "#e8e8e8", fontFamily: "'JetBrains Mono', monospace", fontSize: 11 },
    axisPointer: { type: "cross", crossStyle: { color: "rgba(255,255,255,0.2)" } },
  };

  const CHART_GRID = { left: 52, right: 20, top: 36, bottom: 30, containLabel: true };

  // Colors for the rate-path series (index 0 = Current, 1 = 1w ago, …)
  const PATH_LEGEND = [
    { label: "Current", color: "#6D93FF", current: true },
    { label: "1w ago", color: "#F0CF60" },
    { label: "3w ago", color: "#D1D5DB" },
    { label: "6w ago", color: "#A8AFBA" },
    { label: "10w ago", color: "#7B818D" },
  ];

  const PATH_COLORS = {
    Current: { color: "#6D93FF", width: 3.0, lineType: "solid", symbolSize: 8 },
    "1w ago": { color: "#F0CF60", width: 2.5, lineType: "solid", symbolSize: 7 },
    "3w ago": { color: "#D1D5DB", width: 2.3, lineType: "solid", symbolSize: 7 },
    "6w ago": { color: "#A8AFBA", width: 2.0, lineType: "solid", symbolSize: 7 },
    "10w ago": { color: "#7B818D", width: 2.0, lineType: "solid", symbolSize: 7 },
  };

  const BANK_COUNTRY = {
    FED: "US", ECB: "EU", BOE: "UK", BOJ: "JP",
    RBA: "AU", BOC: "CA", RBNZ: "NZ", SNB: "CH",
  };

  // Rate-path step vs smooth toggle state
  let _stepMode = true;
  let _lastRatePathData = null; // cached so the toggle can re-render without refetch

  /* ── Helpers ─────────────────────────────────────────────────────── */

  /** Dispose any existing ECharts instance on an element and init a new one. */
  function getOrInitChart(containerId) {
    const el = document.getElementById(containerId);
    if (!el || !window.echarts) return null;
    const existing = window.echarts.getInstanceByDom(el);
    if (existing) existing.dispose();
    return window.echarts.init(el, "dark");
  }

  /** "2026-06-11T14:15:00+02:00" → "Jun 11" */
  function formatShortDate(isoStr) {
    try {
      return new Date(isoStr).toLocaleDateString("en-US", { month: "short", day: "numeric" });
    } catch (_) { return isoStr; }
  }

  /** "2026-06-11T14:15:00+02:00" → "Jun 11, 2026" */
  function formatMeetingDate(isoStr) {
    try {
      return new Date(isoStr).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" });
    } catch (_) { return isoStr; }
  }

  /** Return [today-7d, today-21d, today-42d] as YYYY-MM-DD strings. */
  function snapshotDates() {
    return [7, 21, 42, 70].map(function (d) {
      const dt = new Date();
      dt.setDate(dt.getDate() - d);
      return dt.toISOString().slice(0, 10);
    });
  }

  /** Show a centered "no data" text inside an already-initialised ECharts instance. */
  function showNoData(chart, msg) {
    chart.setOption({
      backgroundColor: "transparent",
      graphic: [{
        type: "text", left: "center", top: "middle",
        style: { text: msg || "No data", fill: "#555", font: "12px 'JetBrains Mono', monospace" },
      }],
    }, true);
  }

  /* ── 1. Countdown timer ──────────────────────────────────────────── */

  function startCountdown(meetingIso) {
    const el = document.getElementById("countdown-display");
    if (!el || !meetingIso) return;

    const target = new Date(meetingIso).getTime();
    if (isNaN(target)) return;

    function tick() {
      const diff = target - Date.now();
      if (diff <= 0) { el.textContent = "DECISION DAY"; return; }

      const s = Math.floor(diff / 1000);
      el.textContent =
        Math.floor(s / 86400) + "d " +
        String(Math.floor((s % 86400) / 3600)).padStart(2, "0") + ":" +
        String(Math.floor((s % 3600) / 60)).padStart(2, "0") + ":" +
        String(s % 60).padStart(2, "0");
    }
    tick();
    setInterval(tick, 1000);
  }

  /* ── 2. Step dropdown — meeting table re-render ──────────────────── */

  function outcomeClass(outcome) {
    return { HIKE: "hike", HOLD: "hold", CUT: "cut" }[outcome] || "hold";
  }

  function buildTableRows(meetings, currentRate) {
    if (!meetings || !meetings.length) {
      return "<tr><td colspan='7' style='text-align:center;padding:24px;color:var(--muted)'>No data</td></tr>";
    }
    return meetings.map(function (m, idx) {
      const hasMarket = m.market_data_available !== false;
      const delta = parseFloat(m.delta_bps || 0);
      const cut = parseFloat(m.cut_prob || 0) * 100;
      const hold = parseFloat(m.hold_prob || 0) * 100;
      const hike = parseFloat(m.hike_prob || 0) * 100;
      const cumul    = m.cumulative_delta_bps != null ? parseFloat(m.cumulative_delta_bps) : 0;
      const adjCls = delta > 0.5 ? "rp-hike" : delta < -0.5 ? "rp-cut" : "";
      const dominant = hike >= hold && hike >= cut ? "Hike" : cut >= hold && cut >= hike ? "Cut" : "Hold";
      const moveCls = dominant === "Hike" ? "rp-hike" : dominant === "Cut" ? "rp-cut" : "rp-hold";
      const star     = m.is_official === false ? '<span class="rp-star" title="Projected">*</span>' : "";
      const altCls   = idx % 2 === 1 ? " rp-row-alt" : "";
      function probCell(value, cls) {
        return [
          '<td class="rp-prob-cell">',
          hasMarket ? '<div class="rp-prob-value">' + value.toFixed(1) + '</div>' +
            '<div class="rp-prob-track"><span class="rp-prob-fill ' + cls + '" style="width:' + Math.max(0, Math.min(100, value)).toFixed(1) + '%"></span></div>' : "&mdash;",
          "</td>",
        ].join("");
      }
      return [
        '<tr class="rp-row' + altCls + '">',
        '<td class="rp-meeting-date">' + formatMeetingDate(m.meeting_dt) + star + "</td>",
        '<td class="rp-mono">' + (hasMarket ? cumul.toFixed(1) : "&mdash;") + "</td>",
        '<td class="rp-mono ' + adjCls + '">' + (hasMarket ? (delta > 0 ? "+" : "") + delta.toFixed(1) : "&mdash;") + "</td>",
        '<td>' + (hasMarket ? '<span class="rp-move-pill ' + moveCls + '">' + dominant + '</span>' : '<span class="rp-muted-text">N/A</span>') + "</td>",
        probCell(cut, "rp-bar-cut"),
        probCell(hold, "rp-bar-hold"),
        probCell(hike, "rp-bar-hike"),
        "</tr>",
      ].join("");
    }).join("");
  }

  async function fetchAndRenderMeetings(bank, step, currentRate, snapshotMode) {
    const tbody = document.getElementById("rp-meetings-body");
    if (!tbody) return;
    const snapshotLabel = document.getElementById("rp-snapshot-label");
    tbody.style.opacity = "0.4";
    try {
      const res = await fetch("/api/rate-prob/meetings/" + encodeURIComponent(bank) +
                              "?step=" + encodeURIComponent(step) +
                              "&n=12&snapshot=" + encodeURIComponent(snapshotMode || "current"));
      if (!res.ok) throw new Error("HTTP " + res.status);
      const data = await res.json();
      tbody.innerHTML = buildTableRows(data.meetings || [], currentRate);
      if (snapshotLabel) {
        const label = data.snapshot_label || "Current";
        snapshotLabel.textContent = data.snapshot_date ? label + " · " + data.snapshot_date : label;
      }
    } catch (err) {
      tbody.innerHTML =
        '<tr><td colspan="7" style="color:var(--rp-cut);padding:16px 12px">Failed to load: ' + err.message + "</td></tr>";
      if (snapshotLabel) snapshotLabel.textContent = "Load failed";
    } finally {
      tbody.style.opacity = "1";
    }
  }

  /* ── 3a. Rate-path step line chart ───────────────────────────────── */

  function renderPathLegend(series) {
    const el = document.getElementById("rp-path-legend");
    if (!el) return;
    const available = new Set((series || []).map(function (s) { return s.label; }));
    el.innerHTML = PATH_LEGEND.map(function (item) {
      const muted = !available.has(item.label);
      return [
        '<span class="rp-path-legend-item',
        item.current ? " is-current" : "",
        muted ? " is-muted" : "",
        '" style="color:', item.color, '">',
        '<i class="rp-path-legend-dot" aria-hidden="true"></i>',
        '<span>', item.label, '</span>',
        '</span>',
      ].join("");
    }).join("");
  }

  function renderRatePathChart(containerId, data) {
    if (!data || !data.series || !data.series.length) return;
    _lastRatePathData = data; // cache for toggle
    renderPathLegend(data.series);

    const chart = getOrInitChart(containerId);
    if (!chart) return;

    if (!data.series[0].points.length) { showNoData(chart, "No forward-rate data"); return; }

    const currentRate = parseFloat(data.current_rate);
    const referenceRate = parseFloat(data.series[0].points[0].implied_rate);
    const xCats = ["Current"].concat(
      data.series[0].points.map(function (p) { return formatMeetingDate(p.meeting_dt); })
    );

    const series = data.series.map(function (s, i) {
      const cfg = PATH_COLORS[s.label] || PATH_COLORS.Current;
      const obj = {
        name: s.label,
        type: "line",
        showSymbol: true,
        symbolSize: cfg.symbolSize,
        data: [null].concat(s.points.map(function (p) { return parseFloat(p.implied_rate); })),
        lineStyle: { color: cfg.color, width: cfg.width, type: cfg.lineType },
        itemStyle: { color: cfg.color },
        emphasis: { focus: "series" },
      };
      if (_stepMode) { obj.step = "end"; } else { obj.smooth = true; }
      if (i === 0) {
        obj.markLine = {
          silent: true, symbol: "none",
          lineStyle: { color: "rgba(255,255,255,0.45)", type: "dashed", width: 1 },
          label: {
            formatter: "",
          },
          data: [{ yAxis: Number.isFinite(referenceRate) ? referenceRate : currentRate }],
        };
      }
      return obj;
    });

    chart.setOption({
      backgroundColor: "transparent",
      animationDuration: 280,
      color: PATH_LEGEND.map(function (item) { return item.color; }),
      grid: { left: 52, right: 22, top: 12, bottom: 68, containLabel: true },
      tooltip: Object.assign({}, CHART_TOOLTIP, {
        backgroundColor: "#1a1a1a",
        borderColor: "rgba(255,255,255,0.15)",
        formatter: function (params) {
          const rows = params
            .filter(function (p) { return !p.seriesName.includes("markLine") && p.value != null; })
            .map(function (p) {
              return '<span style="color:' + p.color + '">●</span> ' +
                     p.seriesName + ': <b>' + parseFloat(p.value).toFixed(4) + '%</b>';
            });
          return '<div style="font-family:\'JetBrains Mono\',monospace;font-size:11px">' +
                 (params[0] ? params[0].axisValue : "") + "<br>" + rows.join("<br>") + "</div>";
        },
      }),
      legend: {
        show: false,
      },
      xAxis: Object.assign({ type: "category", data: xCats }, CHART_AXIS, {
        axisTick: { show: false },
        axisLabel: Object.assign({}, CHART_AXIS.axisLabel, {
          color: "#AAB1BF",
          fontSize: 13,
          rotate: 38,
          margin: 18,
        }),
      }),
      yAxis: Object.assign({ type: "value", scale: true }, CHART_AXIS, {
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.07)" } },
        axisLabel: Object.assign({}, CHART_AXIS.axisLabel, {
          color: "#AAB1BF",
          fontSize: 13,
          formatter: function (v) { return parseFloat(v).toFixed(2) + "%"; },
        }),
      }),
      series: series,
    }, true);

    // Wire step/smooth toggle button
    const btn = document.getElementById("rp-step-toggle");
    if (btn) {
      btn.onclick = function () {
        _stepMode = btn.checked;
        if (_lastRatePathData) renderRatePathChart(containerId, _lastRatePathData);
      };
    }
  }

  /* ── 3b. Year-end bar chart (all banks) ──────────────────────────── */

  function renderYearEndBar(containerId, data) {
    const chart = getOrInitChart(containerId);
    if (!chart) return;
    if (!data || !data.banks || !data.banks.length) { showNoData(chart, "No bank data"); return; }

    const banks  = data.banks;
    const names  = banks.map(function (b) { return b.bank; });
    const values = banks.map(function (b) { return parseFloat(b.twelve_month_bps) || 0; });
    const colors = values.map(function (v) { return v >= 0 ? "#1D9E75" : "#D85A30"; });

    chart.setOption({
      backgroundColor: "transparent",
      animationDuration: 280,
      grid: { left: 44, right: 16, top: 20, bottom: 24, containLabel: true },
      tooltip: Object.assign({}, CHART_TOOLTIP, {
        trigger: "item",
        formatter: function (p) {
          const v = parseFloat(p.value);
          return '<span style="color:' + p.color + '">●</span> ' +
                 p.name + ': <b>' + (v > 0 ? "+" : "") + v.toFixed(1) + ' bps</b>';
        },
      }),
      xAxis: Object.assign({ type: "category", data: names }, CHART_AXIS),
      yAxis: Object.assign({ type: "value" }, CHART_AXIS, {
        axisLabel: Object.assign({}, CHART_AXIS.axisLabel, {
          formatter: function (v) { return (v > 0 ? "+" : "") + v + " bp"; },
        }),
      }),
      series: [{
        type: "bar",
        barMaxWidth: 38,
        label: {
          show: true,
          fontFamily: "'JetBrains Mono', monospace",
          fontSize: 10,
          formatter: function (p) {
            const v = parseFloat(p.value);
            return (v > 0 ? "+" : "") + v.toFixed(1);
          },
        },
        data: values.map(function (v, i) {
          return {
            value: v,
            itemStyle: { color: colors[i] },
            label: { position: v >= 0 ? "top" : "bottom", color: colors[i] },
          };
        }),
      }],
    }, true);
  }

  /* ── 3c. Policy rate history line chart ──────────────────────────── */

  async function renderPolicyHistoryChart(containerId, bank) {
    const chart = getOrInitChart(containerId);
    if (!chart) return;

    chart.showLoading({
      text: "Loading history…", textColor: "#666",
      maskColor: "rgba(0,0,0,0)", color: "#1D9E75",
    });

    const country = BANK_COUNTRY[bank] || "US";
    // Try both known canonical names for policy rate
    const candidates = ["policy_rate", "policy_interest_rate", "overnight_rate", "cash_rate"];

    let points = [];
    for (const name of candidates) {
      try {
        const res = await fetch("/api/country/" + encodeURIComponent(country) + "/indicator/" + name);
        if (!res.ok) continue;
        const data = await res.json();
        const raw = data.series || [];
        points = raw
          .filter(function (p) { return p.actual != null; })
          .map(function (p) { return [new Date(p.released_at).getTime(), parseFloat(p.actual)]; })
          .filter(function (p) { return !isNaN(p[0]) && !isNaN(p[1]); });
        if (points.length) break;
      } catch (_) { /* try next */ }
    }

    chart.hideLoading();

    if (!points.length) {
      showNoData(chart, bank + " policy rate history unavailable");
      return;
    }

    chart.setOption({
      backgroundColor: "transparent",
      animationDuration: 280,
      grid: CHART_GRID,
      tooltip: Object.assign({}, CHART_TOOLTIP, {
        formatter: function (params) {
          const p = params[0];
          if (!p) return "";
          const d = new Date(p.value[0]).toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric" });
          return '<span style="color:#1D9E75">●</span> ' + bank + ' Policy Rate<br>' +
                 d + ': <b>' + parseFloat(p.value[1]).toFixed(2) + '%</b>';
        },
      }),
      xAxis: Object.assign({ type: "time" }, CHART_AXIS),
      yAxis: Object.assign({ type: "value", scale: true }, CHART_AXIS, {
        axisLabel: Object.assign({}, CHART_AXIS.axisLabel, {
          formatter: function (v) { return parseFloat(v).toFixed(2) + "%"; },
        }),
      }),
      series: [{
        name: bank + " Policy Rate",
        type: "line",
        step: "end",
        showSymbol: false,
        data: points,
        lineStyle: { color: "#1D9E75", width: 2 },
        itemStyle: { color: "#1D9E75" },
        areaStyle: {
          color: {
            type: "linear", x: 0, y: 0, x2: 0, y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(29,158,117,0.18)" },
              { offset: 1, color: "rgba(29,158,117,0.01)" },
            ],
          },
        },
      }],
    }, true);
  }

  /* ── 4. Fetch all chart data and render ──────────────────────────── */

  async function fetchAndRenderAll(bank) {
    if (!bank) return;
    const snaps = snapshotDates().join(",");

    const [ratePath, allBanks] = await Promise.all([
      fetch("/api/rate-prob/rate-path/" + encodeURIComponent(bank) +
            "?snapshots=" + encodeURIComponent(snaps))
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; }),
      fetch("/api/rate-prob/all-banks")
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; }),
    ]);

    if (ratePath) renderRatePathChart("rate-path-chart", ratePath);
    if (allBanks) renderYearEndBar("year-end-bar", allBanks);
    // Policy history runs independently (async, no await needed)
    renderPolicyHistoryChart("policy-history-chart", bank);
  }

  /* ── Boot ────────────────────────────────────────────────────────── */

  document.addEventListener("DOMContentLoaded", function () {
    const meetingIso  = window._rpMeetingIso  || "";
    const bank        = window._rpBank        || "";
    const currentRate = window._rpCurrentRate || 0;

    startCountdown(meetingIso);

    const stepSelect = document.getElementById("rp-step-select");
    const comparisonSelect = document.getElementById("rp-comparison-select");
    function refreshMeetingsTable() {
      const step = stepSelect ? parseInt(stepSelect.value, 10) || 25 : 25;
      const snapshotMode = comparisonSelect ? comparisonSelect.value || "current" : "current";
      fetchAndRenderMeetings(bank, step, currentRate, snapshotMode);
    }
    if (stepSelect) {
      stepSelect.addEventListener("change", function () {
        refreshMeetingsTable();
      });
    }
    if (comparisonSelect) {
      comparisonSelect.addEventListener("change", refreshMeetingsTable);
    }

    fetchAndRenderAll(bank);

    window.addEventListener("resize", function () {
      ["rate-path-chart", "year-end-bar", "policy-history-chart"].forEach(function (id) {
        const el = document.getElementById(id);
        if (el && window.echarts) {
          const inst = window.echarts.getInstanceByDom(el);
          if (inst) inst.resize();
        }
      });
    });
  });

})();
