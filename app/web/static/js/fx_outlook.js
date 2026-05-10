(() => {
    const root = document.querySelector("[data-fx-outlook]");
    if (!root) return;

    const rowsEl = root.querySelector("[data-fx-outlook-rows]");
    const statusEl = root.querySelector("[data-fx-outlook-status]");
    const pairCountEl = root.querySelector("[data-fx-pair-count]");
    const mostPopularEl = root.querySelector("[data-fx-most-popular]");
    const strongestSkewEl = root.querySelector("[data-fx-strongest-skew]");
    const sourceEl = root.querySelector("[data-myfxbook-source]");

    const clean = (value) => String(value || "").replace(/\s+/g, " ").trim();
    const escapeHtml = (value) => String(value || "")
        .replaceAll("&", "&amp;")
        .replaceAll("<", "&lt;")
        .replaceAll(">", "&gt;")
        .replaceAll('"', "&quot;");

    const symbolFromCell = (cell) => clean(cell?.querySelector("a")?.textContent || cell?.textContent).toUpperCase();

    const extractBar = (cell) => {
        const colored = Array.from(cell?.querySelectorAll("*") || []).filter((node) => {
            const style = window.getComputedStyle(node);
            const color = `${style.backgroundColor} ${style.backgroundImage}`.toLowerCase();
            const width = node.getBoundingClientRect().width;
            return width > 2 && (color.includes("red") || color.includes("green") || color.includes("rgb(2") || color.includes("rgb(1"));
        });

        let bearish = 50;
        let bullish = 50;
        if (colored.length >= 2) {
            const first = colored[0].getBoundingClientRect().width;
            const second = colored[1].getBoundingClientRect().width;
            const total = first + second;
            if (total > 0) {
                bearish = Math.round((first / total) * 100);
                bullish = 100 - bearish;
            }
        }
        return { bearish, bullish };
    };

    const extractPopularity = (cell) => {
        const blocks = Array.from(cell?.querySelectorAll("*") || []);
        const widest = blocks.reduce((max, node) => Math.max(max, node.getBoundingClientRect().width), 0);
        const parent = cell?.getBoundingClientRect().width || 1;
        const percent = Math.max(0, Math.min(100, Math.round((widest / parent) * 100)));
        return Number.isFinite(percent) ? percent : 0;
    };

    const findWidgetTable = () => {
        const tables = Array.from(sourceEl?.querySelectorAll("table") || []);
        return tables.find((table) => /symbol|community trend|current price/i.test(clean(table.textContent)));
    };

    const parseRows = () => {
        const table = findWidgetTable();
        if (!table) return [];
        return Array.from(table.querySelectorAll("tr")).map((row) => {
            const cells = Array.from(row.children);
            if (cells.length < 5) return null;
            const symbol = symbolFromCell(cells[0]);
            if (!/^[A-Z]{3,6}$/.test(symbol)) return null;
            const bar = extractBar(cells[1]);
            const popularity = extractPopularity(cells[2]);
            return {
                symbol,
                bearish: bar.bearish,
                bullish: bar.bullish,
                popularity,
                shortPrice: clean(cells[3]?.textContent) || "-",
                longPrice: clean(cells[4]?.textContent) || "-",
                currentPrice: clean(cells[5]?.textContent) || "-",
            };
        }).filter(Boolean);
    };

    const render = (items) => {
        const sorted = [...items].sort((a, b) => b.popularity - a.popularity);
        rowsEl.innerHTML = sorted.map((item) => {
            const skew = item.bullish - item.bearish;
            const skewClass = skew >= 8 ? "is-bullish" : skew <= -8 ? "is-bearish" : "is-neutral";
            return `
                <tr>
                    <td><strong>${escapeHtml(item.symbol)}</strong></td>
                    <td>
                        <div class="fx-trend ${skewClass}">
                            <span style="width:${item.bearish}%"></span>
                            <i style="width:${item.bullish}%"></i>
                        </div>
                        <small>${item.bearish}% short / ${item.bullish}% long</small>
                    </td>
                    <td>
                        <div class="fx-popularity"><span style="width:${item.popularity}%"></span></div>
                    </td>
                    <td>${escapeHtml(item.shortPrice)}</td>
                    <td>${escapeHtml(item.longPrice)}</td>
                    <td>${escapeHtml(item.currentPrice)}</td>
                </tr>
            `;
        }).join("");

        const strongest = sorted.reduce((best, item) => {
            const skew = Math.abs(item.bullish - item.bearish);
            return !best || skew > best.skew ? { item, skew } : best;
        }, null);

        pairCountEl.textContent = String(items.length);
        mostPopularEl.textContent = sorted[0]?.symbol || "--";
        strongestSkewEl.textContent = strongest ? `${strongest.item.symbol} ${strongest.skew}%` : "--";
        statusEl.textContent = "Myfxbook retail sentiment";
        root.classList.add("is-enhanced");
    };

    let attempts = 0;
    const timer = window.setInterval(() => {
        attempts += 1;
        const items = parseRows();
        if (items.length) {
            window.clearInterval(timer);
            render(items);
        } else if (attempts > 80) {
            window.clearInterval(timer);
            statusEl.textContent = "raw widget fallback";
            rowsEl.innerHTML = '<tr><td colspan="6">Unable to theme the widget automatically. Open the raw widget below.</td></tr>';
        }
    }, 250);
})();
