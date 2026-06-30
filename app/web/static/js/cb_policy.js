/* CB Policy Tracker — tone timeline chart, tab switching, upload, ingest,
   projection charts */

(function () {
  "use strict";

  // ── Bank colours & labels ─────────────────────────────────────────────────

  const BANK_COLORS = {
    FED:  "#ff8c42",
    ECB:  "#50b5ff",
    BOE:  "#23c483",
    BOJ:  "#f3ba63",
    RBA:  "#e05aff",
    BOC:  "#ff5a7e",
    SNB:  "#56cfad",
    RBNZ: "#a78bfa",
  };

  const BANK_LABELS = {
    FED:  "Fed (USD)",
    ECB:  "ECB (EUR)",
    BOE:  "BoE (GBP)",
    BOJ:  "BoJ (JPY)",
    RBA:  "RBA (AUD)",
    BOC:  "BoC (CAD)",
    SNB:  "SNB (CHF)",
    RBNZ: "RBNZ (NZD)",
  };

  // ── Tab switching ─────────────────────────────────────────────────────────

  window.cptSwitchTab = function (tabName) {
    const tabs = document.querySelectorAll(".cpt-tab-content");
    const btns = document.querySelectorAll(".cpt-tab-btn");
    tabs.forEach((t) => (t.style.display = "none"));
    btns.forEach((b) => b.classList.remove("active"));

    const target = document.getElementById("tab-" + tabName);
    if (target) target.style.display = "block";

    const btn = document.getElementById("tab-btn-" + tabName);
    if (btn) btn.classList.add("active");

    // Lazy-init projection charts when switching to that tab
    if (tabName === "projections" && !window._cptProjChartsInit) {
      window._cptProjChartsInit = true;
      const rawEl = document.getElementById("cpt-proj-data");
      if (rawEl && typeof Chart !== "undefined") {
        try {
          const data = JSON.parse(rawEl.textContent || "{}");
          initProjectionCharts(data);
        } catch (e) {
          console.warn("cpt: could not parse projection data", e);
        }
      }
    }
  };

  // ── Tone timeline chart ───────────────────────────────────────────────────

  function initChart() {
    const el = document.getElementById("cpt-tone-chart");
    if (!el) return;

    const rawEl = document.getElementById("cpt-data");
    if (!rawEl) return;

    let allData;
    try {
      allData = JSON.parse(rawEl.textContent || "{}");
    } catch (e) {
      return;
    }

    const datasets = [];
    for (const [bank, series] of Object.entries(allData)) {
      if (!series || series.length === 0) continue;
      const color = BANK_COLORS[bank] || "#888";
      datasets.push({
        label: BANK_LABELS[bank] || bank,
        data: series.map(([d, v]) => ({ x: d, y: v })),
        borderColor: color,
        backgroundColor: color + "22",
        pointBackgroundColor: color,
        pointRadius: 5,
        pointHoverRadius: 7,
        borderWidth: 2,
        tension: 0.3,
        fill: false,
      });
    }

    if (datasets.length === 0) return;

    new Chart(el, {
      type: "line",
      data: { datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        scales: {
          x: {
            type: "category",
            title: { display: false },
            ticks: {
              color: "#8fa5b5",
              font: { family: "'JetBrains Mono', monospace", size: 10 },
              maxTicksLimit: 12,
            },
            grid: { color: "rgba(255,255,255,0.04)" },
          },
          y: {
            title: {
              display: true,
              text: "← Dovish       Hawkish →",
              color: "#8fa5b5",
              font: { size: 10 },
            },
            min: -5,
            max: 5,
            ticks: {
              stepSize: 1,
              color: "#8fa5b5",
              font: { family: "'JetBrains Mono', monospace", size: 10 },
              callback: (v) => (v > 0 ? "+" + v : v),
            },
            grid: { color: "rgba(255,255,255,0.06)" },
          },
        },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "rgba(18,32,43,0.95)",
            borderColor: "#355063",
            borderWidth: 1,
            titleColor: "#dce8f2",
            bodyColor: "#8fa5b5",
            callbacks: {
              label: (ctx) => {
                const v = ctx.parsed.y;
                const sign = v >= 0 ? "+" : "";
                return ` ${ctx.dataset.label}: ${sign}${v.toFixed(1)}`;
              },
            },
          },
        },
      },
    });

    const legendEl = document.getElementById("cpt-chart-legend");
    if (legendEl) {
      legendEl.innerHTML = datasets
        .map(
          (ds) =>
            `<span class="cpt-legend-item">
              <span class="cpt-legend-dot" style="background:${ds.borderColor}"></span>
              ${ds.label}
            </span>`
        )
        .join("");
    }
  }

  // ── Projection charts ─────────────────────────────────────────────────────
  // data = { FED: { as_of: "2026-06-17", path: [{year,inflation,gdp,unemployment},...] }, ... }
  // x-axis = horizon year, y-axis = forecast %, 3 lines per bank.

  window.initProjectionCharts = function (data) {
    for (const [bank, payload] of Object.entries(data)) {
      const path = payload && payload.path;
      if (!path || path.length === 0) continue;
      const el = document.getElementById("cpt-proj-" + bank + "-chart");
      if (!el) continue;

      const labels = path.map((p) => String(p.year));

      const datasets = [];

      const inflPts = path.map((p) => p.inflation);
      if (inflPts.some((v) => v != null)) {
        datasets.push({
          label: "Inflation",
          data: inflPts,
          borderColor: "#ff8c42",
          backgroundColor: "#ff8c4244",
          pointBackgroundColor: "#ff8c42",
          pointRadius: 4,
          pointHoverRadius: 6,
          borderWidth: 2,
          tension: 0.2,
          fill: false,
          spanGaps: true,
        });
      }

      const gdpPts = path.map((p) => p.gdp);
      if (gdpPts.some((v) => v != null)) {
        datasets.push({
          label: "GDP",
          data: gdpPts,
          borderColor: "#23c483",
          backgroundColor: "#23c48344",
          pointBackgroundColor: "#23c483",
          pointRadius: 4,
          pointHoverRadius: 6,
          borderWidth: 2,
          tension: 0.2,
          fill: false,
          spanGaps: true,
        });
      }

      const unempPts = path.map((p) => p.unemployment);
      if (unempPts.some((v) => v != null)) {
        datasets.push({
          label: "Unemployment",
          data: unempPts,
          borderColor: "#50b5ff",
          backgroundColor: "#50b5ff44",
          pointBackgroundColor: "#50b5ff",
          pointRadius: 4,
          pointHoverRadius: 6,
          borderWidth: 2,
          tension: 0.2,
          fill: false,
          spanGaps: true,
        });
      }

      if (!datasets.length) continue;

      new Chart(el, {
        type: "line",
        data: { labels, datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          interaction: { mode: "index", intersect: false },
          scales: {
            x: {
              type: "category",
              ticks: {
                color: "#8fa5b5",
                font: { size: 10 },
              },
              grid: { color: "rgba(255,255,255,0.04)" },
            },
            y: {
              ticks: {
                color: "#8fa5b5",
                font: { size: 9 },
                callback: (v) => v.toFixed(1) + "%",
              },
              grid: { color: "rgba(255,255,255,0.06)" },
            },
          },
          plugins: {
            legend: {
              display: true,
              labels: {
                color: "#8fa5b5",
                font: { size: 9 },
                boxWidth: 8,
                padding: 8,
              },
            },
            tooltip: {
              backgroundColor: "rgba(18,32,43,0.95)",
              borderColor: "#355063",
              borderWidth: 1,
              titleColor: "#dce8f2",
              bodyColor: "#8fa5b5",
              callbacks: {
                title: (items) => "Horizon: " + (items[0] && items[0].label),
                label: (ctx) => {
                  const v = ctx.parsed.y;
                  return v != null ? ` ${ctx.dataset.label}: ${v.toFixed(1)}%` : "";
                },
              },
            },
          },
        },
      });
    }
  };

  // ── Policy Divergence Comparison ─────────────────────────────────────────

  const TONE_COLORS = {
    hawkish: "#ff8c42", "very hawkish": "#ff5a1f",
    dovish: "#50b5ff", "very dovish": "#2196f3",
    neutral: "#8fa5b5",
  };

  const DIV_BADGE = {
    significant: { cls: "cpt-div-significant", label: "Significant" },
    moderate:    { cls: "cpt-div-moderate",    label: "Moderate"    },
    mild:        { cls: "cpt-div-mild",        label: "Mild"        },
  };

  const SIG_DOT = { high: "#ff8c42", medium: "#f3ba63", low: "#50b5ff" };

  function outlookIcon(o) {
    if (!o) return "";
    const l = o.toLowerCase();
    if (l.includes("hawk")) return '<span class="cpt-outlook hawk">↑</span>';
    if (l.includes("dov"))  return '<span class="cpt-outlook dove">↓</span>';
    return '<span class="cpt-outlook neutral">→</span>';
  }

  function bankCard(doc) {
    const tc = TONE_COLORS[doc.tone_label] || "#8fa5b5";
    return `
      <div class="cpt-cmp-card">
        <div class="cpt-cmp-card-header">
          <span class="cpt-cmp-bank">${doc.bank}</span>
          <span class="cpt-cmp-ccy">${doc.currency}</span>
          <span class="cpt-cmp-date">${doc.doc_date}</span>
        </div>
        <div class="cpt-cmp-tone" style="color:${tc}">
          ${doc.tone_label.toUpperCase()}
          <span class="cpt-cmp-score">${doc.tone_score >= 0 ? "+" : ""}${doc.tone_score.toFixed(1)}</span>
        </div>
        <div class="cpt-cmp-outlooks">
          <div class="cpt-cmp-outlook-row">${outlookIcon(doc.inflation_outlook)}<span>Inflation: ${doc.inflation_outlook}</span></div>
          <div class="cpt-cmp-outlook-row">${outlookIcon(doc.growth_outlook)}<span>Growth: ${doc.growth_outlook}</span></div>
          <div class="cpt-cmp-outlook-row">${outlookIcon(doc.labor_outlook)}<span>Labor: ${doc.labor_outlook}</span></div>
        </div>
        ${doc.tone_change_vs_prior ? `<div class="cpt-cmp-shift">Shift: ${doc.tone_change_vs_prior}</div>` : ""}
      </div>`;
  }

  function renderComparison(data) {
    const a = data.bank_a;
    const b = data.bank_b;
    const an = data.analysis;
    const badge = DIV_BADGE[an.overall_divergence] || DIV_BADGE.mild;

    const divergenceRows = (an.key_divergences || []).map((d) => {
      const dot = `<span class="cpt-div-dot" style="background:${SIG_DOT[d.significance] || '#8fa5b5'}"></span>`;
      return `
        <tr>
          <td class="cpt-div-topic">${dot}${d.topic}</td>
          <td>${d.bank_a}</td>
          <td>${d.bank_b}</td>
        </tr>`;
    }).join("");

    const implications = (an.trading_implications || [])
      .map((t) => `<li>${t}</li>`).join("");

    const risks = (an.risk_factors || [])
      .map((r) => `<li>${r}</li>`).join("");

    return `
      <div class="cpt-cmp-result">
        <div class="cpt-cmp-header-row">
          <span class="cpt-div-badge ${badge.cls}">${badge.label} Divergence</span>
          <span class="cpt-cmp-direction">${an.divergence_direction || ""}</span>
          <span class="cpt-cmp-pair">${an.fx_pair || ""}</span>
        </div>

        <div class="cpt-cmp-cards-row">
          ${bankCard(a)}
          <div class="cpt-cmp-arrow">⇄</div>
          ${bankCard(b)}
        </div>

        <div class="cpt-cmp-summary">${an.summary || ""}</div>

        <div class="cpt-cmp-section-title">Key Divergences</div>
        <div class="cpt-table-wrap">
          <table class="cpt-div-table">
            <thead><tr><th>Topic</th><th>${a.bank} (${a.currency})</th><th>${b.bank} (${b.currency})</th></tr></thead>
            <tbody>${divergenceRows}</tbody>
          </table>
        </div>

        <div class="cpt-cmp-two-col">
          <div>
            <div class="cpt-cmp-section-title">FX Bias — ${an.fx_pair || ""}</div>
            <div class="cpt-cmp-fx-bias">${an.fx_bias || "—"}</div>
            <ul class="cpt-cmp-bullets">${implications}</ul>
          </div>
          <div>
            <div class="cpt-cmp-section-title">Risk Factors</div>
            <ul class="cpt-cmp-bullets cpt-cmp-risks">${risks}</ul>
          </div>
        </div>
      </div>`;
  }

  window.cptRunComparison = function () {
    const bankA = document.getElementById("cpt-compare-bank-a").value;
    const bankB = document.getElementById("cpt-compare-bank-b").value;
    const btn = document.getElementById("cpt-compare-btn");
    const result = document.getElementById("cpt-compare-result");

    if (bankA === bankB) {
      result.style.display = "block";
      result.innerHTML = '<div class="cpt-cmp-error">Select two different banks.</div>';
      return;
    }

    btn.disabled = true;
    btn.textContent = "Analyzing…";
    result.style.display = "block";
    result.innerHTML = '<div class="cpt-cmp-loading"><span class="cpt-spinner"></span> Running AI analysis — this takes ~10 seconds…</div>';

    fetch(`/api/cb/compare?bank_a=${bankA}&bank_b=${bankB}`, { method: "POST" })
      .then((r) => r.json().then((d) => ({ ok: r.ok, data: d })))
      .then(({ ok, data }) => {
        if (!ok) throw new Error(data.detail || "Comparison failed");
        result.innerHTML = renderComparison(data);
      })
      .catch((err) => {
        result.innerHTML = `<div class="cpt-cmp-error">Error: ${err.message}</div>`;
      })
      .finally(() => {
        btn.disabled = false;
        btn.textContent = "Analyze Divergence";
      });
  };

  // ── Expand / collapse bank detail ─────────────────────────────────────────

  window.cptToggleDetail = function (bankCode) {
    const detail = document.getElementById("detail-" + bankCode);
    const btn = document.getElementById("expand-btn-" + bankCode);
    if (!detail || !btn) return;
    const isOpen = detail.style.display !== "none";
    detail.style.display = isOpen ? "none" : "block";
    btn.textContent = isOpen ? "History ▼" : "History ▲";
  };

  // ── Upload document ───────────────────────────────────────────────────────

  window.cptUpload = function (event) {
    event.preventDefault();
    const form = document.getElementById("cpt-upload-form");
    const btn = document.getElementById("cpt-upload-btn");
    const result = document.getElementById("cpt-upload-result");
    if (!form || !btn || !result) return;

    btn.disabled = true;
    btn.textContent = "Uploading…";
    result.style.display = "none";
    result.className = "cpt-upload-result";

    const formData = new FormData(form);

    fetch("/api/cb/documents/upload", { method: "POST", body: formData })
      .then((r) => r.json().then((d) => ({ ok: r.ok, data: d })))
      .then(({ ok, data }) => {
        if (!ok) {
          throw new Error(data.detail || "Upload failed");
        }
        result.className = "cpt-upload-result cpt-result-ok";
        const score = data.tone_score != null ? data.tone_score.toFixed(2) : "N/A";
        result.innerHTML = `
          <strong>Analyzed successfully.</strong><br>
          Bank: ${data.bank} &nbsp;|&nbsp; Date: ${data.doc_date} &nbsp;|&nbsp; Type: ${data.doc_type}<br>
          Tone score: <strong>${score}</strong> &nbsp;|&nbsp; Label: <strong>${data.tone_label || "—"}</strong><br>
          <a href="#" onclick="window.location.reload(); return false;" style="color:#ff8c42">Reload page to see updated charts</a>
        `;
        result.style.display = "block";
        btn.textContent = "Upload & Analyze";
        btn.disabled = false;
      })
      .catch((err) => {
        result.className = "cpt-upload-result cpt-result-err";
        result.textContent = "Error: " + err.message;
        result.style.display = "block";
        btn.textContent = "Upload & Analyze";
        btn.disabled = false;
      });
  };

  // ── Ingest from disk ──────────────────────────────────────────────────────

  window.cptIngest = function () {
    const result = document.getElementById("cpt-ingest-result");
    const reanalyzeEl = document.getElementById("ingest-reanalyze");
    if (!result) return;

    const reanalyze = reanalyzeEl ? reanalyzeEl.checked : false;
    const url = "/api/cb/documents/ingest" + (reanalyze ? "?reanalyze=true" : "");

    result.style.display = "block";
    result.className = "cpt-ingest-result cpt-ingest-running";
    result.textContent = "Scanning data/policy/ and ingesting PDFs… this may take several minutes.";

    fetch(url, { method: "POST" })
      .then((r) => r.json().then((d) => ({ ok: r.ok, data: d })))
      .then(({ ok, data }) => {
        if (!ok) {
          throw new Error(data.detail || "Ingest failed");
        }
        result.className = "cpt-ingest-result cpt-ingest-ok";
        result.innerHTML = `
          Ingest complete.<br>
          Scanned: <strong>${data.scanned}</strong> &nbsp;|&nbsp;
          New/updated: <strong>${data.new}</strong> &nbsp;|&nbsp;
          Analyzed: <strong>${data.analyzed}</strong> &nbsp;|&nbsp;
          Projections: <strong>${data.projections}</strong> &nbsp;|&nbsp;
          Errors: <strong>${data.errors}</strong>
          <br><a href="#" onclick="window.location.reload(); return false;" style="color:#ff8c42">Reload page</a>
        `;
      })
      .catch((err) => {
        result.className = "cpt-ingest-result cpt-ingest-err";
        result.textContent = "Error: " + err.message;
      });
  };

  // ── Init ──────────────────────────────────────────────────────────────────

  document.addEventListener("DOMContentLoaded", function () {
    if (typeof Chart !== "undefined") {
      initChart();
    } else {
      const scripts = document.querySelectorAll("script[src*='chart']");
      scripts.forEach((s) =>
        s.addEventListener("load", initChart, { once: true })
      );
      setTimeout(initChart, 800);
    }
  });
})();
