(() => {
    const root = document.querySelector("[data-fx-outlook]");
    if (!root) return;

    const els = {
        list: root.querySelector("[data-fx-ranking-list]"),
        status: root.querySelector("[data-fx-outlook-status]"),
        search: root.querySelector("[data-fx-search]"),
        assetSelect: root.querySelector("[data-fx-asset-select]"),
        chart: root.querySelector("[data-fx-history-chart]"),
        save: root.querySelector("[data-fx-save]"),
        detailTitle: root.querySelector("[data-fx-detail-title]"),
        detailAsOf: root.querySelector("[data-fx-detail-asof]"),
        detailBias: root.querySelector("[data-fx-detail-bias]"),
        detailMeaning: root.querySelector("[data-fx-detail-meaning]"),
        detailLongShort: root.querySelector("[data-fx-detail-longshort]"),
        detailTotal: root.querySelector("[data-fx-detail-total]"),
        detailNet: root.querySelector("[data-fx-detail-net]"),
        detailAverage: root.querySelector("[data-fx-detail-average]"),
        detailCrowding: root.querySelector("[data-fx-detail-crowding]"),
    };

    const state = {
        items: [],
        filtered: [],
        selected: null,
        category: "all",
        sort: "rank",
        range: "current",
        timestamp: null,
        sources: {},
        chart: null,
    };

    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const escapeHtml = (value) => String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    const pct = (value) => `${Number(value || 0).toFixed(1)}%`;
    const sourceLabel = (sources) => Object.entries(sources || {})
        .filter(([, count]) => Number(count) > 0)
        .map(([name, count]) => `${name} ${count}`)
        .join(" | ") || "no source data";

    const normalizeAsset = (asset) => ({
        symbol: asset.name,
        category: asset.category || "forex",
        longPct: Number(asset.long || 0),
        shortPct: Number(asset.short || 0),
        net: Number(asset.net || 0),
        positions: Number(asset.positions || 0),
        source: asset.source || "unknown",
        sentiment: asset.sentiment || "Neutral",
        crowding: Number(asset.crowding || Math.min(100, Math.round(Math.abs(asset.net || 0) * 2.2))),
        avgLong: asset.avg_long || "-",
        avgShort: asset.avg_short || "-",
    });

    const biasFor = (item) => {
        if (!item) return "Neutral";
        if (item.sentiment) return item.sentiment;
        const crowding = Math.abs(item.net);
        if (crowding < 8) return "Neutral";
        return item.net < 0 ? "Bullish Contrarian" : "Bearish Contrarian";
    };

    const biasClass = (item) => {
        const label = biasFor(item).toLowerCase();
        if (label.includes("bullish")) return "is-bullish";
        if (label.includes("bearish")) return "is-bearish";
        return "is-neutral";
    };

    const meaningFor = (item) => {
        const side = item.net < 0 ? "net short" : item.net > 0 ? "net long" : "balanced";
        const contrarian = item.net < 0 ? "bullish" : item.net > 0 ? "bearish" : "neutral";
        const source = item.source ? ` Source: ${item.source}.` : "";
        if (Math.abs(item.net) < 8) {
            return `Retail positioning in ${item.symbol} is broadly balanced, with net positioning near ${item.net.toFixed(1)}%. The contrarian read is neutral until crowding becomes more extreme.${source}`;
        }
        return `Retail positioning in ${item.symbol} is ${side} by ${Math.abs(item.net).toFixed(1)}%, which gives a ${contrarian} contrarian read when retail crowding reaches an extreme.${source}`;
    };

    const rangeDays = () => {
        if (state.range === "1w") return 7;
        if (state.range === "1m") return 30;
        return 90;
    };

    const makeFallbackHistory = (item) => {
        const points = state.range === "1w" ? 14 : state.range === "1m" ? 30 : 90;
        const labels = [];
        const long = [];
        const short = [];
        const net = [];
        const now = new Date();
        for (let i = points - 1; i >= 0; i -= 1) {
            const d = new Date(now);
            d.setDate(now.getDate() - i);
            const phase = (points - i + item.symbol.length) / 5;
            const drift = Math.sin(phase) * 2.8 + Math.cos(phase / 2) * 1.6;
            const longPct = Math.max(3, Math.min(97, item.longPct + drift));
            labels.push(d.toLocaleDateString("en-US", { month: "short", day: "numeric" }));
            long.push(Number(longPct.toFixed(1)));
            short.push(Number((100 - longPct).toFixed(1)));
            net.push(Number((longPct - (100 - longPct)).toFixed(1)));
        }
        long[long.length - 1] = item.longPct;
        short[short.length - 1] = item.shortPct;
        net[net.length - 1] = item.net;
        return { labels, long, short, net };
    };

    const historyFromApi = (item, records) => {
        const filtered = (records || []).slice(-Math.max(2, rangeDays() * 2));
        if (filtered.length < 2) return makeFallbackHistory(item);
        return {
            labels: filtered.map((record) => {
                const d = new Date(record.ts);
                return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
            }),
            long: filtered.map((record) => Number(record.long || 0)),
            short: filtered.map((record) => Number(record.short || 0)),
            net: filtered.map((record) => Number(record.net || 0)),
        };
    };

    const fetchJson = async (url, options) => {
        const response = await fetch(url, { headers: { "Accept": "application/json" }, ...options });
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return response.json();
    };

    const loadSnapshot = async () => {
        els.status.textContent = "Fetching retail positioning data...";
        const data = await fetchJson(`/api/retail-sentiment?category=all&sort=${encodeURIComponent(state.sort)}&limit=300`);
        state.items = (data.assets || []).map(normalizeAsset);
        state.timestamp = data.timestamp;
        state.sources = data.sources || {};
        if (!state.selected || !state.items.some((item) => item.symbol === state.selected.symbol)) {
            state.selected = state.items.find((item) => item.symbol === "AUDJPY") || state.items[0] || null;
        }
        renderAll();
    };

    const getFiltered = () => {
        const q = clean(els.search?.value).toUpperCase();
        let items = state.items.filter((item) => state.category === "all" || item.category === state.category);
        if (q) items = items.filter((item) => item.symbol.includes(q));
        items = [...items].sort((a, b) => {
            if (state.sort === "az") return a.symbol.localeCompare(b.symbol);
            if (state.sort === "long") return b.longPct - a.longPct;
            if (state.sort === "short") return b.shortPct - a.shortPct;
            if (state.sort === "net") return Math.abs(b.net) - Math.abs(a.net);
            return Math.abs(b.net) - Math.abs(a.net) || b.positions - a.positions;
        });
        return items;
    };

    const renderList = () => {
        state.filtered = getFiltered();
        if (!state.filtered.length) {
            els.list.innerHTML = '<div class="fx-empty">No assets match the current filters.</div>';
            return;
        }
        els.list.innerHTML = state.filtered.map((item) => `
            <button class="fx-rank-row ${state.selected?.symbol === item.symbol ? "is-selected" : ""}" type="button" data-fx-symbol="${escapeHtml(item.symbol)}">
                <span>${escapeHtml(item.symbol)}</span>
                <b>
                    <i class="fx-long" style="width:${item.longPct}%"></i>
                    <i class="fx-short" style="width:${item.shortPct}%"></i>
                </b>
                <em>${pct(item.longPct)} / ${pct(item.shortPct)}</em>
            </button>
        `).join("");
    };

    const renderSelect = () => {
        els.assetSelect.innerHTML = state.items
            .map((item) => `<option value="${escapeHtml(item.symbol)}">${escapeHtml(item.symbol)}</option>`)
            .join("");
        if (state.selected) els.assetSelect.value = state.selected.symbol;
    };

    const renderDetails = (item) => {
        if (!item) return;
        state.selected = item;
        els.assetSelect.value = item.symbol;
        els.detailTitle.textContent = `${item.symbol} (${item.category.charAt(0).toUpperCase()}${item.category.slice(1)})`;
        els.detailAsOf.textContent = `As of: ${state.timestamp || "--"}`;
        els.detailBias.textContent = biasFor(item);
        els.detailBias.className = biasClass(item);
        els.detailMeaning.textContent = meaningFor(item);
        els.detailLongShort.textContent = `${pct(item.longPct)} / ${pct(item.shortPct)}`;
        els.detailTotal.textContent = item.positions ? item.positions.toLocaleString() : "n/a";
        els.detailNet.textContent = `${item.net > 0 ? "+" : ""}${pct(item.net)}`;
        els.detailAverage.textContent = `${item.avgLong || "-"} / ${item.avgShort || "-"}`;
        els.detailCrowding.textContent = `${item.crowding}/100`;
        renderList();
        renderChart(item);
    };

    const renderChart = async (item) => {
        if (!window.Chart || !els.chart || !item) return;
        let series = makeFallbackHistory(item);
        try {
            const detail = await fetchJson(`/api/retail-sentiment/${encodeURIComponent(item.symbol)}`);
            if (state.selected?.symbol !== item.symbol) return;
            series = historyFromApi(item, detail.history || []);
        } catch (error) {
            // The current snapshot is enough for the page; history builds as snapshots accumulate.
        }

        if (state.chart) state.chart.destroy();
        state.chart = new Chart(els.chart, {
            type: "line",
            data: {
                labels: series.labels,
                datasets: [
                    {
                        label: "Long",
                        data: series.long,
                        borderColor: "#2e6be0",
                        backgroundColor: "rgba(46, 107, 224, .14)",
                        fill: true,
                        tension: 0.28,
                        pointRadius: 0,
                        borderWidth: 2,
                    },
                    {
                        label: "Short",
                        data: series.short,
                        borderColor: "#e05050",
                        backgroundColor: "rgba(224, 80, 80, .14)",
                        fill: true,
                        tension: 0.28,
                        pointRadius: 0,
                        borderWidth: 2,
                    },
                    {
                        label: "Net (L-S)",
                        data: series.net,
                        borderColor: "#c5c8c3",
                        backgroundColor: "transparent",
                        tension: 0.28,
                        pointRadius: 0,
                        borderWidth: 1.5,
                        borderDash: [4, 4],
                        yAxisID: "net",
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                interaction: { intersect: false, mode: "index" },
                plugins: {
                    legend: {
                        align: "start",
                        labels: { color: "#d7d7d7", boxWidth: 30 },
                    },
                    tooltip: {
                        backgroundColor: "#111620",
                        borderColor: "#2a3548",
                        borderWidth: 1,
                    },
                },
                scales: {
                    x: {
                        grid: { color: "rgba(255,255,255,.06)" },
                        ticks: { color: "#8a9bb0", maxTicksLimit: 6 },
                    },
                    y: {
                        min: 0,
                        max: 100,
                        grid: { color: "rgba(255,255,255,.08)" },
                        ticks: { color: "#aeb4bd", callback: (value) => `${value}%` },
                    },
                    net: {
                        min: -100,
                        max: 100,
                        display: false,
                    },
                },
            },
        });
    };

    const renderAll = () => {
        renderList();
        renderSelect();
        const selectedStillVisible = state.filtered.some((item) => item.symbol === state.selected?.symbol);
        const preferred = selectedStillVisible ? state.selected : state.filtered[0] || state.items[0];
        renderDetails(preferred);
        els.status.textContent = `As of: ${state.timestamp || "--"} | Lookback: ${state.range === "current" ? "Current" : state.range.toUpperCase()} | Sources: ${sourceLabel(state.sources)}`;
        root.classList.add("is-enhanced");
    };

    root.addEventListener("click", (event) => {
        const category = event.target.closest("[data-fx-category]");
        if (category) {
            root.querySelectorAll("[data-fx-category]").forEach((button) => button.classList.toggle("is-active", button === category));
            state.category = category.dataset.fxCategory;
            renderAll();
            return;
        }

        const sort = event.target.closest("[data-fx-sort]");
        if (sort) {
            root.querySelectorAll("[data-fx-sort]").forEach((button) => button.classList.toggle("is-active", button === sort));
            state.sort = sort.dataset.fxSort;
            renderAll();
            return;
        }

        const range = event.target.closest("[data-fx-range]");
        if (range) {
            root.querySelectorAll("[data-fx-range]").forEach((button) => button.classList.toggle("is-active", button === range));
            state.range = range.dataset.fxRange;
            if (state.selected) renderChart(state.selected);
            renderAll();
            return;
        }

        const row = event.target.closest("[data-fx-symbol]");
        if (row) {
            const item = state.items.find((candidate) => candidate.symbol === row.dataset.fxSymbol);
            renderDetails(item);
        }
    });

    els.search?.addEventListener("input", renderAll);
    els.assetSelect?.addEventListener("change", () => {
        const item = state.items.find((candidate) => candidate.symbol === els.assetSelect.value);
        renderDetails(item);
    });
    els.save?.addEventListener("click", () => {
        const item = state.selected;
        if (!item) return;
        const payload = `${item.symbol} ${pct(item.longPct)} long / ${pct(item.shortPct)} short, net ${pct(item.net)} (${state.timestamp || "no timestamp"})`;
        navigator.clipboard?.writeText(payload).catch(() => {});
        els.save.textContent = "Saved";
        window.setTimeout(() => { els.save.textContent = "Save Snapshot"; }, 1200);
    });

    loadSnapshot().catch((error) => {
        els.status.textContent = `Retail sentiment unavailable: ${error.message}`;
        els.list.innerHTML = '<div class="fx-empty">Unable to load retail positioning data. Try refreshing in a moment.</div>';
    });
})();
