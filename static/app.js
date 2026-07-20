"use strict";

const $ = (id) => document.getElementById(id);

// ---- (i) explainer popovers ----
// Any element with class "info-i" and a data-info="…" attribute shows a plain-
// English popover on click. Delegated on document so dynamically-added icons
// work too. One shared, viewport-clamped popover.
(function initInfoPops() {
  const pop = document.createElement("div");
  pop.className = "info-pop-float";
  pop.style.display = "none";
  document.body.appendChild(pop);
  let current = null;
  function place(btn) {
    pop.textContent = btn.getAttribute("data-info") || "";
    pop.style.display = "block";
    const W = Math.min(260, window.innerWidth - 20);
    pop.style.width = W + "px";
    const r = btn.getBoundingClientRect();
    let left = r.left + window.scrollX + r.width / 2 - W / 2;
    const min = window.scrollX + 10;
    const max = window.scrollX + document.documentElement.clientWidth - W - 10;
    pop.style.left = Math.max(min, Math.min(left, max)) + "px";
    pop.style.top = r.bottom + window.scrollY + 6 + "px";
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".info-i");
    if (btn) {
      e.preventDefault(); e.stopPropagation();
      if (current === btn) { pop.style.display = "none"; current = null; }
      else { place(btn); current = btn; }
      return;
    }
    if (current) { pop.style.display = "none"; current = null; }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && current) { pop.style.display = "none"; current = null; }
  });
  window.addEventListener("resize", () => { if (current) place(current); });
})();

// Sent on every state-changing request. Browsers can't set a custom header on a
// cross-origin request without a CORS preflight the server never grants, so its
// presence proves the request came from Dactful's own page (CSRF defense).
const APP_HEADER = { "X-Dactful-App": "1" };
let SESSION = null;
let ROWS = []; // {term, type, source, tag, count, contexts, checked}
let IMAGES = []; // {id, thumb, warn, page, width, height, keep}

// ---- tabs ----
document.querySelectorAll(".tab").forEach((t) => {
  t.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((x) => x.classList.remove("active"));
    document.querySelectorAll(".tabpane").forEach((x) => x.classList.remove("active"));
    t.classList.add("active");
    $("tab-" + t.dataset.tab).classList.add("active");
    if (t.dataset.tab === "dictionary") { loadDictionary(); loadSettings(); }
  });
});

// ---- health / NER badge ----
fetch("/api/health").then((r) => r.json()).then((h) => {
  $("nerBadge").textContent = h.ner_available
    ? "Name detection: on"
    : "Name detection: off";
  // Native folder picker is backend-driven (macOS osascript), so it works even
  // in the browser; hide the Browse button where it isn't available.
  const browse = $("dictBrowseBtn");
  if (browse) browse.classList.toggle("hidden", !h.native_folder_picker);
});

// ---- view mode: Standard <-> Simple ("Mom + Dad") ----
// Simple view hides the advanced raw-path field; you just click "Choose a
// folder…" and the native picker does the rest. Persisted across sessions.
const MODE_KEY = "dactful_view_mode";
function applyViewMode(mode) {
  const simple = mode === "simple";
  document.body.classList.toggle("mode-simple", simple);
  const btn = $("modeToggle");
  btn.textContent = simple ? "Mom and Dad mode: ON" : "Mom and Dad mode: OFF";
  btn.classList.toggle("on", simple);        // neon fill when on
  btn.setAttribute("aria-pressed", simple ? "true" : "false");
}
let VIEW_MODE = localStorage.getItem(MODE_KEY) || "standard";
applyViewMode(VIEW_MODE);
$("modeToggle").addEventListener("click", () => {
  VIEW_MODE = VIEW_MODE === "simple" ? "standard" : "simple";
  localStorage.setItem(MODE_KEY, VIEW_MODE);
  applyViewMode(VIEW_MODE);
});

// ---- Add-a-tag helpers: type guessing, auto-numbered tags, dedup ----
const TYPE_PREFIX = {
  company: "company", person: "person", place: "place",
  email: "email", phone: "phone", ssn: "ssn", other: "custom",
};
let KNOWN_TAGS = new Set(); // uppercase [[TAG]] forms already in the dictionary
async function refreshKnownTags() {
  try {
    const d = await (await fetch("/api/dictionary")).json();
    KNOWN_TAGS = new Set((d.entries || []).map((e) => String(e.tag).toUpperCase()));
  } catch { /* keep last known */ }
}
refreshKnownTags();

