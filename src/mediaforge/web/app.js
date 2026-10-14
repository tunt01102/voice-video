// MediaForge dashboard — build-free vanilla JS.
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const api = async (path, opts = {}) => {
  const r = await fetch(path, { headers: opts.body && !(opts.body instanceof FormData) ? { "content-type": "application/json" } : {}, ...opts });
  if (!r.ok) throw new Error((await r.text()).slice(0, 400));
  return r.json();
};
const toast = (msg) => { const t = document.createElement("div"); t.className = "toast"; t.textContent = msg; document.body.append(t); setTimeout(() => t.remove(), 3500); };
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const TYPE_LABEL = { tts: "Giọng nói", image: "Ảnh", subtitle: "Phụ đề", short: "Short", narrated: "Video", explainer: "Explainer", screen: "Screen", video: "Video", enrol: "Thu mẫu" };

// ------------------------------------------------------------------ tabs
$$("#tabs button").forEach((b) => b.onclick = () => {
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  ({ jobs: loadJobs, voices: loadVoices, lexicon: loadLexicon, system: loadHealth })[b.dataset.tab]?.();
  history.replaceState(null, "", "#" + b.dataset.tab);
});

// ------------------------------------------------------------------ health
async function loadHealth() {
  try {
    const h = await api("/api/health");
    $("#health-dot").className = "dot ok";
    $("#health").textContent = JSON.stringify(h, null, 2);
  } catch { $("#health-dot").className = "dot bad"; }
}
$("#unload").onclick = async () => { const r = await api("/api/models/unload", { method: "POST" }); toast("Đã giải phóng: " + r.unloaded.join(", ")); loadHealth(); };
setInterval(loadHealth, 15000); loadHealth();

// ------------------------------------------------------------------ create form
let kind = "tts";
const VIDEO_KINDS = ["short", "narrated", "explainer"];
function applyKind() {
  const isVideo = VIDEO_KINDS.includes(kind);
  const show = {
    "f-text": kind === "tts" || isVideo, "f-video": isVideo, "f-image": kind === "image", "f-subtitle": kind === "subtitle",
    "f-tts": kind === "tts", "f-voice": kind === "tts" || isVideo, "f-voicefx": kind === "tts" || isVideo,
    "f-lang": kind !== "image", "only-explainer": kind === "explainer",
  };
  $$("#create-form [class*='f-'], #create-form .only-explainer").forEach((el) => {
    const classes = [...el.classList].filter((c) => c in show);
    el.style.display = classes.some((c) => show[c]) ? "" : "none";
  });
  if (kind === "image") $$("#create-form .f-image").forEach((el) => el.style.display = "");
  const f = $("#create-form");
  f.seconds.value = kind === "short" ? 40 : kind === "explainer" ? 90 : 75;
  const src = f.source.value;
  f.text.placeholder = kind === "tts" ? "Văn bản cần đọc (Tiếng Việt / English / trộn lẫn)…"
    : src === "brief" ? "Ý tưởng video, ví dụ: 5 mẹo bảo mật mật khẩu cho nhân viên văn phòng"
    : src === "slides" ? "# Tiêu đề slide\n- ý 1\n- ý 2\n> Lời thuyết minh cho slide này"
    : "Kịch bản lời đọc của bạn…";
  $("#create-hint").textContent = isVideo ? "Video mất vài phút — theo dõi ở tab Jobs." : "";
}
$$("#create-kind button").forEach((b) => b.onclick = () => {
  $$("#create-kind button").forEach((x) => x.classList.toggle("active", x === b));
  kind = b.dataset.kind; applyKind();
});
$("#create-form").source.onchange = applyKind;
$$("input[type=range][data-out]").forEach((r) => { const o = document.getElementById(r.dataset.out); const u = () => o.textContent = (+r.value).toFixed(r.step < 0.1 ? 2 : 1); r.oninput = u; u(); });
applyKind();

