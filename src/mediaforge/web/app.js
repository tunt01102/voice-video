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
const TYPE_LABEL = { tts: "Giọng nói", image: "Ảnh", subtitle: "Phụ đề", short: "Short", narrated: "Video", explainer: "Explainer", screen: "Screen", video: "Video", enrol: "Thu mẫu", rerender: "Đọc lại câu", export: "Xuất file", prepare: "Chuẩn bị kịch bản", resubtitle: "Dựng lại phụ đề", repronounce: "Sửa phát âm", sing: "Hát", recite: "Ngâm thơ" };
const HIDDEN_TYPES = ["enrol", "rerender", "export", "prepare", "resubtitle", "repronounce"];

// ------------------------------------------------------------------ tabs
$$("#tabs button").forEach((b) => b.onclick = () => {
  $$("#tabs button").forEach((x) => x.classList.toggle("active", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  ({ jobs: loadJobs, voices: loadVoices, lexicon: loadLexicon, system: loadHealth, recite: syncSongForms, sing: syncSongForms })[b.dataset.tab]?.();
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
  const sb = $("#submit-btn");
  sb.textContent = isVideo ? "Dựng ngay (bỏ qua kiểm tra)" : "Tạo";
  sb.className = isVideo ? "ghost" : "primary";
  if (!isVideo) $("#prescan").hidden = true;
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

async function buildParams(f) {
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
    return { type, params };
}
async function submitJob(type, params) {
  const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ type, params, source: "dashboard" }) });
  const box = $("#create-result"); box.innerHTML = "";
  box.append(renderJob(job)); followJob(job.id);
  return job;
}
$("#create-form").onsubmit = async (e) => {
  e.preventDefault();
  const btn = $("#submit-btn");
  btn.disabled = true;
  try { const { type, params } = await buildParams(e.target); await submitJob(type, params); $("#prescan").hidden = true; }
  catch (err) { toast(err.message); }
  finally { btn.disabled = false; }
};

// ------------------------------------------------------------------ pronunciation pre-scan (before rendering)
// Step 1 writes the script and lists terms the TTS may misread; the owner writes how each is read; step 2 saves
// the answers to the lexicon and renders exactly that script (plan_file). docs/SDD-subtitle-fit-pronunciation.md §5.
$("#prepare-btn").onclick = async () => {
  const f = $("#create-form"), btn = $("#prepare-btn"), box = $("#prescan");
  btn.disabled = true;
  try {
    const { type, params } = await buildParams(f);
    const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ type: "prepare", params: { ...params, kind: type }, source: "dashboard" }) });
    box.hidden = false;
    box.innerHTML = `<p class="muted">⏳ Đang viết kịch bản và quét các từ AI có thể đọc sai… <span class="ps-prog"></span></p>`;
    followJob(job.id, {
      onProgress: (j) => { const p = $(".ps-prog", box); if (p) p.textContent = j.status === "queued" ? "(đang chờ job khác xong)" : `${(j.progress * 100).toFixed(0)}% ${j.message || ""}`; },
      onEnd: (j) => {
        btn.disabled = false;
        if (j?.status !== "done") { box.innerHTML = `<p class="qa-bad">Không chuẩn bị được kịch bản: ${esc(j?.error || j?.message || "")}</p>`; return; }
        renderPrescan(j, type, params);
      },
    });
  } catch (err) { btn.disabled = false; toast(err.message); }
};

