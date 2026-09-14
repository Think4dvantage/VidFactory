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
  let hints = [];

  const ROLE_COLOR = { normal: "#0ea5e9", launch: "#10b981", landing: "#f43f5e" };

  // Flightlog segment kinds -> marker style. Unknown/future kinds fall back to DEFAULT_HINT_STYLE
  // so new segment kinds from Flightlog show up immediately with no code change here.
  const HINT_STYLE = {
    thermal: { color: "#f59e0b", icon: "🌀", label: "Thermal" },
    glide: { color: "#38bdf8", icon: "🪂", label: "Glide" },
    takeoff: { color: "#10b981", icon: "🛫", label: "Takeoff" },
    landing: { color: "#f43f5e", icon: "🛬", label: "Landing" },
    max_alt: { color: "#a78bfa", icon: "⬆️", label: "Max altitude" },
    top_of_climb: { color: "#facc15", icon: "🔝", label: "Top of climb" },
  };
  const DEFAULT_HINT_STYLE = { color: "#94a3b8", icon: "●", label: "Flightlog hint" };
  const hintStyle = (kind) => HINT_STYLE[kind] || DEFAULT_HINT_STYLE;
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
    loadHints(); // independent of the highlight list; re-checked every load (e.g. after marking launch)
  }

  async function loadHints() {
    const statusEl = document.getElementById("hint-status");
    try {
      const r = await fetch(`/api/projects/${pid}/flightlog-hints`);
      const data = await r.json();
      hints = data.hints || [];
      if (data.status === "no_launch_marked") {
        statusEl.textContent = "Mark a launch highlight to see Flightlog hints (thermals, etc.) on the timeline.";
      } else if (data.status === "ok" && hints.length) {
        statusEl.textContent = `${hints.length} Flightlog hint(s) on the timeline — hover the markers above it.`;
      } else {
        statusEl.textContent = "";
      }
    } catch (e) {
      hints = [];
      statusEl.textContent = "";
    }
    draw();
  }

  // Mirrors core/highlights.py:merge_overlaps — overlapping video ranges collapse into one
  // span so a summary-length estimate doesn't double-count them; pictures are point inserts
  // and always add their own duration. Only highlights flagged "use in summary" count, since
  // that's exactly what a real summary build would include (see build_summary_ep).
  function mergedSummarySeconds(hls) {
    const included = hls.filter((h) => h.use_in_summary);
    const videos = included
      .filter((h) => h.type === "video")
      .sort((a, b) => a.start - b.start || a.end - b.end);
    let total = 0, lastEnd = null;
    videos.forEach((h) => {
      if (lastEnd != null && h.start <= lastEnd) {
        if (h.end > lastEnd) { total += h.end - lastEnd; lastEnd = h.end; }
      } else {
        total += h.end - h.start;
        lastEnd = h.end;
      }
    });
    included.filter((h) => h.type === "picture").forEach((h) => { total += h.duration || 5.0; });
    return total;
  }

  function renderList() {
    document.getElementById("count").textContent = highlights.length;
    document.getElementById("hl-total").textContent = fmt(mergedSummarySeconds(highlights));
    const el = document.getElementById("hl-list");
    el.innerHTML = "";
    highlights.forEach((h) => {
      const row = document.createElement("div");
      row.className = "flex items-center justify-between py-1 gap-2";
      const tag = h.role !== "normal" ? ` [${h.role}]` : "";
      const flags = (h.make_short ? "🎬" : "") + (h.use_in_summary ? "" : " (excl)");
      const icon = h.type === "picture" ? "🖼 " : "";
      const range = h.type === "picture"
        ? `${fmt(h.start)} (${(h.duration || 5).toFixed(1)}s)`
        : `${fmt(h.start)}–${fmt(h.end)}`;
      row.innerHTML =
        `<button class="text-left flex-1 hover:text-sky-300" data-seek="${h.start}">` +
        `<b>${icon}${escapeHtml(h.name)}</b>${tag} <span class="text-slate-500">${range}</span> ${flags}</button>` +
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
      ctx.fillStyle = hl.type === "picture" ? "#a855f7" : (ROLE_COLOR[hl.role] || ROLE_COLOR.normal);
      ctx.fillRect(x, 6, ww, h - 20);
    });
    hints.forEach((hint) => {
      const x = (hint.video_offset_s / dur) * w;
      ctx.fillStyle = hintStyle(hint.kind).color;
      ctx.beginPath();
      ctx.moveTo(x - 4, 0);
      ctx.lineTo(x + 4, 0);
      ctx.lineTo(x, 7);
      ctx.closePath();
      ctx.fill();
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

  // Role/"make short" only make sense for video ranges — both feed straight into ffmpeg trims
  // of the full-flight video elsewhere (shorts hook/launch/landing clips), so a picture there
  // would try to cut a zero-length/nonsensical range. Force them off at the UI level.
  function applyTypeUI(type) {
    document.getElementById("out-group").classList.toggle("hidden", type === "picture");
    document.getElementById("picture-fields").classList.toggle("hidden", type !== "picture");
    const roleSel = document.getElementById("f-role");
    const shortChk = document.getElementById("f-short");
    roleSel.disabled = type === "picture";
    shortChk.disabled = type === "picture";
    if (type === "picture") {
      roleSel.value = "normal";
      shortChk.checked = false;
    }
  }

  function setForm(h) {
    document.getElementById("edit-id").value = h ? h.id : "";
    document.getElementById("f-name").value = h ? h.name : "";
    document.getElementById("f-comment").value = h ? (h.comment || "") : "";
    const type = h ? h.type : "video";
    document.getElementById("f-type-video").checked = type === "video";
    document.getElementById("f-type-picture").checked = type === "picture";
    document.getElementById("f-role").value = h ? h.role : "normal";
    document.getElementById("f-summary").checked = h ? h.use_in_summary : true;
    document.getElementById("f-short").checked = h ? h.make_short : false;
    document.getElementById("f-duration").value = h && h.duration ? h.duration : 5;
    document.getElementById("f-image-path").value = h && h.image_path ? h.image_path : "";
    document.getElementById("f-image-file").value = "";
    document.getElementById("image-upload-status").textContent = "";
    const preview = document.getElementById("image-preview");
    if (h && type === "picture" && h.id) {
      preview.src = `${api}/${h.id}/picture`;
      preview.classList.remove("hidden");
    } else {
      preview.removeAttribute("src");
      preview.classList.add("hidden");
    }
    inT = h ? h.start : null;
    outT = h && type === "video" ? h.end : null;
    document.getElementById("in-label").textContent = fmt(inT);
    document.getElementById("out-label").textContent = fmt(outT);
    document.getElementById("save-btn").textContent = h ? "Save changes" : "Add highlight";
    document.getElementById("cancel-btn").classList.toggle("hidden", !h);
    applyTypeUI(type);
    draw();
  }

  async function save() {
    const id = document.getElementById("edit-id").value;
    const type = document.querySelector('input[name="f-type"]:checked').value;
    const name = document.getElementById("f-name").value.trim();
    if (!name) { alert("Name is required."); return; }
    const comment = document.getElementById("f-comment").value.trim() || null;
    let body;
    if (type === "picture") {
      if (inT == null) { alert("Mark a position first (Set IN)."); return; }
      const imagePath = document.getElementById("f-image-path").value;
      if (!imagePath) { alert("Upload an image first."); return; }
      const duration = parseFloat(document.getElementById("f-duration").value) || 5.0;
      body = {
        name, comment,
        start: inT, end: inT + duration,
        role: "normal",
        use_in_summary: document.getElementById("f-summary").checked,
        make_short: false,
        type: "picture",
        image_path: imagePath,
        duration,
      };
    } else {
      if (inT == null || outT == null || outT <= inT) { alert("Set IN and OUT first (OUT after IN)."); return; }
      body = {
        name, comment,
        start: inT, end: outT,
        role: document.getElementById("f-role").value,
        use_in_summary: document.getElementById("f-summary").checked,
        make_short: document.getElementById("f-short").checked,
        type: "video",
      };
    }
    const url = id ? `${api}/${id}` : api;
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    if (!r.ok) { alert("Save failed: " + (await r.text())); return; }
    log(id ? "updated" : "created", name);
    setForm(null);
    await load();
  }

  document.querySelectorAll('input[name="f-type"]').forEach((radio) => {
    radio.addEventListener("change", () => {
      if (!radio.checked) return;
      if (radio.value === "picture") outT = null;
      applyTypeUI(radio.value);
      draw();
    });
  });

  document.getElementById("f-image-file").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const preview = document.getElementById("image-preview");
    preview.src = URL.createObjectURL(file);
    preview.classList.remove("hidden");
    const status = document.getElementById("image-upload-status");
    status.textContent = "Uploading…";
    const fd = new FormData();
    fd.append("file", file);
    try {
      const r = await fetch(`${api}/picture-upload`, { method: "POST", body: fd });
      if (!r.ok) { status.textContent = "Upload failed: " + (await r.text()); return; }
      const data = await r.json();
      document.getElementById("f-image-path").value = data.image_path;
      status.textContent = "Uploaded ✓";
      log("picture uploaded", data.image_path);
    } catch (err) {
      status.textContent = "Upload failed.";
      log("picture upload error", err);
    }
  });

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
    const mx = e.clientX - rect.left;
    let target = (mx / rect.width) * dur;
    const nearest = nearestHint(mx, rect.width, dur, 6);
    if (nearest) target = nearest.video_offset_s;
    player.currentTime = target;
  });

  function nearestHint(mx, width, dur, thresholdPx) {
    let best = null, bestDist = thresholdPx;
    hints.forEach((hint) => {
      const x = (hint.video_offset_s / dur) * width;
      const dist = Math.abs(x - mx);
      if (dist < bestDist) { best = hint; bestDist = dist; }
    });
    return best;
  }

  canvas.addEventListener("mousemove", (e) => {
    const dur = player.duration || 0;
    const tooltip = document.getElementById("hint-tooltip");
    if (!dur || !hints.length) { tooltip.classList.add("hidden"); return; }
    const rect = canvas.getBoundingClientRect();
    const nearest = nearestHint(e.clientX - rect.left, rect.width, dur, 6);
    if (!nearest) { tooltip.classList.add("hidden"); return; }
    const style = hintStyle(nearest.kind);
    let extra = "";
    if (nearest.alt_change_m != null) extra += ` · ${nearest.alt_change_m.toFixed(0)}m`;
    if (nearest.vertical_velocity_ms != null) extra += ` · ${nearest.vertical_velocity_ms.toFixed(1)}m/s`;
    tooltip.textContent = `${style.icon} ${style.label} @ ${fmt(nearest.video_offset_s)}${extra}`;
    tooltip.style.left = `${e.clientX + 10}px`;
    tooltip.style.top = `${e.clientY - 28}px`;
    tooltip.classList.remove("hidden");
  });
  canvas.addEventListener("mouseleave", () => document.getElementById("hint-tooltip").classList.add("hidden"));

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
