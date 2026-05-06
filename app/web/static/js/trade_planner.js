(() => {
    window.__macroComponents = window.__macroComponents || {};
    window.__macroComponents.tradePlanner = "booting";
    const root = document.querySelector("[data-trade-planner]");
    if (!root) return;

    const dashboard = window.__macroDashboardData || {};
    const pairUniverse = [
        ...(dashboard.plannerPairs || []),
        ...(dashboard.yieldDifferentials?.pairs || []).map((row) => row.name || row.label).filter(Boolean),
        "EUR/USD", "GBP/USD", "USD/JPY", "AUD/USD", "USD/CAD", "USD/CHF", "NZD/USD",
    ];
    const checks = [
        ["w1", "Week ahead + major events", "Flag high-impact data, CB speeches, decisions"],
        ["w2", "Central bank expectations", "What is priced in? Any shift expected?"],
        ["w3", "Growth, inflation & labour trends", "Recent surprises feeding into CB expectations"],
        ["w4", "Research reports", "IB strategy notes, bias updates, new insight"],
        ["w5", "Positioning - COT", "Use when it conflicts with higher-layer confluence"],
        ["t6", "Intermarket analysis + risk tone", "Equities, bonds, commodities, vol"],
        ["t7", "Differentials", "Requires 4 of 7 confirmations below"],
        ["t8", "Retail sentiment", "Short-term tactical overlay"],
        ["t9", "Technical setup and trigger", "Trend, level, catalyst, invalidation"],
    ];
    const diffs = [
        ["d1", "Real yield difference"],
        ["d2", "2Y yield difference"],
        ["d3", "10Y yield difference"],
        ["d4", "Inflation expectations difference"],
        ["d5", "1M risk reversal"],
        ["d6", "CESI difference"],
        ["d7", "CTOT difference"],
    ];
    const state = JSON.parse(localStorage.getItem("macroTradePlanner") || "{}");
    state.checks ||= {};
    state.diffs ||= {};
    state.journal ||= [];

    const save = () => localStorage.setItem("macroTradePlanner", JSON.stringify(state));
    const esc = (v) => String(v || "").replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
    const selectedCount = (obj) => Object.values(obj).filter((v) => v === "yes").length;

    const renderChecks = () => {
        root.querySelector("[data-planner-checklist]").innerHTML = checks.map(([id, title, sub], index) => `
            <div class="planner-row">
                <span>${index + 1}</span>
                <strong>${esc(title)}<small>${esc(sub)}</small></strong>
                ${id === "t7" ? `<em data-diff-inline>${selectedCount(state.diffs)}/7</em>` : ""}
                <button class="${state.checks[id] === "yes" ? "is-yes" : ""}" type="button" data-check="${id}" data-value="yes">YES</button>
                <button class="${state.checks[id] === "no" ? "is-no" : ""}" type="button" data-check="${id}" data-value="no">NO</button>
            </div>
            ${id === "t7" ? `<div class="planner-diff-list">${diffs.map(([diffId, label]) => `<div><span>${esc(label)}</span><button class="${state.diffs[diffId] === "yes" ? "is-yes" : ""}" type="button" data-diff="${diffId}" data-value="yes">Y</button><button class="${state.diffs[diffId] === "no" ? "is-no" : ""}" type="button" data-diff="${diffId}" data-value="no">N</button></div>`).join("")}</div>` : ""}
        `).join("");
    };

    const renderScore = () => {
        const count = selectedCount(state.checks);
        const diffCount = selectedCount(state.diffs);
        const ready = count >= 7 && diffCount >= 4;
        const caution = count >= 5 || diffCount >= 3;
        const verdict = ready ? "Ready" : caution ? "Caution" : "Not ready";
        const cls = ready ? "go" : caution ? "caution" : "no";
        root.querySelector("[data-planner-score]").textContent = `${count}/9`;
        root.querySelector("[data-planner-verdict]").textContent = verdict;
        root.querySelector("[data-planner-verdict]").className = cls;
        root.querySelector("[data-planner-progress]").style.width = `${Math.round((count / 9) * 100)}%`;
        root.querySelector("[data-planner-progress]").className = cls;
        root.querySelector("[data-planner-diff]").textContent = `${diffCount}/7`;
        root.querySelector("[data-diff-inline]")?.replaceChildren(document.createTextNode(`${diffCount}/7`));
    };

    const renderJournal = () => {
        const journal = root.querySelector("[data-planner-journal]");
        journal.innerHTML = state.journal.length ? state.journal.map((item) => `
            <div class="journal-item">
                <div class="journal-meta"><span>${esc(item.date)}</span><span>${esc(item.direction)}</span><span>R:R ${esc(item.rr)}</span></div>
                <div class="journal-pair">${esc(item.pair)}</div>
                <small>Entry ${esc(item.entry)} / Stop ${esc(item.stop)} / Target ${esc(item.target)} / Risk ${esc(item.risk)}%</small>
            </div>
        `).join("") : '<div class="black-empty">No saved setups yet.</div>';
    };

    const hydrateSetupOptions = () => {
        const pairInput = root.querySelector('[data-setup-field="pair"]');
        if (!pairInput) return;
        const listId = "planner-pair-options";
        pairInput.setAttribute("list", listId);
        if (!document.getElementById(listId)) {
            const list = document.createElement("datalist");
            list.id = listId;
            [...new Set(pairUniverse)].forEach((pair) => {
                const option = document.createElement("option");
                option.value = pair;
                list.appendChild(option);
            });
            root.appendChild(list);
        }
        if (!pairInput.value && pairUniverse.length) pairInput.value = pairUniverse[0];
    };

    const rr = () => {
        const entry = Number(root.querySelector('[data-setup-field="entry"]').value);
        const stop = Number(root.querySelector('[data-setup-field="stop"]').value);
        const target = Number(root.querySelector('[data-setup-field="target"]').value);
        const risk = Math.abs(entry - stop);
        const reward = Math.abs(target - entry);
        const value = risk > 0 && reward > 0 ? (reward / risk) : 0;
        const text = value ? value.toFixed(2) : "--";
        root.querySelector("[data-planner-rr]").textContent = `R:R ${text}`;
        root.querySelector("[data-planner-rr]").className = `planner-rr ${value >= 2 ? "good" : value >= 1.3 ? "ok" : value ? "bad" : ""}`;
        return text;
    };

    const tick = () => {
        root.querySelector("[data-planner-clock]").textContent = `${new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", timeZone: "UTC" })} UTC`;
    };

    root.addEventListener("click", (event) => {
        const tab = event.target.closest("[data-planner-tab]");
        if (tab) {
            const key = tab.dataset.plannerTab;
            root.querySelectorAll("[data-planner-tab]").forEach((btn) => btn.classList.toggle("is-active", btn === tab));
            root.querySelectorAll("[data-planner-panel]").forEach((panel) => panel.classList.toggle("is-active", panel.dataset.plannerPanel === key));
        }
        const check = event.target.closest("[data-check]");
        if (check) {
            state.checks[check.dataset.check] = check.dataset.value;
            save(); renderChecks(); renderScore();
        }
        const diff = event.target.closest("[data-diff]");
        if (diff) {
            state.diffs[diff.dataset.diff] = diff.dataset.value;
            state.checks.t7 = selectedCount(state.diffs) >= 4 ? "yes" : "no";
            save(); renderChecks(); renderScore();
        }
        if (event.target.closest("[data-save-plan]")) {
            const read = (name) => root.querySelector(`[data-setup-field="${name}"]`).value;
            state.journal.unshift({ date: new Date().toISOString().slice(0, 10), pair: read("pair"), direction: read("direction"), entry: read("entry"), stop: read("stop"), target: read("target"), risk: read("risk"), rr: rr() });
            state.journal = state.journal.slice(0, 12);
            save(); renderJournal();
        }
    });
    root.addEventListener("input", (event) => {
        if (event.target.matches("[data-setup-field]")) rr();
        if (event.target.matches("[data-planner-notes]")) {
            state.notes = event.target.value;
            save();
        }
    });

    renderChecks();
    renderScore();
    renderJournal();
    hydrateSetupOptions();
    root.querySelector("[data-planner-notes]").value = state.notes || "";
    rr();
    tick();
    setInterval(tick, 30000);
    window.__macroComponents.tradePlanner = "loaded";
})();
