(function () {
    function formatNumber(value) {
        if (value === null || value === undefined) {
            return "N/A";
        }
        return new Intl.NumberFormat(undefined, {
            maximumFractionDigits: 2,
        }).format(value);
    }

    function formatSignedNumber(value, suffix = "") {
        if (value === null || value === undefined) {
            return "N/A";
        }

        const sign = value > 0 ? "+" : "";
        return `${sign}${formatNumber(value)}${suffix}`;
    }

    function formatDate(value) {
        if (!value) {
            return "N/A";
        }
        return new Date(value).toLocaleDateString(undefined, {
            month: "short",
            day: "numeric",
            year: "numeric",
        });
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
                name: "Actual",
                data: points.map((point) => [point.released_at, point.actual]),
                color: "#4a9fe0",
            },
        ];

        if (state.overlayEstimate) {
            series.push({
                name: "Consensus",
                data: points.map((point) => [point.released_at, point.estimate]),
                color: "#f5a623",
            });
        }

        if (state.overlayPrevious) {
            series.push({
                name: "Previous",
                data: points.map((point) => [point.released_at, point.previous]),
                color: "#8892a0",
            });
        }

        if (state.overlayTarget && payload.cb_target !== null && payload.cb_target !== undefined) {
            series.push({
                name: "CB Target",
                data: points.map((point) => [point.released_at, payload.cb_target]),
                color: "#00c896",
                dashed: true,
            });
        }

        return series;
    }

    function buildHeatmapMatrix(points) {
        return points.map((point, index) => [
            index,
            0,
            point.surprise ?? point.actual ?? 0,
        ]);
    }

    function buildReferenceBadgeText(payload, state) {
        if (!state.overlayTarget || payload.cb_target === null || payload.cb_target === undefined) {
            return null;
        }

        return `CB Target ${formatNumber(payload.cb_target)}${payload.indicator.unit ? ` ${payload.indicator.unit}` : ""}`;
    }

    function renderChart(payload, state) {
        const chartTitle = document.getElementById("chart-title");
        const chartMeta = document.getElementById("chart-meta");
        const containerId = "indicator-chart";
        const points = Array.isArray(payload.series) ? payload.series : [];

        chartTitle.textContent = payload.indicator.display_name;
        chartMeta.textContent = `${payload.total_points} points | ${payload.revision_mode.replace("_", " ")} | ${payload.range_key.toUpperCase()}`;

        if (!window.MacroCharts) {
            return;
        }

        if (state.chartType === "line") {
            window.MacroCharts.renderLineChart(
                containerId,
                buildLineSeries(points, state, payload),
                {
                    unit: payload.indicator.unit,
                    title: payload.indicator.display_name,
                    referenceBadgeText: buildReferenceBadgeText(payload, state),
                }
            );
            return;
        }

        if (state.chartType === "bar") {
            const barSeries = [
                {
                    name: "Actual",
                    data: points.map((point) => [point.released_at, point.actual]),
                    color: "#4a9fe0",
                },
            ];
            window.MacroCharts.renderBarChart(
                containerId,
                barSeries,
                {
                    unit: payload.indicator.unit,
                    title: payload.indicator.display_name,
                    referenceLineValue: state.overlayTarget ? payload.cb_target : null,
                    referenceLineLabel: "CB Target",
                    referenceBadgeText: buildReferenceBadgeText(payload, state),
                }
            );
            return;
        }

        if (state.chartType === "histogram") {
            window.MacroCharts.renderHistogram(
                containerId,
                points.map((point) => point.actual).filter((value) => value !== null && value !== undefined),
                {
                    title: `${payload.indicator.display_name} distribution`,
                    referenceValue: state.overlayTarget ? payload.cb_target : null,
                    referenceLineLabel: "CB Target",
                    referenceBadgeText: buildReferenceBadgeText(payload, state),
                }
            );
            return;
        }

        if (state.chartType === "deviation") {
            window.MacroCharts.renderDeviationChart(
                containerId,
                points.map((point) => [point.released_at, point.surprise]),
                {
                    title: `${payload.indicator.display_name} surprise`,
                    referenceBadgeText: buildReferenceBadgeText(payload, state),
                }
            );
            return;
        }

        window.MacroCharts.renderHeatmap(
            containerId,
            buildHeatmapMatrix(points),
            {
                xLabels: points.map((point) => point.period || String(point.released_at).slice(0, 10)),
                yLabels: ["Surprise intensity"],
                title: `${payload.indicator.display_name} heatmap`,
                referenceBadgeText: buildReferenceBadgeText(payload, state),
            }
        );
    }

    function renderRecentPrints(payload) {
        const body = document.getElementById("recent-prints-body");
        const meta = document.getElementById("prints-meta");
        meta.textContent = `${payload.recent_prints.length} rows shown in ${payload.revision_mode.replace("_", " ")} mode.`;

        if (!payload.recent_prints.length) {
            body.innerHTML = `
                <tr>
                    <td colspan="7" class="prints-table__loading">No prints available for this selection.</td>
                </tr>
            `;
            return;
        }

        body.innerHTML = payload.recent_prints.map((point) => `
            <tr>
                <td>${point.period || "N/A"}</td>
                <td>${formatDate(point.released_at)}</td>
                <td>${formatNumber(point.actual)}</td>
                <td>${formatNumber(point.estimate)}</td>
                <td>${formatNumber(point.previous)}</td>
                <td class="${point.surprise > 0 ? "is-positive" : point.surprise < 0 ? "is-negative" : ""}">
                    ${formatSignedNumber(point.surprise)}
                </td>
                <td class="${point.change_percentage > 0 ? "is-positive" : point.change_percentage < 0 ? "is-negative" : ""}">
                    ${formatSignedNumber(point.change_percentage, "%")}
                </td>
            </tr>
        `).join("");
    }

    async function loadIndicatorPage() {
        const state = selectState();
        if (!state.countryCode || !state.canonicalName) {
            return;
        }

        const params = new URLSearchParams({
            range_key: state.rangeKey,
            revision_mode: state.revisionMode,
        });

        const response = await fetch(
            `/api/country/${encodeURIComponent(state.countryCode)}/indicator/${encodeURIComponent(state.canonicalName)}?${params.toString()}`
        );
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }

        const body = await response.json();
        const payload = body.data;
        renderChart(payload, state);
        renderRecentPrints(payload);
    }

    function bindControlGroup(groupId, attributeName) {
        const buttons = document.querySelectorAll(`#${groupId} [data-${attributeName}]`);
        buttons.forEach((button) => {
            button.addEventListener("click", async function () {
                buttons.forEach((item) => item.classList.remove("is-active"));
                button.classList.add("is-active");
                await loadIndicatorPage();
            });
        });
    }

    function bindCheckbox(checkboxId) {
        const checkbox = document.getElementById(checkboxId);
        if (!checkbox) {
            return;
        }
        checkbox.addEventListener("change", loadIndicatorPage);
    }

    document.addEventListener("DOMContentLoaded", function () {
        bindControlGroup("chart-type-control", "chart-type");
        bindControlGroup("range-control", "range-key");
        bindControlGroup("revision-control", "revision-mode");
        bindCheckbox("overlay-estimate");
        bindCheckbox("overlay-target");
        bindCheckbox("overlay-previous");

        loadIndicatorPage().catch((error) => {
            const chartMeta = document.getElementById("chart-meta");
            const body = document.getElementById("recent-prints-body");
            if (chartMeta) {
                chartMeta.textContent = "Unable to load indicator data";
            }
            if (body) {
                body.innerHTML = `
                    <tr>
                        <td colspan="7" class="prints-table__loading">Indicator data could not be loaded.</td>
                    </tr>
                `;
            }
            console.error(error);
        });
    });
})();
