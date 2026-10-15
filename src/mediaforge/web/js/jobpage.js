// Trang duyệt (job review page, #job/<id>): player + sentence timeline + fixes + rating
// (docs/SDD-review-upgrade.md §5.5.2 step 6). Uses the globals from app.js.

const TAGS = ["giọng", "phát âm", "nhịp", "hình", "chuyển cảnh", "phụ đề", "nhạc", "nội dung"];

function timeline(job, player) {
  const r = job.result || {}, spans = r.sentence_spans, sents = r.sentences || [];
  if (!Array.isArray(spans) || spans.length !== sents.length || !player) return null;
  const offset = r.report?.intro_offset || 0;
  const total = r.duration || (spans.at(-1)?.[1] || 1) + offset;
  const bar = document.createElement("div");
  bar.className = "timeline";
  bar.setAttribute("role", "listbox");
  bar.setAttribute("aria-label", "Các câu trong video — bấm để nghe từ câu đó");
  sents.forEach((s, i) => {
    const [a, b] = spans[i], fixed = (s.takes || []).some((t) => t.passed);
    const seg = document.createElement("button");
    seg.className = "tl-seg " + (s.passed ? "ok" : fixed ? "fixed" : "bad") + (s.lessons?.length || s.judge_accepted?.length ? " learned" : "");
    seg.style.left = ((a + offset) / total * 100).toFixed(3) + "%";
    seg.style.width = Math.max(0.4, (b - a) / total * 100).toFixed(3) + "%";
    seg.title = `${i + 1}. ${s.text}${s.passed ? "" : `\nnghe thành: “${s.heard}”`}${s.lessons?.length ? "\n📘 có áp dụng bài học" : ""}`;
    seg.setAttribute("role", "option");
    seg.onclick = () => { player.currentTime = a + offset; player.play?.(); };
    bar.append(seg);
  });
  const head = document.createElement("div");
  head.className = "tl-head";
  bar.append(head);
  player.addEventListener("timeupdate", () => {
    head.style.left = (player.currentTime / total * 100).toFixed(3) + "%";
    const t = player.currentTime - offset;
    const k = spans.findIndex(([a, b]) => t >= a && t <= b + 0.2);
    $$(".tl-seg", bar).forEach((el, i) => el.classList.toggle("now", i === k));
    $$(".qa-list > li").forEach((el, i) => el.classList.toggle("now", i === k));
  });
  bar.addEventListener("keydown", (e) => {
    const segs = $$(".tl-seg", bar), k = segs.indexOf(document.activeElement);
    if (e.key === "ArrowRight" && k < segs.length - 1) { segs[k + 1].focus(); segs[k + 1].click(); e.preventDefault(); }
    if (e.key === "ArrowLeft" && k > 0) { segs[k - 1].focus(); segs[k - 1].click(); e.preventDefault(); }
  });
  return bar;
}

