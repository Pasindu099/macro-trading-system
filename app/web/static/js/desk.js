/* Currency desk: initialise ECharts from data-chart JSON after each HTMX swap. */
(function () {
  "use strict";
  var MUTED = "#9aa3b2", GRID = "#1b2230", BG = "#0e1218";
  var charts = [];

  function axisLabel(fmt) { return { color: MUTED, fontSize: 10, fontFamily: "DM Mono", formatter: fmt }; }

  function lineSeries(s) {
    return {
      name: s.name, type: s.bar ? "bar" : "line", data: s.data, step: s.step ? "end" : false,
      showSymbol: false, connectNulls: true, barMaxWidth: 14,
      itemStyle: { color: s.color },
      lineStyle: { color: s.color, width: s.width || 2, type: s.dashed ? "dashed" : "solid" }
    };
  }

  function refLine(value, color) {
    return { silent: true, symbol: "none", label: { show: false },
      lineStyle: { color: color || "#5a6475", type: "dashed" }, data: [{ yAxis: value }] };
  }

  function buildOption(spec) {
    var opt = {
      backgroundColor: BG, animation: false,
      grid: { left: 44, right: 12, top: 10, bottom: 22 },
      tooltip: { trigger: "axis", backgroundColor: "#12161d", borderColor: "#2c3442",
                 textStyle: { color: "#e6e9ef", fontFamily: "DM Mono", fontSize: 11 } },
      yAxis: { type: "value", scale: true, axisLabel: axisLabel(), splitLine: { lineStyle: { color: GRID } } },
      series: spec.series.map(lineSeries)
    };
    if (spec.kind === "curve") {
      opt.xAxis = { type: "category", data: spec.tenors, boundaryGap: false, axisLabel: axisLabel(),
                    axisLine: { lineStyle: { color: GRID } } };
      opt.series.forEach(function (s) { s.showSymbol = true; s.symbolSize = 6; });
      opt.yAxis.axisLabel = axisLabel("{value}%");
    } else {
      opt.xAxis = { type: "category", data: spec.dates, axisLabel: axisLabel(), axisLine: { lineStyle: { color: GRID } },
                    axisTick: { show: false } };
    }
    var first = opt.series[opt.series.length - 1];
    if (spec.ref !== null && spec.ref !== undefined) {
      // Keep the reference line inside the y-range (scale:true ignores mark lines).
      var values = [].concat.apply([], spec.series.map(function (s) { return s.data; }))
        .filter(function (v) { return v !== null && v !== undefined; }).concat([spec.ref]);
      var lo = Math.min.apply(null, values), hi = Math.max.apply(null, values);
      var step = Math.pow(10, Math.floor(Math.log10((hi - lo) || 1))) / 2;
      opt.yAxis.min = +(Math.floor(lo / step) * step).toFixed(4);
      opt.yAxis.max = +(Math.ceil(hi / step) * step).toFixed(4);
      first.markLine = refLine(spec.ref, spec.kind === "curve" ? "#7c8698" : null);
    }
    if (spec.shade_below) {
      first.markArea = { silent: true, itemStyle: { color: "rgba(90,169,255,0.06)" },
                         data: [[{ yAxis: -1000 }, { yAxis: 0 }]] };
    }
    if (spec.mark_date && spec.dates) {
      var day = String(spec.mark_date);
      var match = spec.dates.filter(function (d) { return String(d) >= day; })[0];
      if (match) {
        first.markLine = first.markLine || { silent: true, symbol: "none", label: { show: false }, data: [] };
        first.markLine.data.push({ xAxis: match, lineStyle: { color: "#d6c4fb", type: "dashed" } });
      }
    }
    return opt;
  }

  function initCharts(root) {
    if (!window.echarts) { return; }
    (root || document).querySelectorAll("[data-chart]").forEach(function (el) {
      if (el.dataset.ready) { return; }
      try {
        var chart = echarts.init(el, null, { renderer: "canvas" });
        chart.setOption(buildOption(JSON.parse(el.dataset.chart)));
        el.dataset.ready = "1";
        charts.push(chart);
      } catch (err) {
        el.textContent = "Chart could not be drawn.";
        el.classList.add("state", "state-error");
        if (window.console) { console.error(err); }
      }
    });
    charts = charts.filter(function (c) { return !c.isDisposed() && document.body.contains(c.getDom()); });
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-expand]");
    if (!btn) { return; }
    var card = btn.closest(".card");
    var open = !card.classList.contains("expanded");
    card.classList.toggle("expanded", open);
    card.querySelector(".card-detail").hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    btn.textContent = open ? "Collapse" : "Expand";
    btn.setAttribute("aria-label", (open ? "Collapse " : "Expand ") + card.querySelector("h3").textContent + " chart");
    var el = card.querySelector("[data-chart]");
    var chart = el && window.echarts && echarts.getInstanceByDom(el);
    if (chart) { chart.resize(); }
  });

  document.addEventListener("htmx:load", function (event) { initCharts(event.detail.elt); });
  window.addEventListener("resize", function () { charts.forEach(function (c) { c.resize(); }); });
})();
