(function () {
    function fmt(value) {
        if (value === null || value === undefined) return "—";
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
    }

    function fmtSurprise(value) {
        if (value === null || value === undefined) return "—";
        const sign = value >= 0 ? "+" : "";
        return `<span class="${value >= 0 ? "is-positive" : "is-negative"}">${sign}${fmt(value)}</span>`;
    }

    function fmtDate(value) {
        if (!value) return "—";
        return new Date(value).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" });
    }

    function selectState() {
        const shell = document.querySelector("[data-country-code][data-canonical-name]");
        return {
            shell,
            countryCode: shell?.dataset.countryCode,
            canonicalName: shell?.dataset.canonicalName,
            chartType: document.querySelector("#chart-type-control .is-active")?.dataset.chartType || "line",
            rangeKey: document.querySelector("#range-control .is-active")?.dataset.rangeKey || "all",
            revisionMode: document.querySelector("#revision-control .is-active")?.dataset.revisionMode || "latest",
            overlayEstimate: document.getElementById("overlay-estimate")?.checked ?? true,
            overlayTarget: document.getElementById("overlay-target")?.checked ?? true,
            overlayPrevious: document.getElementById("overlay-previous")?.checked ?? false,
        };
    }

    function buildLineSeries(points, state, payload) {
        const series = [
            {
                name: "ACTUAL",
                data: points.map((p) => [p.released_at, p.actual]),
                color: "#f5a623",
            },
        ];
        if (state.overlayEstimate) {
            series.push({
                name: "CONSENSUS",
                data: points.map((p) => [p.released_at, p.estimate]),
                color: "#ffcc00",
            });
        }
        if (state.overlayPrevious) {
            series.push({
                name: "PREVIOUS",
                data: points.map((p) => [p.released_at, p.previous]),
                color: "#555555",
            });
        }
        if (state.overlayTarget && payload.cb_target !== null && payload.cb_target !== undefined) {
            series.push({
                name: "CB TARGET",
                data: points.map((p) => [p.released_at, payload.cb_target]),
                color: "#00cc44",
                dashed: true,
            });
        }
        return series;
    }

    function renderChart(payload, state) {
        const chartTitle = document.getElementById("chart-title");
        const chartMeta = document.getElementById("chart-meta");
        const containerId = "indicator-chart";
        const points = Array.isArray(payload.series) ? payload.series : [];

        chartTitle.textContent = payload.indicator.display_name.toUpperCase();
        chartMeta.textContent = `${payload.total_points} PTS | ${payload.revision_mode.replace("_", " ").toUpperCase()} | ${payload.range_key.toUpperCase()}`;

        if (!window.MacroCharts) return;

        if (state.chartType === "line") {
            window.MacroCharts.renderLineChart(containerId, buildLineSeries(points, state, payload), {
                unit: payload.indicator.unit,
                title: payload.indicator.display_name,
            });
            return;
        }
        if (state.chartType === "bar") {
            window.MacroCharts.renderBarChart(
                containerId,
                [{ name: "ACTUAL", data: points.map((p) => [p.released_at, p.actual]), color: "#f5a623" }],
                { unit: payload.indicator.unit, title: payload.indicator.display_name }
            );
            return;
        }
        if (state.chartType === "histogram") {
            window.MacroCharts.renderHistogram(
                containerId,
                points.map((p) => p.actual).filter((v) => v !== null && v !== undefined),
                { title: `${payload.indicator.display_name} DISTRIBUTION` }
            );
            return;
        }
        if (state.chartType === "deviation") {
            window.MacroCharts.renderDeviationChart(
                containerId,
                points.map((p) => [p.released_at, p.surprise]),
                { title: `${payload.indicator.display_name} SURPRISE` }
            );
            return;
        }
        window.MacroCharts.renderHeatmap(
            containerId,
            points.map((p, i) => [i, 0, p.surprise ?? p.actual ?? 0]),
            {
                xLabels: points.map((p) => p.period || String(p.released_at).slice(0, 10)),
                yLabels: ["SURPRISE"],
                title: `${payload.indicator.display_name} HEATMAP`,
            }
        );
    }

    function renderRecentPrints(payload) {
        const body = document.getElementById("recent-prints-body");
        const meta = document.getElementById("prints-meta");
        meta.textContent = `${payload.recent_prints.length} ROWS | ${payload.revision_mode.replace("_", " ").toUpperCase()}`;

        if (!payload.recent_prints.length) {
            body.innerHTML = `<tr class="loading-row"><td colspan="7">NO PRINTS FOR THIS SELECTION</td></tr>`;
            return;
        }

        body.innerHTML = payload.recent_prints.map((p) => `
            <tr>
                <td class="num">${p.period || "—"}</td>
                <td class="num">${fmtDate(p.released_at)}</td>
                <td class="num">${fmt(p.actual)}</td>
                <td class="num">${fmt(p.estimate)}</td>
                <td class="num">${fmt(p.previous)}</td>
                <td class="num">${fmtSurprise(p.surprise)}</td>
                <td class="num ${p.change_percentage >= 0 ? "is-positive" : p.change_percentage < 0 ? "is-negative" : ""}">
                    ${p.change_percentage !== null && p.change_percentage !== undefined ? (p.change_percentage >= 0 ? "+" : "") + fmt(p.change_percentage) + "%" : "—"}
                </td>
            </tr>
        `).join("");
    }

    async function loadIndicatorPage() {
        const state = selectState();
        if (!state.countryCode || !state.canonicalName) return;

        const params = new URLSearchParams({ range_key: state.rangeKey, revision_mode: state.revisionMode });
        const res = await fetch(
            `/api/country/${encodeURIComponent(state.countryCode)}/indicator/${encodeURIComponent(state.canonicalName)}?${params.toString()}`
        );
        if (!res.ok) throw new Error(`HTTP ${res.status}`);

        const body = await res.json();
        renderChart(body.data, state);
        renderRecentPrints(body.data);
    }

    function bindControlGroup(groupId, attribute) {
        const buttons = document.querySelectorAll(`#${groupId} [data-${attribute}]`);
        buttons.forEach((btn) => {
            btn.addEventListener("click", async function () {
                buttons.forEach((b) => b.classList.remove("is-active"));
                btn.classList.add("is-active");
                await loadIndicatorPage();
            });
        });
    }

    function bindCheckbox(id) {
        const el = document.getElementById(id);
        if (el) el.addEventListener("change", loadIndicatorPage);
    }

    document.addEventListener("DOMContentLoaded", function () {
        bindControlGroup("chart-type-control", "chart-type");
        bindControlGroup("range-control", "range-key");
        bindControlGroup("revision-control", "revision-mode");
        bindCheckbox("overlay-estimate");
        bindCheckbox("overlay-target");
        bindCheckbox("overlay-previous");

        loadIndicatorPage().catch((err) => {
            const meta = document.getElementById("chart-meta");
            const body = document.getElementById("recent-prints-body");
            if (meta) meta.textContent = "ERROR LOADING DATA";
            if (body) body.innerHTML = `<tr class="loading-row"><td colspan="7">INDICATOR DATA COULD NOT BE LOADED</td></tr>`;
            console.error(err);
        });
    });
})();
