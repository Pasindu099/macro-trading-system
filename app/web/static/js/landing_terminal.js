/* Landing "command map" terminal.
 *
 * Vanilla IIFE in the house style of charts.js — no build step, no framework.
 * All server data arrives as one JSON blob in #landing-data; nothing here fetches
 * the map imagery or geometry from a third party (see /static/geo/world.geo.json).
 */
(function () {
  "use strict";

  var dataNode = document.getElementById("landing-data");
  if (!dataNode) return;

  var DATA;
  try {
    DATA = JSON.parse(dataNode.textContent || "{}");
  } catch (err) {
    return;
  }

  /* ── Palette ────────────────────────────────────────────────────────────
   * charts.js' shared chartBaseOptions() still carries the old navy theme and
   * a font the app never loads, so the landing page defines its own tokens
   * mirroring macro_design.css.
   */
  var C = {
    accent: "#f97316",
    bull: "#22c55e",
    bear: "#ef4444",
    neutral: "#9a8f85",
    heading: "#f5f0eb",
    body: "#c9bfb3",
    muted: "#6b5f54",
    empty: "#1c1611",
    border: "rgba(255,255,255,0.08)",
    panel: "rgba(22,17,12,0.94)",
    mono: "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
  };

  var esc = function (v) {
    return String(v === null || v === undefined ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  };
  var num = function (v) {
    return v === null || v === undefined || v === "" || isNaN(Number(v)) ? null : Number(v);
  };

  /* Shared ECharts lifecycle: reuse the instance for an element and keep it
   * sized to its container. Same approach as charts.js ensureChart(). */
  var ensureChart = function (el) {
    if (!el || typeof echarts === "undefined") return null;
    var chart = echarts.getInstanceByDom(el);
    if (!chart) {
      chart = echarts.init(el, null, { renderer: "canvas" });
      if (typeof ResizeObserver !== "undefined") {
        new ResizeObserver(function () { chart.resize(); }).observe(el);
      } else {
        window.addEventListener("resize", function () { chart.resize(); });
      }
    }
    return chart;
  };

  var countries = DATA.countries || [];

  /* ══ Zone A · Command map ═══════════════════════════════════════════════ */

  /* direction: +1 means a higher reading is the "good"/green end, -1 means a
   * higher reading is the red end. CPI and the unemployment rate are both
   * inverted — 4% CPI is not a green number. */
  var METRICS = {
    composite: { label: "Composite", direction: 1 },
    rate: { label: "Policy rate", direction: 1 },
    inflation: { label: "Latest CPI", direction: -1 },
    labour: { label: "Unemployment", direction: -1 },
    gdp: { label: "GDP", direction: 1 }
  };

  var metricOf = function (country, key) {
    var list = country.metrics || [];
    for (var i = 0; i < list.length; i++) {
      if (list[i].key === key) return list[i];
    }
    return null;
  };

  /* Value used to shade a country for the active metric. "composite" uses the
   * currency-strength meter; the rest use the raw latest print. */
  var shadeValue = function (country, key) {
    if (key === "composite") return num(country.rawScore);
    var metric = metricOf(country, key);
    return metric ? num(metric.raw) : null;
  };

  var displayValue = function (country, key) {
    if (key === "composite") return country.score || "N/A";
    var metric = metricOf(country, key);
    return metric ? metric.value : "N/A";
  };

  /* GeoJSON feature name -> country, so the tooltip can resolve a hovered
   * euro-area member back to the EU row that shades it. */
  var byGeoName = {};
  countries.forEach(function (country) {
    (country.geoNames || []).forEach(function (name) { byGeoName[name] = country; });
  });

  var mapEl = document.querySelector("[data-command-map]");
  var mapChart = null;
  var activeMetric = "composite";

  var mapZoom = 1;

  var ramp = function (key) {
    var stops = [C.bear, "#f59e0b", C.bull];
    return (METRICS[key] || {}).direction === -1 ? stops.slice().reverse() : stops;
  };

  /* ── Map layers ────────────────────────────────────────────────────────
   * Centroids are derived from the GeoJSON already in memory rather than a
   * second hardcoded coordinate table. Good enough to drop a marker on the
   * right landmass; not a cartographic centroid.
   */
  var centroids = {};
  var buildCentroids = function (geo) {
    (geo.features || []).forEach(function (f) {
      var g = f.geometry || {};
      var rings = g.type === "Polygon" ? [g.coordinates[0]]
        : g.type === "MultiPolygon" ? g.coordinates.map(function (p) { return p[0]; })
          : [];
      var biggest = null;
      rings.forEach(function (r) {
        if (!biggest || r.length > biggest.length) biggest = r;
      });
      if (!biggest || !biggest.length) return;
      var sx = 0, sy = 0;
      biggest.forEach(function (p) { sx += p[0]; sy += p[1]; });
      centroids[f.properties.name] = [sx / biggest.length, sy / biggest.length];
    });
  };

  // Country code -> centroid, via the geo names each country shades.
  var centroidFor = function (code) {
    var country = countries.filter(function (c) { return c.code === code; })[0];
    if (!country) return null;
    var names = country.geoNames || [];
    for (var i = 0; i < names.length; i++) {
      if (centroids[names[i]]) return centroids[names[i]];
    }
    return null;
  };

  var layerState = { banks: true, events: false, surprises: false };
  var layerData = { events: [] };

  var scatter = function (name, data, color, symbolSize) {
    return {
      name: name,
      type: "scatter",
      coordinateSystem: "geo",
      data: data,
      symbolSize: symbolSize,
      itemStyle: { color: color, borderColor: "rgba(0,0,0,0.55)", borderWidth: 1 },
      emphasis: { scale: 1.4 },
      tooltip: {
        formatter: function (p) {
          return '<div class="map-tip"><strong>' + esc(p.data.title) + "</strong>" +
            (p.data.sub ? "<small>" + esc(p.data.sub) + "</small>" : "") + "</div>";
        }
      },
      zlevel: 2
    };
  };

  var layerSeries = function () {
    var out = [];
    if (layerState.banks) {
      out.push(scatter("Central banks", (DATA.centralBanks || []).map(function (b) {
        return {
          value: [b.lon, b.lat],
          title: b.bank + " · " + b.city,
          sub: b.label ? b.label + (b.score !== null ? " (" + b.score.toFixed(2) + ")" : "") : ""
        };
      }), C.accent, 7));
    }
    if (layerState.events) {
      out.push(scatter("High-impact events", layerData.events, C.heading, 6));
    }
    if (layerState.surprises) {
      out.push(scatter("Recent surprises", (DATA.surprises || []).map(function (s) {
        var c = centroidFor(s.country);
        if (!c) return null;
        return {
          value: c.concat([s.surprise]),
          title: s.name + " " + (s.surprise >= 0 ? "+" : "") + Number(s.surprise).toFixed(2),
          sub: s.currency + " · " + s.date,
          itemStyle: { color: Number(s.surprise) >= 0 ? C.bull : C.bear }
        };
      }).filter(Boolean), C.bull, 8));
    }
    return out;
  };

  var mapTooltip = function (params) {
    var country = byGeoName[params.name];
    if (!country) {
      return '<div class="map-tip map-tip--empty"><strong>' + esc(params.name) +
        "</strong><em>No macro coverage</em></div>";
    }
    var rows = (country.metrics || []).map(function (m) {
      return "<span><em>" + esc(m.label) + "</em><b>" + esc(m.value) + "</b></span>";
    }).join("");
    var stance = country.stance ? '<span class="map-tip__stance">' + esc(country.stance) + "</span>" : "";
    return '<div class="map-tip"><strong>' + esc(country.name) + " · " + esc(country.currency) +
      "</strong>" + stance + rows +
      "<small>Updated " + esc(country.updated) + "</small></div>";
  };

  var mapSeriesData = function (key) {
    var data = [];
    countries.forEach(function (country) {
      var value = shadeValue(country, key);
      (country.geoNames || []).forEach(function (name) {
        data.push({ name: name, value: value === null ? "-" : value, code: country.code });
      });
    });
    return data;
  };

  var visualRange = function (key) {
    var values = [];
    countries.forEach(function (country) {
      var v = shadeValue(country, key);
      if (v !== null) values.push(v);
    });
    if (!values.length) return { min: -1, max: 1 };
    var lo = Math.min.apply(null, values);
    var hi = Math.max.apply(null, values);
    if (lo === hi) { lo -= 1; hi += 1; }
    return { min: lo, max: hi };
  };

  var renderMap = function () {
    if (!mapChart) return;
    var range = visualRange(activeMetric);
    mapChart.setOption({
      backgroundColor: "transparent",
      tooltip: {
        trigger: "item",
        backgroundColor: C.panel,
        borderColor: C.border,
        borderWidth: 1,
        padding: 0,
        extraCssText: "box-shadow:0 8px 32px rgba(0,0,0,0.55);border-radius:8px;",
        formatter: mapTooltip
      },
      // Rendered as HTML below the map instead — ECharts' continuous visualMap
      // does not honour orient:"horizontal" reliably here.
      visualMap: {
        type: "continuous",
        min: range.min,
        max: range.max,
        show: false,
        // Amber mid-tone rather than the warm grey, which reads too close to
        // the "no coverage" fill. Reversed for metrics where higher is worse.
        inRange: { color: ramp(activeMetric) }
      },
      /* A standalone geo component (rather than a bare map series) so the layer
       * scatters below can share its coordinate system. */
      geo: {
        map: "world",
        roam: true,
        zoom: mapZoom,
        scaleLimit: { min: 1, max: 8 },
        /* Default fitting rather than layoutCenter/layoutSize: the map column is
         * now full viewport height, and a fixed layoutSize crops the flanks at
         * tall aspect ratios. aspectScale stretches it slightly to use the
         * vertical space without distorting recognisably. */
        top: 46,
        bottom: 54,
        left: 14,
        right: 14,
        // True proportions. Anything below 1 visibly stretches countries
        // east-west, which is what "the map looks stretched" was.
        aspectScale: 1,
        selectedMode: false,
        itemStyle: {
          areaColor: C.empty,
          borderColor: C.border,
          borderWidth: 0.6
        },
        emphasis: {
          label: { show: false },
          itemStyle: { areaColor: C.accent, borderColor: C.heading, borderWidth: 1 }
        }
      },
      series: [{
        type: "map",
        map: "world",
        geoIndex: 0,
        data: mapSeriesData(activeMetric)
      }].concat(layerSeries())
    }, { notMerge: true });

    var scale = document.querySelector("[data-map-scale]");
    if (scale) {
      var fmt = function (v) {
        return activeMetric === "composite" ? v.toFixed(2) : v.toFixed(1);
      };
      scale.innerHTML = "<span>" + fmt(range.min) + '</span><i style="background:linear-gradient(90deg,' +
        ramp(activeMetric).join(",") + ')"></i><span>' + fmt(range.max) + "</span>";
    }

    var label = document.querySelector("[data-map-readout-label]");
    if (label) label.textContent = (METRICS[activeMetric] || {}).label || activeMetric;
  };

  var bootMap = function () {
    if (!mapEl || typeof echarts === "undefined") return;
    fetch("/static/geo/world.geo.json")
      .then(function (r) { return r.ok ? r.json() : Promise.reject(new Error("geo")); })
      .then(function (geo) {
        echarts.registerMap("world", geo);
        buildCentroids(geo);
        mapEl.classList.remove("is-loading");
        mapEl.innerHTML = "";
        mapChart = ensureChart(mapEl);
        renderMap();
        mapChart.on("click", function (params) {
          if (params.componentType !== "series" || params.seriesType !== "map") return;
          var country = byGeoName[params.name];
          if (country && country.href) window.location.href = country.href;
        });
        // Keep our tracked zoom in sync with wheel/drag roaming.
        mapChart.on("georoam", function () {
          var opt = mapChart.getOption();
          if (opt.geo && opt.geo[0]) mapZoom = opt.geo[0].zoom;
        });
      })
      .catch(function () {
        mapEl.classList.remove("is-loading");
        mapEl.classList.add("is-error");
        mapEl.innerHTML = '<div class="map-fallback">World geometry failed to load.</div>';
      });
  };

  /* Metric chips reshade from the payload already in memory — no network. */
  var legend = document.querySelector("[data-map-metrics]");
  if (legend) {
    legend.addEventListener("click", function (evt) {
      var btn = evt.target.closest("[data-map-metric]");
      if (!btn) return;
      activeMetric = btn.dataset.mapMetric;
      legend.querySelectorAll(".pill").forEach(function (p) {
        p.classList.toggle("active", p === btn);
      });
      renderMap();
    });
  }

  /* Zoom buttons. Roaming is also enabled, so mapZoom is kept in sync by the
   * georoam handler above. */
  var zoomBox = document.querySelector("[data-map-zoom]");
  if (zoomBox) {
    zoomBox.addEventListener("click", function (evt) {
      var btn = evt.target.closest("[data-zoom]");
      if (!btn || !mapChart) return;
      var mode = btn.dataset.zoom;
      mapZoom = mode === "in" ? Math.min(mapZoom * 1.4, 8)
        : mode === "out" ? Math.max(mapZoom / 1.4, 1)
          : 1;
      mapChart.setOption({ geo: { zoom: mapZoom, center: mode === "reset" ? null : undefined } });
    });
  }

  var layerBox = document.querySelector("[data-map-layers]");
  if (layerBox) {
    layerBox.addEventListener("change", function (evt) {
      var box = evt.target.closest("[data-layer]");
      if (!box) return;
      layerState[box.dataset.layer] = box.checked;
      // High-impact events need the calendar; fetch once, on first enable.
      if (box.dataset.layer === "events" && box.checked && !layerData.events.length) {
        fetch("/api/calendar?days_back=0&days_forward=7&limit=120")
          .then(function (r) { return r.ok ? r.json() : null; })
          .then(function (p) {
            var events = (p && p.data && p.data.events) || [];
            layerData.events = events
              .filter(function (e) { return Number(e.importance) === 1; })
              .map(function (e) {
                var c = centroidFor(e.country_code);
                if (!c) return null;
                return {
                  value: c.concat([1]),
                  title: e.display_name || "Event",
                  sub: (e.currency_code || e.country_code || "") + " · high impact"
                };
              })
              .filter(Boolean);
            renderMap();
          })
          .catch(function () { renderMap(); });
        return;
      }
      renderMap();
    });
  }

  bootMap();

  /* ══ Zone A side · Rate Path ════════════════════════════════════════════ */

  var yields = DATA.yields || {};
  var rateEl = document.querySelector("[data-rate-path]");
  if (rateEl) {
    var series = (yields.series || []).filter(function (s) { return (s.data || []).length; });
    if (!series.length) {
      rateEl.innerHTML = '<div class="tempty">' +
        esc(yields.message || "Bond yields unavailable.") + "</div>";
    } else {
      var rateChart = ensureChart(rateEl);
      if (rateChart) {
        rateChart.setOption({
          backgroundColor: "transparent",
          grid: { left: 4, right: 4, top: 8, bottom: 4, containLabel: false },
          xAxis: { type: "category", show: false },
          yAxis: { type: "value", scale: true, show: false },
          tooltip: {
            trigger: "axis",
            backgroundColor: C.panel,
            borderColor: C.border,
            textStyle: { color: C.body, fontFamily: C.mono, fontSize: 11 }
          },
          series: series.slice(0, 4).map(function (s, i) {
            return {
              name: s.currency,
              type: "line",
              smooth: true,
              showSymbol: false,
              lineStyle: { width: 1.4, color: [C.accent, C.bull, C.neutral, C.bear][i % 4] },
              data: (s.data || []).map(function (p) { return [p[0], p[1]]; })
            };
          })
        }, { notMerge: true });
      }
    }
  }

  /* ══ Zone C · Inflation Path (real CPI prints) ══════════════════════════ */

  var cpiEl = document.querySelector("[data-inflation-chart]");
  if (cpiEl) {
    // Labelled by country code: DE / EU / FR all report the same currency.
    var cpi = countries.map(function (country) {
      var metric = metricOf(country, "inflation");
      return { code: country.code, value: metric ? num(metric.raw) : null };
    }).filter(function (row) { return row.value !== null; });

    if (!cpi.length) {
      cpiEl.innerHTML = '<div class="tempty">No CPI prints available.</div>';
    } else {
      cpi.sort(function (a, b) { return b.value - a.value; });
      var cpiChart = ensureChart(cpiEl);
      if (cpiChart) {
        cpiChart.setOption({
          backgroundColor: "transparent",
          grid: { left: 6, right: 6, top: 12, bottom: 20, containLabel: true },
          tooltip: {
            trigger: "axis",
            backgroundColor: C.panel,
            borderColor: C.border,
            textStyle: { color: C.body, fontFamily: C.mono, fontSize: 11 },
            valueFormatter: function (v) { return Number(v).toFixed(2) + "%"; }
          },
          xAxis: {
            type: "category",
            data: cpi.map(function (r) { return r.code; }),
            axisLine: { lineStyle: { color: C.border } },
            axisTick: { show: false },
            axisLabel: { color: C.muted, fontFamily: C.mono, fontSize: 10 }
          },
          yAxis: {
            type: "value",
            splitLine: { lineStyle: { color: C.border } },
            axisLabel: { color: C.muted, fontFamily: C.mono, fontSize: 10 }
          },
          series: [{
            type: "bar",
            barWidth: "56%",
            data: cpi.map(function (r) {
              return {
                value: r.value,
                itemStyle: {
                  color: r.value >= 3 ? C.bear : r.value <= 2 ? C.bull : C.accent,
                  borderRadius: [2, 2, 0, 0]
                }
              };
            })
          }]
        }, { notMerge: true });
      }
    }
  }

  /* ══ Lazy panel loader ══════════════════════════════════════════════════
   * Several of these endpoints hit slow third parties (CFTC bulk download,
   * eight CB RSS feeds, MyFxBook). Firing them all at once makes the terminal
   * feel broken, so panels load through a queue with at most two requests in
   * flight, in the priority order below, and only once scrolled into view.
   *
   * `/` is a public route but these APIs require auth, so a 401 is an expected
   * outcome for signed-out visitors — not an error.
   */

  var signedOut = '<div class="tempty">Sign in to unlock this feed. <a href="/login">Sign in</a></div>';

  var pct = function (v) { return (v === null || v === undefined) ? "—" : Number(v).toFixed(0) + "%"; };
  var tone = function (v) { return Number(v) > 0 ? "is-positive" : Number(v) < 0 ? "is-negative" : "is-neutral"; };

  var RENDERERS = {
    calendar: {
      url: "/api/calendar?days_back=0&days_forward=7&limit=40",
      render: function (p) {
        var events = (p && p.data && p.data.events) || [];
        if (!events.length) return '<div class="tempty">No events in the next 7 days.</div>';
        return events.slice(0, 14).map(function (e) {
          var t = e.released_at
            ? new Date(e.released_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
            : "TBD";
          var imp = Number(e.importance) === 1 ? "high" : Number(e.importance) === 2 ? "med" : "low";
          return '<a class="trow trow--cal" href="/calendar"><span class="trow__sub">' + esc(t) +
            '</span><span class="flag flag-' + esc(e.currency_code || e.country_code || "USD") +
            '"></span><span class="trow__title">' + esc(e.display_name || "Event") +
            '</span><span class="imp ' + imp + '"></span></a>';
        }).join("");
      }
    },

    cbnews: {
      url: "/api/cb/feeds",
      render: function (p) {
        var items = [];
        ((p && p.feeds) || []).forEach(function (f) {
          (f.articles || []).slice(0, 3).forEach(function (a) {
            items.push({
              bank: f.currency,
              title: a.title || a.headline || "Untitled",
              link: a.link || a.url || "#",
              when: a.pubDate || a.published || a.date || ""
            });
          });
        });
        if (!items.length) return '<div class="tempty">No central-bank headlines.</div>';
        return items.slice(0, 18).map(function (i) {
          return '<a class="trow trow--news" href="' + esc(i.link) + '" target="_blank" rel="noopener">' +
            '<span class="trow__title">' + esc(i.title) + "</span>" +
            '<span class="trow__sub">' + esc(i.bank) + (i.when ? " · " + esc(String(i.when).slice(0, 16)) : "") +
            "</span></a>";
        }).join("");
      }
    },

    rateprob: {
      url: "/api/rate-prob/all-banks",
      render: function (p) {
        var banks = (p && p.banks) || [];
        if (!banks.length) return '<div class="tempty">No meeting probabilities available.</div>';
        return banks.map(function (b) {
          var when = b.next_meeting_dt
            ? new Date(b.next_meeting_dt).toLocaleDateString([], { month: "short", day: "2-digit" })
            : "TBD";
          return '<a class="trow trow--cot" href="/rate-prob/' + esc(b.bank) + '">' +
            '<span class="trow__code">' + esc(b.bank) + "</span>" +
            '<span class="trow__title">' + esc(b.dominant_outcome || "hold") + " · " + esc(when) + "</span>" +
            "<b>" + pct(b.dominant_prob_pct) + "</b></a>";
        }).join("");
      }
    },

    retail: {
      // category=forex: the unfiltered feed is dominated by crypto pairs, which
      // are noise on a macro FX dashboard.
      url: "/api/retail-sentiment?category=forex&limit=12&sort=net",
      render: function (p) {
        var assets = (p && p.assets) || [];
        if (!assets.length) return '<div class="tempty">No retail positioning available.</div>';
        return assets.slice(0, 12).map(function (a) {
          // Retail positioning is a contrarian signal: crowded long reads red.
          var cls = a.net > 0 ? "is-negative" : a.net < 0 ? "is-positive" : "is-neutral";
          return '<a class="trow trow--cot" href="/fx-outlook">' +
            '<span class="trow__code">' + esc(a.name) + "</span>" +
            '<span class="bar-track"><span class="bar-fill ' +
            (a.net > 0 ? "bear" : "bull") + '" style="width:' + Math.min(Math.abs(a.net), 100) + '%"></span></span>' +
            '<em class="' + cls + '">' + esc(a.long) + "% L</em></a>";
        }).join("");
      }
    },

    cot: {
      url: "/api/cot?pair=EURUSD",
      render: function (p) {
        if (!p || !p.pair_label) return '<div class="tempty">COT data unavailable.</div>';
        var net = Array.isArray(p.net) ? p.net[p.net.length - 1] : p.net;
        var prev = Array.isArray(p.net) && p.net.length > 1 ? p.net[p.net.length - 2] : null;
        var delta = prev === null ? null : Number(net) - Number(prev);
        // Counts come from the API already divided by 1000.
        var k = function (v) { return (v === null || v === undefined) ? null : v + "k"; };
        var rows = [
          ["Net non-comm", k(net), tone(net)],
          ["Weekly change", delta === null ? null : (delta > 0 ? "+" : "") + delta + "k", tone(delta)],
          ["Non-comm long", k(p.noncom_long), "is-neutral"],
          ["Non-comm short", k(p.noncom_short), "is-neutral"],
          ["Open interest", k(p.open_interest), "is-neutral"]
        ];
        return '<div class="trow__sub" style="padding-bottom:4px;">' + esc(p.pair_label) + "</div>" +
          rows.map(function (r) {
            var v = r[1] === null || r[1] === undefined ? "—" : r[1];
            return '<div class="trow trow--kv"><span class="trow__title">' + esc(r[0]) +
              '</span><em class="' + r[2] + '">' + esc(v) + "</em></div>";
          }).join("") +
          '<a class="trow__sub" href="/cot" style="display:block;padding-top:6px;">All markets →</a>';
      }
    }
  };

  // Slowest / least critical last.
  var PANEL_ORDER = ["calendar", "cbnews", "rateprob", "retail", "cot"];

  var queue = [];
  var inFlight = 0;
  var MAX_CONCURRENT = 2;

  var pump = function () {
    while (inFlight < MAX_CONCURRENT && queue.length) {
      run(queue.shift());
    }
  };

  var run = function (key) {
    var panel = document.querySelector('[data-panel="' + key + '"]');
    var body = panel && panel.querySelector("[data-panel-body]");
    var spec = RENDERERS[key];
    if (!body || !spec) return;

    inFlight++;
    fetch(spec.url)
      .then(function (r) {
        if (r.status === 401 || r.status === 403) return { signedOut: true };
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (payload) {
        body.innerHTML = payload && payload.signedOut ? signedOut : spec.render(payload);
      })
      .catch(function () {
        body.innerHTML = '<div class="tempty terror">Feed unavailable.' +
          '<button class="tretry" type="button">Retry</button></div>';
        var btn = body.querySelector(".tretry");
        if (btn) {
          btn.addEventListener("click", function () {
            body.innerHTML = '<div class="tskeleton"><i></i><i></i><i></i><i></i></div>';
            queue.push(key);
            pump();
          });
        }
      })
      .finally(function () {
        inFlight--;
        pump();
      });
  };

  var queued = {};
  var enqueue = function (key) {
    if (queued[key]) return;
    queued[key] = true;
    queue.push(key);
    pump();
  };

  var panels = PANEL_ORDER
    .map(function (k) { return document.querySelector('[data-panel="' + k + '"]'); })
    .filter(Boolean);

  if (typeof IntersectionObserver !== "undefined" && panels.length) {
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        io.unobserve(entry.target);
        enqueue(entry.target.dataset.panel);
      });
    }, { rootMargin: "200px" });
    panels.forEach(function (p) { io.observe(p); });
    // Anything still off-screen after first paint loads anyway, so a panel the
    // user never scrolls to is not permanently empty.
    setTimeout(function () { PANEL_ORDER.forEach(enqueue); }, 2500);
  } else {
    PANEL_ORDER.forEach(enqueue);
  }

  /* ══ CB Terminal · Hawk/Dove ════════════════════════════════════════════ */

  var cbBanks = DATA.banks || [];
  var cbSurprises = DATA.surprises || [];
  var cbRoot = document.querySelector("[data-landing-cb]");

  if (cbRoot && cbBanks.length) {
    var cbState = { currency: cbBanks[0].currency };
    var toneClass = function (s) {
      return Number(s || 0) >= 0.25 ? "bull" : Number(s || 0) <= -0.25 ? "bear" : "neutral";
    };
    var signed = function (v) {
      var n = Number(v || 0);
      return (n >= 0 ? "+" : "") + n.toFixed(Math.abs(n) >= 10 ? 0 : 1);
    };

    var renderCb = function () {
      var bank = cbBanks.find(function (b) { return b.currency === cbState.currency; }) || cbBanks[0];
      cbState.currency = bank.currency;
      cbRoot.querySelectorAll("[data-currency]").forEach(function (btn) {
        btn.classList.toggle("active", btn.dataset.currency === bank.currency);
      });

      var components = cbRoot.querySelector("[data-cb-components]");
      if (components) {
        components.innerHTML = (bank.metrics || []).slice(0, 4).map(function (m) {
          var cls = toneClass(m.raw);
          return '<div><div class="between"><span>' + esc(m.label) + '</span><strong class="' + cls +
            '">' + esc(m.score) + '</strong></div><div class="bar-track"><div class="bar-fill ' + cls +
            '" style="width:' + Number(m.percent || 50) + '%"></div></div></div>';
        }).join("") + '<div class="muted" style="margin-top:4px;">Latest score ' +
          esc(bank.latestDate) + " — " + esc(bank.label) + "</div>";
      }

      var surpriseBox = cbRoot.querySelector("[data-cb-surprises]");
      if (surpriseBox) {
        var matched = cbSurprises.filter(function (s) { return s.currency === bank.currency; }).slice(0, 5);
        surpriseBox.innerHTML = matched.length ? matched.map(function (s) {
          var cls = Number(s.surprise || 0) >= 0 ? "bull" : "bear";
          return '<div class="cb-surprise"><span class="muted">' + esc(s.date) + "</span><span>" +
            esc(s.name) + '</span><span class="' + cls + '">' + signed(s.surprise) + "</span></div>";
        }).join("") : '<div class="muted">No recent surprises for ' + esc(bank.currency) + ".</div>";
      }

      var analysis = cbRoot.querySelector("[data-cb-analysis]");
      if (analysis) {
        analysis.innerHTML = "<strong>" + esc(bank.bank) + "</strong> is " +
          esc(String(bank.label || "").toLowerCase()) + " with a score of <strong>" +
          esc(bank.scoreText) + "</strong>.";
      }
    };

    cbRoot.addEventListener("click", function (evt) {
      var tab = evt.target.closest("[data-currency]");
      if (tab) {
        cbState.currency = tab.dataset.currency || cbState.currency;
        renderCb();
        return;
      }
      if (evt.target.closest("[data-cb-landing-analyze]")) {
        var bank = cbBanks.find(function (b) { return b.currency === cbState.currency; }) || cbBanks[0];
        var out = cbRoot.querySelector("[data-cb-analysis]");
        if (out) out.textContent = "Analyzing " + bank.bank + " communications...";
        fetch("/api/cb/analysis?bank=" + encodeURIComponent(bank.currency), { method: "POST" })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
          .then(function (res) {
            if (!res.ok) throw new Error((res.d && res.d.detail) || "Analysis failed");
            var d = res.d;
            var cls = d.stance === "hawkish" ? "bull" : d.stance === "dovish" ? "bear" : "neutral";
            out.innerHTML = '<span class="' + cls + '">' + esc(d.stance || "neutral") + "</span> — " +
              Number(d.confidence || 0) + "% confidence. " + esc(d.summary || "") +
              (d.fx_implication ? "<br>" + esc(d.fx_implication) : "");
          })
          .catch(function (e) { if (out) out.textContent = e.message || "Analysis unavailable."; });
      }
    });

    renderCb();
  }
})();
