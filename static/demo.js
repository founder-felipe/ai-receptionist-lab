/* ============================================================
   The Rise AI — Receptionist Demo (client-side glue)
   - Loads scenarios.json
   - Wires /ring response → SSE connect URL rewrite
   - Plays scripted scenarios + free-type input
   - Pulses panels on partial swap
   - Reset clears UI
   ============================================================ */

(() => {
  "use strict";

  // Module state
  let callId = null;
  let scenarios = [];
  let scenarioTimers = [];
  let eventSource = null;

  // DOM cache
  const $ = (sel) => document.querySelector(sel);
  const transcriptDiv      = () => $("#transcript");
  const smsDiv             = () => $("#sms-stream");
  const notifyDiv          = () => $("#notify-stream");
  const ownerDiv           = () => $("#owner-notified-stream");
  const leadsWrap          = () => $("#leads-wrap");
  const leadsBody          = () => $("#leads-tbody");
  const scenarioSelect     = () => $("#scenario-select");
  const ringStatus         = () => $("#ring-status");

  // ------------------------------------------------------------
  // 1. Load scenarios on DOM ready
  // ------------------------------------------------------------
  document.addEventListener("DOMContentLoaded", async () => {
    try {
      const res = await fetch("/static/scenarios.json", { cache: "no-store" });
      scenarios = await res.json();
      const sel = scenarioSelect();
      sel.innerHTML = "";
      scenarios.forEach((s, i) => {
        const opt = document.createElement("option");
        opt.value = String(i);
        opt.textContent = s.name;
        sel.appendChild(opt);
      });
    } catch (err) {
      console.error("[demo] failed to load scenarios.json", err);
      const sel = scenarioSelect();
      if (sel) sel.innerHTML = '<option value="">(none)</option>';
    }

    bindRing();
    bindUtteranceForm();
    bindPlayScenario();
    bindReset();
    bindPulseOnSwap();
  });

  // ------------------------------------------------------------
  // 2. Ring button → /ring → rewrite SSE URLs with call_id
  // ------------------------------------------------------------
  function bindRing() {
    const btn = $("#ring-btn");
    if (!btn) return;

    // HTMX fires this on every htmx-driven request.
    btn.addEventListener("htmx:afterRequest", (evt) => {
      if (!evt.detail || !evt.detail.xhr) return;
      if (evt.detail.failed) {
        console.warn("[demo] /ring failed", evt.detail.xhr);
        return;
      }
      try {
        const body = JSON.parse(evt.detail.xhr.responseText || "{}");
        if (!body.call_id) {
          console.warn("[demo] /ring response missing call_id", body);
          return;
        }
        callId = body.call_id;
        connectEventStream(callId);
        setRingStatus("ringing");
      } catch (e) {
        console.error("[demo] could not parse /ring response", e);
      }
    });
  }

  function connectEventStream(id) {
    if (eventSource) {
      eventSource.close();
      eventSource = null;
    }

    eventSource = new EventSource(`/events?call_id=${encodeURIComponent(id)}`);

    eventSource.addEventListener("transcript_line", (evt) => {
      appendHtml(transcriptDiv(), evt.data, "beforeend");
    });

    eventSource.addEventListener("sms_sent", (evt) => {
      appendHtml(smsDiv(), evt.data, "replace");
    });

    eventSource.addEventListener("notify_result", (evt) => {
      appendHtml(notifyDiv(), evt.data, "replace");
    });

    eventSource.addEventListener("owner_notified", (evt) => {
      appendHtml(ownerDiv(), evt.data, "beforeend");
    });

    eventSource.addEventListener("lead_saved", (evt) => {
      appendHtml(leadsBody(), evt.data, "beforeend");
    });

    eventSource.addEventListener("CLOSE", () => {
      eventSource.close();
      eventSource = null;
      setRingStatus("complete");
    });

    eventSource.onerror = () => {
      // EventSource fires an error when the server closes a completed stream.
      // Do not show this as a demo failure; functional errors surface in fetch
      // responses and browser console exceptions.
      if (eventSource && eventSource.readyState === EventSource.CLOSED) {
        eventSource = null;
      }
    };
  }

  function appendHtml(target, html, mode) {
    if (!target || !html) return;
    target.querySelectorAll(".panel__empty").forEach((el) => el.remove());
    if (mode === "replace") {
      target.innerHTML = html;
    } else {
      target.insertAdjacentHTML("beforeend", html);
    }
    const panel = target.closest(".panel") || target;
    panel.classList.add("pulse");
    setTimeout(() => panel.classList.remove("pulse"), 600);
  }

  function renderTranscriptLine(role, content) {
    const roleText = escapeHtml(role || "receptionist");
    const contentText = escapeHtml(content || "");
    return `<div class="transcript-line transcript-line--${roleText}"><span class="transcript-line__role">${roleText}</span><span class="transcript-line__content">${contentText}</span></div>`;
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    })[ch]);
  }

  function setRingStatus(status) {
    const el = ringStatus();
    if (!el) return;
    el.dataset.status = status;
    const label = el.querySelector(".call-card__status-label");
    if (label) label.textContent = `status: ${status}`;
  }

  // ------------------------------------------------------------
  // 3. Free-type input → POST /turn JSON
  // ------------------------------------------------------------
  function bindUtteranceForm() {
    const form = $("#utterance-form");
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = $("#utterance-input");
      const utterance = (input.value || "").trim();
      if (!utterance) return;
      if (!callId) {
        console.warn("[demo] no active call — ring first");
        return;
      }
      await sendTurn(utterance);
      input.value = "";
    });
  }

  async function sendTurn(utterance) {
    try {
      const res = await fetch("/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ call_id: callId, utterance }),
      });
      if (!res.ok) {
        console.warn("[demo] /turn !ok", res.status);
        return null;
      }
      return await res.json();
    } catch (e) {
      console.error("[demo] /turn failed", e);
      return null;
    }
  }

  // ------------------------------------------------------------
  // 4. Play scenario button — 1200ms interval, stops on CLOSE
  // ------------------------------------------------------------
  function bindPlayScenario() {
    const btn = $("#play-scenario-btn");
    if (!btn) return;
    btn.addEventListener("click", async () => {
      if (!callId) {
        console.warn("[demo] ring first");
        return;
      }
      const idx = parseInt(scenarioSelect().value, 10);
      const scenario = scenarios[idx];
      if (!scenario || !scenario.utterances) return;

      cancelScenarioTimers();

      for (let i = 0; i < scenario.utterances.length; i++) {
        const utterance = scenario.utterances[i];
        // Schedule each utterance at i * 1200ms; allow CLOSE to interrupt.
        await new Promise((resolve) => {
          const t = setTimeout(async () => {
            const result = await sendTurn(utterance);
            // Stop early if state machine has closed
            if (result && result.state === "CLOSE") {
              cancelScenarioTimers();
            }
            resolve();
          }, 1200);
          scenarioTimers.push(t);
        });
      }
    });
  }

  function cancelScenarioTimers() {
    scenarioTimers.forEach((t) => clearTimeout(t));
    scenarioTimers = [];
  }

  // ------------------------------------------------------------
  // 5. Reset button — clear UI client-side after server ack
  // ------------------------------------------------------------
  function bindReset() {
    const btn = $("#reset-btn");
    if (!btn) return;
    btn.addEventListener("htmx:afterRequest", (evt) => {
      if (evt.detail && evt.detail.failed) return;
      cancelScenarioTimers();
      if (eventSource) {
        eventSource.close();
        eventSource = null;
      }
      callId = null;
      if (transcriptDiv()) transcriptDiv().innerHTML = '<p class="panel__empty">Click "Ring the venue" to begin.</p>';
      if (smsDiv()) smsDiv().innerHTML = '<p class="panel__empty">No SMS sent yet.</p>';
      if (notifyDiv()) notifyDiv().innerHTML = '<p class="panel__empty">No notifications yet.</p>';
      if (ownerDiv()) ownerDiv().innerHTML = "";
      if (leadsBody()) leadsBody().innerHTML = "";
      setRingStatus("idle");
    });
  }

  // ------------------------------------------------------------
  // 6. Pulse on any HTMX swap
  // ------------------------------------------------------------
  function bindPulseOnSwap() {
    document.body.addEventListener("htmx:afterSwap", (evt) => {
      const target = evt.detail && evt.detail.target;
      if (!target) return;
      // Find nearest panel for the pulse flash
      const panel = target.closest(".panel") || target;
      panel.classList.add("pulse");
      setTimeout(() => panel.classList.remove("pulse"), 600);
    });
  }
})();
