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

function fillPage(job, page) {
  const out = $(".job-out", page);
  renderOutputs(job, out);
  const player = $("video, audio", out);
  const tl = timeline(job, player);
  if (tl) player.after(tl);
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
