// Tab Motion (docs/SDD-motion.md): style first → describe → confirm the brief → build → retype words, re-render,
// one video per spreadsheet row. A native take on the Motion Studio skill. Uses the globals from app.js.

const KIND_VI = { hook: "Mở đầu", title: "Tiêu đề", stat: "Con số", list: "Danh sách", chart: "Biểu đồ", quote: "Trích dẫn",
  image: "Hình ảnh", cta: "Kêu gọi" };
const FIELD_VI = { headline: "Tiêu đề", sub: "Dòng phụ", value: "Con số", label: "Chú thích", quote: "Trích dẫn",
  author: "Tác giả", button: "Nút" };
const ASPECT_HINT = { "16:9": "YouTube, website, màn hình họp", "9:16": "Reels, TikTok, Shorts", "1:1": "Bài đăng vuông",
  "4:5": "Bài đăng feed dọc" };
let STYLES = null, picked = "auto", aspect = "16:9";
const form = $("#motion-form");

function styleCard(s) {
  const c = s.colors, f = s.fonts;
  return `<button type="button" class="mo-style" role="radio" data-id="${esc(s.id)}" aria-checked="${s.id === picked}">
    <div class="mo-pv" style="background:${c.bg};color:${c.ink}">
      <i class="mo-bar" style="background:${c.accent};border-radius:${s.radius / 3}px"></i>
      <b style="font-family:'${esc(f.head)}';font-weight:${s.head_weight};text-transform:${s.case}">${esc(s.name)}</b>
      <span style="font-family:'${esc(f.body)}';color:${c.muted}">Chữ nội dung · 1.200+</span>
      <em style="background:${c.accent};color:${c.bg};border-radius:${s.radius / 2}px;font-family:'${esc(f.head)}'">Đặt ngay</em>
      <span class="mo-sw">${Object.values(c).slice(0, 6).map((v) => `<i style="background:${v}"></i>`).join("")}</span></div>
    <div class="mo-meta"><strong>${esc(s.name)}</strong><small class="mo-mood">${esc(s.mood)}</small>
      <small>${esc(s.when)}</small><small class="muted">${esc(s.look)}</small></div></button>`;
}

function paintStyles() {
  const auto = `<button type="button" class="mo-style mo-auto" role="radio" data-id="auto" aria-checked="${picked === "auto"}">
    <div class="mo-pv"><b>✨</b><span>Chưa biết chọn gì?</span></div>
    <div class="mo-meta"><strong>Tự chọn giúp mình</strong><small>Claude chọn phong cách hợp với nội dung; bạn đổi được ở bước duyệt brief.</small></div></button>`;
  $("#mo-styles").innerHTML = auto + STYLES.map(styleCard).join("");
  $$(".mo-style", $("#mo-styles")).forEach((b) => b.onclick = () => {
    picked = b.dataset.id;
    $$(".mo-style").forEach((x) => x.setAttribute("aria-checked", x.dataset.id === picked));
  });
}

function musicOptions(sel, current) {
  const tracks = (typeof musicTracks !== "undefined" ? musicTracks : []).filter((t) => t.available);
  sel.innerHTML = `<option value="auto">Tự chọn theo không khí video</option><option value="none">Không nhạc</option>` +
    tracks.map((t) => `<option value="${esc(t.id)}">${esc(t.title)} — ${esc(t.mood)}</option>`).join("");
  if (current) sel.value = current;
}

async function recent() {
  const box = $("#motion-result");
  if (box.dataset.loaded) return;
  box.dataset.loaded = "1";
  try {
    const jobs = await api("/api/jobs?limit=6&type=motion");
    if (!jobs.length) return;
    box.insertAdjacentHTML("beforeend", `<h3 class="muted">Video motion gần đây</h3>`);
    jobs.forEach((j) => { box.append(renderJob(j)); if (["queued", "running"].includes(j.status)) followJob(j.id); });
  } catch {}
}

window.loadMotion = async () => {
  if (!STYLES) {
    try { STYLES = (await api("/api/motion/styles")).styles; } catch (e) { toast(e.message); return; }
    paintStyles();
  }
  musicOptions($("#mo-music"), $("#mo-music").value);
  recent();
};

$$("#mo-aspect button").forEach((b) => b.onclick = () => {
  aspect = b.dataset.v;
  $$("#mo-aspect button").forEach((x) => x.classList.toggle("active", x === b));
  $("#mo-aspect-hint").textContent = ASPECT_HINT[aspect];
});
form.seconds.oninput = () => { $("#mo-sec-out").textContent = form.seconds.value + " giây"; };

