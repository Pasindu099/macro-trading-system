/*
 * Chart export — copy a chart to the clipboard as a report-ready image, or its
 * data as a table.
 *
 * The exported image is NOT a screenshot of the dashboard. A dark panel dropped
 * into a client PDF looks broken, and the on-screen chart leans on a hover
 * tooltip that paper cannot deliver. So export re-renders into a detached
 * ECharts instance with its own theme: white surface, dark ink, direct labels on
 * the extremes, and the title / period / source baked into the image.
 *
 * Colour differs on purpose too. On screen the diverging poles stay the finance
 * convention (green/red) with a sign glyph carrying the meaning alongside hue.
 * For export they switch to blue/red: green/red measures ΔE 7.4 under deuteranopia
 * against a 15 floor, and a printed report has no tooltip to fall back on.
 */
(function () {
    "use strict";

    var EXPORT = {
        surface: "#ffffff",
        ink: "#0b0b0b",
        inkSecondary: "#52514e",
        muted: "#898781",
        grid: "#e1e0d9",
        axis: "#c3c2b7",
        positive: "#2a78d6",   // blue  — passes CVD against red (ΔE 21.6)
        negative: "#e34948",   // red
        neutral: "#c3c2b7",
        font: "Roboto, system-ui, -apple-system, 'Segoe UI', sans-serif",
    };

    var PIXEL_RATIO = 2;

    function canCopyImage() {
        return Boolean(
            window.isSecureContext &&
            navigator.clipboard &&
            window.ClipboardItem
        );
    }

    /* Render the export option into a detached node so the on-screen chart is
       never disturbed — no theme flicker, no resize, no lost hover state. */
    function renderOffscreen(option, width, height) {
        if (!window.echarts) return null;

        var holder = document.createElement("div");
        holder.style.cssText =
            "position:fixed;left:-10000px;top:0;pointer-events:none;" +
            "width:" + width + "px;height:" + height + "px;";
        document.body.appendChild(holder);

        var instance = window.echarts.init(holder, null, {
            renderer: "canvas",
            width: width,
            height: height,
        });
        instance.setOption(option, true);

        var url = instance.getDataURL({
            type: "png",
            pixelRatio: PIXEL_RATIO,
            backgroundColor: EXPORT.surface,
        });

        instance.dispose();
        document.body.removeChild(holder);
        return url;
    }

    function dataUrlToBlob(url) {
        var parts = url.split(",");
        var binary = atob(parts[1]);
        var bytes = new Uint8Array(binary.length);
        for (var i = 0; i < binary.length; i += 1) {
            bytes[i] = binary.charCodeAt(i);
        }
        return new Blob([bytes], { type: "image/png" });
    }

    function downloadDataUrl(url, filename) {
        var link = document.createElement("a");
        link.href = url;
        link.download = filename;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    }

    function flash(button, message, isError) {
        var original = button.dataset.label || button.textContent;
        button.dataset.label = original;
        button.textContent = message;
        button.classList.toggle("is-error", Boolean(isError));
        button.classList.toggle("is-done", !isError);
        window.setTimeout(function () {
            button.textContent = original;
            button.classList.remove("is-done", "is-error");
        }, 1800);
    }

    function copyImage(button, spec) {
        var option;
        try {
            option = spec.exportOption();
        } catch (error) {
            flash(button, "Chart not ready", true);
            return;
        }

        var url = renderOffscreen(option, spec.width || 960, spec.height || 460);
        if (!url) {
            flash(button, "Chart not ready", true);
            return;
        }

        // Firefox still gates ClipboardItem for images, and the API needs a
        // secure context. Falling back to a download keeps the feature usable
        // rather than failing silently.
        if (!canCopyImage()) {
            downloadDataUrl(url, spec.filename + ".png");
            flash(button, "Downloaded");
            return;
        }

        navigator.clipboard
            .write([new window.ClipboardItem({ "image/png": dataUrlToBlob(url) })])
            .then(function () { flash(button, "Copied"); })
            .catch(function () {
                downloadDataUrl(url, spec.filename + ".png");
                flash(button, "Downloaded");
            });
    }

    /* Tab-separated, so it pastes as a real table into Excel and Word rather
       than a single mashed cell. */
    function copyTable(button, spec) {
        var rows;
        try {
            rows = spec.table();
        } catch (error) {
            flash(button, "No data", true);
            return;
        }
        if (!rows || !rows.length) {
            flash(button, "No data", true);
            return;
        }

        var tsv = rows.map(function (row) {
            return row.map(function (cell) {
                return cell === null || cell === undefined ? "" : String(cell);
            }).join("\t");
        }).join("\n");

        if (!navigator.clipboard || !window.isSecureContext) {
            flash(button, "Needs HTTPS", true);
            return;
        }
        navigator.clipboard.writeText(tsv)
            .then(function () { flash(button, "Copied"); })
            .catch(function () { flash(button, "Copy failed", true); });
    }

    /* Build the toolbar next to a chart. `spec` supplies exportOption(), table(),
       a filename stem, and the export canvas size. */
    function attach(container, spec) {
        if (!container) return;

        var bar = document.createElement("div");
        bar.className = "chart-actions";

        var imageButton = document.createElement("button");
        imageButton.type = "button";
        imageButton.className = "chart-action";
        imageButton.textContent = canCopyImage() ? "Copy image" : "Download image";
        imageButton.addEventListener("click", function () {
            copyImage(imageButton, spec);
        });

        var tableButton = document.createElement("button");
        tableButton.type = "button";
        tableButton.className = "chart-action";
        tableButton.textContent = "Copy data";
        tableButton.addEventListener("click", function () {
            copyTable(tableButton, spec);
        });

        bar.appendChild(imageButton);
        bar.appendChild(tableButton);
        container.appendChild(bar);
    }

    window.MacroChartExport = {
        attach: attach,
        theme: EXPORT,
        canCopyImage: canCopyImage,
    };
})();
