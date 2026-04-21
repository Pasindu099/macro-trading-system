(function () {
    function ensureChart(element) {
        if (!window.echarts) {
            return null;
        }

        let chart = window.echarts.getInstanceByDom(element);
        if (!chart) {
            chart = window.echarts.init(element, null, { renderer: "canvas" });
            if (window.ResizeObserver) {
                const observer = new ResizeObserver(() => chart.resize());
                observer.observe(element);
                element.__sparkObserver = observer;
            }
        }
        return chart;
    }

    function getChartElement(target) {
        if (typeof target === "string") {
            return document.getElementById(target);
        }
        return target;
    }

    function ensureChartByTarget(target) {
        const element = getChartElement(target);
        if (!element) {
            return null;
        }
        return ensureChart(element);
    }

    function chartBaseOptions(options = {}) {
        return {
            animationDuration: 180,
            backgroundColor: "transparent",
            textStyle: {
                color: "#d4d4d4",
                fontFamily: "'IBM Plex Mono', 'Courier New', Courier, monospace",
                fontSize: 11,
            },
            grid: {
                left: 56,
                right: 24,
                top: 48,
                bottom: 48,
                containLabel: true,
            },
            legend: {
                top: 10,
                textStyle: { color: "#888888", fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 },
            },
            tooltip: {
                trigger: "axis",
                backgroundColor: "#111111",
                borderColor: "#333333",
                borderWidth: 1,
                textStyle: { color: "#d4d4d4", fontFamily: "'IBM Plex Mono', monospace", fontSize: 11 },
                valueFormatter: options.unit ? (value) => `${value ?? "N/A"} ${options.unit}` : undefined,
            },
            xAxis: {
                axisLine: { lineStyle: { color: "#1e1e1e" } },
                axisLabel: { color: "#555555", fontFamily: "'IBM Plex Mono', monospace", fontSize: 10 },
                splitLine: { show: false },
            },
            yAxis: {
                axisLine: { show: false },
                axisLabel: { color: "#555555", fontFamily: "'IBM Plex Mono', monospace", fontSize: 10 },
                splitLine: { lineStyle: { color: "#1a1a1a" } },
                scale: true,
            },
        };
    }

    function buildReferenceBadge(options = {}) {
        if (!options.referenceBadgeText) {
            return [];
        }

        return [
            {
                type: "group",
                right: 24,
                top: 12,
                silent: true,
                children: [
                    {
                        type: "rect",
                        shape: { x: 0, y: 0, width: 140, height: 26, r: 13 },
                        style: {
                            fill: "rgba(35, 196, 131, 0.12)",
                            stroke: "#23c483",
                            lineWidth: 1,
                        },
                    },
                    {
                        type: "text",
                        style: {
                            x: 12,
                            y: 17,
                            text: options.referenceBadgeText,
                            fill: "#23c483",
                            font: "12px 'IBM Plex Mono', monospace",
                        },
                    },
                ],
            },
        ];
    }

    function toSeriesConfig(series) {
        return (Array.isArray(series) ? series : []).map((item) => ({
            name: item.name,
            type: item.type || "line",
            showSymbol: false,
            smooth: item.smooth !== false,
            connectNulls: true,
            data: item.data || [],
            lineStyle: {
                width: 2,
                color: item.color || "#50b5ff",
                type: item.dashed ? "dashed" : "solid",
            },
            itemStyle: {
                color: item.color || "#50b5ff",
            },
            areaStyle: item.area
                ? {
                    opacity: 0.08,
                    color: item.color || "#50b5ff",
                }
                : undefined,
        }));
    }

    function renderLineChart(containerId, series, options = {}) {
        const chart = ensureChartByTarget(containerId);
        if (!chart) {
            return;
        }

        chart.setOption({
            ...chartBaseOptions(options),
            xAxis: {
                ...chartBaseOptions(options).xAxis,
                type: "time",
            },
            yAxis: {
                ...chartBaseOptions(options).yAxis,
                type: "value",
                name: options.unit || "",
            },
            series: toSeriesConfig(series),
            graphic: buildReferenceBadge(options),
        }, true);
    }

    function renderBarChart(containerId, series, options = {}) {
        const chart = ensureChartByTarget(containerId);
        if (!chart) {
            return;
        }

        const categories = (series[0]?.data || []).map((point) => point[0]);
        const renderedSeries = (Array.isArray(series) ? series : []).map((item) => ({
            name: item.name,
            type: "bar",
            barMaxWidth: 24,
            data: (item.data || []).map((point) => point[1]),
            itemStyle: { color: item.color || "#50b5ff" },
        }));

        if (options.referenceLineValue !== null && options.referenceLineValue !== undefined) {
            renderedSeries.push({
                name: options.referenceLineLabel || "Reference",
                type: "line",
                data: categories.map(() => options.referenceLineValue),
                showSymbol: false,
                smooth: false,
                connectNulls: true,
                lineStyle: {
                    width: 2,
                    color: "#23c483",
                    type: "dashed",
                },
                itemStyle: { color: "#23c483" },
            });
        }

        chart.setOption({
            ...chartBaseOptions(options),
            xAxis: {
                ...chartBaseOptions(options).xAxis,
                type: "category",
                data: categories,
                axisLabel: {
                    color: "#555555",
                    formatter: (value) => String(value).slice(0, 10),
                },
            },
            yAxis: {
                ...chartBaseOptions(options).yAxis,
                type: "value",
                name: options.unit || "",
            },
            series: renderedSeries,
            graphic: buildReferenceBadge(options),
        }, true);
    }

    function renderHistogram(containerId, values, options = {}) {
        const chart = ensureChartByTarget(containerId);
        if (!chart) {
            return;
        }

        const cleanValues = (Array.isArray(values) ? values : [])
            .filter((value) => value !== null && value !== undefined)
            .sort((a, b) => a - b);

        if (!cleanValues.length) {
            chart.clear();
            return;
        }

        const min = cleanValues[0];
        const max = cleanValues[cleanValues.length - 1];
        const binCount = Math.min(12, Math.max(5, Math.ceil(Math.sqrt(cleanValues.length))));
        const span = max - min || 1;
        const step = span / binCount;
        const bins = Array.from({ length: binCount }, (_, index) => ({
            label: `${(min + step * index).toFixed(1)}-${(min + step * (index + 1)).toFixed(1)}`,
            count: 0,
        }));

        cleanValues.forEach((value) => {
            const rawIndex = Math.floor((value - min) / step);
            const index = Math.min(binCount - 1, Math.max(0, rawIndex));
            bins[index].count += 1;
        });

        let targetBinLabel = null;
        if (options.referenceValue !== null && options.referenceValue !== undefined) {
            const rawIndex = Math.floor((options.referenceValue - min) / step);
            const index = Math.min(binCount - 1, Math.max(0, rawIndex));
            targetBinLabel = bins[index]?.label || null;
        }

        chart.setOption({
            ...chartBaseOptions(options),
            xAxis: {
                ...chartBaseOptions(options).xAxis,
                type: "category",
                data: bins.map((bin) => bin.label),
            },
            yAxis: {
                ...chartBaseOptions(options).yAxis,
                type: "value",
                name: "Count",
            },
            tooltip: {
                trigger: "item",
                backgroundColor: "#111111",
                borderColor: "#333333",
                textStyle: { color: "#d4d4d4" },
            },
            series: [
                {
                    type: "bar",
                    barWidth: "85%",
                    data: bins.map((bin) => bin.count),
                    itemStyle: { color: "#50b5ff" },
                    markLine: targetBinLabel ? {
                        symbol: "none",
                        label: {
                            show: true,
                            formatter: options.referenceLineLabel || "CB Target",
                            color: "#23c483",
                        },
                        lineStyle: {
                            color: "#23c483",
                            type: "dashed",
                            width: 2,
                        },
                        data: [{ xAxis: targetBinLabel }],
                    } : undefined,
                },
            ],
            graphic: buildReferenceBadge(options),
        }, true);
    }

    function renderDeviationChart(containerId, surprises, options = {}) {
        const chart = ensureChartByTarget(containerId);
        if (!chart) {
            return;
        }

        const cleanPoints = (Array.isArray(surprises) ? surprises : []).map((point) => [
            point[0],
            point[1] ?? 0,
        ]);

        chart.setOption({
            ...chartBaseOptions(options),
            xAxis: {
                ...chartBaseOptions(options).xAxis,
                type: "category",
                data: cleanPoints.map((point) => point[0]),
                axisLabel: {
                    color: "#555555",
                    formatter: (value) => String(value).slice(0, 10),
                },
            },
            yAxis: {
                ...chartBaseOptions(options).yAxis,
                type: "value",
                name: "Surprise",
            },
            series: [
                {
                    name: options.title || "Surprise",
                    type: "bar",
                    data: cleanPoints.map((point) => ({
                        value: point[1],
                        itemStyle: { color: point[1] >= 0 ? "#00cc44" : "#ff3333" },
                    })),
                    barMaxWidth: 28,
                },
            ],
            graphic: buildReferenceBadge(options),
        }, true);
    }

    function renderHeatmap(containerId, matrix, options = {}) {
        const chart = ensureChartByTarget(containerId);
        if (!chart) {
            return;
        }

        chart.setOption({
            animationDuration: 240,
            backgroundColor: "transparent",
            tooltip: {
                position: "top",
                backgroundColor: "#111111",
                borderColor: "#333333",
                textStyle: { color: "#d4d4d4" },
            },
            grid: {
                left: 64,
                right: 28,
                top: 28,
                bottom: 48,
                containLabel: true,
            },
            xAxis: {
                type: "category",
                data: options.xLabels || [],
                axisLine: { lineStyle: { color: "#333333" } },
                axisLabel: {
                    color: "#555555",
                    interval: 0,
                    rotate: options.xLabels && options.xLabels.length > 8 ? 35 : 0,
                },
                splitArea: { show: false },
            },
            yAxis: {
                type: "category",
                data: options.yLabels || [],
                axisLine: { lineStyle: { color: "#333333" } },
                axisLabel: { color: "#555555" },
                splitArea: { show: false },
            },
            visualMap: {
                min: options.min ?? -3,
                max: options.max ?? 3,
                calculable: false,
                orient: "horizontal",
                left: "center",
                bottom: 0,
                textStyle: { color: "#555555" },
                inRange: {
                    color: ["#5f1515", "#0a0a0a", "#0d4020"],
                },
            },
            series: [
                {
                    type: "heatmap",
                    data: Array.isArray(matrix) ? matrix : [],
                    label: { show: false },
                    emphasis: {
                        itemStyle: {
                            borderColor: "#d4d4d4",
                            borderWidth: 1,
                        },
                    },
                },
            ],
            graphic: buildReferenceBadge(options),
        }, true);
    }

    function renderSparkline(element, values, options = {}) {
        const chart = ensureChart(element);
        if (!chart) {
            return;
        }

        const cleanValues = Array.isArray(values) ? values : [];
        chart.setOption({
            animation: false,
            grid: {
                left: 0,
                right: 0,
                top: 1,
                bottom: 1,
                containLabel: false,
            },
            xAxis: {
                type: "category",
                show: false,
                boundaryGap: false,
                data: cleanValues.map((_, index) => index),
            },
            yAxis: {
                type: "value",
                show: false,
                scale: true,
            },
            series: [
                {
                    type: "line",
                    data: cleanValues,
                    smooth: true,
                    showSymbol: false,
                    connectNulls: true,
                    lineStyle: {
                        width: 2,
                        color: options.color || "#50b5ff",
                    },
                    areaStyle: {
                        opacity: 0.08,
                        color: options.color || "#50b5ff",
                    },
                },
            ],
            tooltip: { show: false },
        });
    }

    function bootSparklines(root = document) {
        root.querySelectorAll(".sparkline[data-values]").forEach((element) => {
            let values = [];
            try {
                values = JSON.parse(element.dataset.values || "[]");
            } catch (error) {
                values = [];
            }
            renderSparkline(element, values);
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        bootSparklines(document);
    });

    document.body.addEventListener("htmx:afterSwap", function (event) {
        bootSparklines(event.target);
    });

    window.MacroCharts = {
        renderLineChart,
        renderBarChart,
        renderHistogram,
        renderDeviationChart,
        renderHeatmap,
        renderSparkline,
        bootSparklines,
    };
})();
