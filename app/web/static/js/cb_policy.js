/* CB Policy Tracker — tone timeline chart + refresh + expand/collapse */

(function () {
  "use strict";

  // ── Tone timeline chart ───────────────────────────────────────────────────

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

    const chart = new Chart(el, {
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

    // Build legend
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

  // ── Expand / collapse bank detail ─────────────────────────────────────────

  window.cptToggleDetail = function (bankCode) {
    const detail = document.getElementById("detail-" + bankCode);
    const btn = document.getElementById("expand-btn-" + bankCode);
    if (!detail || !btn) return;
    const isOpen = detail.style.display !== "none";
    detail.style.display = isOpen ? "none" : "block";
    btn.textContent = isOpen ? "History ▼" : "History ▲";
  };

  // ── Refresh analysis ──────────────────────────────────────────────────────

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
          msg.textContent =
            `Done — scraped ${scraped} statements, analyzed ${analyzed}. Reloading…`;
        }
        setTimeout(() => window.location.reload(), 1800);
      })
      .catch((err) => {
        if (msg) msg.textContent = "Error: " + err.message;
        if (btn) btn.disabled = false;
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
      // Fallback
      setTimeout(initChart, 800);
    }
  });
})();
