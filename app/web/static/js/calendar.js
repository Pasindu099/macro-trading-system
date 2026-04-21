(function () {
    const TIMEZONE_STORAGE_KEY = "macro_dashboard.calendar_timezone";
    let currentPayload = null;

    function selectedTimeZoneValue() {
        return document.getElementById("calendar-timezone")?.value || "browser";
    }

    function resolvedTimeZone() {
        const selected = selectedTimeZoneValue();
        if (selected === "browser") {
            return Intl.DateTimeFormat().resolvedOptions().timeZone;
        }
        return selected;
    }

    function formatDateTime(value) {
        return new Date(value).toLocaleString(undefined, {
            month: "short",
            day: "numeric",
            hour: "2-digit",
            minute: "2-digit",
            timeZoneName: "short",
            timeZone: resolvedTimeZone(),
        });
    }

    function formatNumber(value) {
        if (value === null || value === undefined) {
            return "N/A";
        }
        return new Intl.NumberFormat(undefined, {
            maximumFractionDigits: 2,
        }).format(value);
    }

    function activeWindow() {
        return document.querySelector("#calendar-window-control .is-active")?.dataset.window || "7";
    }

    function activeBackWindow() {
        return document.querySelector("#calendar-back-window-control .is-active")?.dataset.window || "14";
    }

    function readFilters() {
        return {
            daysBack: activeBackWindow(),
            daysForward: activeWindow(),
            country: document.getElementById("calendar-country")?.value || "",
            category: document.getElementById("calendar-category")?.value || "",
            importance: document.getElementById("calendar-importance")?.value || "",
        };
    }

    function updateFilterSummary() {
        const summary = document.getElementById("calendar-active-filters");
        if (!summary) {
            return;
        }

        const filters = readFilters();
        const parts = [];
        if (filters.country) {
            parts.push(`Country: ${filters.country}`);
        }
        if (filters.category) {
            parts.push(`Category: ${filters.category}`);
        }
        if (filters.importance) {
            const label = filters.importance === "1" ? "H" : filters.importance === "2" ? "M" : "L";
            parts.push(`Level: ${label}`);
        }

        summary.textContent = parts.length
            ? `Active filters: ${parts.join(" · ")}`
            : "Showing all events.";
    }

    function groupEvents(events) {
        return events.reduce((groups, event) => {
            const dayKey = new Date(event.released_at).toLocaleDateString(undefined, {
                weekday: "short",
                month: "short",
                day: "numeric",
                timeZone: resolvedTimeZone(),
            });
            groups[dayKey] = groups[dayKey] || [];
            groups[dayKey].push(event);
            return groups;
        }, {});
    }

    function statusClass(status) {
        if (status === "released") {
            return "is-positive";
        }
        if (status === "upcoming") {
            return "is-warning";
        }
        return "";
    }

    function importanceBadge(importance) {
        if (Number(importance) === 1) {
            return '<span class="calendar-importance is-high">H</span>';
        }
        if (Number(importance) === 2) {
            return '<span class="calendar-importance is-medium">M</span>';
        }
        return '<span class="calendar-importance is-low">L</span>';
    }

    function actualOutcomeClass(event) {
        if (event.actual === null || event.actual === undefined) {
            return "";
        }
        if (event.previous === null || event.previous === undefined) {
            return "";
        }

        const delta = Number(event.actual) - Number(event.previous);
        if (delta === 0 || event.is_positive_when_higher === null || event.is_positive_when_higher === undefined) {
            return "";
        }

        const isPositive = event.is_positive_when_higher ? delta > 0 : delta < 0;
        return isPositive ? "is-positive" : "is-negative";
    }

    function actualValueMarkup(event) {
        const actualText = `${formatNumber(event.actual)}${event.unit && event.actual !== null ? ` ${event.unit}` : ""}`;
        const outcomeClass = actualOutcomeClass(event);
        const hasSurprise = (
            event.actual !== null
            && event.actual !== undefined
            && event.estimate !== null
            && event.estimate !== undefined
            && Number(event.actual) !== Number(event.estimate)
        );
        const surpriseMarkup = hasSurprise
            ? '<span class="calendar-surprise" title="Actual differed from estimate">⚡</span>'
            : "";

        return `<span class="calendar-actual ${outcomeClass}">${actualText}${surpriseMarkup}</span>`;
    }

    function renderCalendar(payload) {
        currentPayload = payload;
        const groupsRoot = document.getElementById("calendar-groups");
        const title = document.getElementById("calendar-title");
        const meta = document.getElementById("calendar-meta");
        const grouped = groupEvents(payload.events || []);
        const backDayLabel = Number(payload.days_back) === 1 ? "day" : "days";
        const forwardDayLabel = Number(payload.days_forward) === 1 ? "day" : "days";
        const groupCount = Object.keys(grouped).length;
        const sessionLabel = groupCount === 1 ? "day" : "days";
        const timezoneLabel = selectedTimeZoneValue() === "browser" ? resolvedTimeZone() : selectedTimeZoneValue();

        title.textContent = `Past ${payload.days_back} ${backDayLabel} + Next ${payload.days_forward} ${forwardDayLabel}`;
        meta.textContent = `${payload.total_events} events across ${groupCount} ${sessionLabel} · ${timezoneLabel}`;

        if (!payload.events.length) {
            groupsRoot.innerHTML = '<div class="empty-panel">No calendar events matched the current filters.</div>';
            return;
        }

        const renderIndicatorCell = (event) => {
            if (event.canonical_name) {
                return `
                    <a class="calendar-table__link" href="/country/${event.country_code.toLowerCase()}/indicator/${event.canonical_name}">
                        ${event.display_name}
                    </a>
                `;
            }

            return `<span class="calendar-table__plain">${event.display_name}</span>`;
        };

        groupsRoot.innerHTML = Object.entries(grouped).map(([day, events]) => `
            <section class="calendar-day">
                <div class="calendar-day__header">
                    <h3>${day}</h3>
                    <span>${events.length} events</span>
                </div>
                <div class="calendar-table-wrap">
                    <table class="calendar-table">
                        <thead>
                            <tr>
                                <th>Time</th>
                                <th>Country</th>
                                <th>Indicator</th>
                                <th>Category</th>
                                <th>Level</th>
                                <th>Estimate</th>
                                <th>Previous</th>
                                <th>Actual</th>
                                <th>Status</th>
                            </tr>
                        </thead>
                        <tbody>
                            ${events.map((event) => `
                                <tr>
                                    <td>${formatDateTime(event.released_at)}</td>
                                    <td>${event.country_code} / ${event.currency_code}</td>
                                    <td>${renderIndicatorCell(event)}</td>
                                    <td>${event.primary_category}</td>
                                    <td>${importanceBadge(event.importance)}</td>
                                    <td>${formatNumber(event.estimate)}${event.unit ? ` ${event.unit}` : ""}</td>
                                    <td>${formatNumber(event.previous)}${event.unit ? ` ${event.unit}` : ""}</td>
                                    <td>${actualValueMarkup(event)}</td>
                                    <td><span class="calendar-status ${statusClass(event.status)}">${event.status}</span></td>
                                </tr>
                            `).join("")}
                        </tbody>
                    </table>
                </div>
            </section>
        `).join("");
    }

    async function loadCalendar() {
        const filters = readFilters();
        updateFilterSummary();
        const params = new URLSearchParams({
            days_back: filters.daysBack,
            days_forward: filters.daysForward,
            limit: "250",
        });
        if (filters.country) {
            params.set("country", filters.country);
        }
        if (filters.category) {
            params.set("category", filters.category);
        }
        if (filters.importance) {
            params.set("importance", filters.importance);
        }

        const response = await fetch(`/api/calendar?${params.toString()}`);
        if (!response.ok) {
            throw new Error(`HTTP ${response.status}`);
        }
        const body = await response.json();
        renderCalendar(body.data);
    }

    function initializeTimeZoneSelect() {
        const select = document.getElementById("calendar-timezone");
        if (!select) {
            return;
        }

        const saved = window.localStorage?.getItem(TIMEZONE_STORAGE_KEY);
        if (saved && Array.from(select.options).some((option) => option.value === saved)) {
            select.value = saved;
        } else {
            select.value = "browser";
        }
    }

    function bindTimeZoneSelect() {
        const select = document.getElementById("calendar-timezone");
        if (!select) {
            return;
        }

        select.addEventListener("change", function () {
            window.localStorage?.setItem(TIMEZONE_STORAGE_KEY, select.value);
            if (currentPayload) {
                renderCalendar(currentPayload);
            }
        });
    }

    function bindWindowButtons() {
        [
            "#calendar-back-window-control [data-window]",
            "#calendar-window-control [data-window]",
        ].forEach((selector) => {
            const buttons = document.querySelectorAll(selector);
            buttons.forEach((button) => {
                button.addEventListener("click", async function () {
                    buttons.forEach((item) => item.classList.remove("is-active"));
                    button.classList.add("is-active");
                    await loadCalendar();
                });
            });
        });
    }

    function bindSelect(id) {
        const element = document.getElementById(id);
        if (!element) {
            return;
        }
        element.addEventListener("change", async function () {
            updateFilterSummary();
            await loadCalendar();
        });
    }

    function bindResetFilters() {
        const button = document.getElementById("calendar-reset-filters");
        if (!button) {
            return;
        }

        button.addEventListener("click", async function () {
            const country = document.getElementById("calendar-country");
            const category = document.getElementById("calendar-category");
            const importance = document.getElementById("calendar-importance");
            const backButtons = document.querySelectorAll("#calendar-back-window-control [data-window]");
            const forwardButtons = document.querySelectorAll("#calendar-window-control [data-window]");

            if (country) {
                country.value = "";
            }
            if (category) {
                category.value = "";
            }
            if (importance) {
                importance.value = "";
            }

            backButtons.forEach((buttonEl) => {
                buttonEl.classList.toggle("is-active", buttonEl.dataset.window === "14");
            });
            forwardButtons.forEach((buttonEl) => {
                buttonEl.classList.toggle("is-active", buttonEl.dataset.window === "7");
            });

            updateFilterSummary();
            await loadCalendar();
        });
    }

    document.addEventListener("DOMContentLoaded", function () {
        initializeTimeZoneSelect();
        bindWindowButtons();
        bindSelect("calendar-country");
        bindSelect("calendar-category");
        bindSelect("calendar-importance");
        bindTimeZoneSelect();
        bindResetFilters();
        updateFilterSummary();

        loadCalendar().catch((error) => {
            const groupsRoot = document.getElementById("calendar-groups");
            const meta = document.getElementById("calendar-meta");
            if (meta) {
                meta.textContent = "Unable to load calendar events";
            }
            if (groupsRoot) {
                groupsRoot.innerHTML = '<div class="empty-panel">Calendar data could not be loaded.</div>';
            }
            console.error(error);
        });
    });
})();
