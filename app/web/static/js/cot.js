(() => {
    const CACHE_KEY = "macro_cftc_cot_dashboard_v2";
    const CACHE_TTL_MS = 24 * 60 * 60 * 1000;
    const BULL = "#1D9E75";
    const BEAR = "#D85A30";
    const NEUTRAL = "#888780";
    const FX_SYMBOLS = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD"];
    const MAP_SYMBOLS = [...FX_SYMBOLS, "USD"];
    const ALL_SYMBOLS = [...FX_SYMBOLS, "USD", "GOLD", "OIL"];
    const DIFF_PAIRS = [
        { pair: "EUR/USD", base: "EUR", quote: "USD" },
        { pair: "GBP/USD", base: "GBP", quote: "USD" },
        { pair: "AUD/USD", base: "AUD", quote: "USD" },
        { pair: "NZD/USD", base: "NZD", quote: "USD" },
        { pair: "USD/JPY", base: "USD", quote: "JPY" },
        { pair: "USD/CAD", base: "USD", quote: "CAD" },
        { pair: "USD/CHF", base: "USD", quote: "CHF" },
        { pair: "EUR/GBP", base: "EUR", quote: "GBP" },
        { pair: "EUR/JPY", base: "EUR", quote: "JPY" },
        { pair: "AUD/JPY", base: "AUD", quote: "JPY" },
    ];
    const PRICE_PAIR_BY_SYMBOL = {
        EUR: "EURUSD",
        GBP: "GBPUSD",
        JPY: "USDJPY",
        CHF: "USDCHF",
        CAD: "USDCAD",
        AUD: "AUDUSD",
        NZD: "NZDUSD",
        USD: "DXY",
    };
    const AUTO_PRICE_ENDPOINTS = [
        (pair) => `/api/fx-data?pair=${encodeURIComponent(pair)}&weeks=52`,
        (pair) => `/api/rates?pair=${encodeURIComponent(pair)}&weeks=52`,
    ];
    const root = document.querySelector("[data-cot-dashboard]");
    if (!root) return;

    let active = "EUR";
    let cotRows = {};
    let priceSeriesBySymbol = {};
    let positioningMapChart = null;
    let autoPriceEndpointAvailable = null;
    const charts = {};

    const $ = (selector) => root.querySelector(selector);
    const $$ = (selector) => Array.from(root.querySelectorAll(selector));
    const fmt = (value) => Number(value || 0).toLocaleString();
    const signed = (value) => `${Number(value || 0) >= 0 ? "+" : ""}${fmt(Math.round(Number(value || 0)))}`;
    const pct = (value) => `${Math.round(Number(value || 0))}`;
    const colorForIndex = (value) => value > 60 ? BULL : value < 40 ? BEAR : NEUTRAL;
    const clamp = (value, min, max) => Math.max(min, Math.min(max, value));
    const escapeHtml = (value) => String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");

    const number = (value) => {
        if (value === null || value === undefined || value === "") return 0;
        const parsed = Number(String(value).replaceAll(",", ""));
        return Number.isFinite(parsed) ? parsed : 0;
    };

    const getTextColor = () => getComputedStyle(root).getPropertyValue("--cot-text").trim() || "#f3f4f6";
    const getMutedColor = () => getComputedStyle(root).getPropertyValue("--cot-muted").trim() || "#9ca3af";
    const getGridColor = () => getComputedStyle(root).getPropertyValue("--cot-grid").trim() || "rgba(148,163,184,.18)";

    const destroyChart = (key) => {
        if (charts[key]) {
            charts[key].destroy();
            delete charts[key];
        }
    };

    const chartOptions = (extra = {}) => ({
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
            legend: { labels: { color: getMutedColor(), boxWidth: 12 } },
            tooltip: {
                backgroundColor: root.classList.contains("cot-light") ? "#ffffff" : "#101318",
                borderColor: getGridColor(),
                borderWidth: 1,
                titleColor: getTextColor(),
                bodyColor: getTextColor(),
            },
        },
        scales: {
            x: { ticks: { color: getMutedColor(), maxTicksLimit: 8 }, grid: { color: getGridColor() } },
            y: { ticks: { color: getMutedColor() }, grid: { color: getGridColor() } },
            y1: { display: false, position: "right", grid: { drawOnChartArea: false }, ticks: { color: getMutedColor() } },
        },
        ...extra,
    });

    const drawChart = (key, canvas, config) => {
        if (!window.Chart || !canvas) return;
        destroyChart(key);
        charts[key] = new Chart(canvas, config);
    };

    const waitForChart = () => {
        if (window.Chart) return Promise.resolve();
        return Promise.reject(new Error("Chart.js is not loaded. Check the static asset."));
    };

    const loadFromApi = async () => {
        const response = await fetch("/api/cot/rows", { cache: "no-store" });
        if (!response.ok) throw new Error(`COT rows endpoint returned ${response.status}`);
        const data = await response.json();
        const result = {};
        for (const [symbol, rows] of Object.entries(data)) {
            result[symbol] = Array.isArray(rows)
                ? rows.sort((a, b) => a.date.localeCompare(b.date))
                : [];
        }
        return result;
    };

    const readCache = () => {
        try {
            const cached = JSON.parse(localStorage.getItem(CACHE_KEY) || "null");
            if (!cached || !cached.fetchedAt || Date.now() - cached.fetchedAt > CACHE_TTL_MS) return null;
            return cached;
        } catch {
            return null;
        }
    };

    const writeCache = (data) => {
        localStorage.setItem(CACHE_KEY, JSON.stringify({ fetchedAt: Date.now(), rows: data }));
    };

    const setPriceStatus = (message) => {
        const status = $("[data-cot-price-status]");
        if (status) status.textContent = message;
    };

    const setManualPriceVisible = (visible) => {
        const input = $("[data-cot-price-input]");
        const button = $("[data-cot-apply-price]");
        if (input) input.hidden = !visible;
        if (button) button.hidden = !visible;
    };

    const setLoading = (isLoading) => {
        $("[data-cot-loading]").hidden = !isLoading;
    };

    const showError = (message) => {
        const box = $("[data-cot-error]");
        box.hidden = false;
        box.textContent = message;
    };

    const clearError = () => {
        $("[data-cot-error]").hidden = true;
        $("[data-cot-error]").textContent = "";
    };

    const latest = (symbol) => {
        const rows = cotRows[symbol] || [];
        return rows[rows.length - 1];
    };

    const netSeries = (symbol) => (cotRows[symbol] || []).map((row) => row.specNet);
    const commercialSeries = (symbol) => (cotRows[symbol] || []).map((row) => row.commercialNet);
    const labelSeries = (symbol) => (cotRows[symbol] || []).map((row) => row.date);

    const extremeStats = (symbol) => {
        const rows = cotRows[symbol] || [];
        const last = rows[rows.length - 1];
        const windowRows = rows.slice(-52);
        const values = windowRows.map((row) => row.specNet);
        const high = Math.max(...values);
        const low = Math.min(...values);
        const index = high === low ? 50 : ((last.specNet - low) / (high - low)) * 100;
        return { symbol, current: last?.specNet || 0, high, low, index: Math.max(0, Math.min(100, index)) };
    };

    const deltas = (symbol) => {
        const values = netSeries(symbol);
        return values.map((value, index) => index === 0 ? 0 : value - values[index - 1]);
    };

    const mean = (values) => values.length ? values.reduce((sum, value) => sum + value, 0) / values.length : 0;
    const stdev = (values) => {
        const avg = mean(values);
        return Math.sqrt(mean(values.map((value) => (value - avg) ** 2)));
    };

    const parsePriceInput = () => {
        const lines = ($("[data-cot-price-input]").value || "")
            .split(/\r?\n/)
            .map((line) => line.trim())
            .filter(Boolean);
        return lines.map((line) => {
            const parts = line.split(/[,\t ]+/).filter(Boolean);
            const value = number(parts.length > 1 ? parts[parts.length - 1] : parts[0]);
            return value || null;
        }).filter((value) => value !== null);
    };

    const normalizePriceRows = (payload) => {
        const candidates = [
            payload?.prices,
            payload?.history,
            payload?.series,
            payload?.data?.prices,
            payload?.data?.history,
            payload?.data?.series,
            payload?.data,
        ];
        const rows = candidates.find((item) => Array.isArray(item));
        if (!rows) return [];
        return rows.map((row) => {
            if (Array.isArray(row)) return number(row[row.length - 1]);
            if (row && typeof row === "object") {
                return number(row.close ?? row.adjusted_close ?? row.value ?? row.price ?? row.y);
            }
            return number(row);
        }).filter((value) => value > 0);
    };

    const loadAutomaticPriceOverlay = async (symbol) => {
        const pair = PRICE_PAIR_BY_SYMBOL[symbol];
        if (!pair) {
            setManualPriceVisible(true);
            setPriceStatus("Auto-price coming soon. Paste weekly closes in format: date,price (one per line)");
            return false;
        }

        if (priceSeriesBySymbol[symbol]?.length) {
            setManualPriceVisible(false);
            setPriceStatus("Price data loaded automatically");
            return true;
        }

        if (autoPriceEndpointAvailable === false) {
            setManualPriceVisible(true);
            setPriceStatus("Auto-price coming soon. Paste weekly closes in format: date,price (one per line)");
            return false;
        }

        for (const endpointForPair of AUTO_PRICE_ENDPOINTS) {
            try {
                const response = await fetch(endpointForPair(pair), { cache: "no-store" });
                if (!response.ok) continue;
                const prices = normalizePriceRows(await response.json()).slice(-52);
                if (!prices.length) continue;
                priceSeriesBySymbol[symbol] = prices;
                autoPriceEndpointAvailable = true;
                setManualPriceVisible(false);
                setPriceStatus("Price data loaded automatically");
                return true;
            } catch {
                // Keep manual paste fallback when no compatible price endpoint is available.
            }
        }

        autoPriceEndpointAvailable = false;
        setManualPriceVisible(true);
        setPriceStatus("Auto-price coming soon. Paste weekly closes in format: date,price (one per line)");
        return false;
    };

    const alignedPrice = (symbol) => {
        const prices = priceSeriesBySymbol[symbol] || [];
        const rows = cotRows[symbol] || [];
        if (!prices.length || !rows.length) return [];
        const clipped = prices.slice(-rows.length);
        const missing = Array(Math.max(0, rows.length - clipped.length)).fill(null);
        return [...missing, ...clipped];
    };

    const priceDirection = (symbol, weeks = 8) => {
        const prices = alignedPrice(symbol).filter((value) => value !== null);
        if (prices.length > weeks) return prices[prices.length - 1] - prices[prices.length - 1 - weeks];
        const rows = cotRows[symbol] || [];
        if (rows.length <= weeks) return 0;
        return rows[rows.length - 1].specNet - rows[rows.length - 1 - weeks].specNet;
    };

    const divergenceSignalAt = (symbol, endIndex) => {
        const rows = cotRows[symbol] || [];
        if (endIndex < 8) return null;
        const priceMove = priceDirection(symbol, 8);
        const netMove = rows[endIndex].specNet - rows[endIndex - 8].specNet;
        let signal = "No divergence";
        if (priceMove < 0 && netMove > 0) signal = "Bullish divergence";
        if (priceMove > 0 && netMove < 0) signal = "Bearish divergence";
        return { date: rows[endIndex].date, signal, priceMove, netMove };
    };

    const divergenceHistory = (symbol) => {
        const rows = cotRows[symbol] || [];
        const signals = [];
        for (let index = rows.length - 1; index >= 8 && signals.length < 3; index -= 1) {
            const signal = divergenceSignalAt(symbol, index);
            if (signal && signal.signal !== "No divergence") signals.push(signal);
        }
        return signals;
    };

    const signalForIndex = (index) => {
        if (index > 80) return "Extreme long";
        if (index > 60) return "Bullish";
        if (index < 20) return "Extreme short";
        if (index < 40) return "Bearish";
        return "Neutral";
    };

    const renderNetChart = (symbol) => {
        const rows = cotRows[symbol] || [];
        const labels = labelSeries(symbol);
        const nets = netSeries(symbol);
        const price = alignedPrice(symbol);
        const pointColors = nets.map((value, index) => {
            const prev = index > 0 ? nets[index - 1] : value;
            return (prev <= 0 && value > 0) || (prev >= 0 && value < 0) ? "#F2B84B" : (value >= 0 ? BULL : BEAR);
        });
        drawChart("net", $("[data-chart='net']"), {
            type: "line",
            data: {
                labels,
                datasets: [
                    { label: "Non-commercial net", data: nets, borderColor: BULL, backgroundColor: "rgba(29,158,117,.12)", pointBackgroundColor: pointColors, pointRadius: pointColors.map((color) => color === "#F2B84B" ? 5 : 2), tension: 0.25 },
                    { label: "Zero line", data: rows.map(() => 0), borderColor: NEUTRAL, borderDash: [5, 4], pointRadius: 0 },
                    { label: "Price overlay", data: price, yAxisID: "y1", borderColor: "#6DA9FF", pointRadius: 0, tension: 0.25, hidden: !price.length },
                ],
            },
            options: chartOptions({ scales: { ...chartOptions().scales, y1: { display: price.length > 0, position: "right", grid: { drawOnChartArea: false }, ticks: { color: getMutedColor() } } } }),
        });
    };

    const renderExtremeBars = () => {
        const stats = ALL_SYMBOLS
            .filter((symbol) => (cotRows[symbol] || []).length)
            .map(extremeStats)
            .sort((a, b) => b.index - a.index);
        $("[data-extreme-bars]").innerHTML = stats.map((item) => `
            <div class="cot-extreme-row">
                <strong>${item.symbol}</strong>
                <span><i style="width:${pct(item.index)}%;background:${colorForIndex(item.index)}"></i></span>
                <em style="color:${colorForIndex(item.index)}">${pct(item.index)}</em>
            </div>
        `).join("");
        const current = extremeStats(active);
        $("[data-current-extreme]").innerHTML = `
            <strong style="color:${colorForIndex(current.index)}">${active} ${pct(current.index)}/100</strong>
            <span>${signed(current.current)} net contracts</span>
        `;
    };

    const mapColorForPercentile = (percentile) => {
        if (percentile > 60) return "#22c55e";
        if (percentile < 40) return "#ef4444";
        return "#f59e0b";
    };

    const renderPositioningMap = () => {
        const el = document.getElementById("cot-map-chart");
        const insight = $("[data-insight='map']");
        if (!el || !window.echarts) {
            if (insight) insight.textContent = "Positioning map is unavailable because ECharts did not load.";
            return;
        }

        const rows = MAP_SYMBOLS
            .filter((symbol) => (cotRows[symbol] || []).length)
            .map((symbol) => {
                const stats = extremeStats(symbol);
                const current = latest(symbol);
                const percentile = Math.round(stats.index);
                return {
                    symbol,
                    netPosition: current?.specNet || 0,
                    percentile,
                    value: (percentile - 50) * 2,
                    color: mapColorForPercentile(percentile),
                    bias: percentile > 60 ? "bullish" : percentile < 40 ? "bearish" : "neutral",
                };
            })
            .sort((a, b) => b.percentile - a.percentile);

        if (!rows.length) {
            if (insight) insight.textContent = "Waiting for COT rows before drawing the all-currency positioning map.";
            return;
        }

        positioningMapChart = window.echarts.getInstanceByDom(el) || window.echarts.init(el, null, { renderer: "canvas" });
        positioningMapChart.setOption({
            backgroundColor: "transparent",
            grid: { left: 48, right: 54, top: 12, bottom: 28, containLabel: true },
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
                backgroundColor: root.classList.contains("cot-light") ? "#ffffff" : "#101318",
                borderColor: getGridColor(),
                textStyle: { color: getTextColor(), fontSize: 11 },
                formatter: (items) => {
                    const item = Array.isArray(items) ? items[0] : items;
                    const row = rows[item.dataIndex];
                    return `${row.symbol}<br/>COT Index: ${row.percentile}%<br/>Net position: ${signed(row.netPosition)}<br/>Bias: ${row.bias}`;
                },
            },
            xAxis: {
                type: "value",
                min: -100,
                max: 100,
                axisLabel: { color: getMutedColor(), formatter: "{value}" },
                axisLine: { lineStyle: { color: getGridColor() } },
                splitLine: { lineStyle: { color: getGridColor() } },
            },
            yAxis: {
                type: "category",
                data: rows.map((row) => row.symbol),
                axisTick: { show: false },
                axisLine: { show: false },
                axisLabel: { color: getTextColor(), fontWeight: 700 },
            },
            series: [{
                type: "bar",
                data: rows.map((row) => ({
                    value: row.value,
                    itemStyle: { color: row.color, borderWidth: 0 },
                    label: { formatter: `${row.percentile}%` },
                })),
                barWidth: 16,
                label: {
                    show: true,
                    position: "right",
                    color: getTextColor(),
                    fontSize: 11,
                    fontWeight: 700,
                },
            }],
        });

        if (insight) {
            const high = rows[0];
            const low = rows[rows.length - 1];
            insight.textContent = `${high.symbol} is the most crowded long at ${high.percentile}%, while ${low.symbol} is the most crowded short at ${low.percentile}%.`;
        }
    };

    const setDifferentialMode = (enabled) => {
        const diffPanel = document.getElementById("cot-diff-panel");
        const normalGrids = Array.from(root.children).filter((child) => child.classList.contains("cot-grid"));
        normalGrids.forEach((section) => {
            section.hidden = enabled;
        });
        const priceInput = $(".cot-price-input");
        const mapPanel = document.getElementById("cot-positioning-map");
        if (priceInput) priceInput.hidden = enabled;
        if (mapPanel) mapPanel.hidden = enabled;
        if (diffPanel) diffPanel.hidden = !enabled;
    };

    const percentileFromValues = (current, values) => {
        if (!values.length) return 50;
        const low = Math.min(...values);
        const high = Math.max(...values);
        if (high === low) return 50;
        return clamp(((current - low) / (high - low)) * 100, 0, 100);
    };

    const smallSpecNet = (row) => {
        if (!row) return 0;
        return number(row.openInterest)
            - number(row.specLong)
            - number(row.specShort)
            - number(row.commercialLong)
            - number(row.commercialShort);
    };

    const buildCurrencyMetrics = () => {
        const metrics = {};
        MAP_SYMBOLS.forEach((symbol) => {
            const rows = cotRows[symbol] || [];
            const last = rows[rows.length - 1];
            const previous = rows[rows.length - 2];
            if (!last) return;
            const windowRows = rows.slice(-52);
            const specValues = windowRows.map((row) => number(row.specNet));
            const smallValues = windowRows.map(smallSpecNet);
            const currentSmallSpecNet = smallSpecNet(last);
            metrics[symbol] = {
                percentile: percentileFromValues(number(last.specNet), specValues),
                smallPercentile: percentileFromValues(currentSmallSpecNet, smallValues),
                specNet: number(last.specNet),
                commercialNet: number(last.commercialNet),
                specLong: number(last.specLong),
                specShort: number(last.specShort),
                commercialLong: number(last.commercialLong),
                commercialShort: number(last.commercialShort),
                smallSpecNet: currentSmallSpecNet,
                weeklyChange: previous ? number(last.specNet) - number(previous.specNet) : 0,
            };
        });
        return metrics;
    };

    const differentialSignal = (diff) => {
        if (diff > 40) return "LONG BASE";
        if (diff < -40) return "SHORT BASE";
        if (diff > 20) return "MILD LONG";
        if (diff < -20) return "MILD SHORT";
        return "NEUTRAL";
    };

    const differentialConviction = (diff) => {
        const absDiff = Math.abs(diff);
        if (absDiff > 60) return "EXTREME";
        if (absDiff > 40) return "HIGH";
        if (absDiff > 20) return "MODERATE";
        return "LOW";
    };

    const squeezeRisk = (basePercentile, quotePercentile) => {
        const bothExtreme = (basePercentile > 80 && quotePercentile < 20)
            || (basePercentile < 20 && quotePercentile > 80);
        if (bothExtreme) return "HIGH";
        if ([basePercentile, quotePercentile].some((value) => value > 75 || value < 25)) return "MEDIUM";
        return "LOW";
    };

    const signalClass = (signal) => {
        if (signal.includes("LONG")) return "cot-bull";
        if (signal.includes("SHORT")) return "cot-bear";
        return "cot-neutral";
    };

    const diffColorClass = (value) => {
        if (value > 10) return "cot-bull";
        if (value < -10) return "cot-bear";
        return "cot-neutral";
    };

    const buildPairDifferentials = (metrics) => DIFF_PAIRS
        .map((config) => {
            const base = metrics[config.base];
            const quote = metrics[config.quote];
            if (!base || !quote) return null;
            const specDiff = base.percentile - quote.percentile;
            const commercialDiff = -specDiff;
            const smallSpecDiff = base.smallPercentile - quote.smallPercentile;
            const signal = differentialSignal(specDiff);
            const conviction = differentialConviction(specDiff);
            return {
                ...config,
                basePercentile: base.percentile,
                quotePercentile: quote.percentile,
                specDiff,
                commercialDiff,
                smallSpecDiff,
                signal,
                conviction,
                squeezeRisk: squeezeRisk(base.percentile, quote.percentile),
            };
        })
        .filter(Boolean);

    const renderDifferentialTable = (pairs) => {
        const tbody = document.getElementById("cot-diff-tbody");
        if (!tbody) return;
        tbody.innerHTML = pairs.map((item) => {
            const convictionClass = item.conviction === "EXTREME" || item.conviction === "HIGH"
                ? signalClass(item.signal)
                : "cot-muted-badge";
            return `
                <tr>
                    <td><strong>${escapeHtml(item.pair)}</strong></td>
                    <td class="${diffColorClass(item.specDiff)}">${signed(item.specDiff)}</td>
                    <td class="${diffColorClass(item.commercialDiff)}">${signed(item.commercialDiff)}</td>
                    <td class="${diffColorClass(item.smallSpecDiff)}">${signed(item.smallSpecDiff)}</td>
                    <td>${pct(item.basePercentile)}</td>
                    <td>${pct(item.quotePercentile)}</td>
                    <td><span class="cot-diff-badge ${signalClass(item.signal)}">${escapeHtml(item.signal)}</span></td>
                    <td><span class="cot-diff-badge ${convictionClass}">${escapeHtml(item.conviction)}</span></td>
                </tr>
            `;
        }).join("");
    };

    const renderSqueezeList = (pairs) => {
        const list = document.getElementById("cot-squeeze-list");
        if (!list) return;
        const order = { HIGH: 0, MEDIUM: 1, LOW: 2 };
        list.innerHTML = [...pairs]
            .sort((a, b) => order[a.squeezeRisk] - order[b.squeezeRisk] || Math.abs(b.specDiff) - Math.abs(a.specDiff))
            .map((item) => `
                <div class="cot-squeeze-item ${item.squeezeRisk.toLowerCase()}">
                    <div>
                        <strong>${escapeHtml(item.pair)}</strong>
                        <p>Specs ${pct(item.basePercentile)}th pct on ${item.base}, ${pct(item.quotePercentile)}th pct on ${item.quote}</p>
                    </div>
                    <span>${escapeHtml(item.squeezeRisk)}</span>
                </div>
            `).join("");
    };

    const renderDifferentialChart = (pairs) => {
        const el = document.getElementById("cot-diff-chart");
        if (!el || !window.echarts) return;
        const chart = window.echarts.getInstanceByDom(el) || window.echarts.init(el, null, { renderer: "canvas" });
        chart.setOption({
            backgroundColor: "transparent",
            grid: { left: 44, right: 20, top: 24, bottom: 62, containLabel: true },
            legend: { textStyle: { color: getMutedColor(), fontSize: 11 } },
            tooltip: {
                trigger: "axis",
                axisPointer: { type: "shadow" },
                backgroundColor: root.classList.contains("cot-light") ? "#ffffff" : "#101318",
                borderColor: getGridColor(),
                textStyle: { color: getTextColor(), fontSize: 11 },
            },
            xAxis: {
                type: "category",
                data: pairs.map((item) => item.pair),
                axisLabel: { color: getMutedColor(), rotate: 35, fontSize: 10 },
                axisLine: { lineStyle: { color: getGridColor() } },
                axisTick: { show: false },
            },
            yAxis: {
                type: "value",
                axisLabel: { color: getMutedColor() },
                axisLine: { lineStyle: { color: getGridColor() } },
                splitLine: { lineStyle: { color: getGridColor() } },
            },
            series: [
                {
                    name: "Spec Differential",
                    type: "bar",
                    data: pairs.map((item) => ({
                        value: Math.round(item.specDiff),
                        itemStyle: { color: item.specDiff >= 0 ? "#22c55e" : "#ef4444" },
                    })),
                    barWidth: 10,
                },
                {
                    name: "Commercial Differential",
                    type: "bar",
                    data: pairs.map((item) => ({
                        value: Math.round(item.commercialDiff),
                        itemStyle: { color: item.specDiff >= 0 ? "#ef4444" : "#22c55e" },
                    })),
                    barWidth: 10,
                },
            ],
        });
    };

    const scatterColor = (metric) => {
        if (metric.percentile > 60 && metric.weeklyChange > 0) return "#ef4444";
        if (metric.percentile < 40 && metric.weeklyChange < 0) return "#ef4444";
        if (metric.percentile > 60 && metric.weeklyChange < 0) return "#22c55e";
        if (metric.percentile < 40 && metric.weeklyChange > 0) return "#22c55e";
        return "#f59e0b";
    };

    const renderScatterChart = (metrics) => {
        const el = document.getElementById("cot-scatter-chart");
        if (!el || !window.echarts) return;
        const rows = MAP_SYMBOLS
            .filter((symbol) => metrics[symbol])
            .map((symbol) => ({ symbol, ...metrics[symbol] }));
        const chart = window.echarts.getInstanceByDom(el) || window.echarts.init(el, null, { renderer: "canvas" });
        chart.setOption({
            backgroundColor: "transparent",
            grid: { left: 54, right: 30, top: 20, bottom: 44, containLabel: true },
            tooltip: {
                trigger: "item",
                backgroundColor: root.classList.contains("cot-light") ? "#ffffff" : "#101318",
                borderColor: getGridColor(),
                textStyle: { color: getTextColor(), fontSize: 11 },
                formatter: (item) => `${item.data[2]}<br/>COT Index: ${pct(item.data[0])}<br/>Weekly change: ${signed(item.data[1])}`,
            },
            xAxis: {
                type: "value",
                min: 0,
                max: 100,
                name: "COT Index",
                nameTextStyle: { color: getMutedColor() },
                axisLabel: { color: getMutedColor() },
                axisLine: { lineStyle: { color: getGridColor() } },
                splitLine: { lineStyle: { color: getGridColor() } },
            },
            yAxis: {
                type: "value",
                name: "Weekly change",
                nameTextStyle: { color: getMutedColor() },
                axisLabel: { color: getMutedColor() },
                axisLine: { lineStyle: { color: getGridColor() } },
                splitLine: { lineStyle: { color: getGridColor() } },
            },
            series: [{
                type: "scatter",
                symbolSize: 16,
                data: rows.map((row) => [Math.round(row.percentile), Math.round(row.weeklyChange), row.symbol]),
                itemStyle: {
                    color: (item) => {
                        const symbol = item.data[2];
                        return scatterColor(metrics[symbol]);
                    },
                },
                label: {
                    show: true,
                    formatter: (item) => item.data[2],
                    position: "right",
                    color: getTextColor(),
                    fontWeight: 700,
                },
                markLine: {
                    symbol: "none",
                    silent: true,
                    label: { color: getMutedColor(), fontSize: 10 },
                    lineStyle: { color: getGridColor(), type: "dashed" },
                    data: [{ xAxis: 20 }, { xAxis: 80 }, { yAxis: 0 }],
                },
            }],
        });
    };

    const renderDifferentialInsights = (pairs, metrics) => {
        if (!pairs.length) return;
        const strongest = [...pairs].sort((a, b) => Math.abs(b.specDiff) - Math.abs(a.specDiff))[0];
        const neutral = [...pairs].sort((a, b) => Math.abs(a.specDiff) - Math.abs(b.specDiff))[0];
        const squeezePairs = pairs.filter((item) => item.squeezeRisk === "HIGH");
        const inflection = MAP_SYMBOLS
            .filter((symbol) => metrics[symbol] && scatterColor(metrics[symbol]) === "#22c55e")
            .sort((a, b) => Math.abs(metrics[b].weeklyChange) - Math.abs(metrics[a].weeklyChange))[0] || MAP_SYMBOLS[0];
        const continuation = MAP_SYMBOLS
            .filter((symbol) => metrics[symbol] && scatterColor(metrics[symbol]) === "#f59e0b")
            .sort((a, b) => Math.abs(metrics[b].weeklyChange) - Math.abs(metrics[a].weeklyChange))[0] || MAP_SYMBOLS[1];
        $("[data-insight='diff-matrix']").textContent = `Strongest setup: ${strongest.pair}. Most neutral: ${neutral.pair}.`;
        $("[data-insight='squeeze']").textContent = `High squeeze risk: ${squeezePairs.length} pairs. Most at risk: ${(squeezePairs[0] || strongest).pair}.`;
        $("[data-insight='scatter']").textContent = `${inflection} at positioning inflection. ${continuation} in clean trend continuation.`;
    };

    const renderDifferentials = () => {
        const metrics = buildCurrencyMetrics();
        const pairs = buildPairDifferentials(metrics);
        if (!pairs.length) {
            showError("No COT rows are available for differential analysis yet.");
            return;
        }
        clearError();
        renderDifferentialTable(pairs);
        renderSqueezeList(pairs);
        renderDifferentialChart(pairs);
        renderScatterChart(metrics);
        renderDifferentialInsights(pairs, metrics);
        window.requestAnimationFrame(() => {
            window.echarts?.getInstanceByDom(document.getElementById("cot-diff-chart"))?.resize?.();
            window.echarts?.getInstanceByDom(document.getElementById("cot-scatter-chart"))?.resize?.();
        });
    };

    const renderSpreadChart = (symbol) => {
        const labels = labelSeries(symbol);
        const spec = netSeries(symbol);
        const comm = commercialSeries(symbol);
        const gap = spec.map((value, index) => value - comm[index]);
        drawChart("spread", $("[data-chart='spread']"), {
            type: "line",
            data: {
                labels,
                datasets: [
                    { label: "Spec net", data: spec, borderColor: BULL, backgroundColor: "rgba(29,158,117,.16)", fill: "origin", tension: 0.25 },
                    { label: "Commercial net", data: comm, borderColor: BEAR, backgroundColor: "rgba(216,90,48,.14)", fill: "origin", tension: 0.25 },
                    { label: "Gap", data: gap, borderColor: "#F2B84B", pointRadius: 0, borderDash: [5, 4] },
                ],
            },
            options: chartOptions(),
        });
    };

    const renderOpenInterestChart = (symbol) => {
        const rows = cotRows[symbol] || [];
        const prices = alignedPrice(symbol);
        const colors = rows.map((row, index) => {
            if (index === 0) return NEUTRAL;
            const oiRising = row.openInterest > rows[index - 1].openInterest;
            const priceMove = prices.length ? (prices[index] || 0) - (prices[index - 1] || 0) : row.specNet - rows[index - 1].specNet;
            if (oiRising && priceMove > 0) return BULL;
            if (oiRising && priceMove < 0) return BEAR;
            return NEUTRAL;
        });
        drawChart("oi", $("[data-chart='oi']"), {
            type: "bar",
            data: { labels: labelSeries(symbol), datasets: [{ label: "Open interest", data: rows.map((row) => row.openInterest), backgroundColor: colors }] },
            options: chartOptions(),
        });
    };

    const renderDeltaChart = (symbol) => {
        const values = deltas(symbol);
        const threshold = stdev(values.slice(1));
        drawChart("delta", $("[data-chart='delta']"), {
            type: "bar",
            data: {
                labels: labelSeries(symbol),
                datasets: [{
                    label: "Weekly spec net change",
                    data: values,
                    backgroundColor: values.map((value) => Math.abs(value) > threshold ? (value > 0 ? BULL : BEAR) : NEUTRAL),
                }],
            },
            options: chartOptions(),
        });
    };

    const renderDivergence = (symbol) => {
        const rows = cotRows[symbol] || [];
        const current = divergenceSignalAt(symbol, rows.length - 1) || { signal: "No divergence", priceMove: 0, netMove: 0 };
        const cls = current.signal.startsWith("Bullish") ? "bullish" : current.signal.startsWith("Bearish") ? "bearish" : "neutral";
        $("[data-divergence-card]").innerHTML = `
            <span class="${cls}">${escapeHtml(current.signal)}</span>
            <strong>${symbol} 8-week check</strong>
            <p>Price direction: ${signed(current.priceMove)} | Spec net direction: ${signed(current.netMove)}</p>
        `;
        const history = divergenceHistory(symbol);
        $("[data-divergence-history]").innerHTML = history.length
            ? history.map((item) => `<div><strong>${escapeHtml(item.signal)}</strong><span>${item.date}</span></div>`).join("")
            : "<div><strong>No recent divergence signals</strong><span>Last 8-week windows aligned</span></div>";
    };

    const renderStrengthTable = () => {
        const rows = FX_SYMBOLS
            .filter((symbol) => (cotRows[symbol] || []).length)
            .map(extremeStats)
            .sort((a, b) => b.index - a.index);
        $("[data-strength-table]").innerHTML = rows.map((item, index) => `
            <tr>
                <td>${index + 1}</td>
                <td><strong>${item.symbol}</strong></td>
                <td>${signed(item.current)}</td>
                <td>${signed(item.high)}</td>
                <td>${signed(item.low)}</td>
                <td><span style="color:${colorForIndex(item.index)}">${pct(item.index)}</span></td>
                <td>${signalForIndex(item.index)}</td>
            </tr>
        `).join("");
    };

    const usdComposite = () => {
        const available = FX_SYMBOLS.map((symbol) => cotRows[symbol] || []).filter((rows) => rows.length);
        const minLength = Math.min(...available.map((rows) => rows.length));
        if (!Number.isFinite(minLength) || minLength <= 0) return { labels: [], values: [] };
        const labels = available[0].slice(-minLength).map((row) => row.date);
        const values = Array.from({ length: minLength }, (_, index) => {
            return FX_SYMBOLS.reduce((sum, symbol) => {
                const rows = cotRows[symbol] || [];
                const row = rows[rows.length - minLength + index];
                const net = row ? row.specNet : 0;
                return sum - net;
            }, 0);
        });
        return { labels, values };
    };

    const renderUsdChart = () => {
        const composite = usdComposite();
        drawChart("usd", $("[data-chart='usd']"), {
            type: "line",
            data: { labels: composite.labels, datasets: [{ label: "USD composite net", data: composite.values, borderColor: "#6DA9FF", backgroundColor: "rgba(109,169,255,.14)", fill: true, tension: 0.25 }] },
            options: chartOptions(),
        });
    };

    const renderInsights = (symbol) => {
        const stats = extremeStats(symbol);
        const current = latest(symbol);
        const deltaValues = deltas(symbol);
        const latestDelta = deltaValues[deltaValues.length - 1] || 0;
        const spread = current ? current.specNet - current.commercialNet : 0;
        const oiDelta = (cotRows[symbol] || []).length > 1 ? latest(symbol).openInterest - (cotRows[symbol] || [])[cotRows[symbol].length - 2].openInterest : 0;
        const composite = usdComposite().values;
        const usdMove = composite.length > 8 ? composite[composite.length - 1] - composite[composite.length - 9] : 0;

        $("[data-insight='net']").textContent = current?.specNet > 0
            ? `Speculators are net long ${symbol}; zero-line crosses are highlighted in amber for regime changes.`
            : `Speculators are net short ${symbol}; watch for a move back through zero as a sentiment reset.`;
        $("[data-insight='extreme']").textContent = stats.index > 80
            ? "Speculators are near historically extreme longs, which can become a contrarian bearish signal."
            : stats.index < 20
                ? "Speculators are near historically extreme shorts, which can become a contrarian bullish signal."
                : "Positioning is away from the 52-week extremes, so the signal is less crowded.";
        $("[data-insight='spread']").textContent = `Commercials and speculators are separated by ${signed(spread)} contracts; a wider gap means the market is more stretched.`;
        $("[data-insight='oi']").textContent = oiDelta > 0
            ? "Open interest rose this week, meaning fresh participation is entering the contract."
            : "Open interest fell this week, meaning participation is cooling or positions are being closed.";
        $("[data-insight='delta']").textContent = latestDelta > 0
            ? `Speculators added ${signed(latestDelta)} net contracts this week.`
            : `Speculators reduced net exposure by ${signed(Math.abs(latestDelta))} contracts this week.`;
        $("[data-insight='divergence']").textContent = "Divergence compares the 8-week price direction with the 8-week non-commercial net direction.";
        $("[data-insight='table']").textContent = "The table ranks FX contracts by where current speculative net sits inside its 52-week range.";
        $("[data-insight='usd']").textContent = usdMove > 0
            ? "USD composite sentiment is improving across the major currency futures basket."
            : usdMove < 0
                ? "USD composite sentiment is deteriorating across the major currency futures basket."
                : "USD composite sentiment is broadly flat over the recent window.";
    };

    const renderSelected = () => {
        if (active === "DIFF") {
            setDifferentialMode(true);
            renderDifferentials();
            return;
        }
        setDifferentialMode(false);
        const rows = cotRows[active] || [];
        if (!rows.length) {
            showError(`No CFTC rows were returned for ${active}. Try refresh or choose another symbol.`);
            return;
        }
        $("[data-cot-active-label]").textContent = active;
        renderNetChart(active);
        renderExtremeBars();
        renderSpreadChart(active);
        renderOpenInterestChart(active);
        renderDeltaChart(active);
        renderDivergence(active);
        renderStrengthTable();
        renderUsdChart();
        renderPositioningMap();
        renderInsights(active);
    };

    const setUpdated = (timestamp) => {
        const date = new Date(timestamp);
        $("[data-cot-updated]").textContent = `Last updated: ${Number.isNaN(date.getTime()) ? "unknown" : date.toLocaleString()}`;
    };

    const loadData = async ({ force = false } = {}) => {
        setLoading(true);
        clearError();
        try {
            await waitForChart();
            const cached = force ? null : readCache();
            if (cached) {
                cotRows = cached.rows || {};
                setUpdated(cached.fetchedAt);
            } else {
                cotRows = await loadFromApi();
                writeCache(cotRows);
                setUpdated(Date.now());
            }
            await loadAutomaticPriceOverlay(active);
            renderSelected();
            window.requestAnimationFrame(() => {
                Object.values(charts).forEach((chart) => chart?.resize?.());
                positioningMapChart?.resize?.();
                window.scrollTo({ top: 0, left: 0 });
            });
        } catch (error) {
            showError(`Unable to load CFTC data. ${error.message || "Please try again later."}`);
        } finally {
            setLoading(false);
        }
    };

    const applyTheme = (mode) => {
        root.classList.toggle("cot-light", mode === "light");
        $("[data-cot-theme-toggle]").textContent = mode === "light" ? "Dark mode" : "Light mode";
        localStorage.setItem("macro_cot_theme", mode);
        Object.keys(charts).forEach((key) => destroyChart(key));
        positioningMapChart?.dispose?.();
        positioningMapChart = null;
        if (Object.keys(cotRows).length) renderSelected();
    };

    root.addEventListener("click", (event) => {
        const tab = event.target.closest("[data-cot-symbol]");
        if (tab) {
            active = tab.dataset.cotSymbol || "EUR";
            $$("[data-cot-symbol]").forEach((button) => button.classList.toggle("is-active", button === tab));
            if (active === "DIFF") {
                renderSelected();
                return;
            }
            renderSelected();
            loadAutomaticPriceOverlay(active).then((loaded) => {
                if (loaded) renderSelected();
            });
        }

        if (event.target.closest("[data-cot-refresh]")) {
            localStorage.removeItem(CACHE_KEY);
            loadData({ force: true });
        }

        if (event.target.closest("[data-cot-apply-price]")) {
            priceSeriesBySymbol[active] = parsePriceInput();
            if (priceSeriesBySymbol[active].length) {
                setPriceStatus("Manual price overlay applied");
            }
            renderSelected();
        }

        if (event.target.closest("[data-cot-theme-toggle]")) {
            applyTheme(root.classList.contains("cot-light") ? "dark" : "light");
        }
    });

    // Help panel toggle
    document.querySelectorAll(".cot-help-btn")
        .forEach((btn) => {
            btn.addEventListener("click", (e) => {
                e.stopPropagation();
                const key = btn.dataset.help;
                const panel = document.querySelector(
                    `[data-help-panel="${key}"]`);
                if (!panel) return;
                const isHidden = panel.hidden;
                document.querySelectorAll(".cot-help-panel")
                    .forEach((p) => { p.hidden = true; });
                panel.hidden = !isHidden;
            });
        });

    document.querySelectorAll(".cot-help-close")
        .forEach((btn) => {
            btn.addEventListener("click", () => {
                btn.closest(".cot-help-panel").hidden = true;
            });
        });

    applyTheme(localStorage.getItem("macro_cot_theme") || "dark");
    loadData();
})();