// Compare-only normalization: "Company Name Inc." === "company Name inc".
function normKey(s) {
  return String(s).trim().toLowerCase()
    .replace(/[^\p{L}\p{N}\s]/gu, "")   // drop punctuation
    .replace(/\s+/g, " ").trim();       // collapse whitespace
}

// Best-effort type guess from the real value (correctable via the dropdown).
function guessType(v) {
  const s = String(v).trim();
  if (!s) return null;
  if (/\S+@\S+\.\S+/.test(s)) return "email";
  if (/^\d{3}-\d{2}-\d{4}$/.test(s)) return "ssn";
  if (/^[+(]?\d[\d\s().-]{6,}$/.test(s)) return "phone";
  // Legal suffix at the end (Acme Inc, Foo LLC) ...
  if (/\b(inc|llc|l\.l\.c|corp|corporation|ltd|limited|co|company|group|holdings|partners|lp|llp|plc|gmbh|ag|sa|nv|bv|bank)\b\.?$/i.test(s)) return "company";
  // ... or a common company/org word anywhere (Northwind Trading, Foo Labs).
  if (/\b(trading|ventures?|capital|labs?|laboratories|studios?|technolog(?:y|ies)|tech|software|solutions|systems|industries|enterprises?|associates|consult(?:ing|ants)|media|digital|agency|works|collective|international|global|worldwide|foundation|institute|services|brands|partnership|holdings?)\b/i.test(s)) return "company";
  const words = s.split(/\s+/);
  if (words.length >= 2 && words.length <= 4 && words.every((w) => /^[A-Z][\p{L}'’.-]*$/u.test(w))) return "person";
  return "person"; // default when unsure (kept neutral, not business-focused)
}

// Lowest free [[PREFIX_N]] not already used by the dictionary or `extra` tags.
function nextTagFor(type, extra) {
  const P = (TYPE_PREFIX[type] || "custom").toUpperCase();
  const taken = new Set([...KNOWN_TAGS, ...(extra || []).map((t) => String(t).toUpperCase())]);
  let n = 1;
  while (taken.has(`[[${P}_${n}]]`)) n++;
  return `[[${P}_${n}]]`;
}

// Wire a [type-select | value | tag] trio: auto-guess the type, auto-number the
// tag as you type, but stop overwriting once the tag is hand-edited.
function wireAddRow(typeId, valId, tagId, getExtra, defaultType = "person") {
  const S = $(typeId), V = $(valId), T = $(tagId);
  S.value = defaultType;  // initial default before any typing
  let lastAuto = "", typeUserSet = false;
  const recompute = () => {
    const tag = nextTagFor(S.value, getExtra());
    if (T.value === lastAuto || T.value === "") { T.value = tag; lastAuto = tag; }
  };
  V.addEventListener("input", () => {
    if (!typeUserSet) { const g = guessType(V.value); if (g) S.value = g; }
    recompute();
  });
  S.addEventListener("change", () => {
    typeUserSet = true;
    const tag = nextTagFor(S.value, getExtra());
    T.value = tag; lastAuto = tag;
  });
  return {
    reset() { V.value = ""; T.value = ""; lastAuto = ""; typeUserSet = false; S.value = defaultType; },
  };
}

const reviewAdd = wireAddRow("addTermType", "addTermText", "addTermTag", () => ROWS.map((r) => r.tag), "person");
const dictAdd = wireAddRow("dictType", "dictTerm", "dictTag", () => [], "company");

// ---- file input + drag/drop ----
let pickedFile = null;
const dz = $("dropzone");
$("fileInput").addEventListener("change", (e) => {
  pickedFile = e.target.files[0] || null;
  if (pickedFile) dz.querySelector("strong").textContent = pickedFile.name;
});
["dragover", "dragenter"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); })
);
["dragleave", "drop"].forEach((ev) =>
  dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.remove("drag"); })
);
dz.addEventListener("drop", (e) => {
  pickedFile = e.dataTransfer.files[0] || null;
  if (pickedFile) dz.querySelector("strong").textContent = pickedFile.name;
});

