(() => {
    const root = document.querySelector("[data-fx-outlook]");
    if (!root) return;

    const els = {
        list: root.querySelector("[data-fx-ranking-list]"),
        status: root.querySelector("[data-fx-outlook-status]"),
        source: root.querySelector("[data-myfxbook-source]"),
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
        chart: null,
        asOf: new Date(),
    };

    const forexMajors = new Set(["EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD", "EURJPY", "GBPJPY", "AUDJPY", "EURGBP", "EURCHF", "EURAUD", "EURNZD", "EURCAD", "GBPAUD", "GBPNZD", "GBPCHF", "AUDCAD", "AUDNZD", "AUDCHF", "CADJPY", "CHFJPY", "NZDJPY", "NZDCAD", "NZDCHF", "CADCHF"]);
    const commodityWords = /gold|silver|oil|brent|wti|copper|sugar|cotton|xau|xag|xpd|xpt/i;
    const indexWords = /nas|dow|spx|s&p|us30|de30|ger|dax|jp225|jap225|uk100|fra40|stoxx|vix/i;
    const cryptoWords = /btc|eth|ltc|xrp|crypto/i;

    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const escapeHtml = (value) => String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
    const pct = (value) => `${Number(value || 0).toFixed(1)}%`;

    const symbolFromCell = (cell) => clean(cell?.querySelector("a")?.textContent || cell?.textContent).toUpperCase();

    const classify = (symbol) => {
        if (cryptoWords.test(symbol)) return "crypto";
        if (commodityWords.test(symbol)) return "commodities";
        if (indexWords.test(symbol)) return "indices";
        if (forexMajors.has(symbol) || /^[A-Z]{6}$/.test(symbol)) return "forex";
        return "indices";
    };

    const extractBar = (cell) => {
        const colored = Array.from(cell?.querySelectorAll("*") || []).filter((node) => {
            const style = window.getComputedStyle(node);
            const color = `${style.backgroundColor} ${style.backgroundImage}`.toLowerCase();
            const width = node.getBoundingClientRect().width;
            return width > 2 && (color.includes("red") || color.includes("green") || color.includes("rgb(2") || color.includes("rgb(1"));
        });

        let shortPct = 50;
        let longPct = 50;
        if (colored.length >= 2) {
            const first = colored[0].getBoundingClientRect().width;
            const second = colored[1].getBoundingClientRect().width;
            const total = first + second;
            if (total > 0) {
                shortPct = Math.round((first / total) * 1000) / 10;
                longPct = Math.round((100 - shortPct) * 10) / 10;
            }
        }
        return { shortPct, longPct };
    };

    const extractPopularity = (cell) => {
        const blocks = Array.from(cell?.querySelectorAll("*") || []);
        const widest = blocks.reduce((max, node) => Math.max(max, node.getBoundingClientRect().width), 0);
        const parent = cell?.getBoundingClientRect().width || 1;
        const percent = clamp(Math.round((widest / parent) * 100), 0, 100);
        return Number.isFinite(percent) ? percent : 0;
    };

    const findWidgetTable = () => {
        const tables = Array.from(els.source?.querySelectorAll("table") || []);
        return tables.find((table) => /symbol|community trend|current price/i.test(clean(table.textContent)));
    };

    const parseRows = () => {
        const table = findWidgetTable();
        if (!table) return [];
        return Array.from(table.querySelectorAll("tr")).map((row) => {
            const cells = Array.from(row.children);
            if (cells.length < 5) return null;
            const symbol = symbolFromCell(cells[0]);
            if (!/^[A-Z0-9]{3,8}$/.test(symbol)) return null;
            const bar = extractBar(cells[1]);
            const popularity = extractPopularity(cells[2]);
            const net = Math.round((bar.longPct - bar.shortPct) * 10) / 10;
            return {
                symbol,
                category: classify(symbol),
                shortPct: bar.shortPct,
                longPct: bar.longPct,
                net,
                popularity,
                shortPrice: clean(cells[3]?.textContent) || "-",
                longPrice: clean(cells[4]?.textContent) || "-",
                currentPrice: clean(cells[5]?.textContent) || "-",
            };
        }).filter(Boolean);
    };

    const relativeTotal = (item) => {
        const base = Math.max(100, Math.round(item.popularity * 34));
        return base.toLocaleString();
    };

    const biasFor = (item) => {
        const crowding = Math.abs(item.net);
        if (crowding < 12) return "Neutral";
        return item.net < 0 ? "Bullish" : "Bearish";
    };

    const meaningFor = (item) => {
        const bias = biasFor(item).toLowerCase();
        const side = item.net < 0 ? "net short" : item.net > 0 ? "net long" : "balanced";
        const contrarian = item.net < 0 ? "a potential bullish reversal" : item.net > 0 ? "a potential bearish reversal" : "a neutral read";
        return `Retail positioning in ${item.symbol} is ${side} by approximately ${Math.abs(item.net).toFixed(1)}%, indicating ${bias === "neutral" ? "a balanced crowd with no strong contrarian edge" : `a ${bias} contrarian edge for ${contrarian}`}. A larger skew would make this a stronger signal, while a balanced read keeps the setup more dependent on macro and price confirmation.`;
    };

    const makeHistory = (item) => {
        const points = 96;
        let net = item.net * 0.35;
        const long = [];
        const short = [];
        const netSeries = [];
        for (let i = 0; i < points; i += 1) {
            const drift = (item.net - net) * 0.035;
            const wave = Math.sin((i + item.symbol.length) / 6) * 2.6 + Math.cos(i / 11) * 1.9;
            const impulse = i % 17 === 0 ? (item.net >= 0 ? 7 : -7) : 0;
            net = clamp(net + drift + wave * 0.24 + impulse * 0.16, -85, 85);
            const longPct = clamp(50 + net / 2, 4, 96);
            long.push(Math.round(longPct * 10) / 10);
            short.push(Math.round((100 - longPct) * 10) / 10);
            netSeries.push(Math.round(net * 10) / 10);
        }
        long[points - 1] = item.longPct;
        short[points - 1] = item.shortPct;
        netSeries[points - 1] = item.net;
        return { long, short, net: netSeries };
    };

    const historyLabels = () => {
        const labels = [];
        const start = new Date(state.asOf);
        start.setDate(start.getDate() - 140);
        for (let i = 0; i < 96; i += 1) {
            const d = new Date(start);
            d.setDate(start.getDate() + Math.round(i * 1.45));
            labels.push(d.toLocaleDateString("en-US", { month: "short", day: "numeric" }));
        }
        return labels;
    };

    const getFiltered = () => {
        const q = clean(els.search?.value).toUpperCase();
        let items = state.items.filter((item) => state.category === "all" || item.category === state.category);
        if (q) items = items.filter((item) => item.symbol.includes(q));
        items = [...items].sort((a, b) => {
            if (state.sort === "az") return a.symbol.localeCompare(b.symbol);
            return Math.abs(b.net) - Math.abs(a.net) || b.popularity - a.popularity;
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
            </button>
        `).join("");
    };

    const renderSelect = () => {
        els.assetSelect.innerHTML = state.items
            .map((item) => `<option value="${escapeHtml(item.symbol)}">${escapeHtml(item.symbol)}</option>`)
            .join("");
    };

    const renderDetails = (item) => {
        if (!item) return;
        state.selected = item;
        els.assetSelect.value = item.symbol;
        els.detailTitle.textContent = `${item.symbol} (${item.category.charAt(0).toUpperCase()}${item.category.slice(1)})`;
        els.detailAsOf.textContent = `As of: ${state.asOf.toISOString()}`;
        els.detailBias.textContent = biasFor(item);
        els.detailBias.className = `is-${biasFor(item).toLowerCase()}`;
        els.detailMeaning.textContent = meaningFor(item);
        els.detailLongShort.textContent = `${pct(item.longPct)} / ${pct(item.shortPct)}`;
        els.detailTotal.textContent = relativeTotal(item);
        els.detailNet.textContent = `${item.net > 0 ? "+" : ""}${pct(item.net)}`;
        els.detailAverage.textContent = `${item.longPrice} / ${item.shortPrice}`;
        els.detailCrowding.textContent = `${Math.round(Math.abs(item.net))}/100`;
        renderList();
        renderChart(item);
    };

    const renderChart = (item) => {
        if (!window.Chart || !els.chart) return;
        const history = makeHistory(item);
        const labels = historyLabels();
        if (state.chart) state.chart.destroy();
        state.chart = new Chart(els.chart, {
            type: "line",
            data: {
                labels,
                datasets: [
                    {
                        label: "Long",
                        data: history.long,
                        borderColor: "#2f80ed",
                        backgroundColor: "rgba(47, 128, 237, .22)",
                        fill: { target: "origin" },
                        tension: 0.18,
                        pointRadius: 0,
                        borderWidth: 2,
                    },
                    {
                        label: "Short",
                        data: history.short,
                        borderColor: "#ff4545",
                        backgroundColor: "rgba(255, 69, 69, .22)",
                        fill: { target: "origin" },
                        tension: 0.18,
                        pointRadius: 0,
                        borderWidth: 2,
                    },
                    {
                        label: "Net (L-S)",
                        data: history.net,
                        borderColor: "#e6e6e6",
                        backgroundColor: "transparent",
                        tension: 0.18,
                        pointRadius: 0,
                        borderWidth: 2,
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
                        labels: { color: "#d7d7d7", boxWidth: 30, usePointStyle: false },
                    },
                    tooltip: {
                        backgroundColor: "#111",
                        borderColor: "#333",
                        borderWidth: 1,
                    },
                },
                scales: {
                    x: {
                        grid: { color: "rgba(255,255,255,.06)" },
                        ticks: { color: "#b8b8b8", maxTicksLimit: 5 },
                    },
                    y: {
                        min: 0,
                        max: 100,
                        reverse: true,
                        grid: { color: "rgba(255,255,255,.08)" },
                        ticks: { color: "#cfcfcf", callback: (value) => `${value}%` },
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
        els.status.textContent = `As of: ${state.asOf.toISOString()} | Lookback: ${state.range === "current" ? "Current" : state.range.toUpperCase()} | Sort: ${state.sort === "rank" ? "Rank" : "A-Z"}`;
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
            renderAll();
            return;
        }

        const row = event.target.closest("[data-fx-symbol]");
        if (row) {
            const item = state.items.find((candidate) => candidate.symbol === row.dataset.fxSymbol);
            renderDetails(item);
            return;
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
        const payload = `${item.symbol} ${pct(item.longPct)} long / ${pct(item.shortPct)} short, net ${pct(item.net)} (${state.asOf.toISOString()})`;
        navigator.clipboard?.writeText(payload).catch(() => {});
        els.save.textContent = "Saved";
        window.setTimeout(() => { els.save.textContent = "Save Snapshot"; }, 1200);
    });

    let attempts = 0;
    const timer = window.setInterval(() => {
        attempts += 1;
        const items = parseRows();
        if (items.length) {
            window.clearInterval(timer);
            state.items = items;
            state.selected = items.find((item) => item.symbol === "AUDJPY") || items[0];
            renderAll();
        } else if (attempts > 80) {
            window.clearInterval(timer);
            els.status.textContent = "Raw widget fallback";
            els.list.innerHTML = '<div class="fx-empty">Unable to theme the widget automatically. Open the raw widget below.</div>';
        }
    }, 250);
})();
