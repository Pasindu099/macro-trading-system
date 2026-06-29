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

  window.initProjectionCharts = function (data) {
    for (const [bank, projList] of Object.entries(data)) {
      if (!projList || projList.length === 0) continue;
      const el = document.getElementById("cpt-proj-" + bank + "-chart");
      if (!el) continue;

      const color = BANK_COLORS[bank] || "#888";

      // Group unique projection dates (x axis)
      const allDates = [...new Set(projList.map((p) => p.projection_date))].sort();

      // Build per-metric datasets
      const inflData = allDates.map((d) => {
        const rows = projList.filter((p) => p.projection_date === d && p.inflation_forecast != null);
        // Take the first non-null value for this date
        return rows.length ? { x: d, y: rows[0].inflation_forecast } : null;
      }).filter(Boolean);

      const gdpData = allDates.map((d) => {
        const rows = projList.filter((p) => p.projection_date === d && p.gdp_forecast != null);
        return rows.length ? { x: d, y: rows[0].gdp_forecast } : null;
      }).filter(Boolean);

      const unempData = allDates.map((d) => {
        const rows = projList.filter((p) => p.projection_date === d && p.unemployment_forecast != null);
        return rows.length ? { x: d, y: rows[0].unemployment_forecast } : null;
      }).filter(Boolean);

      const datasets = [];
      if (inflData.length) {
        datasets.push({
          label: "Inflation",
          data: inflData,
          borderColor: "#ff8c42",
          backgroundColor: "#ff8c4222",
          pointRadius: 4,
          borderWidth: 2,
          tension: 0.3,
          fill: false,
        });
      }
      if (gdpData.length) {
        datasets.push({
          label: "GDP",
          data: gdpData,
          borderColor: "#23c483",
          backgroundColor: "#23c48322",
          pointRadius: 4,
          borderWidth: 2,
          tension: 0.3,
          fill: false,
        });
      }
      if (unempData.length) {
        datasets.push({
          label: "Unemployment",
          data: unempData,
          borderColor: "#50b5ff",
          backgroundColor: "#50b5ff22",
          pointRadius: 4,
          borderWidth: 2,
          tension: 0.3,
          fill: false,
        });
      }

      if (!datasets.length) continue;

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
              ticks: {
                color: "#8fa5b5",
                font: { size: 9 },
                maxTicksLimit: 8,
              },
              grid: { color: "rgba(255,255,255,0.04)" },
            },
            y: {
              ticks: {
                color: "#8fa5b5",
                font: { size: 9 },
                callback: (v) => v + "%",
              },
              grid: { color: "rgba(255,255,255,0.06)" },
            },
          },
          plugins: {
            legend: {
              display: true,
              labels: {
                color: "#8fa5b5",
                font: { size: 10 },
                boxWidth: 10,
              },
            },
            tooltip: {
              backgroundColor: "rgba(18,32,43,0.95)",
              borderColor: "#355063",
              borderWidth: 1,
              titleColor: "#dce8f2",
              bodyColor: "#8fa5b5",
              callbacks: {
                label: (ctx) => ` ${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}%`,
              },
            },
          },
        },
      });
    }
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

  // ── Refresh analysis (web scraper) ────────────────────────────────────────

  let _refreshPoll = null;

  window.cptRefresh = function () {
    const btn = document.getElementById("cpt-refresh-btn");
    const banner = document.getElementById("cpt-refresh-banner");
    const msg = document.getElementById("cpt-refresh-msg");
    if (btn) btn.disabled = true;
    if (banner) banner.style.display = "flex";

    fetch("/api/cb/policy-reports/refresh", { method: "POST" })
      .then((r) => {
        if (!r.ok) {
          return r.json().then((d) => {
            throw new Error(d.detail || "Refresh failed");
          });
        }
        return r.json();
      })
      .then((data) => {
        const analyzed = data.analyzed || 0;
        const scraped = data.scraped || 0;
        if (msg) {
          msg.textContent = `Done — scraped ${scraped} statements, analyzed ${analyzed}. Reloading…`;
        }
        setTimeout(() => window.location.reload(), 1800);
      })
      .catch((err) => {
        if (msg) msg.textContent = "Error: " + err.message;
        if (btn) btn.disabled = false;
      });
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
    // Chart.js may load after DOMContentLoaded if deferred — wait a tick
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