function showJob(job) {
  const box = $("#motion-result");
  const el = renderJob(job);
  box.prepend(el); followJob(job.id);
  el.scrollIntoView({ behavior: "smooth", block: "start" });
}

form.onsubmit = async (e) => {
  e.preventDefault();
  const btn = $("#mo-submit"); btn.disabled = true; $("#mo-hint").textContent = "Đang gửi…";
  try {
    const params = { style: picked, prompt: form.prompt.value.trim(), message: form.message.value.trim(), offer: form.offer.value.trim(),
      audience: form.audience.value.trim(), lang: form.lang.value, aspect, seconds: +form.seconds.value, music: form.music.value,
      review: form.review.checked ? ["brief"] : [] };
    if (!params.prompt) throw new Error("Hãy mô tả video trước");
    if (form.logo.files[0]) params.logo = await upload(form.logo.files[0]);
    params.images = [];
    for (const f of [...form.images.files].slice(0, 8)) params.images.push(await upload(f));
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ type: "motion", params, source: "dashboard" }) });
    showJob(job);
    $("#mo-hint").textContent = params.review.length ? "Claude đang viết brief — bạn sẽ duyệt ở bên dưới." : "Đang dựng — theo dõi bên dưới.";
  } catch (err) { toast(err.message); $("#mo-hint").textContent = ""; }
  finally { btn.disabled = false; }
};

// ------------------------------------------------------------------ the brief gate
function varInput(v, value) {
  const val = esc(value ?? v.value);
  return v.type === "color" ? `<input type="color" data-var="${esc(v.id)}" value="${val}">`
    : `<input data-var="${esc(v.id)}" value="${val}" ${v.type === "number" ? 'inputmode="decimal"' : ""}>`;
}
function varsTable(vars, values) {
  if (!vars.length) return `<p class="muted">Brief này không có ô nào để sửa nhanh.</p>`;
  return `<div class="mo-vars">${vars.map((v) => `<label>${esc(v.label)} <code>${esc(v.id)}</code>${varInput(v, values?.[v.id])}</label>`).join("")}</div>`;
}
function readVars(box) {
  return Object.fromEntries($$("[data-var]", box).map((i) => [i.dataset.var, i.value]));
}
function styleSelect(cur) {
  return `<select class="mo-style-sel">${(STYLES || []).map((s) => `<option value="${esc(s.id)}" ${s.id === cur ? "selected" : ""}>${esc(s.name)} — ${esc(s.mood)}</option>`).join("")}</select>`;
}

function sceneEditor(sc, i) {
  const fields = Object.keys(FIELD_VI).filter((k) => sc[k] != null).map((k) =>
    `<label>${FIELD_VI[k]}<input data-f="${k}" value="${esc(sc[k])}"></label>`).join("");
  const items = sc.items ? `<label>Các dòng (mỗi dòng một ý)<textarea data-f="items" rows="${sc.items.length}">${esc(sc.items.join("\n"))}</textarea></label>` : "";
  const bars = sc.bars ? `<label>Cột biểu đồ (mỗi dòng “nhãn: số”)<textarea data-f="bars" rows="${sc.bars.length}">${esc(sc.bars.map((b) => `${b.label}: ${b.value}`).join("\n"))}</textarea></label>` : "";
  return `<li data-i="${i}"><div class="mo-scene-head"><span class="chip on">${KIND_VI[sc.kind] || sc.kind}</span>
      <span class="muted">${esc(sc.note || "")}</span>
      <label class="mo-sec">giây<input type="number" data-f="seconds" min="1.6" max="8" step="0.1" value="${sc.seconds}"></label>
      <button type="button" class="ghost small mo-del" title="Bỏ cảnh này">✕</button></div>
    <div class="grid2">${fields}</div>${items}${bars}</li>`;
}
function readScenes(box, scenes) {
  return $$(".mo-scenes > li", box).filter((li) => !li.classList.contains("removed")).map((li) => {
    const sc = { ...scenes[+li.dataset.i] };
    $$("[data-f]", li).forEach((el) => {
      const k = el.dataset.f, v = el.value;
      if (k === "items") sc.items = v.split("\n").map((x) => x.trim()).filter(Boolean);
      else if (k === "bars") sc.bars = v.split("\n").map((x) => x.split(":")).filter((p) => p.length > 1)
        .map(([l, ...r]) => ({ label: l.trim(), value: r.join(":").trim() }));
      else if (k === "seconds") sc.seconds = +v;
      else sc[k] = v;
    });
    return sc;
  });
}