// Background-music library: calm, free tracks (see `mediaforge music sync`), plus "upload my own".
const MOOD_LABEL = { piano: "Piano", ambient: "Ambient", acoustic: "Acoustic", classical: "Cổ điển", lofi: "Lo-fi", strings: "Dây", other: "Khác" };
let musicTracks = [];
async function loadMusic() {
  try { musicTracks = (await api("/api/music")).filter((t) => t.available); } catch { musicTracks = []; }
  const sel = $("#create-form").music, groups = {};
  for (const t of musicTracks) (groups[t.mood] ||= []).push(t);
  for (const [mood, ts] of Object.entries(groups)) {
    const og = document.createElement("optgroup"); og.label = MOOD_LABEL[mood] || mood;
    for (const t of ts) og.append(new Option(`${t.title} — ${t.artist}${t.duration_s ? ` (${Math.floor(t.duration_s / 60)}:${String(Math.round(t.duration_s % 60)).padStart(2, "0")})` : ""}`, t.id));
    sel.insertBefore(og, sel.querySelector("option[value=__upload]"));
  }
}
$("#create-form").music.onchange = (e) => {
  const v = e.target.value, t = musicTracks.find((x) => x.id === v), pv = $("#music-preview"), info = $("#music-info");
  $("#create-form").music_file.hidden = v !== "__upload";
  pv.hidden = !t; if (t) pv.src = t.url; else pv.removeAttribute("src");
  info.textContent = t ? `${t.licence}${t.attribution ? " — cần ghi công: " + t.attribution : " — không cần ghi công"}` : "";
};
loadMusic();

async function upload(file) {
  const fd = new FormData(); fd.append("file", file);
  return (await api("/api/uploads", { method: "POST", body: fd })).path;
}

$("#create-form").onsubmit = async (e) => {
  e.preventDefault();
  const f = e.target, btn = $("#submit-btn");
  btn.disabled = true;
  try {
    const fx = { pitch: +f.pitch.value, pace: +f.pace.value, warmth: +f.warmth.value, clarity: +f.clarity.value };
    let type = kind, params = {};
    if (kind === "tts") params = { text: f.text.value, voice: f.voice.value || null, lang: f.lang.value, engine: f.engine.value || null, fx };
    else if (kind === "image") params = { prompt: f.prompt.value, aspect: f.aspect.value, count: +f.count.value, style: f.style.value };
    else if (kind === "subtitle") {
      if (!f.media_file.files[0]) throw new Error("Chọn file audio/video");
      params = { media: await upload(f.media_file.files[0]), lang: f.lang.value === "auto" ? null : f.lang.value, burn: f.burn.checked, translate_to: f.translate_to.value || null };
    } else {
      params = { lang: f.lang.value, voice: f.voice.value || null, seconds: +f.seconds.value, style: f.style.value, fx, theme: f.theme.value };
      const src = f.source.value;
      if (src === "brief") params.brief = f.text.value; else if (src === "slides") params.slides_markdown = f.text.value; else params.script = f.text.value;
      if (f.music.value === "__upload") { if (f.music_file.files[0]) params.music = await upload(f.music_file.files[0]); }
      else if (f.music.value) params.music = f.music.value;
    }
    if ((params.text ?? params.prompt ?? params.brief ?? params.script ?? params.slides_markdown ?? params.media ?? "").length === 0) throw new Error("Thiếu nội dung");
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ type, params, source: "dashboard" }) });
    const box = $("#create-result"); box.innerHTML = "";
    box.append(renderJob(job)); followJob(job.id);
  } catch (err) { toast(err.message); }
  finally { btn.disabled = false; }
};