// ---- analyze ----
$("analyzeBtn").addEventListener("click", async () => {
  const paste = $("pasteBox").value.trim();
  if (!pickedFile && !paste) { setStatus("analyzeStatus", "Add a file or paste text first."); return; }

  const fd = new FormData();
  if (pickedFile) fd.append("file", pickedFile);
  else fd.append("text", paste);
  fd.append("include_money", $("optMoney").checked);
  fd.append("use_ner", $("optNer").checked);

  setStatus("analyzeStatus", "Scanning…");
  $("analyzeBtn").disabled = true;
  try {
    const res = await fetch("/api/analyze", { method: "POST", headers: APP_HEADER, body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Scan failed.");
    SESSION = data.session_id;
    ROWS = data.suggestions.map((s) => ({ ...s }));
    // Default: keep images unless they look like they hold sensitive text.
    IMAGES = (data.images || []).map((im) => ({ ...im, keep: im.warn !== "sensitive" }));
    renderReview(data);
    setStatus("analyzeStatus", "");
  } catch (err) {
    setStatus("analyzeStatus", err.message);
  } finally {
    $("analyzeBtn").disabled = false;
  }
});

function setStatus(id, msg) { $(id).textContent = msg; }

// ---- render review table ----
function renderReview(data) {
  $("step-review").classList.remove("hidden");
  $("step-result").classList.add("hidden");
  const n = ROWS.length;
  $("reviewSummary").textContent =
    `Found ${n} candidate${n === 1 ? "" : "s"} in ${data.orig_name}. ` +
    `Dictionary and pattern hits are pre-checked; name and company guesses are not. You decide.`;
  drawRows();
  drawImages();
  $("step-review").scrollIntoView({ behavior: "smooth", block: "start" });
}

function drawImages() {
  const wrap = $("reviewImages");
  const grid = $("imgGrid");
  if (!IMAGES.length) { wrap.classList.add("hidden"); grid.innerHTML = ""; return; }
  wrap.classList.remove("hidden");
  grid.innerHTML = IMAGES.map((im) => `
    <div class="img-card ${im.keep ? "keep" : ""}">
      <img class="img-thumb" src="${im.thumb}" data-full="${im.id}" title="Click to enlarge" alt="Image from page ${im.page}" />
      <div class="img-row">
        <label class="img-check"><input type="checkbox" ${im.keep ? "checked" : ""} data-img="${im.id}" /> Keep</label>
        <span class="muted small">p.${im.page}</span>
      </div>
      ${im.warn === "sensitive"
        ? `<div class="img-warn">⚠ May contain sensitive text</div>`
        : im.warn === "text"
        ? `<div class="img-note">Contains text, review it</div>`
        : ""}
    </div>`).join("");
  grid.querySelectorAll("[data-img]").forEach((cb) =>
    cb.addEventListener("change", (e) => {
      const im = IMAGES.find((x) => x.id === +e.target.dataset.img);
      im.keep = e.target.checked;
      e.target.closest(".img-card").classList.toggle("keep", im.keep);
    })
  );
  grid.querySelectorAll(".img-thumb").forEach((img) =>
    img.addEventListener("click", () => openLightbox(+img.dataset.full))
  );
}

// ---- image lightbox (click a thumbnail to enlarge) ----
function openLightbox(id) {
  const lb = $("imgLightbox");
  $("lightboxImg").src = `/api/image/${SESSION}/${id}`;
  lb.classList.remove("hidden");
}
(function initLightbox() {
  const lb = $("imgLightbox");
  if (!lb) return;
  const close = () => { lb.classList.add("hidden"); $("lightboxImg").src = ""; };
  lb.addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !lb.classList.contains("hidden")) close();
  });
})();

function drawRows() {
  const filter = $("filterBox").value.toLowerCase();
  const tbody = $("reviewTable").querySelector("tbody");
  tbody.innerHTML = "";
  ROWS.forEach((row, i) => {
    if (filter && !row.term.toLowerCase().includes(filter) && !row.tag.toLowerCase().includes(filter)) return;
    const tr = document.createElement("tr");
    tr.className = row.checked ? "" : "unchecked";

    const ctx = (row.contexts && row.contexts[0]) ? `<span class="ctx">${escapeHtml(row.contexts[0])}</span>` : "";
    tr.innerHTML = `
      <td><input type="checkbox" ${row.checked ? "checked" : ""} data-i="${i}" class="chk" /></td>
      <td class="term-cell">${escapeHtml(row.term)}${ctx}</td>
      <td><span class="pill ${row.source}">${row.type}</span></td>
      <td>${row.count || "-"}</td>
      <td><input type="text" class="tag-input" data-i="${i}" value="${escapeAttr(row.tag)}" /></td>
      <td>${row.source === "manual" ? `<button class="remove-x" data-del="${i}">×</button>` : ""}</td>
    `;
    tbody.appendChild(tr);
  });

  tbody.querySelectorAll(".chk").forEach((c) =>
    c.addEventListener("change", (e) => { ROWS[+e.target.dataset.i].checked = e.target.checked; drawRows(); })
  );
  tbody.querySelectorAll(".tag-input").forEach((inp) =>
    inp.addEventListener("input", (e) => { ROWS[+e.target.dataset.i].tag = e.target.value; })
  );
  tbody.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", (e) => { ROWS.splice(+e.target.dataset.del, 1); drawRows(); })
  );
  updateCounts();
}