window.motionGate = (job, out) => {
  const r = job.result || {}, b = r.brief_data || {};
  const box = document.createElement("div");
  box.className = "gate";
  const total = (b.scenes || []).reduce((a, s) => a + s.seconds, 0);
  box.innerHTML = `<h3>Duyệt brief</h3>
    <p>${esc(b.summary || "")}</p>
    <p class="muted">${(b.scenes || []).length} cảnh · khoảng ${Math.round(total)} giây · khung ${esc(r.aspect || "")}. Máy chỉ dựng sau khi bạn duyệt. Chữ trong {{ }} là ô sửa nhanh bên dưới.</p>
    ${(b.warnings || []).length ? `<p class="qa-bad">${b.warnings.map(esc).join(" · ")}</p>` : ""}
    <div class="grid2"><label>Tên video<input class="mo-title" value="${esc(b.title || "")}"></label><label>Phong cách${styleSelect(b.style || r.style)}</label></div>
    <h4>Ô sửa nhanh (variables)</h4>${varsTable(b.variables || [])}
    <h4>Các cảnh</h4><ol class="mo-scenes">${(b.scenes || []).map(sceneEditor).join("")}</ol>
    <div class="actions"><button class="primary mo-go">Duyệt brief → dựng video</button>
      <button class="ghost mo-cancel">Huỷ</button><span class="muted mo-st"></span></div>`;
  if (!STYLES) window.loadMotion().then(() => { const s = $(".mo-style-sel", box); if (s) s.outerHTML = styleSelect(b.style || r.style); });
  $$(".mo-del", box).forEach((x) => x.onclick = () => x.closest("li").classList.toggle("removed"));
  $(".mo-cancel", box).onclick = () => api(`/api/jobs/${job.id}/cancel`, { method: "POST" }).then(() => refreshJob(job.id), (e) => toast(e.message));
  $(".mo-go", box).onclick = async (ev) => {
    const btn = ev.currentTarget, vals = readVars(box);
    const brief = { title: $(".mo-title", box).value, style: $(".mo-style-sel", box).value,
      variables: (b.variables || []).map((v) => ({ ...v, value: vals[v.id] ?? v.value })), scenes: readScenes(box, b.scenes || []) };
    if (!brief.scenes.length) return toast("Brief không còn cảnh nào");
    btn.disabled = true; $(".mo-st", box).textContent = "Đang gửi…";
    try { const j = await api(`/api/jobs/${job.id}/approve`, { method: "POST", body: JSON.stringify({ stage: "brief", brief }) }); updateJob(j); followJob(j.id); }
    catch (err) { btn.disabled = false; $(".mo-st", box).textContent = ""; toast(err.message); }
  };
  out.append(box);
};