function markTerms(text, terms) {
  if (!terms.length) return esc(text);
  const alt = [...terms].sort((a, b) => b.length - a.length).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(?:${alt})(?![\\p{L}\\p{N}+])`, "gu");
  let out = "", last = 0;
  for (const m of text.matchAll(re)) { out += esc(text.slice(last, m.index)) + `<mark>${esc(m[0])}</mark>`; last = m.index + m[0].length; }
  return out + esc(text.slice(last));
}
function renderPrescan(job, type, params) {
  const r = job.result, box = $("#prescan"), cands = r.candidates || [];
  const terms = cands.map((c) => c.term);
  const fresh = cands.filter((c) => !c.known), known = cands.filter((c) => c.known);
  const row = (c) => `<tr data-term="${esc(c.term)}">
      <td><b>${esc(c.term)}</b>${c.known ? ` <span class="badge">đã lưu</span>` : ""}<br><small class="muted">${esc(c.reason_labels.join(", "))} · ${c.count}×</small></td>
      <td class="ps-ctx">${markTerms(c.context, [c.term])}</td>
      <td><input class="ps-say" value="${esc(c.say)}" placeholder="cách đọc, VD: Pi En Di"></td>
      <td class="ps-okc"><label class="check"><input type="checkbox" class="ps-ok"> đúng</label></td>
      <td><button class="ghost small ps-listen" title="Nghe thử câu này với cách đọc đã ghi">▶</button></td></tr>`;
  const voiceName = params.voice ? ($("#voice-select").selectedOptions[0]?.textContent || params.voice) : "";
  box.innerHTML = `<h3>Kiểm tra phát âm trước khi dựng</h3>
    <p class="muted">“${esc(r.title || "")}” · ${r.scenes.length} cảnh. Ghi cách đọc cho từ có thể bị đọc sai (VD: <b>PNJ</b> → <i>Pi En Di</i>),
      hoặc tick <b>đúng</b> nếu AI đọc đúng như viết. Bỏ trống = để AI tự đọc. Câu trả lời được lưu vào <b>Từ điển phát âm</b> để dùng cho các lần sau.</p>
    <details><summary>Xem kịch bản (${r.scenes.length} cảnh)</summary><ol class="ps-scenes">${r.scenes.map((s) => `<li>${markTerms(s, terms)}</li>`).join("")}</ol></details>
    ${fresh.length ? `<table class="lex prescan"><thead><tr><th>Từ</th><th>Ngữ cảnh</th><th>Đọc là</th><th></th><th></th></tr></thead><tbody>${fresh.map(row).join("")}</tbody></table>`
      : `<p>✅ Không thấy từ mới nào dễ đọc sai.</p>`}
    ${known.length ? `<details><summary>${known.length} từ đã có trong từ điển</summary><table class="lex prescan"><tbody>${known.map(row).join("")}</tbody></table></details>` : ""}
    <div class="actions">
      <label class="inline-label">Lưu vào <select class="ps-scope"><option value="">Từ điển chung</option>${params.voice ? `<option value="${esc(params.voice)}">Chỉ giọng ${esc(voiceName)}</option>` : ""}</select></label>
      <button class="primary ps-go">2 · Lưu cách đọc &amp; dựng video</button><span class="muted ps-status"></span>
    </div>`;
  const byTerm = Object.fromEntries(cands.map((c) => [c.term, c]));
  $$("tr[data-term]", box).forEach((tr) => {
    const c = byTerm[tr.dataset.term], say = $(".ps-say", tr), ok = $(".ps-ok", tr);
    ok.onchange = () => { say.disabled = ok.checked; };
    $(".ps-listen", tr).onclick = async (ev) => {
      const b = ev.currentTarget, reading = ok.checked ? c.term : say.value.trim();
      const sentence = c.context.replace(/^…|…$/g, "");
      const text = reading ? sentence.split(c.term).join(reading) : sentence;
      b.disabled = true; b.textContent = "⏳";
      try {
        const j = await api("/api/jobs?wait=true&timeout=300", { method: "POST", body: JSON.stringify({ type: "tts", source: "dashboard", params: { text, voice: params.voice, lang: r.lang, fx: params.fx } }) });
        const url = fileUrl(j.files?.mp3) || fileUrl(j.files?.audio);
        if (url) new Audio(url).play(); else toast("Chưa nghe thử được: " + (j.message || j.status));
      } catch (err) { toast(err.message); }
      finally { b.disabled = false; b.textContent = "▶"; }
    };
  });
  $(".ps-go", box).onclick = async (ev) => {
    const b = ev.currentTarget, st = $(".ps-status", box);
    const readings = {}, verified = [];
    $$("tr[data-term]", box).forEach((tr) => {
      const c = byTerm[tr.dataset.term], s = $(".ps-say", tr).value.trim();
      if ($(".ps-ok", tr).checked) verified.push(c.term);
      else if (s && (!c.known || s !== c.say)) readings[c.term] = s;
    });
    b.disabled = true; st.textContent = "Đang lưu…";
    try {
      const scope = $(".ps-scope", box).value;
      if (Object.keys(readings).length || verified.length)
        await api("/api/lexicon/terms" + (scope ? "?voice=" + encodeURIComponent(scope) : ""), { method: "POST", body: JSON.stringify({ lang: r.lang, terms: readings, verified }) });
      await submitJob(type, { ...params, lang: r.lang, plan_file: r.plan_file });
      st.textContent = `Đã lưu ${Object.keys(readings).length} cách đọc, ${verified.length} từ đúng — đang dựng video bên dưới.`;
    } catch (err) { b.disabled = false; st.textContent = ""; toast(err.message); }
  };
}

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
  const bust = job.result?.resubtitled_at ? `?v=${Math.round(job.result.resubtitled_at)}` : "";
  if (fileUrl(f.video)) out.insertAdjacentHTML("beforeend", `<video controls preload="metadata" src="${fileUrl(f.video)}${bust}" poster="${fileUrl(f.thumbnail) || ""}${bust}"></video>`);
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
  if (fileUrl(f.video) && Array.isArray(r.words) && r.plan) out.append(renderResubtitle(job));
  const isSong = ["sing", "recite"].includes(job.type);
  if (!isSong && (fileUrl(f.video) || (fileUrl(f.audio) && Array.isArray(r.sentences)))) out.append(renderExport(job));
  if (Array.isArray(r.sentences) && r.sentences.length) out.append(renderRepronounce(job));
  if (Array.isArray(r.sentences) && r.sentences.length) out.append(renderQA(job));
  if (isSong) out.append(renderSong(job));
  if (r.text && job.type === "subtitle") out.insertAdjacentHTML("beforeend", `<pre>${esc(r.text)}</pre>`);
}
function renderResubtitle(job) {
  const box = document.createElement("div");
  box.className = "resub-row";
  const at = job.result?.resubtitled_at;
  box.innerHTML = `<button class="ghost small">🔤 Dựng lại phụ đề</button>
    <small class="muted">${at ? "đã dựng lại " + new Date(at * 1000).toLocaleString() + " · " : ""}phụ đề luôn vừa khung hình, chữ đang đọc sáng lên (karaoke) — giữ nguyên giọng và hình</small>`;
  $("button", box).onclick = async (ev) => {
    const b = ev.currentTarget;
    if (!confirm("Dựng lại phụ đề cho video này? Bản cũ và các file đã xuất sẽ chuyển vào thùng rác.")) return;
    b.disabled = true; b.textContent = "⏳ Đang dựng lại phụ đề…";
    try {
      const r = await api(`/api/jobs/${job.id}/resubtitle`, { method: "POST", body: "{}" });
      followJob(r.job.id, {
        onProgress: (j) => { b.textContent = `⏳ ${(j.progress * 100).toFixed(0)}%`; },
        onEnd: async (j) => {
          if (j?.status !== "done") { b.disabled = false; b.textContent = "🔤 Dựng lại phụ đề"; toast("Lỗi: " + (j?.error || j?.message || "")); return; }
          toast("Đã dựng lại phụ đề"); await refreshJob(job.id);
        },
      });
    } catch (err) { b.disabled = false; b.textContent = "🔤 Dựng lại phụ đề"; toast(err.message); }
  };
  return box;
}
// ------------------------------------------------------------------ fix pronunciation of a finished job
function renderRepronounce(job) {
  const box = document.createElement("div");
  box.className = "repron";
  const isVideo = !!fileUrl(job.files?.video);
  const hist = job.result?.repronounced || [], fhist = job.files?.repronounced || [];
  const last = hist[hist.length - 1], lastUrl = fileUrl(fhist[fhist.length - 1]?.file);
  box.innerHTML = `<div class="repron-head"><b>🗣 Sửa phát âm</b> <small class="muted">nhập từ bị đọc sai và cách đọc mới — AI đọc lại mọi câu có từ đó rồi ghép vào ${isVideo ? "video" : "audio"}, không dựng lại hình</small></div>
    <div class="repron-rows"></div>
    <div class="repron-actions"><button class="ghost small repron-add">+ thêm từ</button>
      <button class="primary small repron-go">🗣 Đọc lại &amp; xuất ${isVideo ? "video" : "audio"}</button>
      <span class="muted repron-status"></span></div>
    ${last ? `<div class="repron-last"><div class="muted">Bản sửa gần nhất (${new Date(last.at * 1000).toLocaleString()}): ${Object.entries(last.readings).map(([t, s]) => `<b>${esc(t)}</b> → “${esc(s)}”`).join(", ")} · ${last.sentences.length} câu${last.path === "recompose" ? " · đã dựng lại timeline" : ""}</div>
      ${lastUrl ? (isVideo ? `<video controls preload="metadata" src="${lastUrl}"></video>` : `<audio controls src="${lastUrl}"></audio>`) + `<div class="links"><a href="${lastUrl}" download>⬇ Tải bản đã sửa</a></div>` : ""}
      <ul class="repron-sents">${last.sentences.map((s) => `<li class="${s.passed ? "ok" : "warn"}">câu ${s.index + 1}: ${esc(s.text)} <span class="muted">${s.passed ? "✓" : `· kiểm tra chưa chắc, nghe thành “${esc(s.heard)}”`}</span></li>`).join("")}</ul></div>` : ""}`;
  const rows = $(".repron-rows", box);
  let timer = null;
  const count = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const items = $$(".repron-row", rows).map((row) => [row, $(".repron-term", row).value.trim()]).filter(([, t]) => t);
      if (!items.length) return;
      const hits = await api(`/api/jobs/${job.id}/occurrences?terms=${encodeURIComponent(items.map(([, t]) => t.replace(/,/g, " ")).join(","))}`).catch(() => ({}));
      items.forEach(([row, t]) => {
        const n = (hits[t] || []).length;
        const c = $(".repron-count", row);
        c.textContent = n ? `${n} câu` : "không có trong video";
        c.className = "repron-count " + (n ? "muted" : "qa-bad");
      });
    }, 300);
  };
  const addRow = (term = "", say = "") => {
    const row = document.createElement("div");
    row.className = "repron-row";
    row.innerHTML = `<input class="repron-term" placeholder="Từ bị đọc sai, vd: PNJ" value="${esc(term)}"> →
      <input class="repron-say" placeholder="Cách đọc mới, vd: Pi En Di" value="${esc(say)}">
      <span class="repron-count muted"></span><button class="ghost small repron-del" title="Bỏ">✕</button>`;
    $(".repron-term", row).oninput = count;
    $(".repron-del", row).onclick = () => { row.remove(); if (!$$(".repron-row", rows).length) addRow(); };
    rows.append(row);
    if (term) count();
  };
  addRow();
  $(".repron-add", box).onclick = () => addRow();
  $(".repron-go", box).onclick = async (ev) => {
    const btn = ev.currentTarget, st = $(".repron-status", box);
    const readings = {};
    $$(".repron-row", rows).forEach((row) => {
      const t = $(".repron-term", row).value.trim(), s = $(".repron-say", row).value.trim();
      if (t && s) readings[t] = s;
    });
    if (!Object.keys(readings).length) { toast("Nhập từ bị đọc sai và cách đọc mới"); return; }
    btn.disabled = true; st.textContent = "Đang gửi…";
    try {
      const r = await api(`/api/jobs/${job.id}/repronounce`, { method: "POST", body: JSON.stringify({ readings }) });
      const n = new Set(Object.values(r.occurrences).flat()).size;
      st.textContent = `AI sẽ đọc lại ${n} câu…`;
      followJob(r.job.id, {
        onProgress: (j) => { st.textContent = j.status === "queued" ? "Đang chờ GPU…" : `${j.message} · ${(j.progress * 100).toFixed(0)}%`; },
        onEnd: async (j) => {
          btn.disabled = false; st.textContent = "";
          if (j?.status !== "done") { toast("Không sửa được: " + (j?.message || "")); return; }
          // The new takes become these sentences' export choices.
          const sel = selectionFor(job);
          Object.entries(j.result?.selection || {}).forEach(([i, t]) => { sel[i] = t; });
          toast(`Đã đọc lại ${j.result?.sentences?.length || 0} câu và xuất bản mới`);
          await refreshJob(job.id);
        },
      });
    } catch (err) { btn.disabled = false; st.textContent = ""; toast(err.message); }
  };
  return box;
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
// Which voice each sentence uses when exporting: {jobId: {index: takeId | "original"}}.
const selections = new Map();
function defaultSelection(job) {
  const sel = {};
  (job.result?.sentences || []).forEach((s, i) => {
    // A take made for a reading the owner typed (🗣 Sửa phát âm) is kept even if QA was unsure about it.
    const ok = (s.takes || []).filter((t) => t.passed || t.source === "repronounce");
    if (ok.length) sel[i] = ok[ok.length - 1].id;
  });
  return sel;
}
function selectionFor(job) {
  if (!selections.has(job.id)) selections.set(job.id, defaultSelection(job));
  return selections.get(job.id);
}
let exportFormats = null;
async function loadExportFormats() {
  if (!exportFormats) exportFormats = await api("/api/export-formats").catch(() => ({ video: [{ id: "mp4", label: "MP4" }], audio: [{ id: "mp3", label: "MP3" }] }));
  return exportFormats;
}
function renderExport(job) {
  const box = document.createElement("div");
  box.className = "export-bar";
  const isVideo = !!fileUrl(job.files?.video);
  box.innerHTML = `<div class="export-row">
      <span class="export-sum muted"></span>
      <label class="inline-label">Định dạng <select class="export-fmt"></select></label>
      <button class="primary small export-go">⬇ ${isVideo ? "Xuất video" : "Xuất audio"}</button>
      <span class="muted export-status"></span></div>
    <div class="export-list links"></div>`;
  loadExportFormats().then((fm) => {
    const list = isVideo ? fm.video : fm.audio;
    $(".export-fmt", box).innerHTML = list.map((f, i) => `<option value="${f.id}" ${i === 0 ? "selected" : ""}>${esc(f.label)}</option>`).join("");
  });
  const sum = () => {
    const n = Object.values(selectionFor(job)).filter((v) => v && v !== "original").length;
    $(".export-sum", box).textContent = n ? `Giọng: ${n} câu dùng bản AI đã sửa, còn lại giữ bản gốc` : "Giọng: bản gốc";
  };
  sum(); box.refreshSummary = sum;
  const exports = job.files?.exports || [];
  $(".export-list", box).innerHTML = exports.slice().reverse().map((e) =>
    `<a href="${fileUrl(e.file)}" download>⬇ ${esc(e.label || e.format)}</a>`).join("");
  $(".export-go", box).onclick = async (ev) => {
    const btn = ev.currentTarget, st = $(".export-status", box);
    btn.disabled = true; st.textContent = "Đang xuất…";
    try {
      const r = await api(`/api/jobs/${job.id}/export`, { method: "POST", body: JSON.stringify({ selection: selectionFor(job), format: $(".export-fmt", box).value }) });
      followJob(r.job.id, {
        onProgress: (j) => { st.textContent = j.status === "running" ? `Đang xuất… ${(j.progress * 100).toFixed(0)}%` : ""; },
        onEnd: async (j) => {
          btn.disabled = false;
          if (j?.status !== "done") { st.textContent = ""; toast("Xuất thất bại: " + (j?.message || "")); return; }
          toast("Đã xuất xong"); await refreshJob(job.id);
          const url = fileUrl(j.files?.file); if (url) { const a = document.createElement("a"); a.href = url; a.download = ""; document.body.append(a); a.click(); a.remove(); }
        },
      });
    } catch (err) { btn.disabled = false; st.textContent = ""; toast(err.message); }
  };
  return box;
}
function renderTakes(job, s, i, li) {
  const fs = job.files?.sentences?.[i] || {};
  const takes = s.takes || [];
  if (!takes.length) return;
  const sel = selectionFor(job);
  const name = `sel-${job.id}-${i}`;
  const cur = sel[i] || "original";
  const opt = (id, label, url, meta) => `<label class="take ${cur === id ? "on" : ""}">
      <input type="radio" name="${name}" value="${id}" ${cur === id ? "checked" : ""}>
      <span class="take-label">${label}</span>${meta ? `<span class="muted take-meta">${meta}</span>` : ""}
      ${url ? `<audio controls preload="none" src="${url}"></audio>` : ""}</label>`;
  const rows = [opt("original", "Bản gốc (AI)", fileUrl(fs.original_audio), `lỗi ${(s.score * 100).toFixed(0)}%`)];
  takes.forEach((t, k) => {
    const ft = fs.takes?.[k] || {};
    const what = t.source === "guided" ? "AI đọc lại theo bản ghi của bạn"
      : t.source === "repronounce" ? `Đọc lại: ${Object.entries(t.readings || {}).map(([a, b]) => `${esc(a)} → ${esc(b)}`).join(", ")}` : "AI tạo lại";
    rows.push(opt(t.id, `${what} #${k + 1}`, fileUrl(ft.audio), `${esc(t.engine)} · lỗi ${(t.score * 100).toFixed(0)}% ${t.passed ? "✓" : `· nghe thành: “${esc(t.heard)}”`}`));
  });
  const recs = takes.map((t, k) => fileUrl(fs.takes?.[k]?.recording)).filter(Boolean);
  const box = document.createElement("div");
  box.className = "takes";
  box.innerHTML = `<div class="muted takes-head">Chọn giọng cho câu này khi xuất:</div>${rows.join("")}` +
    (recs.length ? `<div class="take rec-ref"><span class="take-label">🎙 Bản ghi của bạn</span><span class="muted take-meta">chỉ để nghe đối chiếu — không chèn vào video</span><audio controls preload="none" src="${recs[recs.length - 1]}"></audio></div>` : "");
  $$("input[type=radio]", box).forEach((rb) => rb.onchange = () => {
    sel[i] = rb.value;
    $$(".take", box).forEach((x) => x.classList.toggle("on", $("input", x)?.checked));
    jobEls.get(job.id) && $(".export-bar", jobEls.get(job.id))?.refreshSummary?.();
  });
  li.append(box);
}
function renderQA(job) {
  const r = job.result, sents = r.sentences;
  const failed = sents.filter((s) => !s.passed && !(s.takes || []).some((t) => t.passed));
  const nFixed = sents.filter((s) => !s.passed && (s.takes || []).some((t) => t.passed)).length;
  const box = document.createElement("div");
  box.className = "qa";
  box.innerHTML = `<div class="qa-head">${failed.length
      ? `<span class="qa-bad">⚠ ${failed.length}/${sents.length} câu chưa đạt kiểm tra phát âm — bôi đỏ bên dưới. Bấm 🎙 để đọc mẫu câu đó: AI dùng bản ghi của bạn làm hướng dẫn để đọc lại bằng giọng nhân bản (bản ghi không bị chèn vào video, và được lưu để giọng lần sau tốt hơn). 🔁 để AI tạo lại. Bản mới hiện ngay dưới câu để bạn chọn khi xuất.</span>`
      : `<span class="qa-ok">✓ ${sents.length}/${sents.length} câu đạt kiểm tra phát âm${nFixed ? ` (${nFixed} câu nhờ bản AI đọc lại)` : ""}</span>`}
    <button class="ghost small qa-toggle">${failed.length || sents.some((s) => s.takes?.length) ? "Ẩn/hiện tất cả câu" : "Xem từng câu"}</button></div>
    <ol class="qa-list ${failed.length || sents.some((s) => s.takes?.length) ? "" : "collapsed"}"></ol>`;
  const list = $(".qa-list", box);
  sents.forEach((s, i) => {
    const li = document.createElement("li");
    const fixed = (s.takes || []).some((t) => t.passed);
    li.className = s.passed ? (s.source === "recording" ? "rec-ok" : "ok") : fixed ? "fixed" : "bad";
    li.innerHTML = `<div class="qa-text">${s.passed ? esc(s.text) : markMissing(s.text, s.missing, s.heard)}</div>
      <div class="qa-meta">${s.source === "recording" ? "🎙 giọng ghi âm của bạn (bản cũ)" :
        `${s.lang} · ${s.engine} · lỗi ${(s.score * 100).toFixed(0)}%${s.passed ? "" : ` · nghe thành: “${esc(s.heard)}”`}`}${fixed ? " · ✓ đã có bản AI sửa" : ""}</div>
      <div class="qa-actions">
        <button class="rec small" data-act="rec">🎙 Ghi âm câu này</button>
        <button class="ghost small" data-act="regen">🔁 Tạo lại</button>
        <span class="muted qa-status"></span></div>`;
    if (s.passed && !(s.takes || []).length) li.classList.add("only-hover");
    $("[data-act=rec]", li).onclick = (e) => recordSentence(job, s, i, e.currentTarget, li);
    $("[data-act=regen]", li).onclick = () => rerenderSentence(job, i, {}, li);
    renderTakes(job, s, i, li);
    list.append(li);
  });
  $(".qa-toggle", box).onclick = () => list.classList.toggle("collapsed");
  return box;
}
async function rerenderSentence(job, i, body, li) {
  const st = $(".qa-status", li);
  st.textContent = "AI đang đọc lại câu này…";
  try {
    const r = await api(`/api/jobs/${job.id}/sentences/${i}/rerender`, { method: "POST", body: JSON.stringify(body) });
    followJob(r.job.id, {
      onProgress: (j) => { if (j.status === "running") st.textContent = `${j.message} · ${(j.progress * 100).toFixed(0)}%`; else if (j.status === "queued") st.textContent = "Đang chờ GPU…"; },
      onEnd: async (j) => {
        if (j?.status !== "done") { st.textContent = ""; toast("Không đọc lại được: " + (j?.message || "")); return; }
        const t = j.result?.take;
        // The new take becomes this sentence's export choice when it passes QA.
        if (t?.passed) selectionFor(job)[i] = t.id;
        toast(t?.passed ? "Đã có bản AI đọc lại — hiện ngay dưới câu" : "Bản AI mới vẫn chưa đạt — thử ghi âm lại hoặc tạo lại");
        await refreshJob(job.id);
      },
    });
  } catch (err) { st.textContent = ""; toast(err.message); }
}
let sentRec = null;
async function recordSentence(job, s, i, btn, li) {
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
      try {
        const file = await upload(new File([blob], `sentence.${ext}`, { type: blob.type }));
        await rerenderSentence(job, i, { file, lang: s.lang }, li);
      } catch (err) { $(".qa-status", li).textContent = ""; toast(err.message); }
    };
    sentRec.start(); btn.classList.add("on"); btn.textContent = "■ Dừng";
    $(".qa-status", li).textContent = "Đang ghi — đọc câu trên rồi bấm Dừng";
  } catch (err) { toast("Không truy cập được micro: " + err.message); }
}
async function refreshJob(id) {
  const j = await api(`/api/jobs/${id}`);
  const el = jobEls.get(id);
  if (el) { el.dataset.rendered = ""; updateJob(j); }
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
function followJob(id, { onProgress, onEnd } = {}) {
  if (followers.has(id)) return; followers.add(id);
  const es = new EventSource(`/api/jobs/${id}/events`);
  let last = null;
  es.addEventListener("progress", (e) => { last = JSON.parse(e.data); updateJob(last); onProgress?.(last); });
  es.addEventListener("end", () => { es.close(); followers.delete(id); loadHealth(); onEnd?.(last); });
  es.onerror = () => { es.close(); followers.delete(id); if (onEnd) api(`/api/jobs/${id}`).then(onEnd, () => onEnd(null)); };
}
async function loadJobs() {
  const status = $("#jobs-filter").value;
  const jobs = await api("/api/jobs?limit=60" + (status ? "&status=" + status : ""));
  const list = $("#jobs-list"); list.innerHTML = "";
  jobs.filter((j) => !HIDDEN_TYPES.includes(j.type)).forEach((j) => { list.append(renderJob(j)); if (["queued", "running"].includes(j.status)) followJob(j.id); });
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
    if (k && s) out[k] = { ...(lex[lang][k] || {}), say: s, source: ["learned", "verified"].includes(src) && lex[lang][k]?.say === s ? src : "manual", hits: lex[lang][k]?.hits || 0 };
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

// ------------------------------------------------------------------ ngâm thơ / hát
const MOOD_VI = { "hung-trang": "Hùng tráng", "tram-mac": "Trầm mặc", "u-hoai": "U hoài", "bi-ai": "Bi ai" };
let poemList = [];
function renderSong(job) {
  const r = job.result || {}, f = job.files || {}, rep = r.report || {};
  const box = document.createElement("div"); box.className = "song-out";
  const links = [];
  const add = (label, v) => fileUrl(v) && links.push(`<a href="${fileUrl(v)}" download>${label}</a>`);
  add("Giọng (WAV)", f.vocal); add("Nhạc đệm (WAV)", f.accompaniment); add("Giai điệu (JSON)", f.melody);
  const lines = (rep.lines || []).map((l) => `<tr><td>${esc(l.text)}</td><td>${esc(l.sung_heard || "")}</td><td>${esc(l.sung_syllables || "")}</td><td>${esc(l.sung_with_tones || "")}</td><td>${esc(l.engine || "")} · lần ${l.take}/${l.takes_tried}</td></tr>`).join("");
  box.innerHTML = `
    <p><b>${esc(r.title || "")}</b>${r.author ? " — " + esc(r.author) : ""} · ${esc(MOOD_VI[r.mood] || r.mood || "")} · giọng ${esc(r.key || "")}${r.bpm ? " · " + r.bpm + " BPM" : ""} · ${r.duration || "?"} s</p>
    ${rep.spirit ? `<p class="spirit">“${esc(rep.spirit)}”</p>` : ""}
    ${links.length ? `<div class="links">${links.join("")}</div>` : ""}
    ${lines ? `<details><summary>Độ rõ lời (AI nghe lại từng câu)</summary><table class="lex"><thead><tr><th>Câu</th><th>AI nghe được</th><th>Âm tiết</th><th>Kèm dấu</th><th>Bản đọc</th></tr></thead><tbody>${lines}</tbody></table>
      <p class="muted">Whisper được huấn luyện trên lời nói, nên nghe giọng ngân kém hơn người. Con số chỉ để tham khảo.</p></details>` : ""}`;
  return box;
}
async function songForm(section) {
  const kind = section.dataset.kind, isRecite = kind === "recite";
  section.append($("#tpl-song").content.cloneNode(true));
  const form = $("form", section), out = $(".song-result", section);
  $$(isRecite ? ".only-sing" : ".only-recite", form).forEach((e) => e.remove());
  $(".song-intro", form).textContent = isRecite
    ? "Ngâm một bài thơ bằng chính giọng của bạn: giai điệu đi theo thanh điệu tiếng Việt, câu cuối ngân láy, đàn tranh và sáo vọng theo từng câu."
    : "Hát lời của bạn bằng chính giọng của bạn: giai điệu ngũ cung theo dấu thanh (lời vẫn rõ), ban nhạc đàn tranh, sáo, mõ và trống theo nhịp.";
  $(".text-label", form).textContent = isRecite ? "Bài thơ (mỗi câu một dòng)" : "Lời bài hát (mỗi câu một dòng)";
  form.text.placeholder = isRecite ? "Mình ta với cả trời sao sáng,\nNâng chén sương đêm cạn hết sầu" : "Mỗi câu một dòng…";
  $("button[type=submit]", form).textContent = isRecite ? "🪷 Ngâm" : "🎵 Hát";
  syncSongForms();
  form.key_shift.oninput = () => { form.key_shift.dataset.touched = "1"; $(".o-key", form).textContent = (+form.key_shift.value > 0 ? "+" : "") + form.key_shift.value; };
  if (form.tempo) form.tempo.oninput = () => { $(".o-tempo", form).textContent = (+form.tempo.value).toFixed(2); };
  if (isRecite) {
    try { poemList = await api("/api/poems"); } catch { poemList = []; }
    for (const p of poemList) form.poem.append(new Option(`${p.title} — ${p.author}`, p.id));
    const fillVersions = () => {
      const p = poemList.find((x) => x.id === form.poem.value);
      form.version.innerHTML = ""; $(".poem-meta", form).innerHTML = "";
      form.version.closest("label").hidden = !p;
      if (!p) { form.text.readOnly = false; return; }
      for (const v of p.versions) form.version.append(new Option(v === "phien-am" ? "Phiên âm Hán-Việt" : "Dịch thơ: " + v.slice(5), v));
      if (p.versions.length > 1) form.version.value = p.versions[1];
      showPoem();
    };
    const showPoem = () => {
      const p = poemList.find((x) => x.id === form.poem.value); if (!p) return;
      const v = form.version.value, tr = (p.translations || []).find((t) => "dich:" + t.translator === v);
      form.text.value = (tr ? tr.lines : p.phien_am).join("\n"); form.text.readOnly = true;
      form.title.value = p.title; form.author.value = p.author;
      $(".poem-meta", form).innerHTML = `${p.title_han ? esc(p.title_han) + " · " : ""}${esc(p.form || "")}${p.dich_nghia ? `<br><i>Dịch nghĩa:</i> ${esc(p.dich_nghia)}` : ""}${tr?.note ? `<br><small>Ghi chú văn bản: ${esc(tr.note)}</small>` : ""}`;
    };
    form.poem.onchange = fillVersions; form.version.onchange = showPoem; fillVersions();
  }
  form.onsubmit = async (e) => {
    e.preventDefault();
    const btn = $("button[type=submit]", form); btn.disabled = true;
    try {
      if (!form.text.value.trim()) throw new Error(isRecite ? "Nhập bài thơ hoặc chọn một bài" : "Nhập lời bài hát");
      const params = { text: form.text.value, voice: form.voice.value || null, mood: form.mood.value,
        spirit: form.spirit.value || null, title: form.title.value || null, author: form.author.value || null,
        accompaniment: form.accompaniment.value, video: form.video.checked, aspect: form.aspect.value };
      if (form.key_shift.dataset.touched) params.key_shift = +form.key_shift.value;  // else: the voice's own pitch setting
      if (isRecite) { params.tempo = +form.tempo.value; if (form.poem.value) { params.poem_id = form.poem.value; params.version = form.version.value; } }
      else if (form.bpm.value) params.bpm = +form.bpm.value;
      const job = await api("/api/jobs", { method: "POST", body: JSON.stringify({ type: kind, params, source: "dashboard" }) });
      out.innerHTML = ""; out.append(renderJob(job)); followJob(job.id);
    } catch (err) { toast(err.message); } finally { btn.disabled = false; }
  };
}
// Voice and library lists load asynchronously; copy them into the song forms whenever a song tab opens.
function syncSongForms() {
  for (const form of $$(".song-form")) {
    const vs = $(".voice-select", form), src = $("#voice-select");
    if (src.options.length && vs.options.length !== src.options.length) { vs.innerHTML = src.innerHTML; vs.value = src.value; }
    if (!form.accompaniment.querySelector("option[data-lib]"))
      for (const t of musicTracks) { const o = new Option(`Thư viện: ${t.title}`, t.id); o.dataset.lib = "1"; form.accompaniment.append(o); }
  }
}
$$(".song-tab").forEach((sec) => songForm(sec));
