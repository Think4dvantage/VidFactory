// File browser: multiselect checkboxes -> bulk download / delete. Delegated on `document` so it
// survives htmx swapping #listing's contents (no re-wiring needed after navigation).
(() => {
  const log = (...a) => console.log("[VF:browser]", ...a);

  function itemCheckboxes(form) {
    return Array.from(form.querySelectorAll("[data-select-item]"));
  }

  function updateToolbar(form) {
    const boxes = itemCheckboxes(form);
    const checked = boxes.filter((cb) => cb.checked);
    const countEl = form.querySelector("[data-selected-count]");
    if (countEl) countEl.textContent = `${checked.length} selected`;
    form.querySelectorAll("[data-action='download-selected'], [data-action='delete-selected']").forEach((btn) => {
      btn.disabled = checked.length === 0;
    });
    const selectAll = form.querySelector("[data-select-all]");
    if (selectAll) selectAll.checked = boxes.length > 0 && checked.length === boxes.length;
  }

  function downloadSelected(form) {
    const root = form.dataset.root;
    const checked = itemCheckboxes(form).filter((cb) => cb.checked);
    log("downloading", checked.length, "file(s) from root", root);
    checked.forEach((cb, i) => {
      setTimeout(() => {
        const a = document.createElement("a");
        a.href = `/api/download/${root}?path=${encodeURIComponent(cb.value)}`;
        a.download = "";
        document.body.appendChild(a);
        a.click();
        a.remove();
      }, i * 400); // staggered: browsers throttle/block many simultaneous programmatic downloads
    });
  }

  document.addEventListener("change", (ev) => {
    const form = ev.target.closest("form[data-root]");
    if (!form) return;
    if (ev.target.matches("[data-select-all]")) {
      itemCheckboxes(form).forEach((cb) => (cb.checked = ev.target.checked));
      log("select all ->", ev.target.checked);
    }
    updateToolbar(form);
  });

  function deleteOne(btn, form) {
    if (!confirm(`Delete ${btn.dataset.name}? This cannot be undone.`)) return;
    const body = new URLSearchParams();
    body.append("paths", btn.dataset.rel);
    body.append("path", form.dataset.path);
    log("deleting", btn.dataset.rel, "from root", form.dataset.root);
    fetch(`/api/browse/${form.dataset.root}/delete`, { method: "POST", body })
      .then((r) => r.text())
      .then((html) => {
        const listing = document.getElementById("listing");
        listing.innerHTML = html;
        htmx.process(listing);
        const newForm = listing.querySelector("form[data-root]");
        if (newForm) updateToolbar(newForm);
      })
      .catch((err) => console.error("[VF:browser] delete failed", err));
  }

  document.addEventListener("click", (ev) => {
    const downloadBtn = ev.target.closest("[data-action='download-selected']");
    if (downloadBtn) {
      const form = downloadBtn.closest("form[data-root]");
      if (form) downloadSelected(form);
      return;
    }
    const deleteBtn = ev.target.closest("[data-action='delete-one']");
    if (deleteBtn) {
      const form = deleteBtn.closest("form[data-root]");
      if (form) deleteOne(deleteBtn, form);
    }
  });

  document.body.addEventListener("htmx:afterSwap", () => {
    const form = document.querySelector("#listing form[data-root]");
    if (form) {
      log("listing swapped, resetting toolbar");
      updateToolbar(form);
    }
  });
})();