async function ratingBox(job) {
  const box = document.createElement("div");
  box.className = "card rating";
  const cur = await api(`/api/jobs/${job.id}/rating`).catch(() => ({}));
  let stars = cur.stars || 0;
  const tags = new Set(cur.tags || []);
  box.innerHTML = `<h3>Đánh giá video này</h3>
    <p class="muted">Điểm và thẻ giúp các video sau tốt hơn: 2 lần chê cùng một điều thì hệ thống tự tránh; ghi chú kèm thẻ <b>nội dung</b> thành quy tắc viết kịch bản.</p>
    <div class="stars" role="radiogroup" aria-label="Số sao">${[1, 2, 3, 4, 5].map((n) => `<button class="star" data-n="${n}" role="radio" aria-label="${n} sao">★</button>`).join("")}</div>
    <div class="chips">${TAGS.map((t) => `<button class="chip" data-t="${t}">${t}</button>`).join("")}</div>
    <textarea rows="2" placeholder="Ghi chú (tuỳ chọn) — VD: mở đầu dài quá, hình tối">${esc(cur.note || "")}</textarea>
    <div class="actions"><button class="primary small rt-save">Lưu đánh giá</button><span class="muted rt-status">${cur.stars ? "Đã chấm " + cur.stars + "★" : ""}</span></div>`;
  const paint = () => {
    $$(".star", box).forEach((b) => { b.classList.toggle("on", +b.dataset.n <= stars); b.setAttribute("aria-checked", +b.dataset.n === stars); });
    $$(".chip", box).forEach((b) => b.classList.toggle("on", tags.has(b.dataset.t)));
  };
  $$(".star", box).forEach((b) => b.onclick = () => { stars = +b.dataset.n; paint(); });
  $$(".chip", box).forEach((b) => b.onclick = () => { tags.has(b.dataset.t) ? tags.delete(b.dataset.t) : tags.add(b.dataset.t); paint(); });
  $(".rt-save", box).onclick = async () => {
    if (!stars) return toast("Chọn số sao trước");
    try {
      const r = await api(`/api/jobs/${job.id}/rating`, { method: "POST", body: JSON.stringify({ stars, tags: [...tags], note: $("textarea", box).value }) });
      const learned = (r.lessons || []).filter((l) => l.status === "active").length;
      $(".rt-status", box).textContent = `Đã lưu ${stars}★` + (learned ? ` · ${learned} bài học đang áp dụng` : "");
    } catch (e) { toast(e.message); }
  };
  paint();
  return box;
}

// ---- 🎙 Đổi giọng (revoice): read the whole video again in another voice; script and pictures are kept
// (docs/SDD-review-upgrade.md §11.5). Stock voices play their pre-rendered preview at once, like the Giọng mẫu card.
let rvAudio = null;
const fmtSec = (s) => s >= 90 ? `${Math.floor(s / 60)} phút ${Math.round(s % 60)} giây` : `${Math.round(s)} giây`;

