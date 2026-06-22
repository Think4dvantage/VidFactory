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

window.VF.followJob = function (jobId, onUpdate) {
  console.log(`[VF:jobs] following job ${jobId}`);
  const es = new EventSource(`/events/${jobId}`);
  es.addEventListener("progress", (ev) => {
    const data = JSON.parse(ev.data);
    onUpdate(data);
    if (["done", "error", "cancelled"].includes(data.status)) {
      console.log(`[VF:jobs] job ${jobId} ${data.status}`);
      es.close();
    }
  });
  es.addEventListener("error", () => {
    console.error(`[VF:jobs] SSE error for job ${jobId}`);
    es.close();
  });
  return es;
};
