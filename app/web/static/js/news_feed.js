(() => {
    const REFRESH_MS = 90000;
    const sentimentCache = new Map();
    let articles = [];
    let activeCategory = "All";
    let selectedUrl = "";

    const section = document.querySelector("[data-news-feed-section]");
    if (!section) return;

    const listEl    = section.querySelector("[data-news-list]");
    const filterEl  = section.querySelector("[data-news-filter]");
    const stateEl   = document.querySelector("[data-news-refresh-state]");
    const sentimentBody = section.querySelector("[data-sentiment-body]");

    const esc = (v) => String(v || "")
        .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;").replaceAll('"', "&quot;").replaceAll("'", "&#039;");

    const relTime = (d) => {
        const date = new Date(d);
        if (Number.isNaN(date.getTime())) return "recent";
        const s = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
        if (s < 60) return "now";
        const m = Math.floor(s / 60);
        if (m < 60) return `${m} min ago`;
        const h = Math.floor(m / 60);
        if (h < 24) return `${h} hr ago`;
        return `${Math.floor(h / 24)} d ago`;
    };

    const catClass = (c) => {
        if (c === "FX") return "fx";
        if (c === "Equities") return "equities";
        if (c === "Central banks") return "central-banks";
        if (c === "Geopolitical") return "geopolitical";
        return "macro";
    };

    const classifyTheme = (text) => {
        const t = text.toLowerCase();
        if (/war|sanction|conflict|military|nato|\bun\b|treaty|tariff|trade war|election|government|ministry|president|prime minister|diplomacy|geopolit|invasion|coup|protest|border|embargo/.test(t)) return "geopolitical";
        if (/\bfed\b|ecb|boj|boe|rba|rbnz|snb|central.bank|rate.decision|interest.rate|monetary.policy|powell|lagarde|inflation|taper|\bqe\b|\bqt\b/.test(t)) return "central-banks";
        if (/\beur\b|\bgbp\b|\bjpy\b|\baud\b|\bcad\b|\bchf\b|\bnzd\b|forex|currency|\busd\b|dollar|pound|yen|euro/.test(t)) return "fx";
        if (/stock|shares|earnings|s&p|nasdaq|dow|ftse|dax|\bipo\b|dividend|equity|market.cap/.test(t)) return "equities";
        return "macro";
    };

    const fetchFeed = async () => {
        if (stateEl) stateEl.textContent = "refreshing";
        try {
            const resp = await fetch("/api/news/feed?limit=40", { cache: "no-store" });
            if (resp.ok) {
                const data = await resp.json();
                articles = Array.isArray(data.articles)
                    ? data.articles.filter((a) => a.link).slice(0, 40)
                    : [];
                if (stateEl) stateEl.textContent = data.source || "intelligence_monitor";
                renderList();
                if (!selectedUrl && articles.length) selectArticle(articles[0]);
                return;
            }
        } catch (e) { throw new Error(e.message || "Feed unavailable"); }
        throw new Error("Feed unavailable");
    };

    const renderList = () => {
        const visible = activeCategory === "All"
            ? articles
            : articles.filter((a) => a.category === activeCategory);
        if (!visible.length) {
            listEl.innerHTML = '<div class="intel-empty">No headlines matched this filter.</div>';
            return;
        }
        listEl.innerHTML = visible.map((a) => `
            <button class="intel-item ${a.link === selectedUrl ? "is-selected" : ""}" type="button" data-url="${esc(a.link)}">
                <div class="intel-item__row">
                    <i class="intel-pill intel-pill--${catClass(a.category)}">${esc(a.category)}</i>
                    <span class="intel-item__time">${esc(relTime(a.pubDate))}</span>
                </div>
                <div class="intel-item__title">${esc(a.title)}</div>
                <span class="intel-item__src">${esc(a.source)}</span>
            </button>
        `).join("");
    };

    const selectArticle = (article) => {
        selectedUrl = article.link;
        renderList();
        if (sentimentCache.has(article.link)) {
            renderSentiment(sentimentCache.get(article.link), article);
            return;
        }
        analyzeArticle(article);
    };

    const analyzeArticle = async (article) => {
        renderSkeleton(article);
        try {
            const res = await fetch("/api/news/sentiment", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ headline: article.title, description: article.description }),
            });
            if (!res.ok) {
                const p = await res.json().catch(() => ({}));
                throw new Error(p.detail || "Analysis failed");
            }
            const result = await res.json();
            sentimentCache.set(article.link, result);
            renderSentiment(result, article);
        } catch (e) {
            renderError(article, e);
        }
    };

    const sentCls = (s) => s === "bullish" ? "is-positive" : s === "bearish" ? "is-negative" : "is-neutral";
    const sentLabel = (s) => s.charAt(0).toUpperCase() + s.slice(1);

    const barRow = (label, value, cls) => {
        const pct = Math.max(0, Math.min(100, Number(value || 0)));
        return `<div class="intel-bar-row">
            <span>${label}</span>
            <div class="intel-bar-track"><div class="intel-bar-fill ${cls}" style="width:${pct}%"></div></div>
            <em>${pct}%</em>
        </div>`;
    };

    const assetRow = (a) => {
        const impact = Math.max(0, Math.min(100, Number(a.impact || 0)));
        const cls = impact >= 70 ? "is-negative" : impact >= 45 ? "is-neutral" : "is-positive";
        return `<div class="intel-asset-row">
            <strong>${esc(a.symbol || "MKT")}</strong>
            <div class="intel-bar-track"><div class="intel-bar-fill ${cls}" style="width:${impact}%"></div></div>
            <em>${esc(a.label || "~ Mixed")}</em>
        </div>`;
    };

    const renderSkeleton = (article) => {
        sentimentBody.innerHTML = `
            <div class="intel-sel">
                <div class="intel-sel__label">Analyzing</div>
                <div class="intel-sel__title">${esc(article.title)}</div>
            </div>
            <div class="intel-skeleton"><i></i><i></i><i></i><i></i></div>`;
    };

    const renderError = (article, err) => {
        sentimentBody.innerHTML = `
            <div class="intel-sel">
                <div class="intel-sel__label">Analysis failed</div>
                <div class="intel-sel__title">${esc(article.title)}</div>
            </div>
            <p class="intel-error">${esc(err.message || "Unable to analyze this headline.")}</p>
            <button class="intel-retry" type="button" data-retry-url="${esc(article.link)}">Retry analysis</button>`;
    };

    const renderSentiment = (data, article) => {
        const sentiment = String(data.sentiment || "neutral").toLowerCase();
        const cls = sentCls(sentiment);
        sentimentBody.innerHTML = `
            <div class="intel-sel">
                <div class="intel-sel__label">Selected headline</div>
                <div class="intel-sel__title">${esc(article.title)}</div>
            </div>
            <div class="intel-verdict">
                <span class="intel-verdict__badge ${cls}">${esc(sentLabel(sentiment))}</span>
                <span class="intel-verdict__conf">${Number(data.confidence || 0)}% confidence</span>
            </div>
            <div class="intel-bars">
                ${barRow("Bearish", data.bearish_pct, "is-negative")}
                ${barRow("Neutral", data.neutral_pct, "is-neutral")}
                ${barRow("Bullish", data.bullish_pct, "is-positive")}
            </div>
            <div class="intel-summary">${esc(data.summary || "No summary returned.")}</div>
            <div class="intel-section-label">Assets affected</div>
            <div class="intel-assets">
                ${(data.assets || []).length
                    ? data.assets.map(assetRow).join("")
                    : '<div style="font-size:11px;color:var(--text-muted);font-family:var(--mono)">No specific assets identified.</div>'}
            </div>
            <div class="intel-section-label">Key themes</div>
            <div class="intel-themes">
                ${(data.themes || []).map((t) => `<i class="intel-pill intel-pill--${classifyTheme(t)}">${esc(t)}</i>`).join("") || '<span style="font-size:11px;color:var(--text-muted)">No themes returned.</span>'}
            </div>`;
    };

    filterEl?.addEventListener("click", (evt) => {
        const btn = evt.target.closest("[data-category]");
        if (!btn) return;
        activeCategory = btn.dataset.category || "All";
        filterEl.querySelectorAll("button").forEach((b) => b.classList.remove("is-active"));
        btn.classList.add("is-active");
        renderList();
    });

    listEl?.addEventListener("click", (evt) => {
        const item = evt.target.closest("[data-url]");
        if (!item) return;
        const article = articles.find((a) => a.link === item.dataset.url);
        if (article) selectArticle(article);
    });

    sentimentBody?.addEventListener("click", (evt) => {
        const retry = evt.target.closest("[data-retry-url]");
        if (!retry) return;
        const article = articles.find((a) => a.link === retry.dataset.retryUrl);
        if (article) analyzeArticle(article);
    });

    fetchFeed().catch((err) => {
        if (stateEl) stateEl.textContent = "feed error";
        listEl.innerHTML = `<div class="intel-empty">${esc(err.message || "Unable to load intelligence monitor feed.")}</div>`;
    });
    window.setInterval(() => {
        fetchFeed().catch(() => { if (stateEl) stateEl.textContent = "feed error"; });
    }, REFRESH_MS);
})();