function revoiceBox(job) {
  const r = job.result || {}, box = document.createElement("div");
  box.className = "revoice";
  const last = (r.report?.revoiced || []).at(-1);
  const warns = last?.warnings || [], dropped = last?.dropped || {};
  const nTakes = (dropped.takes || []).reduce((n, t) => n + t.takes, 0) + (dropped.recordings || []).length;
  box.innerHTML = `<div class="rv-head"><button class="ghost small rv-open" aria-expanded="false">🎙 Đổi giọng</button>
      <small class="muted">đọc lại cả video bằng giọng khác — giữ kịch bản và hình; phụ đề, card và nhạc khớp theo lời mới</small></div>
    ${last ? `<div class="rv-last muted">Đã đổi giọng ${new Date(last.at * 1000).toLocaleString()}: ${esc(last.from?.voice || "?")} → <b>${esc(last.to?.name || last.to?.voice)}</b>
      · ${last.duration?.before ?? "?"} s → ${last.duration?.after ?? "?"} s${last.seconds?.total ? ` · mất ${fmtSec(last.seconds.total)}` : ""}
      ${nTakes ? ` · bỏ ${nTakes} bản đọc lại/ghi âm của giọng cũ` : ""}${Object.keys(last.readings_kept || {}).length ? ` · giữ ${Object.keys(last.readings_kept).length} cách đọc` : ""}
      ${warns.length ? `<ul class="rv-warn">${warns.map((w) => `<li>⚠ ${esc(w)}</li>`).join("")}</ul>` : ""}</div>` : ""}
    <div class="rv-panel" hidden>
      <div class="rv-tabs" role="tablist"><button class="chip on" data-k="own" role="tab">Giọng của bạn</button><button class="chip" data-k="stock" role="tab">Giọng mẫu</button></div>
      <div class="rv-list stock-grid" role="radiogroup" aria-label="Chọn giọng mới"><p class="muted">Đang tải giọng…</p></div>
      <label class="rv-pitch">Cao độ <input type="range" min="-6" max="6" step="1" value="0"> <span>0</span> nửa cung</label>
      <div class="actions"><button class="primary small rv-go" disabled>🎙 Đổi sang giọng này</button><span class="muted rv-status">Chọn một giọng</span></div>
    </div>`;
  const panel = $(".rv-panel", box), list = $(".rv-list", box), st = $(".rv-status", box), go = $(".rv-go", box);
  const pitch = $(".rv-pitch input", box);
  let all = [], tab = "own", pick = null, playing = null;
  pitch.oninput = () => { $(".rv-pitch span", box).textContent = pitch.value; };
  const stopAudio = () => { rvAudio?.pause(); rvAudio = null; playing = null; };
  const paint = () => {
    const cur = r.voice_id;
    const shown = all.filter((v) => (tab === "stock") === (v.kind === "stock") && v.id !== cur && v.id !== "demo");
    list.innerHTML = shown.map((v) => {
      const sub = v.kind === "stock" ? (v.engine === "voxcpm" ? "giọng thiết kế" : "giọng có sẵn") + (v.preview_ready ? "" : " · chưa có bản nghe thử")
        : v.kind === "blend" ? "giọng pha" : v.default ? "giọng của bạn ★" : v.consent ? "đã có đồng ý" : "cần ghi nhận đồng ý";
      return `<div class="stock-item rv-item ${pick === v.id ? "sel" : ""}" data-id="${v.id}" role="radio" aria-checked="${pick === v.id}" tabindex="0">
        ${v.kind === "stock" ? `<button class="play ghost" ${v.preview_ready ? "" : "disabled"} title="Nghe thử">${playing === v.id ? "■" : "▶"}</button>` : ""}
        <div class="meta"><b>${esc(v.name)}</b><small class="muted">${esc(sub)}</small></div></div>`;
    }).join("") || `<p class="muted">${tab === "stock" ? "Chưa cài giọng mẫu (tab Giọng nói → Giọng mẫu)." : "Chưa có giọng nào khác."}</p>`;
    $$(".rv-item", list).forEach((el) => {
      const choose = () => { pick = el.dataset.id; paint(); estimate(); };
      el.onclick = (e) => { if (!e.target.closest(".play")) choose(); };
      el.onkeydown = (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(); } };
    });
    $$(".rv-item .play", list).forEach((b) => b.onclick = () => {
      const id = b.closest(".rv-item").dataset.id, was = playing;
      stopAudio();
      if (was !== id) {
        playing = id; rvAudio = new Audio(`/api/voices/${id}/preview`);
        rvAudio.onended = () => { playing = null; paint(); };
        rvAudio.play().catch((e) => { playing = null; toast(e.message); paint(); });
      }
      paint();
    });
  };
  const estimate = async () => {
    go.disabled = !pick;
    if (!pick) return;
    st.textContent = "Đang ước lượng…";
    try {
      const e = await api(`/api/jobs/${job.id}/revoice/estimate?voice=${encodeURIComponent(pick)}`);
      st.textContent = `Ước lượng ≈ ${fmtSec(e.seconds)} (${e.sentences} câu, đọc ${fmtSec(e.voice)} + ghép ${fmtSec(e.compose)}) · không vẽ lại hình`;
    } catch (err) { st.textContent = err.message; }
  };
  $$(".rv-tabs .chip", box).forEach((b) => b.onclick = () => {
    tab = b.dataset.k; $$(".rv-tabs .chip", box).forEach((x) => x.classList.toggle("on", x === b)); paint();
  });
  $(".rv-open", box).onclick = async (ev) => {
    panel.hidden = !panel.hidden; ev.currentTarget.setAttribute("aria-expanded", String(!panel.hidden));
    if (!panel.hidden && !all.length) {
      try { all = await api("/api/voices"); } catch (err) { list.innerHTML = `<p class="qa-bad">${esc(err.message)}</p>`; return; }
      paint();
    }
    if (panel.hidden) stopAudio();
  };
  go.onclick = async () => {
    const v = all.find((x) => x.id === pick);
    if (!v || !confirm(`Đổi giọng video này sang “${v.name}”? Bản cũ và các file đã xuất chuyển vào thùng rác; bản đọc lại từng câu của giọng cũ sẽ bỏ.`)) return;
    stopAudio(); go.disabled = true; st.textContent = "Đang gửi…";
    const body = { voice: pick, ...(+pitch.value ? { fx: { pitch: +pitch.value } } : {}) };
    try {
      const res = await api(`/api/jobs/${job.id}/revoice`, { method: "POST", body: JSON.stringify(body) });
      const t0 = Date.now(), eta = res.estimate?.seconds || 0;
      followJob(res.job.id, {
        onProgress: (j) => {
          const el = Math.round((Date.now() - t0) / 1000);
          st.textContent = j.status === "queued" ? "Đang chờ GPU…" : `${j.message} · ${(j.progress * 100).toFixed(0)}% · ${fmtSec(el)}${eta ? ` / ≈ ${fmtSec(eta)}` : ""}`;
        },
        onEnd: async (j) => {
          if (j?.status !== "done") { go.disabled = false; st.textContent = ""; toast("Không đổi được giọng: " + (j?.message || "")); return; }
          const w = j.result?.warnings || [];
          toast(`Đã đổi sang ${v.name}` + (w.length ? ` · ${w.length} cảnh báo về clip` : ""));
          await refreshJob(job.id);
        },
      });
    } catch (err) { go.disabled = false; st.textContent = ""; toast(err.message); }
  };
  return box;
}

