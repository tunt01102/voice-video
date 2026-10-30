// Tab Motion (docs/SDD-motion.md): style first → describe → confirm the brief → build → retype words, re-render,
// one video per spreadsheet row. A native take on the Motion Studio skill. Uses the globals from app.js.

const KIND_VI = { hook: "Mở đầu", title: "Tiêu đề", stat: "Con số", list: "Danh sách", chart: "Biểu đồ", quote: "Trích dẫn",
  image: "Hình ảnh", cta: "Kêu gọi" };
const FIELD_VI = { headline: "Tiêu đề", sub: "Dòng phụ", value: "Con số", label: "Chú thích", quote: "Trích dẫn",
  author: "Tác giả", button: "Nút" };
const ASPECT_HINT = { "16:9": "YouTube, website, màn hình họp", "9:16": "Reels, TikTok, Shorts", "1:1": "Bài đăng vuông",
  "4:5": "Bài đăng feed dọc" };
const ROLE_VI = { bg: "Nền", surface: "Thẻ / khối", ink: "Chữ", muted: "Chữ phụ", accent: "Điểm nhấn", accent2: "Nhấn phụ" };
const OPT_VI = { texture: "Hoạ tiết nền", deco: "Trang trí", shadow: "Đổ bóng", motion: "Chuyển động chữ", case: "Kiểu chữ tiêu đề",
  transition: "Chuyển cảnh", head_weight: "Độ đậm tiêu đề" };
const VAL_VI = { none: "không", grid: "lưới", dots: "chấm", stripes: "sọc chéo", rules: "kẻ ngang", grain: "hạt giấy", blobs: "quầng sáng",
  blocks: "mảng khối", rings: "vòng tròn", bubbles: "bong bóng", lines: "đường kẻ", frame: "khung viền", soft: "mềm", hard: "cứng",
  glow: "phát sáng", snappy: "bật lên", smooth: "mượt", bouncy: "nảy", typing: "gõ chữ", uppercase: "VIẾT HOA", fade: "mờ dần",
  smoothleft: "trượt trái", fadeblack: "qua nền đen", circleopen: "mở tròn", slideup: "trượt lên", dissolve: "hoà tan" };
let STYLES = null, OPTIONS = null, ROLES = [], picked = "auto", aspect = "16:9";
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
  $$(".mo-style", $("#mo-styles")).forEach((b) => b.onclick = () => pick(b.dataset.id));
  pick(STYLES.some((s) => s.id === picked) ? picked : "auto");
}
function pick(id) {
  picked = id;
  $$(".mo-style", $("#mo-styles")).forEach((x) => x.setAttribute("aria-checked", x.dataset.id === picked));
  $("#mo-own-act").hidden = !STYLES.find((s) => s.id === picked)?.own;
}
async function loadStyles() {
  const r = await api("/api/motion/styles");
  STYLES = r.styles; OPTIONS = r.options; ROLES = r.roles;
  paintStyles();
}