function updateCounts() {
  const sel = ROWS.filter((r) => r.checked).length;
  $("selCount").textContent = `${sel} selected`;
  const declined = ROWS.filter((r) => !r.checked).length;
  $("declineWarn").textContent = declined
    ? `${declined} flagged term${declined === 1 ? "" : "s"} will be left in the document.`
    : "";
}

$("filterBox").addEventListener("input", drawRows);
$("checkAll").addEventListener("click", () => { ROWS.forEach((r) => (r.checked = true)); drawRows(); });
$("uncheckAll").addEventListener("click", () => { ROWS.forEach((r) => (r.checked = false)); drawRows(); });

// ---- add a manual term ----
$("addTermText").addEventListener("input", () => {
  const v = normKey($("addTermText").value);
  const dup = v && ROWS.find((r) => normKey(r.term) === v);
  $("addTermPreview").textContent = dup ? `You already have this as ${dup.tag}.` : "";
});
$("addTermBtn").addEventListener("click", () => {
  const term = $("addTermText").value.trim();
  if (!term) return;
  const dup = ROWS.find((r) => normKey(r.term) === normKey(term));
  if (dup) { $("addTermPreview").textContent = `Already added as ${dup.tag}.`; return; }
  const tag = $("addTermTag").value.trim() || nextTagFor($("addTermType").value, ROWS.map((r) => r.tag));
  ROWS.unshift({ term, type: $("addTermType").value, source: "manual", tag, count: null, contexts: [], checked: true });
  reviewAdd.reset(); $("addTermPreview").textContent = "";
  drawRows();
});

