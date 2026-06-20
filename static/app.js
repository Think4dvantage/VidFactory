// VidFactory shared frontend helpers.
// Subscribe to a job's progress stream and call onUpdate({status, stage, percent, ...}).
window.VF = window.VF || {};

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
