// VidFactory shared frontend helpers.
// Subscribe to a job's progress stream and call onUpdate({status, stage, percent, ...}).
window.VF = window.VF || {};

// Show/hide the Hike & Fly fields based on the category select. Idempotent: safe to
// call repeatedly (e.g. after each htmx swap that re-renders the outing form).
window.VF.wireHikeFly = function (root) {
  const scope = root && root.querySelectorAll ? root : document;
  scope.querySelectorAll("[data-hf-toggle]").forEach((sel) => {
    if (sel._vfWired) return;
    sel._vfWired = true;
    const form = sel.closest("form");
    const panel = form && form.querySelector("[data-hf-panel]");
    if (!panel) return;
    const update = () => {
      panel.style.display = sel.value === sel.dataset.hfToggle ? "" : "none";
    };
    sel.addEventListener("change", update);
    update();
  });
};

document.addEventListener("DOMContentLoaded", () => window.VF.wireHikeFly(document));
document.body.addEventListener("htmx:load", () => window.VF.wireHikeFly(document));

// Generic "copy this textarea/element's text to the clipboard" button, delegated on the
// document so it works on any page without per-page wiring (e.g. project.html's music-credits
// boxes) — <button data-copy-target="some-id">.
document.addEventListener("click", (ev) => {
  const btn = ev.target.closest("[data-copy-target]");
  if (!btn) return;
  const el = document.getElementById(btn.dataset.copyTarget);
  if (!el) return;
  const text = "value" in el ? el.value : el.textContent;
  navigator.clipboard.writeText(text).then(
    () => {
      const original = btn.textContent;
      btn.textContent = "✅ Copied";
      setTimeout(() => { btn.textContent = original; }, 1500);
    },
    (err) => console.error("[VF:app] clipboard copy failed", err),
  );
});

window.VF.followJob = function (jobId, onUpdate) {
  console.log(`[VF:jobs] following job ${jobId}`);
  const es = new EventSource(`/events/${jobId}`);
  let lastStatus = null, lastStage = null, lastDecile = -1;
  es.addEventListener("progress", (ev) => {
    const data = JSON.parse(ev.data);
    // Log transitions only (status / stage / each 10% step), not every tick.
    const decile = Math.floor((data.percent || 0) / 10);
    if (data.status !== lastStatus || data.stage !== lastStage || decile !== lastDecile) {
      console.log(`[VF:jobs] job ${jobId} ${data.kind || ""} status=${data.status} stage="${data.stage || ""}" ` +
        `${Math.round(data.percent || 0)}%` + (data.status === "pending" ? ` queue_position=${data.queue_position}` : ""));
      lastStatus = data.status; lastStage = data.stage; lastDecile = decile;
    }
    onUpdate(data);
    if (["done", "error", "cancelled"].includes(data.status)) {
      if (data.status === "error") {
        console.error(`[VF:jobs] job ${jobId} FAILED: ${data.message || "(no message)"}`, data);
      } else {
        console.log(`[VF:jobs] job ${jobId} ${data.status}`, data);
      }
      es.close();
    }
  });
  es.addEventListener("error", (ev) => {
    console.error(`[VF:jobs] SSE connection error for job ${jobId} (readyState=${es.readyState}) — the job may still be running server-side; check /jobs`, ev);
    es.close();
  });
  return es;
};