// ---- redact ----
$("redactBtn").addEventListener("click", async () => {
  const entries = ROWS.filter((r) => r.checked).map((r) => ({ term: r.term, tag: r.tag }));
  if (!entries.length) { alert("Select at least one term to redact."); return; }
  $("redactBtn").disabled = true;
  $("redactBtn").textContent = "Redacting…";
  try {
    const res = await fetch("/api/redact", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...APP_HEADER },
      body: JSON.stringify({
        session_id: SESSION,
        entries,
        redact_filename: $("optRedactName").checked,
        keep_images: IMAGES.filter((i) => i.keep).map((i) => i.id),
      }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Redaction failed.");
    renderResult(data);
  } catch (err) {
    alert(err.message);
  } finally {
    $("redactBtn").disabled = false;
    $("redactBtn").textContent = "Redact";
  }
});

function renderResult(data) {
  const box = $("resultBody");
  $("step-result").classList.remove("hidden");

  if (!data.ok) {
    box.innerHTML = `
      <div class="result-block bad">
        <strong>Held back: validation failed.</strong>
        <p>These terms still appeared in the generated file, so Dactful did not release it:</p>
        <ul>${data.leaked.map((t) => `<li>${escapeHtml(t)}</li>`).join("")}</ul>
        <p class="small">This check stopped the file from going out with those terms still inside. Please report the document shape so it can be fixed.</p>
      </div>`;
    $("step-result").scrollIntoView({ behavior: "smooth" });
    return;
  }

  const dl = (kind, label, secondary) =>
    `<a class="dl ${secondary ? "secondary" : ""}" href="/api/download/${SESSION}/${kind}" download>${label}</a>`;

  const rows = (data.entries || [])
    .map((e) => `<tr><td class="map-tag">${escapeHtml(e.tag)}</td><td class="map-arrow">←</td>
      <td class="map-val">${escapeHtml(e.value)}</td>
      <td class="map-count">${e.count}×</td></tr>`)
    .join("");

  const isText = data.redacted_text != null;
  const lead = isText ? "Your redacted text is ready" : "Your redacted document is ready";
  const output = isText
    ? `<label class="restored-label">Redacted text</label>
       <textarea class="restored-out" readonly rows="8">${escapeHtml(data.redacted_text)}</textarea>
       <div class="dl-row"><button class="dl" id="copyRedacted" type="button">Copy text</button></div>`
    : `<div class="dl-row">${dl("redacted", "⬇ Download Redacted Document")}</div>`;

  box.innerHTML = `
    <div class="result-block success">
      <p class="result-lead">${lead}</p>
      <p class="muted small">Dactful hid ${data.entries.length} piece${data.entries.length === 1 ? "" : "s"} of
        sensitive info (${data.replacements} spot${data.replacements === 1 ? "" : "s"} in total).</p>
    </div>

    ${output}

    <div class="saved-note">
      <span class="saved-check">✓</span>
      <div>When your finished draft comes back from the AI, open the
        <button class="inline-link" id="goRestore">Restore</button> tab and use it to restore your data.</div>
    </div>

    <details class="reveal">
      <summary>See what will be put back (and download a backup key)</summary>
      <p class="muted small">These are the real values Dactful is holding for you. They stay on this
        computer, so don't upload this part anywhere.</p>
      <table class="map-table"><tbody>${rows}</tbody></table>
      <div class="dl-row">
        ${dl("mapping_txt", "Backup key (.txt)", true)}
        ${dl("mapping_json", "Backup key (.json)", true)}
      </div>
    </details>`;

  const goRestore = $("goRestore");
  if (goRestore) goRestore.addEventListener("click", () => {
    document.querySelector('.tab[data-tab="restore"]').click();
  });
  const copyBtn = $("copyRedacted");
  if (copyBtn) copyBtn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(data.redacted_text); copyBtn.textContent = "Copied!"; }
    catch { copyBtn.textContent = "Copy failed"; }
    setTimeout(() => { copyBtn.textContent = "Copy text"; }, 1500);
  });
  $("step-result").scrollIntoView({ behavior: "smooth" });
}

// ---- restore ----
$("restoreBtn").addEventListener("click", async () => {
  const doc = $("restoreDoc").files[0];
  const paste = $("restorePaste").value.trim();
  const mapEl = $("restoreMap");
  const map = mapEl ? mapEl.files[0] : null;
  if (!doc && !paste) {
    setStatus("restoreStatus", "Add your finished document, or paste the text.");
    return;
  }
  const fd = new FormData();
  // Prefer a pasted text if given; otherwise use the uploaded document.
  if (paste) fd.append("text", paste);
  else fd.append("file", doc);
  // If no mapping file is given, the server applies every saved redaction at
  // once (tag ids are globally unique, so only the matching ones fire).
  if (map) fd.append("mapping_file", map);
  setStatus("restoreStatus", "Restoring…");
  $("restoreBtn").disabled = true;
  try {
    const res = await fetch("/api/restore", { method: "POST", headers: APP_HEADER, body: fd });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Restore failed.");
    renderRestore(data);
    setStatus("restoreStatus", "");
  } catch (err) {
    setStatus("restoreStatus", err.message);
  } finally {
    $("restoreBtn").disabled = false;
  }
});

function renderRestore(data) {
  const r = data.report;
  const lines = [];
  if (data.restored_text != null) {
    // Pasted-text mode: show the restored text with a Copy button.
    lines.push(`<label class="restored-label">Your restored text</label>`);
    lines.push(`<textarea class="restored-out" readonly rows="8">${escapeHtml(data.restored_text)}</textarea>`);
    lines.push(`<div class="dl-row"><button class="dl" id="copyRestored" type="button">Copy text</button></div>`);
  } else {
    lines.push(`<div class="dl-row"><a class="dl" href="/api/download/${data.session_id}/restored" download>⬇ Restored document (.docx)</a></div>`);
  }
  lines.push(`<div class="rep-line rep-ok">${r.total} value${r.total === 1 ? "" : "s"} put back.</div>`);
  Object.entries(r.mangled).forEach(([tag, n]) => {
    if (n > 0) lines.push(`<div class="rep-line rep-warn">Repaired ${n} mangled <code>${escapeHtml(tag)}</code> tag${n === 1 ? "" : "s"}.</div>`);
  });
  const leftover = r.leftover || [];
  if (leftover.length) {
    lines.push(`<div class="rep-line rep-warn">These are still in your document and Dactful has no value for them, so they may be tags the AI changed or invented: ${leftover.map(escapeHtml).join(", ")}.</div>`);
  } else {
    lines.push(`<div class="rep-line rep-ok">No leftover tags. Everything was put back.</div>`);
  }
  $("restoreResult").innerHTML = `<div class="result-block">${lines.join("")}</div>`;

  const copyBtn = $("copyRestored");
  if (copyBtn) copyBtn.addEventListener("click", async () => {
    try { await navigator.clipboard.writeText(data.restored_text); copyBtn.textContent = "Copied!"; }
    catch { copyBtn.textContent = "Copy failed"; }
    setTimeout(() => { copyBtn.textContent = "Copy text"; }, 1500);
  });
}