// ------------------------------------------------------------------ jobs
const jobEls = new Map();
function fileUrl(v) { return v && typeof v === "object" && v.url ? v.url : null; }
function renderOutputs(job, out) {
  out.innerHTML = "";
  const f = job.files || {};
  if (job.status === "failed") {
    const err = job.error || "", head = job.message || err.split("\n")[0];
    out.innerHTML = `<p class="qa-bad">${esc(head)}</p>` + (err.includes("\n") ? `<details><summary class="muted">Chi tiết lỗi</summary><pre class="qa-bad">${esc(err)}</pre></details>` : "");
    return;
  }
  if (job.status !== "done") return;
  if (fileUrl(f.video)) out.insertAdjacentHTML("beforeend", `<video controls preload="metadata" src="${fileUrl(f.video)}" poster="${fileUrl(f.thumbnail) || ""}"></video>`);
  const credit = job.result?.report?.music_credit;
  if (credit) out.insertAdjacentHTML("beforeend", `<p class="muted">♪ Ghi công nhạc nền (bắt buộc khi đăng): ${esc(credit)}</p>`);
  if (Array.isArray(f.images)) f.images.forEach((i) => out.insertAdjacentHTML("beforeend", `<a href="${fileUrl(i)}" target="_blank"><img src="${fileUrl(i)}"></a>`));
  if (fileUrl(f.audio)) out.insertAdjacentHTML("beforeend", `<audio controls src="${fileUrl(f.audio)}"></audio>`);
  const links = [];
  const add = (label, v) => fileUrl(v) && links.push(`<a href="${fileUrl(v)}" download>${label}</a>`);
  add("MP4", f.video); add("WAV", f.audio || f.voice); add("MP3", f.mp3);
  if (f.subtitles) { add("SRT", f.subtitles.srt); add("VTT", f.subtitles.vtt); add("ASS", f.subtitles.ass); }
  if (f.translated) add("SRT (dịch)", f.translated.srt);
  if (links.length) out.insertAdjacentHTML("beforeend", `<div class="links">${links.join("")}</div>`);
  const r = job.result || {};
  if (Array.isArray(r.sentences) && r.sentences.length) out.append(renderQA(job));
  if (r.text && job.type === "subtitle") out.insertAdjacentHTML("beforeend", `<pre>${esc(r.text)}</pre>`);
}
// ------------------------------------------------------------------ pronunciation QA panel
const normTok = (t) => t.toLowerCase().replace(/[^\p{L}\p{N}']/gu, "");
function markMissing(text, missing, heard) {
  // Underline the words the recogniser did not hear correctly. Older reports have no `missing`
  // list, so fall back to words of the sentence that are absent from what was heard.
  let words = (missing || []).join(" ").toLowerCase().split(/\s+/).filter(Boolean);
  if (!words.length && heard) {
    const h = new Set(heard.split(/\s+/).map(normTok));
    words = text.split(/\s+/).map(normTok).filter((k) => k && !h.has(k));
  }
  const bad = new Set(words.map(normTok));
  return text.split(/(\s+)/).map((tok) => {
    const k = normTok(tok);
    return k && bad.has(k) ? `<mark class="miss">${esc(tok)}</mark>` : esc(tok);
  }).join("");
}
function renderQA(job) {
  const r = job.result, sents = r.sentences;
  const failed = sents.filter((s) => !s.passed);
  const box = document.createElement("div");
  box.className = "qa";
  box.innerHTML = `<div class="qa-head">${failed.length
      ? `<span class="qa-bad">⚠ ${failed.length}/${sents.length} câu chưa đạt kiểm tra phát âm — bôi đỏ bên dưới. Bấm 🎙 để tự ghi âm câu đó (bản ghi thay vào kết quả và được lưu làm mẫu giọng) hoặc 🔁 để tạo lại.</span>`
      : `<span class="qa-ok">✓ ${sents.length}/${sents.length} câu đạt kiểm tra phát âm</span>`}
    <button class="ghost small qa-toggle">${failed.length ? "Ẩn/hiện tất cả câu" : "Xem từng câu"}</button></div>
    <ol class="qa-list ${failed.length ? "" : "collapsed"}"></ol>`;
  const list = $(".qa-list", box);
  sents.forEach((s) => {
    const li = document.createElement("li");
    li.className = s.passed ? (s.source === "recording" ? "rec-ok" : "ok") : "bad";
    li.innerHTML = `<div class="qa-text">${s.passed ? esc(s.text) : markMissing(s.text, s.missing, s.heard)}</div>
      <div class="qa-meta">${s.source === "recording" ? "🎙 giọng ghi âm của bạn" :
        `${s.lang} · ${s.engine} · lỗi ${(s.score * 100).toFixed(0)}%${s.passed ? "" : ` · nghe thành: “${esc(s.heard)}”`}`}</div>
      <div class="qa-actions">
        <button class="rec small" data-act="rec">🎙 Ghi âm câu này</button>
        <button class="ghost small" data-act="regen">🔁 Tạo lại</button>
        <span class="muted qa-status"></span></div>`;
    if (s.passed) li.classList.add("only-hover");
    $("[data-act=rec]", li).onclick = (e) => recordSentence(job, s, e.currentTarget, li);
    $("[data-act=regen]", li).onclick = () => patchJob(job, { regen: [s.text] }, li);
    list.append(li);
  });
  $(".qa-toggle", box).onclick = () => list.classList.toggle("collapsed");
  return box;
}
async function patchJob(job, body, li) {
  $(".qa-status", li).textContent = "Đang tạo bản mới…";
  try {
    const r = await api(`/api/jobs/${job.id}/patch`, { method: "POST", body: JSON.stringify(body) });
    const el = renderJob(r.job);
    jobEls.get(job.id)?.after(el); followJob(r.job.id);
    $(".qa-status", li).textContent = "→ bản mới đang render ngay bên dưới";
    if (r.enrol) toast("Đã lưu bản ghi làm mẫu giọng");
  } catch (err) { $(".qa-status", li).textContent = ""; toast(err.message); }
}
let sentRec = null;
async function recordSentence(job, s, btn, li) {
  if (sentRec && sentRec.state === "recording") { sentRec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
    const parts = []; sentRec = new MediaRecorder(stream);
    sentRec.ondataavailable = (e) => e.data.size && parts.push(e.data);
    sentRec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop()); btn.classList.remove("on"); btn.textContent = "🎙 Ghi âm câu này";
      const blob = new Blob(parts, { type: sentRec.mimeType || "audio/webm" });
      const ext = (sentRec.mimeType || "").includes("mp4") ? "m4a" : "webm";
      $(".qa-status", li).textContent = "Đang tải bản ghi…";
      const file = await upload(new File([blob], `sentence.${ext}`, { type: blob.type }));
      await patchJob(job, { record: { text: s.text, file, lang: s.lang } }, li);
    };
    sentRec.start(); btn.classList.add("on"); btn.textContent = "■ Dừng";
    $(".qa-status", li).textContent = "Đang ghi — đọc câu trên rồi bấm Dừng";
  } catch (err) { toast("Không truy cập được micro: " + err.message); }
}

