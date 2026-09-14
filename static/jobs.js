// Job Queue page: poll /api/jobs and render a live table with cancel buttons. Plain polling
// rather than N EventSource connections — simpler, and this page shows every job at once rather
// than following one at a time the way project.html's per-build progress bars do.
(() => {
  const body = document.getElementById("jobs-body");
  if (!body) return;
  const log = (...a) => console.log("[VF:jobs-page]", ...a);

  const STATUS_CLASS = {
    running: "bg-sky-700",
    pending: "bg-slate-600",
    done: "bg-emerald-700",
    error: "bg-red-800",
    cancelled: "bg-slate-700",
  };

  function fmtElapsed(seconds) {
    if (seconds == null) return "—";
    const m = Math.floor(seconds / 60);
    const s = Math.round(seconds % 60);
    return m ? `${m}m ${s}s` : `${s}s`;
  }

  function statusLabel(job) {
    if (job.status === "pending") {
      return job.queue_position > 0 ? `queued — ${job.queue_position} ahead` : "queued — next up";
    }
    if (job.status === "running") return job.stage || "running";
    return job.status;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function row(job) {
    const statusClass = STATUS_CLASS[job.status] || "bg-slate-600";
    const cancellable = job.status === "pending" || job.status === "running";
    return `
      <tr class="border-t border-slate-700/60">
        <td class="px-2 py-1"><a class="text-sky-300 hover:underline" href="/projects/${job.project_id}">Project #${job.project_id}</a></td>
        <td class="px-2 py-1">${escapeHtml(job.kind)}${job.title ? ` <span class="text-slate-400">— ${escapeHtml(job.title)}</span>` : ""}</td>
        <td class="px-2 py-1"><span class="text-xs rounded px-2 py-0.5 ${statusClass}">${escapeHtml(statusLabel(job))}</span></td>
        <td class="px-2 py-1">
          <div class="flex items-center gap-2">
            <progress max="100" value="${job.percent || 0}" class="w-24"></progress>
            <span class="text-xs text-slate-400">${job.percent || 0}%</span>
          </div>
        </td>
        <td class="px-2 py-1 text-slate-400">${fmtElapsed(job.elapsed_seconds)}</td>
        <td class="px-2 py-1">${cancellable
          ? `<button type="button" data-cancel="${job.id}" class="text-xs rounded bg-red-900 px-2 py-0.5 hover:bg-red-800 text-red-200">✕ cancel</button>`
          : ""}</td>
      </tr>`;
  }

  async function refresh() {
    let jobs;
    try {
      jobs = (await (await fetch("/api/jobs")).json()).data;
    } catch (e) {
      console.error("[VF:jobs-page] failed to load /api/jobs", e);
      return;
    }
    jobs.reverse(); // registry.list() returns creation order; newest first reads better here
    body.innerHTML = jobs.length
      ? jobs.map(row).join("")
      : `<tr><td colspan="6" class="px-2 py-3 text-slate-400">No jobs yet.</td></tr>`;
  }

  body.addEventListener("click", async (ev) => {
    const btn = ev.target.closest("[data-cancel]");
    if (!btn) return;
    btn.disabled = true;
    log("cancelling job", btn.dataset.cancel);
    try {
      await fetch(`/api/jobs/${btn.dataset.cancel}/cancel`, { method: "POST" });
    } catch (e) {
      console.error("[VF:jobs-page] cancel failed", e);
    }
    refresh();
  });

  refresh();
  setInterval(refresh, 2000);
})();