// ---- start over (reset a flow without reloading the page) ----
const DZ_DEFAULT_LABEL = dz.querySelector("strong").textContent;

function resetRedact() {
  SESSION = null;
  ROWS = [];
  IMAGES = [];
  pickedFile = null;
  $("fileInput").value = "";
  dz.querySelector("strong").textContent = DZ_DEFAULT_LABEL;
  $("pasteBox").value = "";
  $("filterBox").value = "";
  reviewAdd.reset();
  $("addTermPreview").textContent = "";
  $("reviewTable").querySelector("tbody").innerHTML = "";
  $("imgGrid").innerHTML = "";
  $("reviewImages").classList.add("hidden");
  $("resultBody").innerHTML = "";
  $("step-review").classList.add("hidden");
  $("step-result").classList.add("hidden");
  setStatus("analyzeStatus", "");
  $("step-input").scrollIntoView({ behavior: "smooth", block: "start" });
}

function resetRestore() {
  $("restoreDoc").value = "";
  $("restorePaste").value = "";
  const mapEl = $("restoreMap");
  if (mapEl) {
    mapEl.value = "";
    const det = mapEl.closest("details");
    if (det) det.open = false;
  }
  $("restoreResult").innerHTML = "";
  setStatus("restoreStatus", "");
}

$("reviewRestartBtn").addEventListener("click", resetRedact);
$("redactRestartBtn").addEventListener("click", resetRedact);
$("restoreRestartBtn").addEventListener("click", resetRestore);

// ---- dictionary storage location ----
async function loadSettings() {
  try {
    const res = await fetch("/api/settings");
    const s = await res.json();
    $("dictPathDisplay").textContent = s.is_default ? "On this device" : s.dict_path;
    $("dictResetBtn").classList.toggle("hidden", s.is_default);
    const badge = $("syncBadge");
    if (s.synced) {
      badge.className = "sync-badge cloud";
      badge.textContent = `Can sync with ${s.provider || "a cloud service"} 🟡`;
    } else {
      badge.className = "sync-badge local";
      badge.textContent = "On this device only ✅";
    }
  } catch {
    $("dictPathDisplay").textContent = "";
  }
}

function showLocMsg(msg, isError) {
  $("dictLocMsg").className = isError ? "small warn" : "small ok-msg";
  $("dictLocMsg").textContent = msg;
}