function renderJob(job) {
  const el = $("#tpl-job").content.firstElementChild.cloneNode(true);
  el.dataset.id = job.id;
  $(".job-cancel", el).onclick = async () => { await api(`/api/jobs/${job.id}/cancel`, { method: "POST" }); };
  $(".job-del", el).onclick = async () => { if (!confirm("Xoá job và file kết quả?")) return; await api(`/api/jobs/${job.id}?files=true`, { method: "DELETE" }); el.remove(); };
  jobEls.set(job.id, el); updateJob(job); return el;
}
function updateJob(job) {
  const el = jobEls.get(job.id); if (!el) return;
  const b = $(".badge", el); b.className = "badge " + job.status; b.textContent = job.status;
  $(".job-type", el).textContent = TYPE_LABEL[job.type] || job.type;
  const p = job.params || {};
  $(".job-title", el).textContent = (job.result?.title || p.text || p.prompt || p.brief || p.script || p.media || "").slice(0, 90);
  $(".job-time", el).textContent = new Date(job.created_at * 1000).toLocaleString();
  $(".progress div", el).style.width = (job.progress * 100).toFixed(1) + "%";
  $(".job-msg", el).textContent = job.status === "running" ? job.message : "";
  $(".job-cancel", el).style.display = ["queued", "running"].includes(job.status) ? "" : "none";
  $(".job-del", el).style.display = ["queued", "running"].includes(job.status) ? "none" : "";
  if (el.dataset.rendered !== job.status) { el.dataset.rendered = job.status; renderOutputs(job, $(".job-out", el)); }
}
const followers = new Set();
function followJob(id) {
  if (followers.has(id)) return; followers.add(id);
  const es = new EventSource(`/api/jobs/${id}/events`);
  es.addEventListener("progress", (e) => updateJob(JSON.parse(e.data)));
  es.addEventListener("end", () => { es.close(); followers.delete(id); loadHealth(); });
  es.onerror = () => { es.close(); followers.delete(id); };
}
async function loadJobs() {
  const status = $("#jobs-filter").value;
  const jobs = await api("/api/jobs?limit=60" + (status ? "&status=" + status : ""));
  const list = $("#jobs-list"); list.innerHTML = "";
  jobs.filter((j) => j.type !== "enrol").forEach((j) => { list.append(renderJob(j)); if (["queued", "running"].includes(j.status)) followJob(j.id); });
  if (!list.children.length) list.innerHTML = `<p class="muted">Chưa có job nào.</p>`;
}
$("#jobs-filter").onchange = loadJobs; $("#jobs-refresh").onclick = loadJobs;

