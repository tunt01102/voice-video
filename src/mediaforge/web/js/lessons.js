// Kinh nghiệm — what MediaForge learned from mistakes and ratings (docs/SDD-review-upgrade.md §5.5.1).
// Uses the globals from app.js ($, $$, api, esc, toast).

const KIND_VI = {
  reading: "Cách đọc", judge_accept: "Máy nghe nhầm", lint: "Lỗi kịch bản", engine_route: "Chọn engine",
  visual_pref: "Hình ảnh", motion_pref: "Chuyển cảnh", music_pref: "Nhạc", plan_rule: "Quy tắc kịch bản", ops: "Sự cố",
};
const CLASS_VI = { repeat: "lặp từ", judge: "máy nghe nhầm", truncated: "bị cắt / nuốt chữ", terms: "từ viết tắt / nước ngoài", other: "khác" };

function scopeText(sc) {
  const bits = [];
  if (sc.voice) bits.push("giọng " + sc.voice);
  if (sc.lang) bits.push(sc.lang);
  if (sc.engine) bits.push(sc.engine);
  if (sc.kind) bits.push(sc.kind);
  if (sc.type) bits.push(sc.type);
  return bits.join(" · ") || "chung";
}

function valueText(l) {
  const v = l.value || {};
  if (l.kind === "reading") return `→ <b>${esc(v.say)}</b>`;
  if (l.kind === "judge_accept") return `<small class="muted">trong “${esc((v.sentence || "").slice(0, 80))}”</small>`;
  if (["visual_pref", "motion_pref", "music_pref"].includes(l.kind))
    return `${v.direction === "dislike" ? "👎 không thích" : "👍 thích"} <small class="muted">(${v.votes?.like || 0}👍 ${v.votes?.dislike || 0}👎)${v.last_note ? " — “" + esc(v.last_note) + "”" : ""}</small>`;
  if (l.kind === "ops") return `<small class="muted">${esc(v.last_error || "")}</small>`;
  if (l.kind === "plan_rule") return "";
  return `<small class="muted">${esc(JSON.stringify(v).slice(0, 100))}</small>`;
}

function lessonRow(l) {
  const acts = {
    candidate: `<button class="primary small" data-st="active">✓ Áp dụng</button> <button class="ghost small" data-st="retired">✕ Bỏ</button>`,
    active: `<button class="ghost small" data-st="retired">✕ Bỏ</button>`,
    retired: `<button class="ghost small" data-st="active">↺ Khôi phục</button>`,
  }[l.status];
  const stats = l.kind === "reading" ? ` · ${l.evidence} câu thắng · dùng ${l.uses} lần, đạt ngay ${l.wins}` : l.evidence > 1 ? ` · ${l.evidence} lần` : "";
  return `<tr data-id="${l.id}">
    <td><span class="badge">${esc(KIND_VI[l.kind] || l.kind)}</span><br><small class="muted">${esc(scopeText(l.scope))}</small></td>
    <td><b>${esc(l.key)}</b> ${valueText(l)}<br><small class="muted">${esc(l.note || "")}${stats}</small></td>
    <td class="nowrap">${acts}</td></tr>`;
}

function table(list, empty) {
  return list.length ? `<table class="lex lessons"><tbody>${list.map(lessonRow).join("")}</tbody></table>` : `<p class="muted">${empty}</p>`;
}

async function loadLessons() {
  const box = $("#lessons-body");
  box.innerHTML = `<p class="muted">Đang tải…</p>`;
  const [all, ins] = await Promise.all([api("/api/lessons?limit=1000"), api("/api/insights?days=30")]);
  const by = (st) => all.filter((l) => l.status === st && l.kind !== "ops");
  const classes = Object.entries(ins.by_class || {}).sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `<li>${esc(CLASS_VI[k] || k)}: <b>${n}</b></li>`).join("");
  box.innerHTML = `
    <div class="stats">
      <div class="stat"><b>${ins.qa_failures}</b><small>câu trượt QA (30 ngày)</small></div>
      <div class="stat"><b>${ins.lessons.active}</b><small>bài học đang áp dụng</small></div>
      <div class="stat"><b>${ins.lessons.candidate}</b><small>chờ bạn duyệt</small></div>
      <div class="stat"><b>${ins.median_rating ?? "—"}</b><small>điểm trung vị (${ins.ratings} lượt chấm)</small></div>
    </div>
    <div class="card"><h3>Hàng chờ duyệt</h3>
      <p class="muted">Máy tự đề xuất, chưa áp dụng. Một cách đọc tự áp dụng khi đã thắng ở 2 câu khác nhau; bạn duyệt thì áp dụng ngay.</p>
      ${table(by("candidate"), "Không có gì chờ duyệt.")}</div>
    <div class="card"><h3>Đang áp dụng</h3>${table(by("active"), "Chưa có bài học nào.")}</div>
    ${classes || ins.recurring?.length ? `<div class="card"><h3>Lỗi hay gặp (30 ngày)</h3><ul>${classes}</ul>
      ${ins.recurring?.length ? `<p class="muted">Câu trượt nhiều lần:</p><ol>${ins.recurring.map(([t, n]) => `<li>${esc(t)} <small class="muted">× ${n}</small></li>`).join("")}</ol>` : ""}</div>` : ""}
    <details class="card"><summary><b>Đã bỏ</b> (${by("retired").length}) — giữ lại để khôi phục nếu cần</summary>${table(by("retired"), "Trống.")}</details>`;
  $$("tr[data-id] button[data-st]", box).forEach((b) => b.onclick = async () => {
    b.disabled = true;
    try { await api(`/api/lessons/${b.closest("tr").dataset.id}`, { method: "PATCH", body: JSON.stringify({ status: b.dataset.st }) }); loadLessons(); }
    catch (e) { b.disabled = false; toast(e.message); }
  });
}

async function loadIncidents() {
  const box = $("#incidents");
  if (!box) return;
  const list = await api("/api/incidents?days=30");
  box.innerHTML = list.length
    ? `<ul>${list.slice(0, 20).map((s) => `<li><small class="muted">${new Date(s.created_at * 1000).toLocaleString()}</small> ${s.job_id ? `<a href="#job/${s.job_id}">${s.job_id}</a>` : ""} ${esc(s.payload.type || "")}: ${esc(s.payload.error || "")}</li>`).join("")}</ul>`
    : `<p class="muted">Không có sự cố nào trong 30 ngày.</p>`;
}

window.loadLessons = loadLessons;
window.loadIncidents = loadIncidents;
if (location.hash === "#lessons") loadLessons();
loadIncidents().catch(() => {});
