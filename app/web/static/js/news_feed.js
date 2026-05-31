(() => {
    const REFRESH_MS = 90000;
    const sentimentCache = new Map();
    let articles = [];
    let activeCategory = "All";
    let selectedUrl = "";

    const section = document.querySelector("[data-news-feed-section]");
    if (!section) return;

    const listEl = section.querySelector("[data-news-list]");
    const filterEl = section.querySelector("[data-news-filter]");
    const stateEl = section.querySelector("[data-news-refresh-state]");
    const sentimentBody = section.querySelector("[data-sentiment-body]");

    const escapeHtml = (value) => String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;")
        .replaceAll("'", "&#039;");

    const relativeTime = (dateValue) => {
        const date = new Date(dateValue);
        if (Number.isNaN(date.getTime())) return "recent";
        const diffSeconds = Math.max(0, Math.floor((Date.now() - date.getTime()) / 1000));
        if (diffSeconds < 60) return "now";
        const diffMinutes = Math.floor(diffSeconds / 60);
        if (diffMinutes < 60) return `${diffMinutes} min ago`;
        const diffHours = Math.floor(diffMinutes / 60);
        if (diffHours < 24) return `${diffHours} hr ago`;
        return `${Math.floor(diffHours / 24)} d ago`;
    };

    const fetchFeed = async () => {
        stateEl.textContent = "refreshing";

        try {
            const resp = await fetch("/api/news/feed?limit=40", { cache: "no-store" });
            if (resp.ok) {
                const data = await resp.json();
                articles = Array.isArray(data.articles)
                    ? data.articles.filter((item) => item.link).slice(0, 40)
                    : [];
                stateEl.textContent = data.source || "intelligence_monitor";
                renderNewsList();
                if (!selectedUrl && articles.length) selectArticle(articles[0]);
                return;
            }
        } catch (error) {
            throw new Error(error.message || "News feed unavailable");
        }
        throw new Error("News feed unavailable");
    };

    const categoryClass = (category) => {
        if (category === "FX") return "fx";
        if (category === "Equities") return "equities";
        if (category === "Central banks") return "central-banks";
        if (category === "Geopolitical") return "geopolitical";
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

    const renderNewsList = () => {
        const visible = activeCategory === "All"
            ? articles
            : articles.filter((item) => item.category === activeCategory);
        if (!visible.length) {
            listEl.innerHTML = '<div class="black-empty">No headlines matched this filter.</div>';
            return;
        }
        listEl.innerHTML = visible.map((item) => `
            <button class="news-item ${item.link === selectedUrl ? "is-selected" : ""}" type="button" data-url="${escapeHtml(item.link)}">
                <span class="news-item__meta">
                    <i class="news-pill news-pill--${categoryClass(item.category)}">${escapeHtml(item.category)}</i>
                    <em>${escapeHtml(relativeTime(item.pubDate))}</em>
                </span>
                <strong>${escapeHtml(item.title)}</strong>
                <small>${escapeHtml(item.source)}</small>
            </button>
        `).join("");
    };

    const selectArticle = (article) => {
        selectedUrl = article.link;
        renderNewsList();
        if (sentimentCache.has(article.link)) {
            renderSentiment(sentimentCache.get(article.link), article);
            return;
        }
        analyzeArticle(article);
    };

    const analyzeArticle = async (article) => {
        renderSkeleton(article);
        try {
            const response = await fetch("/api/news/sentiment", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    headline: article.title,
                    description: article.description,
                }),
            });
            if (!response.ok) {
                const payload = await response.json().catch(() => ({}));
                throw new Error(payload.detail || "Sentiment call failed");
            }
            const result = await response.json();
            sentimentCache.set(article.link, result);
            renderSentiment(result, article);
        } catch (error) {
            renderError(article, error);
        }
    };

    const sentimentClass = (sentiment) => {
        if (sentiment === "bullish") return "is-positive";
        if (sentiment === "bearish") return "is-negative";
        return "is-neutral";
    };

    const renderSkeleton = (article) => {
        sentimentBody.innerHTML = `
            <div class="sentiment-selected">
                <span>Analyzing</span>
                <strong>${escapeHtml(article.title)}</strong>
            </div>
            <div class="sentiment-skeleton"><i></i><i></i><i></i><i></i></div>
        `;
    };

    const renderError = (article, error) => {
        sentimentBody.innerHTML = `
            <div class="sentiment-selected">
                <span>Analysis failed</span>
                <strong>${escapeHtml(article.title)}</strong>
            </div>
            <p class="sentiment-error">${escapeHtml(error.message || "Unable to analyze this headline.")}</p>
            <button class="sentiment-retry" type="button" data-retry-url="${escapeHtml(article.link)}">Retry</button>
        `;
    };

    const renderSentiment = (data, article) => {
        const sentiment = String(data.sentiment || "neutral").toLowerCase();
        sentimentBody.innerHTML = `
            <div class="sentiment-selected">
                <span>Selected headline</span>
                <strong>${escapeHtml(article.title)}</strong>
            </div>
            <div class="sentiment-header">
                <span class="sentiment-badge ${sentimentClass(sentiment)}">${escapeHtml(sentimentLabel(sentiment))}</span>
                <strong>${Number(data.confidence || 0)}% confidence</strong>
            </div>
            <div class="sentiment-bars">
                ${barRow("Bearish", data.bearish_pct, "is-negative")}
                ${barRow("Neutral", data.neutral_pct, "is-neutral")}
                ${barRow("Bullish", data.bullish_pct, "is-positive")}
            </div>
            <p class="ai-summary">${escapeHtml(data.summary || "No summary returned.")}</p>
            <section class="asset-impact-list">
                <span>Assets affected</span>
                ${(data.assets || []).length ? data.assets.map(assetRow).join("") : '<small>No specific assets identified.</small>'}
            </section>
            <section class="theme-pills">
                <span>Key themes</span>
                <div>${(data.themes || []).map((theme) => `<i class="news-pill--${classifyTheme(theme)}">${escapeHtml(theme)}</i>`).join("") || "<small>No themes returned.</small>"}</div>
            </section>
        `;
    };

    const sentimentLabel = (sentiment) => sentiment.charAt(0).toUpperCase() + sentiment.slice(1);

    const barRow = (label, value, className) => {
        const pct = Math.max(0, Math.min(100, Number(value || 0)));
        return `
            <div>
                <span>${label}</span>
                <b><i class="${className}" style="width: ${pct}%;"></i></b>
                <em>${pct}%</em>
            </div>
        `;
    };

    const assetRow = (asset) => {
        const impact = Math.max(0, Math.min(100, Number(asset.impact || 0)));
        const impactClass = impact >= 70 ? "is-negative" : impact >= 45 ? "is-warning" : "is-positive";
        return `
            <div class="asset-impact">
                <strong>${escapeHtml(asset.symbol || "Market")}</strong>
                <b><i class="${impactClass}" style="width: ${impact}%;"></i></b>
                <em>${escapeHtml(asset.label || "~ Mixed")}</em>
            </div>
        `;
    };

    filterEl?.addEventListener("click", (event) => {
        const button = event.target.closest("[data-category]");
        if (!button) return;
        activeCategory = button.dataset.category || "All";
        filterEl.querySelectorAll("button").forEach((item) => item.classList.remove("is-active"));
        button.classList.add("is-active");
        renderNewsList();
    });

    listEl?.addEventListener("click", (event) => {
        const item = event.target.closest("[data-url]");
        if (!item) return;
        const article = articles.find((entry) => entry.link === item.dataset.url);
        if (article) selectArticle(article);
    });

    sentimentBody?.addEventListener("click", (event) => {
        const retry = event.target.closest("[data-retry-url]");
        if (!retry) return;
        const article = articles.find((entry) => entry.link === retry.dataset.retryUrl);
        if (article) analyzeArticle(article);
    });

    fetchFeed().catch((error) => {
        stateEl.textContent = "feed error";
        listEl.innerHTML = `<div class="black-empty">${escapeHtml(error.message || "Unable to load intelligence monitor feed.")}</div>`;
    });
    window.setInterval(() => {
        fetchFeed().catch(() => {
            stateEl.textContent = "feed error";
        });
    }, REFRESH_MS);
})();