// ------------------------------------------------------------------ voices
let voices = [], selVoice = null, prompts = { vi: [], en: [] };
async function loadVoices() {
  voices = await api("/api/voices");
  prompts = await api("/api/voices/prompts");
  const sel = $("#voice-select"); const cur = sel.value;
  sel.innerHTML = `<option value="">Mặc định</option>` + voices.map((v) => `<option value="${v.id}">${esc(v.name)}${v.default ? " ★" : ""}</option>`).join("");
  sel.value = cur;
  $("#voices-list").innerHTML = voices.map((v) => `<div class="voice-item ${v.id === selVoice ? "sel" : ""}" data-id="${v.id}">
      <span>${v.default ? "★ " : ""}<b>${esc(v.name)}</b> <small class="muted">${v.id}</small></span>
      <small class="muted">${v.samples.length} mẫu · ${Object.keys(v.refs || {}).join("/")}</small></div>`).join("") || `<p class="muted">Chưa có giọng nào.</p>`;
  $$(".voice-item").forEach((el) => el.onclick = () => { selVoice = el.dataset.id; loadVoices(); });
  if (selVoice) renderVoice(voices.find((v) => v.id === selVoice));
}
$("#voice-create").onsubmit = async (e) => {
  e.preventDefault();
  try { const v = await api("/api/voices", { method: "POST", body: JSON.stringify({ name: e.target.name.value }) }); selVoice = v.id; e.target.reset(); loadVoices(); }
  catch (err) { toast(err.message); }
};
$("#demo-voice").onclick = async () => {
  const r = await api("/api/voices", { method: "POST", body: JSON.stringify({ demo: true }) });
  toast("Đang tạo giọng demo…"); const es = new EventSource(`/api/jobs/${r.job.id}/events`);
  es.addEventListener("end", () => { es.close(); selVoice = "demo"; loadVoices(); });
};