function fillPage(job, page) {
  const out = $(".job-out", page);
  renderOutputs(job, out);
  const player = $("video, audio", out);
  const tl = timeline(job, player);
  if (tl) player.after(tl);
  const r = job.result || {};
  if (job.status === "done" && r.video && r.plan && Array.isArray(r.words) && !["sing", "recite"].includes(job.type)) {
    const anchor = $(".resub-row", out);
    anchor ? anchor.after(revoiceBox(job)) : out.append(revoiceBox(job));
  }
}
// app.js updateJob() delegates here when the job is shown on its page (after ✓ Nghe đúng rồi, a take, an export…).
window.refreshJobPage = (job, page) => fillPage(job, page);

async function openJobPage(id) {
  const sec = $("#tab-job");
  $$("#tabs button").forEach((x) => x.classList.remove("active"));
  $$(".tab").forEach((t) => t.classList.toggle("active", t === sec));
  sec.innerHTML = `<p class="muted">Đang tải…</p>`;
  let job;
  try { job = await api(`/api/jobs/${id}`); } catch (e) { sec.innerHTML = `<p class="qa-bad">${esc(e.message)}</p>`; return; }
  const r = job.result || {};
  sec.innerHTML = `<div class="page-head"><a href="#jobs" class="ghost small">← Jobs</a>
      <h2>${esc(r.title || TYPE_LABEL[job.type] || job.type)}</h2>
      <small class="muted">${esc(TYPE_LABEL[job.type] || job.type)} · ${new Date(job.created_at * 1000).toLocaleString()} · ${job.status}</small></div>
    <div class="card job-page"><div class="job-out"></div></div>`;
  const page = $(".job-page", sec);
  jobEls.set(job.id, page);
  fillPage(job, page);
  if (job.status === "done") {
    const strip = await window.sceneStrip?.(job);
    if (strip) sec.append(strip);
    sec.append(await ratingBox(job));
  }
  if (["queued", "running"].includes(job.status)) followJob(job.id, { onEnd: () => openJobPage(id) });
}

function route() {
  const m = location.hash.match(/^#job\/([\w-]+)/);
  if (m) openJobPage(m[1]);
}
window.addEventListener("hashchange", route);
route();
window.openJobPage = openJobPage;