// Move the dictionary to `folder` (used by both Browse and the manual field).
async function applyDictLocation(folder) {
  if (!folder) { showLocMsg("Choose a folder first.", true); return; }
  try {
    const res = await fetch("/api/settings/dictionary-location", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...APP_HEADER },
      body: JSON.stringify({ folder }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not move the dictionary.");
    $("dictFolderInput").value = "";
    showLocMsg(data.adopted_existing
      ? "Moved and merged with the dictionary already in that folder."
      : "Dictionary moved to that folder.", false);
    loadSettings();
    loadDictionary();
  } catch (err) {
    showLocMsg(err.message, true);
  }
}

// Browse opens the native folder picker and applies the choice immediately, so
// the simple (Mom + Dad) view needs no path field or Save step.
$("dictBrowseBtn").addEventListener("click", async () => {
  $("dictBrowseBtn").disabled = true;
  showLocMsg("", false);
  try {
    const res = await fetch("/api/pick-folder", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...APP_HEADER },
      body: "{}",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Couldn't open the picker.");
    if (data.path) await applyDictLocation(data.path);
  } catch (err) {
    showLocMsg(err.message, true);
  } finally {
    $("dictBrowseBtn").disabled = false;
  }
});

$("dictSaveLocBtn").addEventListener("click", () =>
  applyDictLocation($("dictFolderInput").value.trim())
);

$("dictResetBtn").addEventListener("click", async () => {
  try {
    const res = await fetch("/api/settings/dictionary-location/reset", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...APP_HEADER },
      body: "{}",
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Reset failed.");
    showLocMsg("Your dictionary is back on this device.", false);
    loadSettings();
    loadDictionary();
  } catch (err) {
    showLocMsg(err.message, true);
  }
});

// ---- local tag dictionary ----
let DICT = [];

async function loadDictionary() {
  const tbody = $("dictTable").querySelector("tbody");
  try {
    const res = await fetch("/api/dictionary");
    const data = await res.json();
    DICT = data.entries || [];
  } catch {
    DICT = [];
  }
  $("dictCount").textContent = DICT.length
    ? `${DICT.length} saved tag${DICT.length === 1 ? "" : "s"}`
    : "";
  $("dictEmpty").textContent = DICT.length
    ? ""
    : "No saved tags yet. Terms you confirm during a redaction are remembered here automatically, or add one above.";
  tbody.innerHTML = DICT.map((e, i) => `
    <tr>
      <td class="term-cell">${escapeHtml(e.term)}</td>
      <td><span class="map-tag">${escapeHtml(e.tag)}</span></td>
      <td class="src-cell">${e.source === "redaction" ? "From a redaction" : "Manually added"}</td>
      <td class="td-right"><button class="del-x" data-del="${i}">Delete</button></td>
    </tr>`).join("");
  tbody.querySelectorAll("[data-del]").forEach((b) =>
    b.addEventListener("click", () => openDeleteModal(DICT[+b.dataset.del]))
  );
}

$("dictTerm").addEventListener("input", () => {
  const v = normKey($("dictTerm").value);
  const dup = v && DICT.find((e) => normKey(e.term) === v);
  $("dictAddMsg").textContent = dup ? `Already saved as ${dup.tag}.` : "";
});
$("dictAddBtn").addEventListener("click", async () => {
  const term = $("dictTerm").value.trim();
  if (!term) { $("dictAddMsg").textContent = "Enter a real value."; return; }
  const tag = $("dictTag").value.trim() || nextTagFor($("dictType").value, []);
  try {
    const res = await fetch("/api/dictionary/add", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...APP_HEADER },
      body: JSON.stringify({ term, tag }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Could not add.");
    DICT = data.entries;
    dictAdd.reset();
    $("dictAddMsg").textContent = "Saved.";
    setTimeout(() => ($("dictAddMsg").textContent = ""), 1500);
    await refreshKnownTags();
    loadDictionary();
  } catch (err) {
    $("dictAddMsg").textContent = err.message;
  }
});

// ---- delete confirmation modal ----
let DEL_TARGET = null;

function openDeleteModal(entry) {
  DEL_TARGET = entry;
  $("delTag").textContent = entry.tag;
  $("delVal").textContent = entry.term;
  $("delConfirmInput").value = "";
  $("delConfirm").disabled = true;
  $("delModal").classList.remove("hidden");
  $("delConfirmInput").focus();
}

function closeDeleteModal() {
  DEL_TARGET = null;
  $("delModal").classList.add("hidden");
}

$("delConfirmInput").addEventListener("input", (e) => {
  $("delConfirm").disabled = e.target.value !== "DELETE";
});
$("delConfirmInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.value === "DELETE") $("delConfirm").click();
});
$("delCancel").addEventListener("click", closeDeleteModal);
$("delModal").addEventListener("click", (e) => { if (e.target === $("delModal")) closeDeleteModal(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && !$("delModal").classList.contains("hidden")) closeDeleteModal();
});

$("delConfirm").addEventListener("click", async () => {
  if (!DEL_TARGET || $("delConfirmInput").value !== "DELETE") return;
  $("delConfirm").disabled = true;
  try {
    const res = await fetch("/api/dictionary/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json", ...APP_HEADER },
      body: JSON.stringify({ term: DEL_TARGET.term }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Delete failed.");
    DICT = data.entries;
    closeDeleteModal();
    loadDictionary();
  } catch (err) {
    alert(err.message);
    $("delConfirm").disabled = false;
  }
});

// ---- helpers ----
function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function escapeAttr(s) { return escapeHtml(s); }
