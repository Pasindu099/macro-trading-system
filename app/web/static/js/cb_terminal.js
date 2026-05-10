(() => {
    window.__macroComponents = window.__macroComponents || {};
    window.__macroComponents.cbTerminal = "booting";

    const root = document.querySelector("[data-cb-terminal]");
    if (!root) return;

    const dashboard = window.__macroDashboardData || {};
    const meter = Array.isArray(dashboard.currencyMeter) ? dashboard.currencyMeter : [];
    const surprises = Array.isArray(dashboard.surprises) ? dashboard.surprises : [];
    const yieldDiffs = dashboard.yieldDifferentials || {};
    const yieldRows = Array.isArray(yieldDiffs.rows) ? yieldDiffs.rows : [];
    const yieldPairs = Array.isArray(yieldDiffs.pairs) ? yieldDiffs.pairs : [];
    const colors = ["#f59e0b", "#3b82f6", "#a3e635", "#22c55e", "#f97316", "#ef4444", "#38bdf8", "#eab308"];
    const cbNames = {
        USD: "Fed", EUR: "ECB", GBP: "BoE", JPY: "BoJ",
        AUD: "RBA", CAD: "BoC", CHF: "SNB", NZD: "RBNZ",
    };
    // reverse map: "Fed" -> "USD" etc., used for API calls
    const bankToCurrency = Object.fromEntries(Object.entries(cbNames).map(([ccy, name]) => [name, ccy]));

    let active = null;
    let chart = null;
    let feedsData = [];
    let analysisData = null;
    let feedRefreshTimer = null;

    const esc = (value) => String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");
    const raw = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
    const scoreToPercent = (value) => Math.max(0, Math.min(100, Math.round(((raw(value) + 2.5) / 5) * 100)));
    const tone = (value) => raw(value) >= 0.25 ? "hawkish" : raw(value) <= -0.25 ? "dovish" : "neutral";
    const toneClass = (value) => tone(value) === "hawkish" ? "hawk" : tone(value) === "dovish" ? "dove" : "neutral";
    const signed = (value, suffix = "") => {
        const number = raw(value);
        return `${number >= 0 ? "+" : ""}${number.toFixed(Math.abs(number) >= 10 ? 0 : 2)}${suffix}`;
    };
    const dateLabel = (value) => {
        if (!value) return "Recent";
        const date = new Date(value);
        return Number.isNaN(date.getTime()) ? "Recent" : date.toLocaleDateString([], { month: "short", day: "numeric" });
    };
    const shortDate = (value) => {
        if (!value) return "";
        const d = new Date(value);
        return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString([], { month: "short", day: "numeric" });
    };

    const banks = meter.map((row) => ({
        id: cbNames[row.currency] || row.currency,
        currency: row.currency,
        countryCode: row.countryCode,
        score: raw(row.rawScore),
        scoreText: row.score,
        label: row.label,
        latestDate: row.latestDate,
        metrics: Array.isArray(row.metrics) ? row.metrics : [],
    }));

    const shownBanks = () => active ? banks.filter((bank) => bank.id === active) : banks;

    const metricValue = (bank, slug) => {
        const metric = bank.metrics.find((item) => item.slug === slug || item.label?.toLowerCase() === slug);
        return metric?.values?.[0] || null;
    };

    const policyRows = () => {
        const currencies = new Set(shownBanks().map((bank) => bank.currency));
        return surprises
            .filter((item) => !active || currencies.has(item.currencyCode))
            .slice(0, 8);
    };

    const fxRows = () => {
        const currencies = new Set(shownBanks().map((bank) => bank.currency));
        const spreadRows = yieldRows
            .filter((row) => !active || currencies.has(row.currency))
            .map((row) => ({
                pair: `${row.currency}/${yieldDiffs.baseCurrency || "USD"}`,
                driver: `${row.currency} rates`,
                impact: row.spreadDisplay || signed(row.spreadBp, " bp"),
                note: `${row.label || row.currency} differential versus ${yieldDiffs.baseCurrency || "USD"}`,
            }));
        const pairRows = yieldPairs
            .filter((row) => !active || currencies.has(row.leftCurrency) || currencies.has(row.rightCurrency))
            .slice(0, 8)
            .map((row) => ({
                pair: row.name || row.label,
                driver: `${row.leftCurrency || ""}-${row.rightCurrency || ""}`,
                impact: row.spreadDisplay || signed(row.spreadBp, " bp"),
                note: "Yield differential series from dashboard rates data",
            }));
        return [...pairRows, ...spreadRows].slice(0, 8);
    };

    // --- Feed fetching ---

    const fetchFeeds = async () => {
        const ccy = active ? bankToCurrency[active] : null;
        const url = ccy ? `/api/cb/feeds?bank=${encodeURIComponent(ccy)}` : "/api/cb/feeds";
        try {
            const res = await fetch(url);
            if (!res.ok) throw new Error("feed error");
            const data = await res.json();
            feedsData = data.feeds || [];
        } catch {
            feedsData = [];
        }
        renderFeeds();
    };

    const fetchAnalysis = async () => {
        const el = root.querySelector("[data-cb-ai]");
        const btn = root.querySelector("[data-cb-analyze]");
        if (!el) return;
        if (btn) btn.disabled = true;
        el.innerHTML = '<div class="cb-ai-loading">Analyzing CB communications…</div>';
        const ccy = active ? bankToCurrency[active] : null;
        const url = ccy ? `/api/cb/analysis?bank=${encodeURIComponent(ccy)}` : "/api/cb/analysis";
        try {
            const res = await fetch(url, { method: "POST" });
            if (!res.ok) {
                const err = await res.json().catch(() => ({}));
                throw new Error(err.detail || "analysis error");
            }
            analysisData = await res.json();
        } catch (e) {
            analysisData = null;
            el.innerHTML = `<div class="black-empty">${esc(e.message || "Analysis failed. Please try again.")}</div>`;
            if (btn) btn.disabled = false;
            return;
        }
        if (btn) btn.disabled = false;
        renderAI();
    };

    const scheduleRefresh = () => {
        if (feedRefreshTimer) clearInterval(feedRefreshTimer);
        feedRefreshTimer = setInterval(fetchFeeds, 15 * 60 * 1000);
    };

    // --- Render helpers ---

    const renderFeeds = () => {
        const el = root.querySelector("[data-cb-feeds]");
        if (!el) return;
        const label = root.querySelector("[data-cb-feeds-label]");
        if (label) label.textContent = active || "All banks";

        const articles = feedsData
            .flatMap((feed) => (feed.articles || []).map((a) => ({ ...a, bankName: feed.name, currency: feed.currency })))
            .slice(0, 12);

        if (!articles.length) {
            el.innerHTML = '<div class="black-empty">No feed articles available.</div>';
            return;
        }
        el.innerHTML = articles.map((a) => {
            const ds = shortDate(a.pubDate);
            return `<div class="cb-feed-item">
                <a href="${esc(a.link)}" target="_blank" rel="noopener noreferrer">${esc(a.title)}</a>
                <div class="cb-feed-item__meta">
                    <span class="cb-feed-item__tag">${esc(a.bankName || a.currency)}</span>
                    ${ds ? `<span>${esc(ds)}</span>` : ""}
                    ${a.description ? `<span class="cb-feed-item__desc">${esc(a.description.slice(0, 90))}…</span>` : ""}
                </div>
            </div>`;
        }).join("");
    };

    const renderAI = () => {
        const el = root.querySelector("[data-cb-ai]");
        if (!el) return;
        if (!analysisData) {
            el.innerHTML = '<div class="black-empty">Click Analyze to generate AI insights.</div>';
            return;
        }
        const sc = analysisData.stance === "hawkish" ? "hawkish" : analysisData.stance === "dovish" ? "dovish" : "neutral";
        const biasMap = { cut: "↓ Cut", hold: "⊙ Hold", hike: "↑ Hike" };
        const bias = biasMap[analysisData.rate_bias] || analysisData.rate_bias || "";
        const ts = analysisData.generated_at
            ? new Date(analysisData.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
            : "";
        el.innerHTML = `<div class="cb-ai-box">
            <div class="cb-ai-stance ${sc}">${esc(analysisData.stance)}${bias ? ` \xb7 ${esc(bias)}` : ""} \xb7 ${analysisData.confidence || 0}% confidence</div>
            <p class="cb-ai-summary">${esc(analysisData.summary)}</p>
            ${analysisData.fx_implication ? `<div class="cb-ai-fx">${esc(analysisData.fx_implication)}</div>` : ""}
            ${analysisData.key_themes?.length ? `<div class="cb-ai-themes">${analysisData.key_themes.map((t) => `<span class="cb-ai-theme">${esc(t)}</span>`).join("")}</div>` : ""}
            ${analysisData.risk_factors?.length ? `<div class="cb-ai-risks">Risks: ${esc(analysisData.risk_factors.join(" \xb7 "))}</div>` : ""}
            ${ts ? `<span class="cb-ai-ts">Generated ${ts}</span>` : ""}
        </div>`;
    };

    // --- Main render ---

    const render = () => {
        if (!banks.length) {
            root.querySelector("[data-cb-tabs]").innerHTML = "";
            root.querySelector("[data-cb-sentiment]").innerHTML = '<div class="black-empty">Central-bank score data has not been built yet.</div>';
            root.querySelector("[data-cb-news]").innerHTML = '<div class="black-empty">No recent surprises available.</div>';
            root.querySelector("[data-cb-timeline]").innerHTML = '<div class="black-empty">No dashboard events available.</div>';
            root.querySelector("[data-cb-fx]").innerHTML = '<div class="black-empty">No rates differential data available.</div>';
            window.__macroComponents.cbTerminal = "loaded";
            return;
        }

        root.querySelector("[data-cb-tabs]").innerHTML =
            `<button class="${!active ? "is-active" : ""}" type="button" data-bank="">All</button>` +
            banks.map((bank) => `<button class="${active === bank.id ? "is-active " : ""}${toneClass(bank.score)}" type="button" data-bank="${esc(bank.id)}">${esc(bank.id)}</button>`).join("");

        const avg = shownBanks().reduce((sum, bank) => sum + scoreToPercent(bank.score), 0) / shownBanks().length;
        root.querySelector("[data-cb-spectrum-fill]").style.width = `${Math.round(avg)}%`;
        root.querySelector("[data-cb-active-label]").textContent = active || "All banks";
        root.querySelector("[data-cb-news-label]").textContent = active || "Recent surprises";
        root.querySelector("[data-cb-fx-label]").textContent = active || "Rates linked";

        root.querySelector("[data-cb-sentiment]").innerHTML = shownBanks().map((bank) => {
            const inflation = metricValue(bank, "inflation");
            const growth = metricValue(bank, "growth");
            const labor = metricValue(bank, "labor");
            return `<div class="cb-row">
                <strong>${esc(bank.id)}</strong>
                <span class="cb-badge ${toneClass(bank.score)}">${esc(tone(bank.score))}</span>
                <b><i style="width:${scoreToPercent(bank.score)}%"></i></b>
                <em>${esc(bank.scoreText)}</em>
                <small>INF ${esc(inflation?.score || "N/A")} / GRW ${esc(growth?.score || "N/A")} / LAB ${esc(labor?.score || "N/A")}</small>
            </div>`;
        }).join("");

        const events = policyRows();
        root.querySelector("[data-cb-news]").innerHTML = events.length ? events.map((item) => `
            <div class="cb-news-item">
                <span>${esc(item.currencyCode || item.countryCode)} <i class="cb-badge ${toneClass(item.surprise)}">${esc(dateLabel(item.releasedAt))}</i></span>
                <strong>${esc(item.displayName)}</strong>
                <small>Actual ${esc(item.actual ?? "N/A")} / Est ${esc(item.estimate ?? "N/A")} / Surprise ${esc(item.surprise ?? "N/A")} ${esc(item.unit || "")}</small>
            </div>
        `).join("") : '<div class="black-empty">No recent surprises matched this bank.</div>';

        root.querySelector("[data-cb-timeline]").innerHTML = shownBanks().map((bank) => `
            <div class="cb-line">
                <strong>${esc(bank.id)}</strong>
                <span>${esc(bank.label)}</span>
                <em>${esc(bank.scoreText)}</em>
                <small>Latest dashboard score: ${esc(dateLabel(bank.latestDate))}</small>
            </div>
        `).join("");

        const impacts = fxRows();
        root.querySelector("[data-cb-fx]").innerHTML = impacts.length ? impacts.map((row) => `
            <div class="cb-line">
                <strong>${esc(row.pair)}</strong>
                <span>${esc(row.impact)}</span>
                <em>${esc(row.driver)}</em>
                <small>${esc(row.note)}</small>
            </div>
        `).join("") : '<div class="black-empty">No rates differential rows matched this bank.</div>';

        drawTrend();
        fetchFeeds();
    };

    const drawTrend = () => {
        const canvas = root.querySelector("[data-cb-trend-chart]");
        if (!window.Chart || !canvas) return;
        if (chart) chart.destroy();
        const selected = shownBanks();
        const labels = ["6M Ago", "3M Ago", "1M Ago", "Current"];
        chart = new Chart(canvas, {
            type: "line",
            data: {
                labels,
                datasets: selected.map((bank, index) => {
                    const overall = bank.metrics.find((item) => item.slug === "overall" || item.label === "Overall");
                    const values = overall?.values || [];
                    return {
                        label: bank.id,
                        data: [...values].reverse().map((item) => raw(item.rawScore)),
                        borderColor: colors[index % colors.length],
                        pointRadius: 2,
                        tension: 0.3,
                    };
                }),
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: { legend: { labels: { color: "#888888", boxWidth: 10 } } },
                scales: {
                    x: { ticks: { color: "#888888" }, grid: { color: "rgba(255,255,255,.06)" } },
                    y: { ticks: { color: "#888888" }, grid: { color: "rgba(255,255,255,.06)" } },
                },
            },
        });
    };

    root.addEventListener("click", (event) => {
        const bankBtn = event.target.closest("[data-bank]");
        if (bankBtn) {
            active = bankBtn.dataset.bank || null;
            analysisData = null;
            render();
            return;
        }
        if (event.target.closest("[data-cb-analyze]")) {
            fetchAnalysis();
        }
    });

    render();
    scheduleRefresh();
    window.__macroComponents.cbTerminal = "loaded";
})();