let recIdx = 0, recLang = "vi", mediaRec = null, chunks = [];
function renderVoice(v) {
  if (!v) return;
  const fx = v.fx || {};
  const ps = prompts[recLang] || [];
  $("#voice-detail").innerHTML = `
    <div class="job-head"><h3 style="margin:0">${esc(v.name)}</h3><span class="spacer"></span>
      ${v.default ? `<span class="badge done">mặc định</span>` : `<button class="ghost small" id="v-default">Đặt mặc định</button>`}
      <button class="ghost small" id="v-delete">Xoá giọng</button></div>
    ${["vi", "en"].filter((l) => !(v.samples || []).some((x) => x.lang === l)).map((l) =>
      `<div class="warn-box">⚠ Chưa có mẫu ${l === "vi" ? "tiếng Việt" : "tiếng Anh"} — giọng ${l === "vi" ? "tiếng Việt" : "tiếng Anh"} đang mượn mẫu của ngôn ngữ kia nên dễ đọc sai (ví dụ nuốt âm cuối). Hãy thu ít nhất 3 câu.</div>`).join("")}
    <h4>1. Thu âm mẫu</h4>
    <p class="muted">Đọc tự nhiên, phòng yên tĩnh, micro cách 15–20 cm. Nên có ≥3 câu tiếng Việt và ≥3 câu tiếng Anh. Mỗi mẫu 5–12 giây.</p>
    <div class="toolbar">
      <select id="rec-lang"><option value="vi">Tiếng Việt</option><option value="en">English</option></select>
      <button class="ghost small" id="rec-prev">‹</button><span class="muted">Câu ${recIdx + 1}/${ps.length}</span><button class="ghost small" id="rec-next">›</button>
    </div>
    <div class="prompt-box" id="rec-prompt">${esc(ps[recIdx] || "")}</div>
    <div class="toolbar"><button class="rec" id="rec-btn">● Ghi âm</button>
      <label class="ghost" style="margin:0;flex-direction:row;align-items:center;gap:6px;cursor:pointer">⬆ Tải file<input type="file" id="rec-file" accept="audio/*,video/*" hidden></label>
      <span id="rec-status" class="muted"></span></div>
    <div id="samples">${(v.samples || []).map((s) => `
      <div class="sample"><span class="badge ${v.refs?.[s.lang] === s.id ? "done" : ""}">${s.lang}${v.refs?.[s.lang] === s.id ? " ★" : ""}</span>
        <div><audio controls preload="none" src="/api/voices/${v.id}/samples/${s.id}/audio"></audio>
          <small class="muted">${s.duration}s · SNR ${s.snr_db} dB · “${esc(s.text)}”</small></div>
        <button class="ghost small" data-del="${s.id}">✕</button></div>`).join("") || `<p class="muted">Chưa có mẫu.</p>`}</div>
    <h4>2. Chất giọng mặc định</h4>
    <div class="grid4">
      <label>Độ trầm <output>${fx.pitch ?? 0}</output><input type="range" id="vf-pitch" min="-6" max="6" step="0.5" value="${fx.pitch ?? 0}"></label>
      <label>Tốc độ <output>${fx.pace ?? 1}</output><input type="range" id="vf-pace" min="0.7" max="1.4" step="0.05" value="${fx.pace ?? 1}"></label>
      <label>Độ ấm <output>${fx.warmth ?? 0}</output><input type="range" id="vf-warmth" min="0" max="1" step="0.1" value="${fx.warmth ?? 0}"></label>
      <label>Độ rõ <output>${fx.clarity ?? 0.5}</output><input type="range" id="vf-clarity" min="0" max="1" step="0.1" value="${fx.clarity ?? 0.5}"></label>
      <label>Engine tiếng Việt<select id="ve-vi"><option>vieneu</option><option>voxcpm</option><option>best</option></select></label>
      <label>Engine English<select id="ve-en"><option>voxcpm</option><option>vieneu</option><option>best</option></select></label>
    </div>
    <div class="toolbar"><button class="primary" id="v-save">Lưu</button>
      <input id="v-test-text" value="Xin chào, đây là giọng nói của tôi. Hello, this is my cloned voice." style="flex:1;min-width:200px">
      <button class="ghost" id="v-test">▶ Nghe thử</button></div>
    <div id="v-test-out"></div>`;
  $("#rec-lang").value = recLang;
  $("#ve-vi").value = v.engines?.vi || "vieneu"; $("#ve-en").value = v.engines?.en || "voxcpm";
  $$("#voice-detail input[type=range]").forEach((r) => r.oninput = () => r.previousElementSibling.textContent = r.value);
  $("#rec-lang").onchange = (e) => { recLang = e.target.value; recIdx = 0; renderVoice(v); };
  $("#rec-prev").onclick = () => { recIdx = Math.max(0, recIdx - 1); renderVoice(v); };
  $("#rec-next").onclick = () => { recIdx = Math.min(ps.length - 1, recIdx + 1); renderVoice(v); };
  $("#rec-btn").onclick = () => toggleRecord(v);
  $("#rec-file").onchange = (e) => e.target.files[0] && sendSample(v, e.target.files[0], null);
  $$("[data-del]").forEach((b) => b.onclick = async () => { if (confirm("Xoá mẫu này?")) { await api(`/api/voices/${v.id}/samples/${b.dataset.del}`, { method: "DELETE" }); loadVoices(); } });
  $("#v-default") && ($("#v-default").onclick = async () => { await api(`/api/voices/${v.id}/default`, { method: "POST" }); loadVoices(); });
  $("#v-delete").onclick = async () => { if (confirm(`Xoá giọng ${v.name}? (Bản ghi được chuyển vào thùng rác data/trash và có bản sao lưu trong data/backups.)`)) { await api(`/api/voices/${v.id}`, { method: "DELETE" }); selVoice = null; $("#voice-detail").innerHTML = ""; loadVoices(); } };
  const readFx = () => ({ pitch: +$("#vf-pitch").value, pace: +$("#vf-pace").value, warmth: +$("#vf-warmth").value, clarity: +$("#vf-clarity").value });
  $("#v-save").onclick = async () => { await api(`/api/voices/${v.id}`, { method: "PATCH", body: JSON.stringify({ fx: readFx(), engines: { vi: $("#ve-vi").value, en: $("#ve-en").value } }) }); toast("Đã lưu"); loadVoices(); };
  $("#v-test").onclick = async () => {
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ type: "tts", source: "dashboard", params: { text: $("#v-test-text").value, voice: v.id, lang: "auto", fx: readFx() } }) });
    const out = $("#v-test-out"); out.innerHTML = ""; out.append(renderJob(job)); followJob(job.id);
  };
}
async function toggleRecord(v) {
  const btn = $("#rec-btn");
  if (mediaRec && mediaRec.state === "recording") { mediaRec.stop(); return; }
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 } });
    chunks = []; mediaRec = new MediaRecorder(stream);
    mediaRec.ondataavailable = (e) => e.data.size && chunks.push(e.data);
    mediaRec.onstop = async () => {
      stream.getTracks().forEach((t) => t.stop()); btn.classList.remove("on"); btn.textContent = "● Ghi âm";
      const blob = new Blob(chunks, { type: mediaRec.mimeType || "audio/webm" });
      const ext = (mediaRec.mimeType || "").includes("mp4") ? "m4a" : "webm";
      await sendSample(v, new File([blob], `rec.${ext}`, { type: blob.type }), (prompts[recLang] || [])[recIdx]);
    };
    mediaRec.start(); btn.classList.add("on"); btn.textContent = "■ Dừng";
    $("#rec-status").textContent = "Đang ghi… đọc câu phía trên rồi bấm Dừng";
  } catch (err) { toast("Không truy cập được micro: " + err.message); }
}
async function sendSample(v, file, text) {
  $("#rec-status").textContent = "Đang xử lý mẫu (khử nhiễu, nhận dạng)…";
  const fd = new FormData(); fd.append("file", file); fd.append("lang", recLang); if (text) fd.append("text", text);
  try {
    const job = await api(`/api/voices/${v.id}/samples`, { method: "POST", body: fd });
    if (job.status !== "done") throw new Error(job.message || job.status);
    const s = job.result.sample;
    toast(`Đã lưu mẫu ${s.duration}s — nghe được: “${s.asr_text.slice(0, 60)}”`);
    recIdx = Math.min(recIdx + 1, (prompts[recLang] || []).length - 1);
    const openHashTab = () => { const t = location.hash.slice(1); const b = t && $(`#tabs button[data-tab="${t}"]`); if (b && !b.classList.contains("active")) b.click(); };
window.addEventListener("hashchange", openHashTab);
loadVoices().then(openHashTab);
  } catch (err) { $("#rec-status").textContent = ""; toast(err.message); }
}

