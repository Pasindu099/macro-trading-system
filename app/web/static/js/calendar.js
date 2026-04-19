(function () {
    function fmtTime(value) {
        return new Date(value).toLocaleTimeString(undefined, {
            hour: "2-digit",
            minute: "2-digit",
            timeZoneName: "short",
        });
    }

    function fmt(value) {
        if (value === null || value === undefined) return "—";
        return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
    }

    function activeWindow() {
        return document.querySelector(".sidebar-btn.is-active[data-window]")?.dataset.window || "7";
    }

    function readFilters() {
        return {
            daysForward: activeWindow(),
            country: document.getElementById("calendar-country")?.value || "",
            category: document.getElementById("calendar-category")?.value || "",
            importance: document.getElementById("calendar-importance")?.value || "",
        };
    }

    function groupByDay(events) {
        return events.reduce((acc, ev) => {
            const d = new Date(ev.released_at);
            const key = d.toLocaleDateString(undefined, { weekday: "long", year: "numeric", month: "long", day: "numeric" });
            acc[key] = acc[key] || [];
            acc[key].push(ev);
            return acc;
        }, {});
    }

    function tierClass(tier) {
        if (tier === 1) return "tier-1";
        if (tier === 2) return "tier-2";
        return "";
    }

    function renderCalendar(payload) {
        const root = document.getElementById("calendar-groups");
        const title = document.getElementById("calendar-title");
        const meta = document.getElementById("calendar-meta");
        const grouped = groupByDay(payload.events || []);

        title.textContent = `NEXT ${payload.days_forward}D — ${payload.total_events} EVENTS`;
        meta.textContent = `${Object.keys(grouped).length} SESSION(S)`;

        if (!payload.events.length) {
            root.innerHTML = `<div style="padding:24px 14px;font-size:11px;color:var(--muted)">NO EVENTS MATCHED THE CURRENT FILTERS.</div>`;
            return;
        }

        root.innerHTML = Object.entries(grouped).map(([day, events]) => `
            <div class="cal-day">
                <div class="cal-day__hdr">
                    <span class="cal-day__date">${day.toUpperCase()}</span>
                    <span class="cal-day__count">${events.length} EVENT(S)</span>
                </div>
                <table class="cal-table">
                    <thead>
                        <tr>
                            <th>TIME</th>
                            <th>CCY</th>
                            <th>INDICATOR</th>
                            <th>CAT</th>
                            <th>T</th>
                            <th>PREV</th>
                            <th>EST</th>
                            <th>ACTUAL</th>
                            <th>SURPRISE</th>
                            <th>STATUS</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${events.map((ev) => {
                            const surprise = ev.surprise !== null && ev.surprise !== undefined
                                ? `<span class="${ev.surprise >= 0 ? "is-positive" : "is-negative"}">${ev.surprise >= 0 ? "+" : ""}${fmt(ev.surprise)}</span>`
                                : "—";
                            return `
                            <tr>
                                <td class="num">${fmtTime(ev.released_at)}</td>
                                <td style="color:var(--accent);font-weight:700">${ev.currency_code}</td>
                                <td>
                                    <a class="cal-table__link" href="/country/${ev.country_code.toLowerCase()}/indicator/${ev.canonical_name}">
                                        ${ev.display_name}
                                    </a>
                                </td>
                                <td style="color:var(--muted)">${ev.primary_category.toUpperCase().slice(0,4)}</td>
                                <td><span class="tier-pip ${tierClass(ev.importance)}">${ev.importance}</span></td>
                                <td class="num">${fmt(ev.previous)}${ev.unit ? " " + ev.unit : ""}</td>
                                <td class="num">${fmt(ev.estimate)}${ev.unit ? " " + ev.unit : ""}</td>
                                <td class="num" style="font-weight:700;color:var(--text)">${fmt(ev.actual)}${ev.unit && ev.actual !== null ? " " + ev.unit : ""}</td>
                                <td class="num">${surprise}</td>
                                <td><span class="cal-status ${ev.status}">${ev.status.toUpperCase()}</span></td>
                            </tr>`;
                        }).join("")}
                    </tbody>
                </table>
            </div>
        `).join("");
    }

    async function loadCalendar() {
        const filters = readFilters();
        const params = new URLSearchParams({ days_back: "1", days_forward: filters.daysForward, limit: "250" });
        if (filters.country) params.set("country", filters.country);
        if (filters.category) params.set("category", filters.category);
        if (filters.importance) params.set("importance", filters.importance);

        const res = await fetch(`/api/calendar?${params.toString()}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const body = await res.json();
        renderCalendar(body.data);
    }

    document.addEventListener("DOMContentLoaded", function () {
        // Sidebar window buttons
        document.querySelectorAll(".sidebar-btn[data-window]").forEach((btn) => {
            btn.addEventListener("click", async function () {
                document.querySelectorAll(".sidebar-btn[data-window]").forEach((b) => b.classList.remove("is-active"));
                btn.classList.add("is-active");
                await loadCalendar();
            });
        });

        // Selects
        ["calendar-country", "calendar-category", "calendar-importance"].forEach((id) => {
            const el = document.getElementById(id);
            if (el) el.addEventListener("change", loadCalendar);
        });

        loadCalendar().catch((err) => {
            const root = document.getElementById("calendar-groups");
            const meta = document.getElementById("calendar-meta");
            if (meta) meta.textContent = "ERROR";
            if (root) root.innerHTML = `<div style="padding:24px 14px;font-size:11px;color:var(--muted)">CALENDAR DATA COULD NOT BE LOADED.</div>`;
            console.error(err);
        });
    });
})();