// ------------------------------------------------------------------ a finished video: retype, re-render, batch
function parseCSV(text) {
  text = text.replace(/^﻿/, "");
  const first = text.split(/\r?\n/)[0] || "", delim = (first.match(/;/g) || []).length > (first.match(/,/g) || []).length ? ";" : ",";
  const rows = []; let row = [], cell = "", q = false;
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (q) { if (ch === '"' && text[i + 1] === '"') { cell += '"'; i++; } else if (ch === '"') q = false; else cell += ch; }
    else if (ch === '"') q = true;
    else if (ch === delim) { row.push(cell); cell = ""; }
    else if (ch === "\n" || ch === "\r") { if (ch === "\r" && text[i + 1] === "\n") i++; row.push(cell); rows.push(row); row = []; cell = ""; }
    else cell += ch;
  }
  if (cell || row.length) { row.push(cell); rows.push(row); }
  const [head, ...body] = rows.filter((r) => r.some((c) => c.trim()));
  return body.map((r) => Object.fromEntries(head.map((h, i) => [h.trim(), (r[i] ?? "").trim()])));
}
function csvCell(v) { v = String(v ?? ""); return /[",;\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v; }

window.motionResult = (job) => {
  const r = job.result || {}, b = r.brief_data || {}, p = job.params || {}, vars = b.variables || [];
  const box = document.createElement("div");
  box.className = "mo-after";
  const versions = (job.files?.versions || []).filter((v) => fileUrl(v.video));
  box.innerHTML = `${versions.length ? `<div class="links mo-versions">${versions.map((v) => `<a href="${fileUrl(v.video)}" download>⬇ ${esc(v.name)}.mp4</a>`).join("")}</div>` : ""}
    <details class="mo-edit"><summary>✏️ Sửa chữ, đổi phong cách hoặc khung hình &amp; dựng lại</summary>
      ${varsTable(vars, r.values)}
      <div class="grid2"><label>Phong cách${styleSelect(b.style || r.style)}</label>
        <label>Khung hình<select class="mo-asp">${Object.keys(ASPECT_HINT).map((a) => `<option ${a === r.aspect ? "selected" : ""}>${a}</option>`).join("")}</select></label>
        <label>Nhạc nền<select class="mo-mus"></select></label></div>
      <div class="actions"><button class="primary small mo-redo">Dựng lại</button><span class="muted">Không gọi lại Claude: chỉ thay chữ và dựng, nhanh hơn nhiều.</span></div>
    </details>
    ${vars.length ? `<details class="mo-batch"><summary>📊 Nhiều phiên bản từ bảng tính (CSV)</summary>
      <p class="muted">Mỗi dòng là một video. Cột đặt tên theo ô sửa nhanh (mã hoặc nhãn, ví dụ “${esc(vars[0].label)}”), thêm cột <code>name</code> để đặt tên file. Ô để trống giữ nguyên như bản gốc. Excel: lưu dạng “CSV UTF-8”.</p>
      <div class="actions"><a class="btn ghost small mo-tpl" download="motion-mau.csv">⬇ CSV mẫu</a>
        <input type="file" accept=".csv,text/csv" class="mo-csv"><button class="primary small mo-run" disabled>Dựng mỗi dòng một video</button>
        <span class="muted mo-csv-st"></span></div></details>` : ""}`;
  const mus = $(".mo-mus", box);
  musicOptions(mus, r.report?.music_id || "none");
  const base = () => ({ brief: b, style: $(".mo-style-sel", box).value, aspect: $(".mo-asp", box).value, music: mus.value,
    seconds: (b.scenes || []).reduce((a, s) => a + s.seconds, 0), images: p.images || [], logo: p.logo || null, lang: p.lang,
    prompt: p.prompt, review: [] });
  const go = async (extra, btn) => {
    btn.disabled = true;
    try {
      const j = await api("/api/jobs", { method: "POST", body: JSON.stringify({ type: "motion", params: { ...base(), ...extra }, source: "dashboard" }) });
      if ($("#tab-motion").classList.contains("active")) showJob(j); else { toast("Đang dựng — theo dõi ở tab Motion hoặc Jobs"); followJob(j.id); }
    } catch (err) { toast(err.message); }
    finally { btn.disabled = false; }
  };
  $(".mo-redo", box).onclick = (e) => go({ variables: readVars($(".mo-edit", box)) }, e.currentTarget);
  if (vars.length) {
    const tpl = ["name", ...vars.map((v) => v.label)].map(csvCell).join(",") + "\n" +
      ["ban-goc", ...vars.map((v) => r.values?.[v.id] ?? v.value)].map(csvCell).join(",") + "\n";
    $(".mo-tpl", box).href = URL.createObjectURL(new Blob(["﻿" + tpl], { type: "text/csv" }));
    let rows = [];
    $(".mo-csv", box).onchange = async (e) => {
      const f = e.target.files[0]; rows = [];
      if (f) try { rows = parseCSV(await f.text()); } catch (err) { toast("Không đọc được CSV: " + err.message); }
      const known = new Set(["name", ...vars.flatMap((v) => [v.id, v.label.toLowerCase()])]);
      const cols = rows.length ? Object.keys(rows[0]) : [], unknown = cols.filter((c) => !known.has(c) && !known.has(c.toLowerCase()));
      $(".mo-csv-st", box).textContent = rows.length ? `${rows.length} dòng` + (unknown.length ? ` · bỏ qua cột: ${unknown.join(", ")}` : "") : "";
      $(".mo-run", box).disabled = !rows.length || rows.length > 50;
      if (rows.length > 50) toast("Tối đa 50 dòng một lần");
    };
    $(".mo-run", box).onclick = (e) => go({ variables: readVars($(".mo-edit", box)), rows }, e.currentTarget);
  }
  if (!STYLES) window.loadMotion().then(() => $$(".mo-style-sel", box).forEach((s) => { s.outerHTML = styleSelect(b.style || r.style); }));
  return box;
};

if (location.hash === "#motion") window.loadMotion();