// ------------------------------------------------------------------ lexicon
let lex = null;
async function loadLexicon() {
  if (!voices.length) voices = await api("/api/voices");
  const scope = $("#lex-scope"); const cur = scope.value;
  scope.innerHTML = `<option value="">Chung (mọi giọng)</option>` + voices.map((v) => `<option value="${v.id}">Giọng: ${esc(v.name)}</option>`).join("");
  scope.value = cur || "";
  lex = await api("/api/lexicon" + (scope.value ? "?voice=" + scope.value : ""));
  renderLex();
}
function renderLex() {
  const lang = $("#lex-lang").value; const rows = Object.entries(lex[lang] || {});
  $("#lex-body").innerHTML = rows.map(([k, v]) => `<tr><td><input value="${esc(k)}" data-k></td><td><input value="${esc(v.say)}" data-s></td>
    <td><span class="badge ${v.source === "learned" ? "running" : ""}">${v.source}</span></td><td><button class="ghost small" data-rm>✕</button></td></tr>`).join("");
  $$("[data-rm]").forEach((b) => b.onclick = () => b.closest("tr").remove());
}
function collectLex() {
  const lang = $("#lex-lang").value; const out = {};
  $$("#lex-body tr").forEach((tr) => {
    const k = $("[data-k]", tr).value.trim(), s = $("[data-s]", tr).value.trim(), src = $(".badge", tr).textContent;
    if (k && s) out[k] = { say: s, source: src === "learned" && lex[lang][k]?.say === s ? "learned" : "manual", hits: lex[lang][k]?.hits || 0 };
  });
  lex[lang] = out;
}
$("#lex-scope").onchange = loadLexicon;
$("#lex-lang").onchange = () => renderLex();
$("#lex-add").onclick = () => { $("#lex-body").insertAdjacentHTML("afterbegin", `<tr><td><input data-k placeholder="VD: Kubernetes"></td><td><input data-s placeholder="cu-bơ-nét-tít"></td><td><span class="badge">manual</span></td><td><button class="ghost small" data-rm>✕</button></td></tr>`); $$("[data-rm]").forEach((b) => b.onclick = () => b.closest("tr").remove()); };
$("#lex-save").onclick = async () => {
  collectLex(); const v = $("#lex-scope").value;
  lex = await api("/api/lexicon" + (v ? "?voice=" + v : ""), { method: "PUT", body: JSON.stringify(lex) });
  renderLex(); toast("Đã lưu từ điển");
};

const openHashTab = () => { const t = location.hash.slice(1); const b = t && $(`#tabs button[data-tab="${t}"]`); if (b && !b.classList.contains("active")) b.click(); };
window.addEventListener("hashchange", openHashTab);
loadVoices().then(openHashTab);
