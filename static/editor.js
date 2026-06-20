// Highlight editor: scrub the full flight, mark named IN/OUT highlights, manage the list + timeline.
(() => {
  const root = document.getElementById("editor");
  if (!root) return;
  const pid = root.dataset.project;
  const api = `/api/projects/${pid}/highlights`;
  const player = document.getElementById("player");
  const canvas = document.getElementById("timeline");
  const ctx = canvas.getContext("2d");

  let highlights = [];
  let inT = null, outT = null;

  const ROLE_COLOR = { normal: "#0ea5e9", launch: "#10b981", landing: "#f43f5e" };
  const fmt = (s) => {
    if (s == null || isNaN(s)) return "—";
    s = Math.max(0, s);
    const m = Math.floor(s / 60), sec = (s % 60).toFixed(1).padStart(4, "0");
    return `${m}:${sec}`;
  };
  const log = (...a) => console.log("[VF:editor]", ...a);

  async function load() {
    const r = await fetch(api);
    highlights = (await r.json()).data || [];
    log("loaded", highlights.length, "highlights");
    renderList();
    draw();
  }

  function renderList() {
    document.getElementById("count").textContent = highlights.length;
    const el = document.getElementById("hl-list");
    el.innerHTML = "";
    highlights.forEach((h) => {
      const row = document.createElement("div");
      row.className = "flex items-center justify-between py-1 gap-2";
      const tag = h.role !== "normal" ? ` [${h.role}]` : "";
      const flags = (h.make_short ? "🎬" : "") + (h.use_in_summary ? "" : " (excl)");
      row.innerHTML =
        `<button class="text-left flex-1 hover:text-sky-300" data-seek="${h.start}">` +
        `<b>${escapeHtml(h.name)}</b>${tag} <span class="text-slate-500">${fmt(h.start)}–${fmt(h.end)}</span> ${flags}</button>` +
        `<span class="flex gap-1 shrink-0">` +
        `<button class="rounded bg-slate-700 hover:bg-slate-600 px-2" data-edit="${h.id}">edit</button>` +
        `<button class="rounded bg-rose-800 hover:bg-rose-700 px-2" data-del="${h.id}">✕</button></span>`;
      el.appendChild(row);
    });
  }

  function draw() {
    const w = (canvas.width = canvas.clientWidth);
    const h = canvas.height;
    const dur = player.duration || 0;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#1e293b";
    ctx.fillRect(0, 0, w, h);
    if (!dur) return;
    highlights.forEach((hl) => {
      const x = (hl.start / dur) * w;
      const ww = Math.max(2, ((hl.end - hl.start) / dur) * w);
      ctx.fillStyle = ROLE_COLOR[hl.role] || ROLE_COLOR.normal;
      ctx.fillRect(x, 6, ww, h - 20);
    });
    if (inT != null && outT != null && outT > inT) {
      ctx.fillStyle = "rgba(245,158,11,0.5)";
      ctx.fillRect((inT / dur) * w, 0, ((outT - inT) / dur) * w, h);
    }
    const px = (player.currentTime / dur) * w;
    ctx.fillStyle = "#e2e8f0";
    ctx.fillRect(px - 1, 0, 2, h);
  }

  function escapeHtml(s) {
    const d = document.createElement("div");
    d.textContent = s || "";
    return d.innerHTML;
  }

  function setForm(h) {
    document.getElementById("edit-id").value = h ? h.id : "";
    document.getElementById("f-name").value = h ? h.name : "";
    document.getElementById("f-comment").value = h ? (h.comment || "") : "";
    document.getElementById("f-role").value = h ? h.role : "normal";
    document.getElementById("f-summary").checked = h ? h.use_in_summary : true;
    document.getElementById("f-short").checked = h ? h.make_short : false;
    inT = h ? h.start : null;
    outT = h ? h.end : null;
    document.getElementById("in-label").textContent = fmt(inT);
    document.getElementById("out-label").textContent = fmt(outT);
    document.getElementById("save-btn").textContent = h ? "Save changes" : "Add highlight";
    document.getElementById("cancel-btn").classList.toggle("hidden", !h);
    draw();
  }

  async function save() {
    const id = document.getElementById("edit-id").value;
    if (inT == null || outT == null || outT <= inT) { alert("Set IN and OUT first (OUT after IN)."); return; }
    const name = document.getElementById("f-name").value.trim();
    if (!name) { alert("Name is required."); return; }
    const body = {
      name,
      start: inT, end: outT,
      comment: document.getElementById("f-comment").value.trim() || null,
      role: document.getElementById("f-role").value,
      use_in_summary: document.getElementById("f-summary").checked,
      make_short: document.getElementById("f-short").checked,
      type: "video",
    };
    const url = id ? `${api}/${id}` : api;
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { alert("Save failed: " + (await r.text())); return; }
    log(id ? "updated" : "created", name);
    setForm(null);
    await load();
  }

  // events
  document.getElementById("set-in").addEventListener("click", () => {
    inT = player.currentTime; document.getElementById("in-label").textContent = fmt(inT); draw();
  });
  document.getElementById("set-out").addEventListener("click", () => {
    outT = player.currentTime; document.getElementById("out-label").textContent = fmt(outT); draw();
  });
  document.getElementById("save-btn").addEventListener("click", save);
  document.getElementById("cancel-btn").addEventListener("click", () => setForm(null));

  document.getElementById("hl-list").addEventListener("click", async (e) => {
    const seek = e.target.closest("[data-seek]");
    if (seek) { player.currentTime = parseFloat(seek.dataset.seek); return; }
    const edit = e.target.closest("[data-edit]");
    if (edit) { setForm(highlights.find((h) => h.id == edit.dataset.edit)); window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    const del = e.target.closest("[data-del]");
    if (del) {
      if (!confirm("Delete this highlight?")) return;
      await fetch(`${api}/${del.dataset.del}/delete`, { method: "POST" });
      await load();
    }
  });

  canvas.addEventListener("click", (e) => {
    const dur = player.duration || 0;
    if (!dur) return;
    const rect = canvas.getBoundingClientRect();
    player.currentTime = ((e.clientX - rect.left) / rect.width) * dur;
  });

  document.addEventListener("keydown", (e) => {
    if (["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) return;
    if (e.code === "Space") { e.preventDefault(); player.paused ? player.play() : player.pause(); }
    else if (e.key === "i" || e.key === "I") { document.getElementById("set-in").click(); }
    else if (e.key === "o" || e.key === "O") { document.getElementById("set-out").click(); }
  });

  player.addEventListener("timeupdate", draw);
  player.addEventListener("loadedmetadata", draw);
  window.addEventListener("resize", draw);
  load();
})();
