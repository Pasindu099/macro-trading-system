(() => {
    // ── constants ────────────────────────────────────────────────────────────
    const BANK_NAMES  = { USD:"Fed",  EUR:"ECB",  GBP:"BoE", JPY:"BoJ", AUD:"RBA", CAD:"BoC", CHF:"SNB",  NZD:"RBNZ" };
    const BANK_FLAGS  = { USD:"🇺🇸", EUR:"🇪🇺", GBP:"🇬🇧", JPY:"🇯🇵", AUD:"🇦🇺", CAD:"🇨🇦", CHF:"🇨🇭", NZD:"🇳🇿" };
    const BANK_COLORS = { USD:"#E8A020", EUR:"#378ADD", GBP:"#9FE1CB", JPY:"#FAC775", AUD:"#EF9F27", CAD:"#85B7EB", CHF:"#5DCAA5", NZD:"#F09595" };

    // ── data ─────────────────────────────────────────────────────────────────
    const cbData     = window.__cbData || {};
    const meter      = Array.isArray(cbData.currencyMeter) ? cbData.currencyMeter : [];
    const surprises  = Array.isArray(cbData.surprises)     ? cbData.surprises     : [];
    const yieldDiffs = cbData.yieldDifferentials           || {};
    const yieldRows  = Array.isArray(yieldDiffs.rows)      ? yieldDiffs.rows      : [];
    const yieldPairs = Array.isArray(yieldDiffs.pairs)     ? yieldDiffs.pairs     : [];

    // ── DOM refs ──────────────────────────────────────────────────────────────
    const section = document.querySelector("[data-cbn-section]");
    if (!section) return;
    const q = (sel) => section.querySelector(sel);

    // ── state ─────────────────────────────────────────────────────────────────
    let activeBank  = null;   // currency code e.g. "USD"
    let feedData    = [];     // [{currency, name, articles:[...]}]
    let feedSearch  = "";
    let selectedLink = null;
    let trendChart  = null;

    // ── helpers ────────────────────────────────────────────────────────────────
    const esc = (v) => String(v || "")
        .replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;");

    const raw = (v) => Number.isFinite(Number(v)) ? Number(v) : 0;

    const tone = (rawScore) => {
        const n = raw(rawScore);
        return n >= 0.25 ? "hawkish" : n <= -0.25 ? "dovish" : "neutral";
    };

    const toneClass = (t) => t === "hawkish" ? "hawk" : t === "dovish" ? "dove" : "neutral";

    const toneColor = (t) => t === "hawkish" ? "#E8A020" : t === "dovish" ? "#1DB88A" : "#6B7280";

    const scoreNorm = (rawScore) =>
        Math.max(0, Math.min(100, Math.round(((raw(rawScore) + 2.5) / 5) * 100)));

    const relTime = (v) => {
        const d = new Date(v);
        if (isNaN(d.getTime())) return "";
        const s = Math.max(0, Math.floor((Date.now() - d.getTime()) / 1000));
        if (s < 60)    return "just now";
        if (s < 3600)  return `${Math.floor(s / 60)}m ago`;
        if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
        return d.toLocaleDateString([], { month: "short", day: "numeric" });
    };

    const shortDate = (v) => {
        const d = new Date(v);
        return isNaN(d.getTime()) ? "" : d.toLocaleDateString([], { month: "short", day: "numeric" });
    };

    const signed = (v, suffix = "") => {
        const n = raw(v);
        return `${n >= 0 ? "+" : ""}${n.toFixed(0)}${suffix}`;
    };

    // ── bank objects (from real backend data) ─────────────────────────────────
    const banks = meter.map((row) => ({
        currency:     row.currency,
        name:         BANK_NAMES[row.currency]  || row.currency,
        flag:         BANK_FLAGS[row.currency]  || "",
        color:        BANK_COLORS[row.currency] || "#888",
        sentiment:    tone(row.rawScore),
        score:        scoreNorm(row.rawScore) / 100,
        scorePercent: scoreNorm(row.rawScore),
        label:        row.label || "",
        metrics:      Array.isArray(row.metrics) ? row.metrics : [],
    }));

    const shown = () => activeBank ? banks.filter((b) => b.currency === activeBank) : banks;

    // ── render: bank tabs ────────────────────────────────────────────────────
    const renderTabs = () => {
        const el = q("[data-cbn-tabs]");
        if (!el) return;
        el.innerHTML =
            `<button class="cbn-tab${!activeBank ? " is-active" : ""}" type="button" data-cbn-tab="">All</button>` +
            banks.map((b) => {
                const tc = toneClass(b.sentiment);
                const active = activeBank === b.currency;
                return `<button class="cbn-tab${active ? " is-active" : ""} cbn-tab--${tc}" type="button" data-cbn-tab="${esc(b.currency)}">${esc(b.flag)} ${esc(b.name)}</button>`;
            }).join("");
    };

    // ── render: spectrum dots ────────────────────────────────────────────────
    const renderSpectrum = () => {
        const el = q("[data-cbn-dots]");
        if (!el) return;
        el.innerHTML = banks.map((b) => {
            const col = b.color;
            return `<div class="cbn-dot" style="left:${b.scorePercent}%;background:${col}" title="${esc(b.name)}: ${b.scorePercent}" data-cbn-tab="${esc(b.currency)}">
                <div class="cbn-dot-label" style="color:${col}">${esc(b.flag)}</div>
            </div>`;
        }).join("");
    };

    // ── render: sentiment rows ────────────────────────────────────────────────
    const renderSentiment = () => {
        const el = q("[data-cbn-sentiment]");
        const lbl = q("[data-cbn-bank-label]");
        if (!el) return;
        if (lbl) lbl.textContent = activeBank ? (BANK_NAMES[activeBank] || activeBank) : "All banks";

        const list = shown();
        if (!list.length) { el.innerHTML = '<div class="cbn-empty">No scoring data available.</div>'; return; }

        el.innerHTML = list.map((b) => {
            const tc   = toneClass(b.sentiment);
            const col  = toneColor(b.sentiment);
            const pct  = b.scorePercent;
            const inf  = b.metrics.find((m) => m.slug === "inflation");
            const grw  = b.metrics.find((m) => m.slug === "growth");
            const lab  = b.metrics.find((m) => m.slug === "labor");
            const infS = inf?.values?.[0]?.score ?? "N/A";
            const grwS = grw?.values?.[0]?.score ?? "N/A";
            const labS = lab?.values?.[0]?.score ?? "N/A";
            return `<div class="cbn-sent-row">
                <span class="cbn-sent-flag">${esc(b.flag)}</span>
                <span class="cbn-sent-name">${esc(b.name)}</span>
                <span class="cbn-sent-badge cbn-badge--${tc}">${esc(b.sentiment)}</span>
                <div class="cbn-sent-meter"><div class="cbn-sent-fill" style="width:${pct}%;background:${col}"></div></div>
                <span class="cbn-sent-score" style="color:${col}">${pct}</span>
                <span class="cbn-sent-sub">INF&nbsp;${esc(infS)}&nbsp;GRW&nbsp;${esc(grwS)}&nbsp;LAB&nbsp;${esc(labS)}</span>
            </div>`;
        }).join("");
    };

    // ── render: live CB news (from RSS feeds) ────────────────────────────────
    const renderNews = () => {
        const el  = q("[data-cbn-news]");
        const lbl = q("[data-cbn-news-status]");
        if (!el) return;

        const allArticles = feedData.flatMap((f) =>
            (f.articles || []).map((a) => ({ ...a, bankName: f.name, currency: f.currency }))
        );

        let items = allArticles;
        if (activeBank) items = items.filter((a) => a.currency === activeBank);
        if (feedSearch) {
            const q2 = feedSearch.toLowerCase();
            items = items.filter((a) =>
                a.title.toLowerCase().includes(q2) || (a.description || "").toLowerCase().includes(q2)
            );
        }

        if (lbl) {
            if (feedData.length === 0) {
                lbl.textContent = "loading…";
            } else {
                const loaded = feedData.filter((f) => f.articles?.length > 0).length;
                lbl.textContent = `${allArticles.length} articles · ${loaded}/8`;
            }
        }

        if (!items.length) {
            const errors = feedData
                .filter((f) => f.error)
                .map((f) => `${f.name || f.currency}: ${f.error}`)
                .slice(0, 3);
            el.innerHTML = feedData.length
                ? '<div class="cbn-empty">No articles match filter.</div>'
                : '<div class="cbn-empty">Fetching live feeds...</div>';
            if (feedData.length && errors.length && allArticles.length === 0) {
                el.innerHTML = `<div class="cbn-empty">No CB feed articles loaded. ${esc(errors.join(" | "))}</div>`;
            }
            return;
        }

        el.innerHTML = items.slice(0, 20).map((a) => {
            const rt  = relTime(a.pubDate);
            const sel = selectedLink === a.link;
            const speakerTag = a.speaker
                ? `<span class="cbn-news-speaker">🎙 ${esc(a.speaker)}</span>`
                : (a.is_speech ? `<span class="cbn-news-speaker">🎙 Speech</span>` : "");
            return `<button class="cbn-news-item${sel ? " is-selected" : ""}" type="button" data-article-link="${esc(a.link)}">
                <div class="cbn-news-meta">
                    <span class="cbn-news-bank">${esc(a.bankName)}</span>
                    ${speakerTag}
                    ${rt ? `<em>${esc(rt)}</em>` : ""}
                </div>
                <strong>${esc(a.title)}</strong>
                ${a.description ? `<small>${esc(a.description.slice(0, 120))}…</small>` : ""}
                ${a.link ? `<a class="cbn-link" href="${esc(a.link)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">Open ↗</a>` : ""}
            </button>`;
        }).join("");
    };

    // ── render: policy signals (surprises) ───────────────────────────────────
    const renderSignals = () => {
        const el = q("[data-cbn-signals]");
        if (!el) return;

        const ccys = new Set(shown().map((b) => b.currency));
        const items = surprises.filter((s) => !activeBank || ccys.has(s.currencyCode)).slice(0, 8);

        if (!items.length) { el.innerHTML = '<div class="cbn-empty">No recent signals.</div>'; return; }

        el.innerHTML = items.map((s) => {
            const surp = raw(s.surprise);
            const dir  = surp > 0 ? "hawk" : surp < 0 ? "dove" : "neutral";
            const ds   = shortDate(s.releasedAt);
            return `<div class="cbn-tl-row">
                <span class="cbn-tl-date">${esc(ds)}</span>
                <span class="cbn-tl-bank">${esc(s.currencyCode)}</span>
                <span class="cbn-tl-dec cbn-dec--${dir}">${surp >= 0 ? "+" : ""}${surp.toFixed(2)}</span>
                <span class="cbn-tl-name">${esc(s.displayName)}</span>
                <span class="cbn-tl-note">${esc(s.actual ?? "?")} vs est ${esc(s.estimate ?? "?")} ${esc(s.unit || "")}</span>
            </div>`;
        }).join("");
    };

    // ── render: FX impact (yield spreads) ───────────────────────────────────
    const renderFX = () => {
        const el  = q("[data-cbn-fx]");
        const lbl = q("[data-cbn-fx-label]");
        if (!el) return;
        if (lbl) lbl.textContent = activeBank ? `${BANK_NAMES[activeBank] || activeBank} pairs` : "vs USD";

        const ccys = new Set(shown().map((b) => b.currency));
        const rows  = [...yieldPairs, ...yieldRows.map((r) => ({
            label: `${r.currency}/${yieldDiffs.baseCurrency || "USD"}`,
            leftCurrency: r.currency, rightCurrency: yieldDiffs.baseCurrency || "USD",
            spreadBp: r.spreadBp, spreadDisplay: r.spreadDisplay,
        }))].filter((r) => !activeBank || ccys.has(r.leftCurrency) || ccys.has(r.rightCurrency)).slice(0, 8);

        if (!rows.length) { el.innerHTML = '<div class="cbn-empty">No yield spread data.</div>'; return; }

        el.innerHTML = rows.map((r) => {
            const bp  = raw(r.spreadBp);
            const dir = bp > 5 ? "up" : bp < -5 ? "dn" : "";
            const arr = bp > 5 ? "↑" : bp < -5 ? "↓" : "→";
            return `<div class="cbn-fx-row">
                <span class="cbn-fx-pair">${esc(r.label)}</span>
                <span class="cbn-fx-arr cbn-${dir}">${arr}</span>
                <span class="cbn-fx-spread cbn-${dir}">${esc(r.spreadDisplay || signed(bp, " bp"))}</span>
                <span class="cbn-fx-note">${esc(r.leftCurrency || "")} vs ${esc(r.rightCurrency || "")}&nbsp;yield differential</span>
            </div>`;
        }).join("");
    };

    // ── render: historical trend chart ───────────────────────────────────────
    const renderTrend = () => {
        const canvas = q("[data-cbn-chart]");
        const legend = q("[data-cbn-legend]");
        if (!window.Chart || !canvas) return;
        if (trendChart) trendChart.destroy();

        const list = shown();
        const labels = ["6M Ago", "3M Ago", "1M Ago", "Current"];

        if (legend) {
            legend.innerHTML = list.map((b) =>
                `<span class="cbn-legend-item"><i style="background:${b.color}"></i>${esc(b.name)}</span>`
            ).join("");
        }

        const datasets = list.map((b) => {
            const overall = b.metrics.find((m) => m.slug === "overall" || m.label === "Overall");
            const vals    = overall?.values || [];
            return {
                label:           b.name,
                data:            [...vals].reverse().map((v) => scoreNorm(v.rawScore) / 100),
                borderColor:     b.color,
                backgroundColor: "transparent",
                borderWidth:     1.5,
                pointRadius:     2,
                pointBackgroundColor: b.color,
                tension:         0.3,
            };
        });

        trendChart = new Chart(canvas, {
            type: "line",
            data: { labels, datasets },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: "#1a1d24",
                        borderColor: "#2b3340",
                        borderWidth: 1,
                        titleColor: "#8892a0",
                        bodyColor: "#e8eaf0",
                        callbacks: { label: (ctx) => `${ctx.dataset.label}: ${(ctx.parsed.y * 100).toFixed(0)}` },
                    },
                },
                scales: {
                    x: { grid: { color: "#1e2022" }, ticks: { color: "#5C6168", font: { size: 9 } } },
                    y: {
                        min: 0, max: 1,
                        grid: { color: "#1e2022" },
                        ticks: {
                            color: "#5C6168", font: { size: 9 }, stepSize: 0.25,
                            callback: (v) => v === 0 ? "Dove" : v === 0.5 ? "Neut" : v === 1 ? "Hawk" : "",
                        },
                    },
                },
            },
        });
    };

    // ── AI: article sentiment ─────────────────────────────────────────────────
    const analyzeSentiment = async (article) => {
        const out = q("[data-cbn-ai-out]");
        if (!out) return;
        out.innerHTML = '<div class="cbn-ai-load">Analyzing article…</div>';
        try {
            const res = await fetch("/api/news/sentiment", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ headline: article.title, description: article.description || "" }),
            });
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const d = await res.json();
            const st  = d.sentiment || "neutral";
            const stc = st === "bullish" ? "hawk" : st === "bearish" ? "dove" : "neutral";
            out.innerHTML = `<div class="cbn-ai-box">
                <div class="cbn-ai-source">Article sentiment — ${esc(article.bankName)}</div>
                <span class="cbn-ai-stance cbn-badge--${stc}">${esc(st)} · ${d.confidence || 0}%</span>
                <p class="cbn-ai-summary">${esc(d.summary)}</p>
                ${d.themes?.length ? `<div class="cbn-ai-tags">${d.themes.map((t) => `<span class="cbn-ai-tag">${esc(t)}</span>`).join("")}</div>` : ""}
                ${d.assets?.length ? `<div class="cbn-ai-assets">${d.assets.slice(0,5).map((a) => {
                    const cls = a.direction === "up" ? "cbn-up" : a.direction === "down" ? "cbn-dn" : "";
                    return `<div class="cbn-asset"><strong>${esc(a.symbol)}</strong><span class="${cls}">${esc(a.label)}</span></div>`;
                }).join("")}</div>` : ""}
            </div>`;
        } catch (e) {
            out.innerHTML = `<div class="cbn-empty">Sentiment failed: ${esc(e.message)}</div>`;
        }
    };

    // ── AI: bank stance analysis ──────────────────────────────────────────────
    const analyzeStance = async () => {
        const out = q("[data-cbn-ai-out]");
        const btn = q("[data-cbn-analyze]");
        if (!out) return;
        if (btn) btn.disabled = true;
        const label = activeBank ? (BANK_NAMES[activeBank] || activeBank) : "G8 Central Banks";
        out.innerHTML = '<div class="cbn-ai-load">Generating stance analysis…</div>';
        const url = activeBank ? `/api/cb/analysis?bank=${encodeURIComponent(activeBank)}` : "/api/cb/analysis";
        try {
            const res = await fetch(url, { method: "POST" });
            if (!res.ok) { const e = await res.json().catch(() => ({})); throw new Error(e.detail || `HTTP ${res.status}`); }
            const d = await res.json();
            const sc = d.stance === "hawkish" ? "hawk" : d.stance === "dovish" ? "dove" : "neutral";
            const biasMap = { cut: "↓ Cut", hold: "⊙ Hold", hike: "↑ Hike" };
            const bias = biasMap[d.rate_bias] || d.rate_bias || "";
            const ts   = d.generated_at ? new Date(d.generated_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "";
            out.innerHTML = `<div class="cbn-ai-box">
                <div class="cbn-ai-source">Stance analysis — ${esc(label)}</div>
                <span class="cbn-ai-stance cbn-badge--${sc}">${esc(d.stance)}${bias ? ` · ${esc(bias)}` : ""} · ${d.confidence || 0}%</span>
                <p class="cbn-ai-summary">${esc(d.summary)}</p>
                ${d.fx_implication ? `<div class="cbn-ai-fx">${esc(d.fx_implication)}</div>` : ""}
                ${d.key_themes?.length ? `<div class="cbn-ai-tags">${d.key_themes.map((t) => `<span class="cbn-ai-tag">${esc(t)}</span>`).join("")}</div>` : ""}
                ${d.risk_factors?.length ? `<div class="cbn-ai-risks">Risks: ${esc(d.risk_factors.join(" · "))}</div>` : ""}
                ${ts ? `<span class="cbn-ai-ts">Generated ${ts}</span>` : ""}
            </div>`;
        } catch (e) {
            out.innerHTML = `<div class="cbn-empty">${esc(e.message || "Analysis failed.")}</div>`;
        }
        if (btn) btn.disabled = false;
    };

    // ── fetch live feeds ──────────────────────────────────────────────────────
    const fetchFeeds = async () => {
        try {
            const res = await fetch("/api/cb/feeds");
            if (!res.ok) throw new Error(`HTTP ${res.status}`);
            const data = await res.json();
            feedData = data.feeds || [];
        } catch {
            feedData = [];
        }
        renderNews();
        const ts = q("[data-cbn-ts]");
        if (ts) ts.textContent = "Updated " + new Date().toLocaleTimeString();
    };

    // ── full re-render ────────────────────────────────────────────────────────
    const render = () => {
        renderTabs();
        renderSpectrum();
        renderSentiment();
        renderNews();
        renderSignals();
        renderFX();
        renderTrend();
        const btn = q("[data-cbn-analyze]");
        if (btn) btn.textContent = `AI Stance Analysis — ${activeBank ? (BANK_NAMES[activeBank] || activeBank) : "All"} →`;
    };

    // ── event wiring ─────────────────────────────────────────────────────────
    section.addEventListener("click", (e) => {
        // bank tab / dot click
        const tabBtn = e.target.closest("[data-cbn-tab]");
        if (tabBtn) {
            activeBank = tabBtn.dataset.cbnTab || null;
            render();
            return;
        }
        // article click → sentiment
        const articleBtn = e.target.closest("[data-article-link]");
        if (articleBtn) {
            const link = articleBtn.dataset.articleLink;
            const allA = feedData.flatMap((f) => (f.articles || []).map((a) => ({ ...a, bankName: f.name, currency: f.currency })));
            const art  = allA.find((a) => a.link === link);
            if (!art) return;
            selectedLink = link;
            renderNews();
            analyzeSentiment(art);
            return;
        }
        // analyze button
        if (e.target.closest("[data-cbn-analyze]")) { analyzeStance(); return; }
        // refresh button
        if (e.target.closest("[data-cbn-refresh]")) { fetchFeeds(); return; }
    });

    q("[data-cbn-search]")?.addEventListener("input", (e) => {
        feedSearch = e.target.value.trim();
        renderNews();
    });

    // ── boot ──────────────────────────────────────────────────────────────────
    render();
    fetchFeeds();
    setInterval(fetchFeeds, 10 * 60 * 1000);
})();
