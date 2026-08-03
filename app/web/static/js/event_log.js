/* Event Reaction Log — filter/pick candidates, and the autosaving detail page. */
(() => {
    const API_BASE = "/api/admin/event-log";

    async function getJSON(url) {
        const res = await fetch(url);
        if (!res.ok) throw new Error(`GET ${url} failed: ${res.status}`);
        return res.json();
    }

    async function sendJSON(url, method, body) {
        const res = await fetch(url, {
            method,
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
        });
        if (!res.ok) throw new Error(`${method} ${url} failed: ${res.status}`);
        return res.json();
    }

    function setStatus(el, text, isError) {
        if (!el) return;
        el.textContent = text;
        el.style.color = isError ? "#dc2626" : "";
        if (text) {
            window.clearTimeout(el._clearTimer);
            el._clearTimer = window.setTimeout(() => { el.textContent = " "; }, 3000);
        }
    }

    // ── List page ────────────────────────────────────────────────────────────

    function initListPage() {
        const dateInput = document.getElementById("evl-date");
        const countrySelect = document.getElementById("evl-country");
        const importanceSelect = document.getElementById("evl-importance");
        const candidatesSelect = document.getElementById("evl-candidates");
        const logBtn = document.getElementById("evl-log-btn");
        const statusEl = document.getElementById("evl-status");

        async function refreshCandidates() {
            const params = new URLSearchParams({ date: dateInput.value });
            if (countrySelect.value) params.set("country", countrySelect.value);
            if (importanceSelect.value) params.set("importance", importanceSelect.value);

            candidatesSelect.innerHTML = "<option value=\"\">Loading…</option>";
            logBtn.disabled = true;
            try {
                const data = await getJSON(`${API_BASE}/candidates?${params}`);
                if (!data.candidates.length) {
                    candidatesSelect.innerHTML = "<option value=\"\">No releases match this filter</option>";
                    return;
                }
                candidatesSelect.innerHTML = data.candidates
                    .map((c) => {
                        const time = new Date(c.released_at).toISOString().slice(11, 16);
                        const logged = c.existing_note_id ? " (already logged)" : "";
                        return `<option value="${c.release_id}" data-existing="${c.existing_note_id || ""}">`
                            + `${time} UTC · ${c.country_code} · ${c.importance_label} · ${c.indicator_name}${logged}</option>`;
                    })
                    .join("");
                logBtn.disabled = !candidatesSelect.value;
            } catch (err) {
                candidatesSelect.innerHTML = "<option value=\"\">Failed to load — try again</option>";
            }
        }

        [dateInput, countrySelect, importanceSelect].forEach((el) => {
            el.addEventListener("change", refreshCandidates);
        });
        candidatesSelect.addEventListener("change", () => {
            logBtn.disabled = !candidatesSelect.value;
        });

        logBtn.addEventListener("click", async () => {
            const option = candidatesSelect.selectedOptions[0];
            if (!option) return;
            const existing = option.dataset.existing;
            if (existing) {
                window.location.href = `/event-log/${existing}`;
                return;
            }
            logBtn.disabled = true;
            setStatus(statusEl, "Creating event log entry…");
            try {
                const note = await sendJSON(`${API_BASE}/notes`, "POST", {
                    indicator_release_id: Number(option.value),
                });
                window.location.href = `/event-log/${note.id}`;
            } catch (err) {
                setStatus(statusEl, "Could not create the entry — try again.", true);
                logBtn.disabled = false;
            }
        });

        document.querySelectorAll(".evl-row").forEach((row) => {
            row.addEventListener("click", () => {
                window.location.href = row.dataset.href;
            });
        });

        refreshCandidates();
    }

    // ── Detail page ──────────────────────────────────────────────────────────

    function initDetailPage() {
        const root = document.getElementById("content");
        const noteId = root.dataset.noteId;
        const statusEl = document.getElementById("evd-save-status");
        const aiStatusEl = document.getElementById("evd-ai-status");

        function patchNote(field, value) {
            setStatus(statusEl, "Saving…");
            sendJSON(`${API_BASE}/notes/${noteId}`, "PATCH", { [field]: value === "" ? null : value })
                .then(() => setStatus(statusEl, "Saved."))
                .catch(() => setStatus(statusEl, "Save failed — try again.", true));
        }

        const forecastInput = document.getElementById("evd-forecast");
        const actualInput = document.getElementById("evd-actual");
        const previousInput = document.getElementById("evd-previous");
        const notesTextarea = document.getElementById("evd-manual-notes");

        forecastInput.addEventListener("blur", () => patchNote("forecast_value", forecastInput.value));
        actualInput.addEventListener("blur", () => patchNote("actual_value", actualInput.value));
        previousInput.addEventListener("blur", () => patchNote("previous_value", previousInput.value));
        notesTextarea.addEventListener("blur", () => patchNote("manual_notes", notesTextarea.value));

        const aiBtn = document.getElementById("evd-generate-ai");
        const aiText = document.getElementById("evd-ai-text");
        aiBtn.addEventListener("click", async () => {
            aiBtn.disabled = true;
            setStatus(aiStatusEl, "Generating…");
            try {
                const note = await sendJSON(`${API_BASE}/notes/${noteId}/ai`, "POST", {});
                aiText.value = note.ai_interpretation || "";
                setStatus(aiStatusEl, "Generated — edit freely, it autosaves on blur.");
            } catch (err) {
                setStatus(aiStatusEl, "Generation failed — check the OpenAI key and try again.", true);
            } finally {
                aiBtn.disabled = false;
            }
        });
        aiText.addEventListener("blur", () => patchNote("ai_interpretation", aiText.value));

        document.querySelectorAll(".evd-cell-input").forEach((input) => {
            input.addEventListener("blur", async () => {
                if (input.value === "") return;
                const instrument = input.dataset.instrument;
                const horizon = input.dataset.horizon;
                try {
                    const result = await sendJSON(`${API_BASE}/notes/${noteId}/price`, "PUT", {
                        instrument,
                        horizon,
                        raw_price: Number(input.value),
                    });
                    result.cells.forEach((cell) => {
                        const span = document.querySelector(
                            `.evd-cell-change[data-instrument="${instrument}"][data-horizon="${cell.horizon}"]`
                        );
                        if (!span) return;
                        if (cell.pip_change !== null && cell.pip_change !== undefined) {
                            span.textContent = `${cell.pip_change}p`;
                            span.className = "evd-cell-change " + (cell.pip_change > 0 ? "is-positive" : cell.pip_change < 0 ? "is-negative" : "");
                        } else if (cell.pct_change !== null && cell.pct_change !== undefined) {
                            span.textContent = `${cell.pct_change}%`;
                            span.className = "evd-cell-change " + (cell.pct_change > 0 ? "is-positive" : cell.pct_change < 0 ? "is-negative" : "");
                        } else {
                            span.textContent = "";
                            span.className = "evd-cell-change";
                        }
                    });
                } catch (err) {
                    input.style.borderColor = "#dc2626";
                }
            });
        });
    }

    window.EventLog = { initListPage, initDetailPage };
})();
