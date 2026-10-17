// Phòng quay thuyết trình (docs/SDD-live-studio.md): the camera + mic are recorded in the browser while the owner
// clicks through their own slides; each click is stamped on the recording's clock. The take + the slide PNGs + that
// timeline are the master (file gốc) the `present` job renders at Full HD. Uses the globals from app.js.

const form = $("#create-form"), dlg = $("#live-studio");
const S = { mode: "live", slides: [], deckKey: "", cur: 0, stream: null, rec: null, chunks: [], track: [], t0: 0,
  paused: 0, pauseAt: 0, timer: 0, meter: 0, audio: null, take: null, uploaded: null };
const KEY_NEXT = ["ArrowRight", "ArrowDown", "PageDown", " ", "Enter"], KEY_PREV = ["ArrowLeft", "ArrowUp", "PageUp", "Backspace"];
const recording = () => S.rec && S.rec.state !== "inactive";
const fmtTime = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;

// ---- mode: record here | upload a recording made elsewhere
$$(".present-mode button").forEach((b) => b.onclick = () => {
  S.mode = b.dataset.pm;
  $$(".present-mode button").forEach((x) => x.classList.toggle("active", x === b));
  $(".pm-upload").hidden = S.mode !== "upload";
  $(".pm-live").hidden = S.mode !== "live";
});

// Same rules as present.split_script: `---`, `[slide 3]`, `[3]`, `Slide 3:`, `Trang 3` lines, else blank lines.
const SPLIT_RE = /^\s*(?:-{3,}|\[\s*(?:slide|trang)?\s*\d+\s*\]|(?:slide|trang)\s*\d+\s*[:.\-–]?)\s*$/i;
function scriptParts() {
  const text = form.text.value || "", lines = text.split("\n");
  let parts;
  if (lines.some((l) => SPLIT_RE.test(l))) {
    parts = [[]];
    for (const l of lines) if (SPLIT_RE.test(l)) parts.push([]); else parts[parts.length - 1].push(l.trim());
    parts = parts.map((p) => p.join(" "));
    if (parts.length && !parts[0].trim()) parts.shift();
  } else parts = text.split(/\n\s*\n/);
  return parts.map((p) => p.split(/\s+/).join(" ").trim()).filter(Boolean);
}

// ---- the deck: uploaded once, rendered by the server to the exact PNGs the video will use
async function loadDeck() {
  const files = [...form.slides_files.files].sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  const key = files.map((f) => `${f.name}:${f.size}:${f.lastModified}`).join("|");
  if (key === S.deckKey) return;
  S.slides = []; S.deckKey = key; S.cur = 0;
  if (!files.length) return;
  status("⏳ Đang tải slide lên…");
  try {
    const paths = []; for (const f of files) paths.push(await upload(f));
    const r = await api("/api/present/slides", { method: "POST", body: JSON.stringify({ slides: paths, aspect: "16:9" }) });
    S.slides = r.slides;
    S.slides.forEach((x) => { new Image().src = x.url; });  // preloaded: a click shows the slide at once
    status(`${S.slides.length} slide sẵn sàng — mở phòng quay để bắt đầu.`);
  } catch (err) { S.deckKey = ""; status("⚠ " + err.message); throw err; }
}
form.slides_files.addEventListener("change", () => { if (S.mode === "live") loadDeck().catch((e) => toast(e.message)); });

function status(html) { $("#live-status").innerHTML = html; }
function takeStatus() {
  if (!S.take) { status("Bật camera + micro, bấm qua từng slide khi nói — mỗi lần chuyển slide được ghi đúng thời điểm."); return; }
  const n = S.take.track.length - 1;
  status(`<span class="take-row"><b>✓ Đã có bản quay ${fmtTime(S.take.duration)}</b> · ${n} lần chuyển slide · ${esc(S.take.size)}
    <button type="button" class="ghost small" id="live-review">Xem lại</button>
    <a class="btn ghost small" href="${S.take.url}" download="${esc(S.take.name)}">⬇ File gốc</a>
    <button type="button" class="ghost small" id="live-drop">✕ Bỏ</button></span>`);
  $("#live-review").onclick = () => openStudio();
  $("#live-drop").onclick = () => { if (confirm("Bỏ bản quay này?")) dropTake(); };
}
function dropTake() {
  if (S.take) URL.revokeObjectURL(S.take.url);
  S.take = null; S.uploaded = null; takeStatus();
}

