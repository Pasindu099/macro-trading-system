(() => {
    window.__macroComponents = window.__macroComponents || {};
    window.__macroComponents.cot = "booting";
    const pairs = [
        { key: "EUR", label: "EUR", pair: "EUR/USD" },
        { key: "GBP", label: "GBP", pair: "GBP/USD" },
        { key: "JPY", label: "JPY", pair: "USD/JPY" },
        { key: "CAD", label: "CAD", pair: "USD/CAD" },
        { key: "CHF", label: "CHF", pair: "USD/CHF" },
        { key: "AUD", label: "AUD", pair: "AUD/USD" },
        { key: "NZD", label: "NZD", pair: "NZD/USD" },
        { key: "MXN", label: "MXN", pair: "USD/MXN" },
    ];
    const section = document.querySelector("[data-cot-section]");
    if (!section) return;

    let active = "EUR";
    let rows = [];
    const charts = {};

    const css = getComputedStyle(document.documentElement);
    const text = css.getPropertyValue("--bt-text").trim() || "#e8e8e8";
    const muted = css.getPropertyValue("--bt-muted").trim() || "#888888";
    const green = css.getPropertyValue("--bt-green").trim() || "#22c55e";
    const red = css.getPropertyValue("--bt-red").trim() || "#ef4444";
    const amber = css.getPropertyValue("--bt-amber").trim() || "#f59e0b";
    const blue = css.getPropertyValue("--bt-blue").trim() || "#3b82f6";

    const fmt = (n) => `${Number(n || 0) >= 0 ? "+" : ""}${Number(n || 0).toLocaleString()}k`;
    const raw = (row, key) => Number(row?.[key] || 0);
    const cls = (value) => Number(value || 0) > 0 ? "is-positive" : Number(value || 0) < 0 ? "is-negative" : "is-neutral";
    const esc = (value) => String(value || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;").replaceAll('"', "&quot;");

    const mount = () => {
        section.innerHTML = `
            <header class="black-panel__head">
                <span>COT live dashboard</span>
                <em data-cot-status>Fetching CFTC data...</em>
            </header>
            <div class="terminal-tabs" data-cot-tabs>
                <button class="is-active" type="button" data-cot-tab="overview">Overview</button>
                <button type="button" data-cot-tab="history">History</button>
                <button type="button" data-cot-tab="groups">3-group breakdown</button>
                <button type="button" data-cot-tab="signals">Signals</button>
            </div>
            <div class="cot-error" data-cot-error hidden><span>Unable to load CFTC positioning.</span><button type="button" data-cot-retry>Retry</button></div>
            <div class="cot-metric-grid" data-cot-metrics></div>
            <div class="cot-tab-panel is-active" data-cot-panel="overview">
                <article class="cot-card"><header><span>Net speculative positions</span><em>Non-commercial futures</em></header><div class="cot-chart-wrap"><canvas data-cot-net-chart></canvas></div></article>
                <article class="cot-card"><header><span>Commercials vs non-commercials</span><em>Divergence map</em></header><div class="cot-chart-wrap"><canvas data-cot-divergence-chart></canvas></div></article>
            </div>
            <div class="cot-tab-panel" data-cot-panel="history">
                <div class="cot-pair-selector" data-cot-pairs>${pairs.map((p) => `<button class="${p.key === active ? "is-active" : ""}" type="button" data-pair="${p.key}">${p.pair}</button>`).join("")}</div>
                <div class="cot-chart-grid">
                    <article class="cot-card"><header><span data-cot-history-title>EUR - 16-week positioning history</span><em>All trader groups</em></header><div class="cot-chart-wrap"><canvas data-cot-history-chart></canvas></div></article>
                    <article class="cot-card"><header><span>Gross long/short positions</span><em data-cot-gross-label>Current week</em></header><div class="cot-chart-wrap"><canvas data-cot-gross-chart></canvas></div></article>
                </div>
            </div>
            <div class="cot-tab-panel" data-cot-panel="groups">
                <div class="cot-chart-grid cot-chart-grid--three">
                    <article class="cot-card"><header><span>Non-commercial net</span><em>Large speculators</em></header><div class="cot-chart-wrap"><canvas data-cot-nc-chart></canvas></div></article>
                    <article class="cot-card"><header><span>Commercial net</span><em>Hedgers</em></header><div class="cot-chart-wrap"><canvas data-cot-cm-chart></canvas></div></article>
                    <article class="cot-card"><header><span>Non-reportable net</span><em>Small traders</em></header><div class="cot-chart-wrap"><canvas data-cot-nr-chart></canvas></div></article>
                </div>
            </div>
            <div class="cot-tab-panel" data-cot-panel="signals">
                <article class="cot-card cot-card--breakdown"><header><span>COT signal table</span><em>Momentum, extremes, divergence</em></header>
                    <div class="black-table-wrap"><table class="black-fx-table cot-signal-table"><thead><tr><th>Pair</th><th>NC net</th><th>WoW</th><th>Comm net</th><th>Retail net</th><th>Divergence</th><th>Signal</th></tr></thead><tbody data-cot-signals></tbody></table></div>
                </article>
            </div>`;
    };

    const options = () => ({
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
            legend: { labels: { color: muted, boxWidth: 10 } },
            tooltip: { backgroundColor: "#0a0a0a", borderColor: "#2a2a2a", borderWidth: 1, titleColor: text, bodyColor: text },
        },
        scales: {
            x: { ticks: { color: muted }, grid: { color: "rgba(255,255,255,.06)" } },
            y: { ticks: { color: muted, callback: (v) => `${v}k` }, grid: { color: "rgba(255,255,255,.06)" } },
        },
    });

    const draw = (name, canvas, config) => {
        if (!window.Chart || !canvas) return;
        if (charts[name]) charts[name].destroy();
        charts[name] = new Chart(canvas, config);
    };

    const renderMetrics = () => {
        const totalNet = rows.reduce((sum, row) => sum + raw(row, "net_position"), 0);
        const strongest = [...rows].sort((a, b) => raw(b, "net_position") - raw(a, "net_position"))[0];
        const weakest = [...rows].sort((a, b) => raw(a, "net_position") - raw(b, "net_position"))[0];
        const crowded = [...rows].sort((a, b) => Math.abs(Number(b.signal?.divergence || 0)) - Math.abs(Number(a.signal?.divergence || 0)))[0] || strongest;
        section.querySelector("[data-cot-metrics]").innerHTML = `
            <article class="black-kpi-card"><span>Tracked markets</span><strong>${rows.length}</strong><small>CFTC legacy futures</small></article>
            <article class="black-kpi-card"><span>Strongest spec net</span><strong class="${cls(raw(strongest, "net_position"))}">${esc(strongest?.label)}</strong><small>${fmt(raw(strongest, "net_position"))}</small></article>
            <article class="black-kpi-card"><span>Weakest spec net</span><strong class="${cls(raw(weakest, "net_position"))}">${esc(weakest?.label)}</strong><small>${fmt(raw(weakest, "net_position"))}</small></article>
            <article class="black-kpi-card"><span>Aggregate net</span><strong class="${cls(totalNet)}">${fmt(totalNet)}</strong><small>Non-commercial total</small></article>`;
        if (crowded) section.querySelector("[data-cot-status]").textContent = `${crowded.label} ${crowded.signal?.tone || "updated"}`;
    };

    const renderOverview = () => {
        const labels = rows.map((r) => r.label);
        draw("net", section.querySelector("[data-cot-net-chart]"), {
            type: "bar",
            data: { labels, datasets: [
                { type: "bar", label: "NC net", data: rows.map((r) => raw(r, "net_position")), backgroundColor: rows.map((r) => raw(r, "net_position") >= 0 ? green : red) },
                { type: "line", label: "WoW delta", data: rows.map((r) => raw(r, "chg_net")), borderColor: blue, pointRadius: 3, tension: 0.25 },
            ] },
            options: options(),
        });
        draw("div", section.querySelector("[data-cot-divergence-chart]"), {
            type: "bar",
            data: { labels, datasets: [
                { label: "Non-commercial", data: rows.map((r) => raw(r, "net_position")), backgroundColor: blue },
                { label: "Commercial", data: rows.map((r) => raw(r, "commercial_net_current")), backgroundColor: red },
            ] },
            options: options(),
        });
    };

    const renderHistory = () => {
        const row = rows.find((item) => item.pair === active) || rows[0];
        if (!row) return;
        section.querySelector("[data-cot-history-title]").textContent = `${row.label} - 16-week positioning history`;
        section.querySelector("[data-cot-gross-label]").textContent = row.pair_label || row.label;
        draw("hist", section.querySelector("[data-cot-history-chart]"), {
            type: "line",
            data: { labels: row.weeks, datasets: [
                { label: "Non-commercial", data: row.net, borderColor: blue, tension: 0.3 },
                { label: "Commercial", data: row.commercial_net, borderColor: red, borderDash: [5, 4], tension: 0.3 },
                { label: "Non-reportable", data: row.nonreportable_net, borderColor: amber, tension: 0.3 },
            ] },
            options: options(),
        });
        draw("gross", section.querySelector("[data-cot-gross-chart]"), {
            type: "bar",
            data: { labels: ["Non-commercial", "Commercial", "Non-reportable"], datasets: [
                { label: "Longs", data: [row.noncom_long, row.com_long, row.nonrept_long], backgroundColor: green },
                { label: "Shorts", data: [row.noncom_short, row.com_short, row.nonrept_short], backgroundColor: red },
            ] },
            options: options(),
        });
    };

    const renderGroups = () => {
        const mk = (name, selector, key) => draw(name, section.querySelector(selector), {
            type: "bar",
            data: { labels: rows.map((r) => r.label), datasets: [{ label: key, data: rows.map((r) => raw(r, key)), backgroundColor: rows.map((r) => raw(r, key) >= 0 ? green : red) }] },
            options: options(),
        });
        mk("nc", "[data-cot-nc-chart]", "net_position");
        mk("cm", "[data-cot-cm-chart]", "commercial_net_current");
        mk("nr", "[data-cot-nr-chart]", "nonreportable_net_current");
    };

    const renderSignals = () => {
        section.querySelector("[data-cot-signals]").innerHTML = rows.map((row) => {
            const signal = row.signal || {};
            const sCls = signal.bias === "Bullish" ? "is-positive" : signal.bias === "Bearish" ? "is-negative" : "is-neutral";
            return `<tr><td>${esc(row.pair_label)}</td><td class="${cls(row.net_position)}">${fmt(row.net_position)}</td><td class="${cls(row.chg_net)}">${fmt(row.chg_net)}</td><td class="${cls(row.commercial_net_current)}">${fmt(row.commercial_net_current)}</td><td class="${cls(row.nonreportable_net_current)}">${fmt(row.nonreportable_net_current)}</td><td>${fmt(signal.divergence)}</td><td><span class="${sCls}">${esc(signal.bias || "Neutral")}</span><small>${esc(signal.tone || "")}</small></td></tr>`;
        }).join("");
    };

    const renderAll = () => {
        renderMetrics();
        renderOverview();
        renderSignals();
    };

    const renderPanel = (key) => {
        if (key === "overview") renderOverview();
        if (key === "history") renderHistory();
        if (key === "groups") renderGroups();
        if (key === "signals") renderSignals();
        Object.values(charts).forEach((chart) => chart?.resize?.());
    };

    const load = async () => {
        section.querySelector("[data-cot-error]").hidden = true;
        section.querySelector("[data-cot-status]").textContent = "Fetching CFTC data...";
        try {
            const settled = await Promise.allSettled(pairs.map((p) => fetch(`/api/cot?pair=${p.key}`, { cache: "no-store" }).then((r) => {
                if (!r.ok) throw new Error(`${p.label} failed`);
                return r.json();
            })));
            rows = settled.filter((r) => r.status === "fulfilled").map((r) => r.value);
            if (!rows.length) throw new Error("CFTC COT data is unavailable.");
            section.querySelector("[data-cot-status]").textContent = "CFTC legacy futures";
            renderAll();
        } catch (error) {
            section.querySelector("[data-cot-status]").textContent = "feed error";
            const err = section.querySelector("[data-cot-error]");
            err.hidden = false;
            err.querySelector("span").textContent = error.message || "Unable to load CFTC positioning.";
        }
    };

    mount();
    section.addEventListener("click", (event) => {
        const tab = event.target.closest("[data-cot-tab]");
        if (tab) {
            const key = tab.dataset.cotTab;
            section.querySelectorAll("[data-cot-tab]").forEach((btn) => btn.classList.toggle("is-active", btn === tab));
            section.querySelectorAll("[data-cot-panel]").forEach((panel) => panel.classList.toggle("is-active", panel.dataset.cotPanel === key));
            renderPanel(key);
        }
        const pair = event.target.closest("[data-pair]");
        if (pair) {
            active = pair.dataset.pair || "EUR";
            section.querySelectorAll("[data-pair]").forEach((btn) => btn.classList.toggle("is-active", btn.dataset.pair === active));
            renderHistory();
        }
        if (event.target.closest("[data-cot-retry]")) load();
    });
    load();
    window.__macroComponents.cot = "loaded";
})();
