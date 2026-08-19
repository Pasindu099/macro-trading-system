/*
 * Country surprise charts.
 *
 * Two forms, both diverging around a zero baseline, because the job of this data
 * is polarity — did the economy beat or miss what was expected — not identity.
 * A categorical multi-series chart here would bury the one thing the reader
 * needs. Each chart is a single series, so neither carries a legend: the title
 * names the series.
 *
 * Screen keeps the finance convention (green above / red below). The bar's
 * direction from the zero baseline is the secondary encoding that makes the
 * green/red pair safe; export swaps to blue/red outright. See chart_export.js.
 */
(function () {
    "use strict";

    var SCREEN = {
        positive: "#22c55e",
        negative: "#ef4444",
        neutral: "#6b5f54",
        ink: "#f5f0eb",
        secondary: "#c9bfb3",
        muted: "#6b5f54",
        grid: "rgba(255,255,255,0.06)",
        axis: "rgba(255,255,255,0.14)",
        font: "Roboto, Arial, sans-serif",
    };

    function payload() {
        var node = document.getElementById("country-chart-data");
        if (!node) return null;
        try {
            return JSON.parse(node.textContent);
        } catch (error) {
            return null;
        }
    }

    function barColor(theme, value) {
        if (value === null || value === undefined) return theme.neutral;
        return value >= 0 ? theme.positive : theme.negative;
    }

    function sigma(value) {
        return value === null || value === undefined
            ? "n/a"
            : (value >= 0 ? "+" : "") + value.toFixed(2) + "σ";
    }

    /* A diverging chart must be symmetric about zero, or the longer arm looks
       stronger than it is purely because the axis gave it more room. The extra
       padding also keeps a direct label from overrunning the axis labels. */
    var NICE_STEPS = [1, 1.2, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10];

    function symmetricBound(values) {
        var peak = 0;
        values.forEach(function (value) {
            if (value === null || value === undefined) return;
            peak = Math.max(peak, Math.abs(value));
        });
        if (peak === 0) return 1;

        // Round the padded peak up to a readable step, otherwise the axis ends
        // on a tick like 0.5577 and the reader has to parse noise.
        var padded = peak * 1.25;
        var magnitude = Math.pow(10, Math.floor(Math.log10(padded)));
        for (var i = 0; i < NICE_STEPS.length; i += 1) {
            // toPrecision trims the float artifact (0.6000000000000001), which
            // would otherwise be printed verbatim as an axis tick.
            var candidate = Number((NICE_STEPS[i] * magnitude).toPrecision(12));
            if (candidate >= padded) return candidate;
        }
        return Number((10 * magnitude).toPrecision(12));
    }

    /* Label only the single strongest bar in each direction. A number on every
       column is noise and goes unread. */
    function extremeIndexes(values) {
        var maxIndex = -1;
        var minIndex = -1;
        values.forEach(function (value, index) {
            if (value === null || value === undefined) return;
            if (maxIndex === -1 || value > values[maxIndex]) maxIndex = index;
            if (minIndex === -1 || value < values[minIndex]) minIndex = index;
        });
        return { max: maxIndex, min: minIndex };
    }

    // ── surprise over time ───────────────────────────────────────────────────

    function timeseriesOption(data, theme, opts) {
        var options = opts || {};
        var labels = data.timeseries.map(function (point) { return point.label; });
        var values = data.timeseries.map(function (point) { return point.average; });
        var extremes = options.directLabels ? extremeIndexes(values) : { max: -1, min: -1 };

        return {
            backgroundColor: options.background || "transparent",
            animation: !options.forExport,
            textStyle: { color: theme.ink, fontFamily: theme.font },
            title: options.forExport ? {
                text: data.country_name + " — macro surprise vs consensus",
                subtext:
                    data.window_label +
                    "  ·  average surprise per bucket, in standard deviations (σ)" +
                    "\nof each indicator's own 3-year surprise history" +
                    "  ·  positive = " + data.country_code + " currency-positive" +
                    "\nSource: EODHD  ·  as of " + new Date().toISOString().slice(0, 10),
                left: 16,
                top: 12,
                textStyle: { color: theme.ink, fontSize: 16, fontWeight: 600 },
                subtextStyle: { color: theme.inkSecondary || theme.secondary, fontSize: 11, lineHeight: 16 },
            } : undefined,
            grid: {
                left: 46,
                // Room for a direct label sitting outside the tallest bar.
                right: 34,
                top: options.forExport ? 116 : 16,
                bottom: 30,
            },
            tooltip: options.forExport ? undefined : {
                trigger: "axis",
                axisPointer: { type: "shadow" },
                backgroundColor: "rgba(16,13,10,0.96)",
                borderColor: "rgba(255,255,255,0.14)",
                textStyle: { color: theme.ink, fontFamily: theme.font, fontSize: 11 },
                formatter: function (params) {
                    var point = data.timeseries[params[0].dataIndex];
                    if (!point) return "";
                    if (point.average === null) {
                        return point.label + "<br/>no scored releases";
                    }
                    return (
                        point.label + "<br/><strong>" + sigma(point.average) +
                        "</strong><br/>" + point.count +
                        (point.count === 1 ? " release" : " releases")
                    );
                },
            },
            xAxis: {
                type: "category",
                data: labels,
                axisLine: { lineStyle: { color: theme.axis } },
                axisTick: { show: false },
                axisLabel: {
                    color: theme.muted,
                    fontSize: options.forExport ? 11 : 9,
                    fontFamily: theme.font,
                    interval: labels.length > 16 ? "auto" : 0,
                },
            },
            yAxis: {
                type: "value",
                // The unit lives in the subtitle rather than an axis name: an
                // axis name here collides with the baked-in title block.
                min: -symmetricBound(values),
                max: symmetricBound(values),
                splitLine: { lineStyle: { color: theme.grid, type: "solid" } },
                axisLabel: {
                    color: theme.muted,
                    fontSize: options.forExport ? 11 : 9,
                    fontFamily: theme.font,
                },
            },
            series: [{
                type: "bar",
                data: values,
                barMaxWidth: options.forExport ? 26 : 18,
                // Rounded data-end anchored to the zero baseline, both directions.
                itemStyle: {
                    color: function (params) { return barColor(theme, params.value); },
                    borderRadius: params_borderRadius,
                },
                label: {
                    show: options.directLabels,
                    fontFamily: theme.font,
                    fontSize: 11,
                    color: theme.inkSecondary || theme.secondary,
                    formatter: function (params) {
                        if (params.dataIndex !== extremes.max && params.dataIndex !== extremes.min) {
                            return "";
                        }
                        return sigma(params.value);
                    },
                },
                /* ECharts ignores a function for label.position — it silently
                   falls back to "inside", printing the value on top of its own
                   bar. labelLayout does take a callback, so the label is placed
                   just beyond whichever end of the bar points away from zero. */
                labelLayout: function (params) {
                    var value = values[params.dataIndex];
                    var rect = params.rect;
                    if (value === null || value === undefined || !rect) return {};
                    return value >= 0
                        ? { y: rect.y - 10, align: "center" }
                        : { y: rect.y + rect.height + 10, align: "center" };
                },
                markLine: {
                    silent: true,
                    symbol: "none",
                    lineStyle: { color: theme.axis, width: 1, type: "solid" },
                    data: [{ yAxis: 0 }],
                    label: { show: false },
                },
            }],
        };
    }

    function params_borderRadius(params) {
        return params.value >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4];
    }

    // ── category pulse ───────────────────────────────────────────────────────

    function pulseOption(data, theme, opts) {
        var options = opts || {};
        // Horizontal bars read top-down, so reverse for strongest at the top.
        var items = data.category_pulse.slice().reverse();
        var labels = items.map(function (item) { return item.category; });
        var values = items.map(function (item) { return Number(item.average.toFixed(3)); });

        return {
            backgroundColor: options.background || "transparent",
            animation: !options.forExport,
            textStyle: { color: theme.ink, fontFamily: theme.font },
            title: options.forExport ? {
                text: data.country_name + " — surprise by category",
                subtext:
                    data.window_label +
                    "  ·  positive = " + data.country_code + " currency-positive" +
                    "\nSource: EODHD  ·  as of " + new Date().toISOString().slice(0, 10),
                left: 16,
                top: 12,
                textStyle: { color: theme.ink, fontSize: 16, fontWeight: 600 },
                subtextStyle: { color: theme.inkSecondary || theme.secondary, fontSize: 11, lineHeight: 16 },
            } : undefined,
            grid: {
                left: 110,
                // Both edges need room for a value label outside the bar end.
                right: 64,
                top: options.forExport ? 92 : 12,
                bottom: 24,
            },
            tooltip: options.forExport ? undefined : {
                trigger: "item",
                backgroundColor: "rgba(16,13,10,0.96)",
                borderColor: "rgba(255,255,255,0.14)",
                textStyle: { color: theme.ink, fontFamily: theme.font, fontSize: 11 },
                formatter: function (params) {
                    var item = items[params.dataIndex];
                    return (
                        item.category + "<br/><strong>" + sigma(item.average) +
                        "</strong><br/>" + item.count +
                        (item.count === 1 ? " scored release" : " scored releases")
                    );
                },
            },
            xAxis: {
                type: "value",
                min: -symmetricBound(values),
                max: symmetricBound(values),
                splitLine: { lineStyle: { color: theme.grid, type: "solid" } },
                axisLabel: {
                    color: theme.muted,
                    fontSize: options.forExport ? 11 : 9,
                    fontFamily: theme.font,
                },
            },
            yAxis: {
                type: "category",
                data: labels,
                axisLine: { lineStyle: { color: theme.axis } },
                axisTick: { show: false },
                axisLabel: {
                    color: options.forExport ? theme.ink : theme.secondary,
                    fontSize: options.forExport ? 12 : 10,
                    fontFamily: theme.font,
                },
            },
            series: [{
                type: "bar",
                data: values,
                barMaxWidth: 18,
                itemStyle: {
                    color: function (params) { return barColor(theme, params.value); },
                    borderRadius: function (params) {
                        return params.value >= 0 ? [0, 4, 4, 0] : [4, 0, 0, 4];
                    },
                },
                label: {
                    show: true,
                    fontFamily: theme.font,
                    fontSize: options.forExport ? 12 : 10,
                    color: options.forExport ? theme.inkSecondary : theme.secondary,
                    formatter: function (params) { return sigma(params.value); },
                },
                // Same labelLayout fix as the column chart — see the note there.
                labelLayout: function (params) {
                    var value = values[params.dataIndex];
                    var rect = params.rect;
                    if (value === null || value === undefined || !rect) return {};
                    return value >= 0
                        ? { x: rect.x + rect.width + 8, align: "left" }
                        : { x: rect.x - 8, align: "right" };
                },
                markLine: {
                    silent: true,
                    symbol: "none",
                    lineStyle: { color: theme.axis, width: 1, type: "solid" },
                    data: [{ xAxis: 0 }],
                    label: { show: false },
                },
            }],
        };
    }

    // ── wiring ───────────────────────────────────────────────────────────────

    function mount(elementId, data, builder, tableBuilder, filenameStem, exportHeight) {
        var element = document.getElementById(elementId);
        if (!element || !window.echarts) return;

        var chart = window.echarts.getInstanceByDom(element) ||
            window.echarts.init(element, null, { renderer: "canvas" });
        chart.setOption(builder(data, SCREEN, { forExport: false }), true);

        if (window.ResizeObserver) {
            new ResizeObserver(function () { chart.resize(); }).observe(element);
        }

        if (!window.MacroChartExport) return;
        var exportTheme = window.MacroChartExport.theme;
        window.MacroChartExport.attach(element.parentElement, {
            exportOption: function () {
                return builder(data, exportTheme, {
                    forExport: true,
                    directLabels: true,
                    background: exportTheme.surface,
                });
            },
            table: tableBuilder,
            filename: filenameStem,
            width: 960,
            height: exportHeight,
        });
    }

    function boot() {
        var data = payload();
        if (!data) return;

        if (data.timeseries && data.timeseries.length) {
            mount(
                "chart-surprise-timeseries",
                data,
                timeseriesOption,
                function () {
                    return [["Period", "Avg surprise (sigma)", "Releases"]].concat(
                        data.timeseries.map(function (point) {
                            return [point.label, point.average, point.count];
                        })
                    );
                },
                data.country_code + "-macro-surprise",
                460
            );
        }

        if (data.category_pulse && data.category_pulse.length) {
            mount(
                "chart-category-pulse",
                data,
                pulseOption,
                function () {
                    return [["Category", "Avg surprise (sigma)", "Scored releases"]].concat(
                        data.category_pulse.map(function (item) {
                            return [item.category, Number(item.average.toFixed(3)), item.count];
                        })
                    );
                },
                data.country_code + "-surprise-by-category",
                380
            );
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot);
    } else {
        boot();
    }

    // Exposed so the option builders can be rendered and inspected outside the
    // page — the export variant is otherwise only reachable through a click.
    window.MacroCountryCharts = {
        timeseriesOption: timeseriesOption,
        pulseOption: pulseOption,
        screenTheme: SCREEN,
    };
})();