// ---- camera + mic
async function startStream() {
  stopStream();
  const cam = $(".ls-cam", dlg).value, mic = $(".ls-mic", dlg).value;
  S.stream = await navigator.mediaDevices.getUserMedia({
    video: { ...(cam ? { deviceId: { exact: cam } } : {}), width: { ideal: 1920 }, height: { ideal: 1080 }, frameRate: { ideal: 30 } },
    // raw microphone, like the voice-sample recorders: the server denoises and normalises
    audio: { ...(mic ? { deviceId: { exact: mic } } : {}), echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 },
  });
  $(".ls-cambox", dlg).srcObject = S.stream;
  const v = S.stream.getVideoTracks()[0]?.getSettings() || {}, res = $(".ls-res", dlg);
  res.textContent = v.width ? `Camera ${v.width}×${v.height}${v.frameRate ? ` · ${Math.round(v.frameRate)} fps` : ""}` : "Không có camera";
  res.classList.toggle("low", !v.width || Math.min(v.width, v.height) < 1080);
  if (v.width && Math.min(v.width, v.height) < 1080) res.textContent += " — dưới Full HD (video vẫn xuất 1080p, khung camera kém nét hơn)";
  await listDevices();
  startMeter();
}
function stopStream() {
  S.stream?.getTracks().forEach((t) => t.stop()); S.stream = null;
  cancelAnimationFrame(S.meter); S.audio?.close().catch(() => {}); S.audio = null;
}
async function listDevices() {
  const devs = await navigator.mediaDevices.enumerateDevices();
  for (const [sel, kind, track] of [[".ls-cam", "videoinput", S.stream?.getVideoTracks()[0]], [".ls-mic", "audioinput", S.stream?.getAudioTracks()[0]]]) {
    const el = $(sel, dlg), now = track?.getSettings().deviceId;
    el.innerHTML = devs.filter((d) => d.kind === kind).map((d, i) => `<option value="${esc(d.deviceId)}">${esc(d.label || `${kind} ${i + 1}`)}</option>`).join("");
    if (now) el.value = now;
  }
}
function startMeter() {
  const at = S.stream?.getAudioTracks()[0];
  if (!at) return;
  S.audio = new AudioContext();
  const an = S.audio.createAnalyser(); an.fftSize = 1024;
  S.audio.createMediaStreamSource(new MediaStream([at])).connect(an);
  const buf = new Float32Array(an.fftSize), bar = $(".ls-meter i", dlg);
  const tick = () => {
    an.getFloatTimeDomainData(buf);
    let peak = 0; for (const x of buf) peak = Math.max(peak, Math.abs(x));
    bar.style.width = `${Math.min(100, Math.round(Math.sqrt(peak) * 100))}%`;
    S.meter = requestAnimationFrame(tick);
  };
  tick();
}
$(".ls-cam", dlg).onchange = $(".ls-mic", dlg).onchange = () => startStream().catch((e) => toast(e.message));

// ---- slides on stage
function show() {
  const n = S.slides.length, stage = $(".ls-stage", dlg);
  stage.classList.toggle("camonly", !n);
  $(".ls-slide", dlg).hidden = !n; $(".ls-noslide", dlg).hidden = !!n;
  if (n) $(".ls-slide", dlg).src = S.slides[S.cur].url;
  $(".ls-num", dlg).textContent = n ? `${S.cur + 1} / ${n}` : "—";
  const nx = S.slides[S.cur + 1];
  $(".ls-next", dlg).hidden = !nx; if (nx) $(".ls-next img", dlg).src = nx.url;
  const parts = scriptParts(), part = parts.length === n ? parts[S.cur] : n ? "" : parts.join("\n\n");
  $(".ls-prompt", dlg).hidden = !part; $(".ls-prompt p", dlg).textContent = part || "";
  $(".ls-prev", dlg).disabled = S.cur === 0; $(".ls-nextbtn", dlg).disabled = S.cur >= n - 1;
  $(".ls-track", dlg).innerHTML = S.track.map((e) => `<li>slide ${e.slide + 1} · ${fmtTime(e.t)}<small>.${String(Math.round((e.t % 1) * 10))}</small></li>`).join("");
}
function go(i) {
  if (i < 0 || i >= S.slides.length || i === S.cur || S.take && !recording()) return;
  S.cur = i;
  if (recording()) S.track.push({ slide: i, t: +elapsed().toFixed(3) });
  show();
}
$(".ls-prev", dlg).onclick = () => go(S.cur - 1);
$(".ls-nextbtn", dlg).onclick = () => go(S.cur + 1);