// ------------------------------------------------------------------ own looks: editor, palette from a picture
function editor(s) {
  const box = $("#mo-editor");
  const sel = (key, vals, cur) => `<label>${OPT_VI[key]}<select data-o="${key}">${vals.map((v) =>
    `<option value="${esc(v)}" ${String(v) === String(cur) ? "selected" : ""}>${esc(VAL_VI[v] || v)}</option>`).join("")}</select></label>`;
  const font = (k, lab) => `<label>${lab}<select data-font="${k}">${OPTIONS.fonts.map((f) =>
    `<option ${f === s.fonts[k] ? "selected" : ""} style="font-family:'${esc(f)}'">${esc(f)}</option>`).join("")}</select></label>`;
  box.innerHTML = `<h4>${s.id ? "Sửa phong cách" : "Phong cách mới"}</h4>
    <div class="mo-ed-grid"><div>
      <div class="grid2"><label>Tên<input class="mo-ed-name" value="${esc(s.name || "")}" maxlength="40"></label>
        <label>Dựa trên<select class="mo-ed-base">${STYLES.filter((x) => !x.own).map((x) =>
          `<option value="${esc(x.id)}" ${x.id === s.base ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></label></div>
      <label>Lấy màu từ ảnh mẫu (ảnh chụp website, poster, bảng màu thương hiệu)<input type="file" accept="image/*" class="mo-ed-img"></label>
      <div class="mo-ed-pal"></div>
      <div class="mo-ed-cols">${ROLES.map((r) => `<label>${ROLE_VI[r]}<input type="color" data-role="${r}" value="${esc(s.colors[r])}"></label>`).join("")}</div>
      <div class="grid2">${font("head", "Font tiêu đề")}${font("body", "Font nội dung")}${font("label", "Font nhãn")}
        ${sel("head_weight", OPTIONS.head_weight, s.head_weight)}${sel("case", OPTIONS.case, s.case)}
        <label>Bo góc: <output class="mo-ed-rv">${s.radius}</output><input type="range" data-n="radius" min="0" max="40" value="${s.radius}"></label>
        <label>Viền: <output class="mo-ed-bv">${s.border}</output><input type="range" data-n="border" min="0" max="6" value="${s.border}"></label>
        ${["shadow", "texture", "deco", "motion", "transition"].map((k) => sel(k, OPTIONS[k], s[k])).join("")}</div>
      <small class="muted">Chỉ có các font đã kiểm đủ dấu tiếng Việt. Màu chữ quá giống màu nền sẽ được tự chỉnh cho dễ đọc khi lưu.</small>
    </div><div class="mo-ed-pv"></div></div>
    <div class="actions"><button type="button" class="primary small mo-ed-save">Lưu phong cách</button>
      <button type="button" class="ghost small mo-ed-cancel">Đóng</button><span class="muted mo-ed-st"></span></div>`;
  const read = () => ({ id: s.id, name: $(".mo-ed-name", box).value.trim(), base: $(".mo-ed-base", box).value,
    colors: Object.fromEntries($$("[data-role]", box).map((i) => [i.dataset.role, i.value])),
    fonts: Object.fromEntries($$("[data-font]", box).map((i) => [i.dataset.font, i.value])),
    ...Object.fromEntries($$("[data-o]", box).map((i) => [i.dataset.o, i.dataset.o === "head_weight" ? +i.value : i.value])),
    ...Object.fromEntries($$("[data-n]", box).map((i) => [i.dataset.n, +i.value])), mood: s.mood, when: s.when, look: s.look });
  const preview = () => {
    const v = read();
    $(".mo-ed-rv", box).textContent = v.radius; $(".mo-ed-bv", box).textContent = v.border;
    $(".mo-ed-pv", box).innerHTML = styleCard({ ...s, ...v, id: "_pv", name: v.name || "Phong cách của tôi" });
  };
  box.oninput = preview; box.onchange = preview;
  $(".mo-ed-base", box).onchange = () => {  // a new base fills everything except the name
    const b = STYLES.find((x) => x.id === $(".mo-ed-base", box).value);
    editor({ ...b, id: s.id, name: $(".mo-ed-name", box).value, base: b.id, mood: "", when: "", look: "" });
  };
  $(".mo-ed-img", box).onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    $(".mo-ed-st", box).textContent = "Đang đọc màu…";
    try {
      const d = await api("/api/motion/styles/from-image", { method: "POST", body: JSON.stringify({ image: await upload(f), name: $(".mo-ed-name", box).value.trim() || null }) });
      const cur = read();  // only the colours come from the picture; fonts, shapes and motion stay as set
      editor({ ...s, ...cur, name: cur.name || d.name, colors: d.colors, look: d.look });
      $(".mo-ed-pal", $("#mo-editor")).innerHTML = `<span class="muted">Màu trong ảnh:</span> ` + d.palette.map((p) =>
        `<i title="${esc(p.color)} · ${Math.round(p.share * 100)}%" style="background:${esc(p.color)}"></i>`).join("");
    } catch (err) { toast(err.message); $(".mo-ed-st", box).textContent = ""; }
  };
  $(".mo-ed-cancel", box).onclick = () => { box.hidden = true; };
  $(".mo-ed-save", box).onclick = async (e) => {
    const btn = e.currentTarget; btn.disabled = true;
    try {
      const saved = await api("/api/motion/styles", { method: "POST", body: JSON.stringify(read()) });
      await loadStyles(); pick(saved.id); box.hidden = true; toast(`Đã lưu “${saved.name}”`);
    } catch (err) { toast(err.message); } finally { btn.disabled = false; }
  };
  box.hidden = false; preview();
  box.scrollIntoView({ behavior: "smooth", block: "nearest" });
}
$("#mo-new-style").onclick = () => {
  const b = STYLES.find((x) => x.id === picked && !x.own) || STYLES.find((x) => x.id === picked) || STYLES[0];
  editor({ ...b, id: null, base: b.base || b.id, name: "", mood: "", when: "", look: "" });
};
$("#mo-edit-style").onclick = () => editor(STYLES.find((x) => x.id === picked));
$("#mo-del-style").onclick = async () => {
  const s = STYLES.find((x) => x.id === picked);
  if (!s?.own || !confirm(`Xoá phong cách “${s.name}”? Video đã dựng không bị ảnh hưởng.`)) return;
  try { await api(`/api/motion/styles/${encodeURIComponent(s.id)}`, { method: "DELETE" }); picked = "auto"; $("#mo-editor").hidden = true; await loadStyles(); }
  catch (err) { toast(err.message); }
};

async function voiceOptions(sel, current) {
  if (typeof voices !== "undefined" && !voices.length) { try { voices = await api("/api/voices"); } catch {} }
  const list = typeof voices !== "undefined" ? voices : [], dv = list.find((v) => v.default);
  sel.innerHTML = `<option value="">Không đọc (chỉ chữ và nhạc)</option><option value="default">Giọng mặc định${dv ? ` · ${esc(dv.name)}` : ""}</option>` +
    list.map((v) => `<option value="${esc(v.id)}">${v.kind === "stock" ? "Giọng mẫu: " : ""}${esc(v.name)}${v.default ? " ★" : ""}</option>`).join("");
  sel.value = current || ""; if (sel.value !== (current || "")) sel.value = "";
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
    try { await loadStyles(); } catch (e) { toast(e.message); return; }
  }
  musicOptions($("#mo-music"), $("#mo-music").value);
  voiceOptions($("#mo-voice"), $("#mo-voice").value);
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
      review: form.review.checked ? ["brief"] : [], alpha: form.alpha.checked, voice: form.voice.value };
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

function sceneEditor(sc, i, narrated) {
  const fields = Object.keys(FIELD_VI).filter((k) => sc[k] != null).map((k) =>
    `<label>${FIELD_VI[k]}<input data-f="${k}" value="${esc(sc[k])}"></label>`).join("");
  const items = sc.items ? `<label>Các dòng (mỗi dòng một ý)<textarea data-f="items" rows="${sc.items.length}">${esc(sc.items.join("\n"))}</textarea></label>` : "";
  const said = narrated ? `<label>Lời đọc (giọng thuyết minh nói gì ở cảnh này${sc.narration ? "" : "; để trống = đọc chữ trên cảnh"})<textarea data-f="narration" rows="2">${esc(sc.narration || "")}</textarea></label>` : "";
  const bars = sc.bars ? `<label>Cột biểu đồ (mỗi dòng “nhãn: số”)<textarea data-f="bars" rows="${sc.bars.length}">${esc(sc.bars.map((b) => `${b.label}: ${b.value}`).join("\n"))}</textarea></label>` : "";
  return `<li data-i="${i}"><div class="mo-scene-head"><span class="chip on">${KIND_VI[sc.kind] || sc.kind}</span>
      <span class="muted">${esc(sc.note || "")}</span>
      <label class="mo-sec">giây<input type="number" data-f="seconds" min="1.6" max="8" step="0.1" value="${sc.seconds}"></label>
      <button type="button" class="ghost small mo-del" title="Bỏ cảnh này">✕</button></div>
    <div class="grid2">${fields}</div>${items}${bars}${said}</li>`;
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
    <h4>Các cảnh</h4><ol class="mo-scenes">${(b.scenes || []).map((sc, i) => sceneEditor(sc, i, !!job.params?.voice)).join("")}</ol>
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
  const alpha = fileUrl(job.files?.alpha) ? `<a href="${fileUrl(job.files.alpha)}" download>⬇ MOV nền trong suốt</a>` : "";
  box.innerHTML = `${versions.length || alpha ? `<div class="links mo-versions">${alpha}${versions.map((v) => `<a href="${fileUrl(v.video)}" download>⬇ ${esc(v.name)}.mp4</a>`).join("")}</div>` : ""}
    <details class="mo-edit"><summary>✏️ Sửa chữ, đổi phong cách hoặc khung hình &amp; dựng lại</summary>
      ${varsTable(vars, r.values)}
      <div class="grid2"><label>Phong cách${styleSelect(b.style || r.style)}</label>
        <label>Khung hình<select class="mo-asp">${Object.keys(ASPECT_HINT).map((a) => `<option ${a === r.aspect ? "selected" : ""}>${a}</option>`).join("")}</select></label>
        <label>Nhạc nền<select class="mo-mus"></select></label>
        <label>Giọng đọc<select class="mo-voice"></select></label></div>
      <label class="check"><input type="checkbox" class="mo-alpha" ${p.alpha ? "checked" : ""}> Kèm bản MOV nền trong suốt</label>
      <div class="actions"><button class="primary small mo-redo">Dựng lại</button><span class="muted">Không gọi lại Claude: chỉ thay chữ và dựng, nhanh hơn nhiều.</span></div>
    </details>
    ${vars.length ? `<details class="mo-batch"><summary>📊 Nhiều phiên bản từ bảng tính (CSV)</summary>
      <p class="muted">Mỗi dòng là một video. Cột đặt tên theo ô sửa nhanh (mã hoặc nhãn, ví dụ “${esc(vars[0].label)}”), thêm cột <code>name</code> để đặt tên file. Ô để trống giữ nguyên như bản gốc. Excel: lưu dạng “CSV UTF-8”.</p>
      <div class="actions"><a class="btn ghost small mo-tpl" download="motion-mau.csv">⬇ CSV mẫu</a>
        <input type="file" accept=".csv,text/csv" class="mo-csv"><button class="primary small mo-run" disabled>Dựng mỗi dòng một video</button>
        <span class="muted mo-csv-st"></span></div></details>` : ""}`;
  const mus = $(".mo-mus", box);
  musicOptions(mus, r.report?.music_id || "none");
  voiceOptions($(".mo-voice", box), p.voice || "");
  const base = () => ({ brief: b, style: $(".mo-style-sel", box).value, aspect: $(".mo-asp", box).value, music: mus.value,
    seconds: (b.scenes || []).reduce((a, s) => a + s.seconds, 0), images: p.images || [], logo: p.logo || null, lang: p.lang,
    prompt: p.prompt, review: [], alpha: $(".mo-alpha", box).checked, voice: $(".mo-voice", box).value });
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