// ---- the recorder. Its clock: t = 0 at onstart; paused time is left out (MediaRecorder leaves it out of the file).
function elapsed() { return ((S.rec?.state === "paused" ? S.pauseAt : performance.now()) - S.t0 - S.paused) / 1000; }
function pickMime() {
  return ["video/mp4;codecs=avc1.640028,mp4a.40.2", "video/mp4", "video/webm;codecs=vp9,opus", "video/webm;codecs=vp8,opus", "video/webm"]
    .find((m) => MediaRecorder.isTypeSupported(m)) || "";
}
async function countdown() {
  const el = $(".ls-count", dlg); el.hidden = false;
  for (const k of [3, 2, 1]) { el.textContent = k; await new Promise((r) => setTimeout(r, 1000)); }
  el.hidden = true;
}
async function startRec() {
  if (!S.stream?.getAudioTracks().length) { toast("Không có micro — cần micro để thu tiếng"); return; }
  $(".ls-start", dlg).disabled = true;
  await countdown();
  const mimeType = pickMime();
  S.rec = new MediaRecorder(S.stream, { ...(mimeType ? { mimeType } : {}), videoBitsPerSecond: 8_000_000, audioBitsPerSecond: 192_000 });
  S.chunks = []; S.paused = 0;
  S.rec.ondataavailable = (e) => e.data.size && S.chunks.push(e.data);
  S.rec.onstart = () => { S.t0 = performance.now(); S.track = [{ slide: S.cur, t: 0 }]; show(); };
  S.rec.onpause = () => { S.pauseAt = performance.now(); };
  S.rec.onresume = () => { S.paused += performance.now() - S.pauseAt; };
  S.rec.onstop = finishTake;
  S.rec.start(1000);  // 1 s slices: a crash loses at most the last second
  $(".ls-start", dlg).hidden = true; $(".ls-start", dlg).disabled = false;
  $(".ls-pause", dlg).hidden = $(".ls-stop", dlg).hidden = false;
  $(".ls-rec", dlg).hidden = false;
  S.timer = setInterval(() => { $(".ls-time", dlg).textContent = fmtTime(elapsed()); }, 250);
}
function togglePause() {
  if (!recording()) return;
  const paused = S.rec.state === "paused";
  if (paused) S.rec.resume(); else S.rec.pause();
  $(".ls-pause", dlg).textContent = paused ? "⏸ Tạm dừng (P)" : "▶ Quay tiếp (P)";
  $(".ls-rec", dlg).classList.toggle("paused", !paused);
}
function stopRec() { if (recording()) { S.take = { duration: elapsed() }; S.rec.stop(); } }
function finishTake() {
  clearInterval(S.timer);
  const mime = S.rec.mimeType || "video/webm", ext = mime.includes("mp4") ? "mp4" : "webm";
  const d = new Date(), pad = (x) => String(x).padStart(2, "0");
  const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}`;
  const blob = new Blob(S.chunks, { type: mime.split(";")[0] });
  S.take = { ...S.take, blob, url: URL.createObjectURL(blob), name: `thuyet-trinh-${stamp}.${ext}`, track: S.track,
    size: `${(blob.size / 1e6).toFixed(1)} MB` };
  S.uploaded = null; S.chunks = [];
  stopStream();  // camera light off while reviewing; a retake turns it back on
  review();
}

// ---- review: play the take back, keep it, save it, or record again
function review() {
  const rv = $(".ls-review", dlg);
  rv.hidden = false; rv.src = S.take.url;
  $(".ls-rec", dlg).hidden = true;
  $(".ls-live-ctl", dlg).hidden = true; $(".ls-review-ctl", dlg).hidden = false;
  $(".ls-prev", dlg).hidden = $(".ls-nextbtn", dlg).hidden = true;
  const save = $(".ls-save", dlg); save.href = S.take.url; save.download = S.take.name;
  S.track = S.take.track; show();
  takeStatus();
}
function liveMode() {
  $(".ls-review", dlg).hidden = true; $(".ls-review", dlg).removeAttribute("src");
  $(".ls-live-ctl", dlg).hidden = false; $(".ls-review-ctl", dlg).hidden = true;
  $(".ls-prev", dlg).hidden = $(".ls-nextbtn", dlg).hidden = false;
  $(".ls-start", dlg).hidden = false; $(".ls-pause", dlg).hidden = $(".ls-stop", dlg).hidden = true;
  $(".ls-pause", dlg).textContent = "⏸ Tạm dừng (P)"; $(".ls-rec", dlg).classList.remove("paused");
  S.track = []; S.cur = 0; show();
}
$(".ls-start", dlg).onclick = () => startRec().catch((e) => { toast(e.message); $(".ls-start", dlg).disabled = false; });
$(".ls-pause", dlg).onclick = togglePause;
$(".ls-stop", dlg).onclick = stopRec;
$(".ls-retake", dlg).onclick = () => { if (confirm("Quay lại từ đầu? Bản vừa quay sẽ bị bỏ (hãy ⬇ Lưu file gốc trước nếu muốn giữ).")) {
  dropTake(); liveMode(); startStream().catch((e) => toast("Không bật được camera/micro: " + e.message)); } };
$(".ls-use", dlg).onclick = () => { closeStudio(); form.requestSubmit(); };

// ---- open / close
async function openStudio() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { toast("Trình duyệt này không quay được camera"); return; }
  try { await loadDeck(); } catch { return; }
  dlg.showModal();
  if (S.take) { review(); } else { liveMode(); }
  if (!S.take) await startStream().catch((e) => toast("Không bật được camera/micro: " + e.message));
}
function closeStudio() {
  if (recording()) { if (!confirm("Đang quay — dừng và giữ bản quay?")) return; stopRec(); }
  stopStream(); dlg.close();
}
$("#live-open").onclick = () => openStudio();
$(".ls-close", dlg).onclick = closeStudio;
dlg.addEventListener("cancel", (e) => { e.preventDefault(); closeStudio(); });  // Esc
document.addEventListener("keydown", (e) => {
  if (!dlg.open || ["SELECT", "INPUT", "TEXTAREA"].includes(e.target.tagName)) return;
  if (e.target.tagName === "BUTTON" && (e.key === " " || e.key === "Enter")) return;  // a focused button clicks itself
  if (KEY_NEXT.includes(e.key)) { e.preventDefault(); go(S.cur + 1); }
  else if (KEY_PREV.includes(e.key)) { e.preventDefault(); go(S.cur - 1); }
  else if (e.key === "Home") { e.preventDefault(); go(0); }
  else if (e.key === "r" || e.key === "R") { e.preventDefault(); if (recording()) stopRec(); else if (!S.take) $(".ls-start", dlg).click(); }
  else if (e.key === "p" || e.key === "P") { e.preventDefault(); togglePause(); }
});

// ---- the master for the job (app.js buildParams): the take is uploaded once, with a progress bar
function xhrUpload(blob, name, onProgress) {
  return new Promise((resolve, reject) => {
    const fd = new FormData(); fd.append("file", blob, name);
    const x = new XMLHttpRequest();
    x.open("POST", "/api/uploads");
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    x.onload = () => x.status < 300 ? resolve(JSON.parse(x.responseText).path) : reject(new Error(`Tải bản quay lên lỗi ${x.status}: ${x.responseText.slice(0, 200)}`));
    x.onerror = () => reject(new Error("Tải bản quay lên lỗi mạng — bản quay vẫn còn, bấm Tạo lại hoặc ⬇ File gốc"));
    x.send(fd);
  });
}
window.liveStudio = {
  active: () => S.mode === "live",
  async master() {
    if (!S.take?.blob) throw new Error("Chưa có bản quay — bấm 🎥 Mở phòng quay");
    if (!S.uploaded) {
      S.uploaded = await xhrUpload(S.take.blob, S.take.name, (f) => status(`⏳ Đang tải bản quay lên… ${Math.round(f * 100)}%`));
      takeStatus();
    }
    const m = { recording: S.uploaded, slide_track: S.take.track, title: "Bản quay " + S.take.name.replace(/^thuyet-trinh-|\.\w+$/g, "").replace(/^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)$/, "$3/$2/$1 $4:$5") };
    if (S.slides.length) m.slides = S.slides.map((s) => s.path);
    else m.layout = "recording";  // no deck: the camera full-frame
    return m;
  },
};
