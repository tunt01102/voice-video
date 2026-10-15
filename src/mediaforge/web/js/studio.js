// Review steps of a staged video (docs/SDD-review-upgrade.md §5.5.2): the plan gate (script, art direction, hook,
// risky words) and the storyboard gate (pictures, redraw one). Uses the globals from app.js.

const MOOD_VI = { calm: "êm", reflective: "suy tư", curious: "tò mò", tense: "căng thẳng", urgent: "gấp gáp",
  hopeful: "hy vọng", triumphant: "chiến thắng", playful: "vui tươi" };
const moodVi = (m) => MOOD_VI[m] || m;
const AD_FIELDS = [["motif", "Nhân vật / chi tiết lặp lại"], ["camera", "Cách quay"], ["palette", "Bảng màu"],
  ["music_mood", "Không khí nhạc"], ["structure", "Cấu trúc"]];

function planGate(job, out) {
  const r = job.result, plan = r.plan_data || {}, scenes = plan.scenes || [], ad = plan.art_direction || {};
  const pres = (plan.prescan || []).filter((c) => !c.known);
  const box = document.createElement("div");
  box.className = "gate";
  box.innerHTML = `<h3>Bước 1/2 · Duyệt kịch bản</h3>
    <p class="muted">Sửa lời đọc, xoá cảnh thừa, ghi cách đọc từ khó. Máy chỉ đọc và vẽ sau khi bạn duyệt. Chỉ đạo hình ảnh và mô tả hình viết bằng tiếng Anh (cho máy vẽ).</p>
    <label>Tựa đề <input class="g-title" value="${esc(plan.title || "")}"></label>
    ${plan.hooks?.length > 1 ? `<fieldset class="hooks"><legend>Câu mở đầu (hook)</legend>${plan.hooks.map((h, i) =>
      `<label class="check"><input type="radio" name="hook-${job.id}" value="${i}" ${i === (plan.hook_choice ?? 0) ? "checked" : ""}> ${esc(h)}</label>`).join("")}</fieldset>` : ""}
    ${Object.keys(ad).length ? `<details class="ad" open><summary>Chỉ đạo hình ảnh (dùng chung cho mọi cảnh)</summary><div class="grid2">${AD_FIELDS.map(([k, label]) =>
      ad[k] != null ? `<label>${label}<input data-ad="${k}" value="${esc(Array.isArray(ad[k]) ? ad[k].join(", ") : ad[k])}"></label>` : "").join("")}</div></details>` : ""}
    ${typeof lintHtml === "function" ? lintHtml(plan.lint) : ""}
    <ol class="g-scenes">${scenes.map((sc, i) => `<li data-i="${i}">
      <textarea class="g-narr" rows="2">${esc(sc.narration)}</textarea>
      <details><summary class="muted">Mô tả hình${sc.mood ? ` · không khí: ${esc(moodVi(sc.mood))}` : ""}</summary><textarea class="g-vp" rows="2">${esc(sc.visual_prompt || "")}</textarea></details>
      <button type="button" class="ghost small g-del" title="Bỏ cảnh này">✕</button></li>`).join("")}</ol>
    ${pres.length ? `<h4>Từ có thể bị đọc sai</h4><table class="lex prescan"><tbody>${pres.map((c) => `<tr data-term="${esc(c.term)}">
      <td><b>${esc(c.term)}</b><br><small class="muted">${esc((c.reason_labels || []).join(", "))}</small></td>
      <td class="ps-ctx">${typeof markTerms === "function" ? markTerms(c.context, [c.term]) : esc(c.context)}</td>
      <td><input class="ps-say" value="${esc(c.source === "english" ? "" : c.say || "")}" placeholder="cách đọc"></td>
      <td>${psChoice(c)}</td></tr>`).join("")}</tbody></table>
      <p class="muted"><b>tiếng Anh</b> = đọc nguyên tiếng Anh bằng chính giọng này (không phiên âm, không đổi giọng giữa câu).
        <button type="button" class="ghost small ps-all-en">Mọi từ nước ngoài → tiếng Anh</button></p>` : ""}
    <div class="actions">
      <button class="primary g-go">Duyệt kịch bản → đọc &amp; vẽ</button>
      <button class="ghost g-all" title="Không dừng ở storyboard nữa">Duyệt hết, dựng luôn</button>
      <button class="ghost g-cancel">Huỷ</button><span class="muted g-status"></span></div>`;
  $$(".g-del", box).forEach((b) => b.onclick = () => b.closest("li").classList.toggle("removed"));
  $$("tr[data-term]", box).forEach(psChoiceWire);
  const allEn = $(".ps-all-en", box);
  if (allEn) allEn.onclick = () => psAllEnglish(box, pres);
  const send = async (skip) => {
    const scenesOut = $$(".g-scenes > li", box).filter((li) => !li.classList.contains("removed"))
      .map((li) => ({ from: +li.dataset.i, narration: $(".g-narr", li).value, visual_prompt: $(".g-vp", li).value }));
    if (!scenesOut.length) return toast("Phải giữ ít nhất một cảnh");
    const adOut = {};
    $$("[data-ad]", box).forEach((i) => { adOut[i.dataset.ad] = i.dataset.ad === "palette" ? i.value.split(",").map((x) => x.trim()).filter(Boolean) : i.value; });
    const hook = $(`input[name="hook-${job.id}"]:checked`, box);
    const readings = {}, verified = [], english = [];
    $$("tr[data-term]", box).forEach((tr) => {
      if ($(".ps-en", tr).checked) english.push(tr.dataset.term);
      else if ($(".ps-ok", tr).checked) verified.push(tr.dataset.term);
      else if ($(".ps-say", tr).value.trim()) readings[tr.dataset.term] = $(".ps-say", tr).value.trim();
    });
    const body = { stage: "plan", skip_rest: skip, plan: { title: $(".g-title", box).value, scenes: scenesOut, art_direction: adOut },
      pronunciations: readings, verified_terms: verified, english_terms: english };
    if (hook) body.plan.hook = +hook.value;
    $(".g-status", box).textContent = "Đang gửi…";
    try { const j = await api(`/api/jobs/${job.id}/approve`, { method: "POST", body: JSON.stringify(body) }); updateJob(j); followJob(j.id); }
    catch (e) { $(".g-status", box).textContent = ""; toast(e.message); }
  };
  $(".g-go", box).onclick = () => send(false);
  $(".g-all", box).onclick = () => send(true);
  $(".g-cancel", box).onclick = async () => { await api(`/api/jobs/${job.id}/cancel`, { method: "POST" }); refreshJob(job.id); };
  out.append(box);
}

function storyGate(job, out) {
  const r = job.result, board = r.storyboard || [], files = job.files?.storyboard || [];
  const bust = r.updated_at ? `?v=${Math.round(r.updated_at * 1000)}` : "";
  const box = document.createElement("div");
  box.className = "gate";
  box.innerHTML = `<h3>Bước 2/2 · Storyboard</h3>
    <p class="muted">Giọng đã đọc xong. Xem từng cảnh; sửa mô tả rồi bấm 🔁 để vẽ lại cảnh chưa ưng. Rồi bấm Dựng video.</p>
    ${r.voice && job.files?.voice?.url ? `<audio controls src="${job.files.voice.url}"></audio>` : ""}
    <div class="board">${board.map((sc, i) => `<figure data-i="${i}">
      <img src="${files[i]?.image?.url || ""}${bust}" alt="Cảnh ${i + 1}" loading="lazy">
      <figcaption><b>${i + 1}.</b> ${esc(sc.narration.slice(0, 140))}${sc.narration.length > 140 ? "…" : ""}
        <small class="muted">${sc.duration}s${sc.mood ? " · " + esc(moodVi(sc.mood)) : ""}${sc.motion ? " · " + esc(sc.motion) : ""}</small></figcaption>
      <textarea rows="2" class="b-vp" placeholder="Mô tả hình (tiếng Anh)">${esc(sc.visual_prompt || "")}</textarea>
      <button class="ghost small b-redo">🔁 Vẽ lại</button></figure>`).join("")}</div>
    <div class="actions"><button class="primary b-go">Dựng video</button><button class="ghost b-cancel">Huỷ</button><span class="muted b-status"></span></div>`;
  $$(".board figure", box).forEach((fig) => {
    const i = +fig.dataset.i, btn = $(".b-redo", fig);
    btn.onclick = async () => {
      const prompt = $(".b-vp", fig).value.trim();
      btn.disabled = true; btn.textContent = "⏳ đang vẽ…";
      try {
        const res = await api(`/api/jobs/${job.id}/restage`, { method: "POST", body: JSON.stringify({ stage: "visuals", scene: i,
          prompt: prompt !== (board[i].visual_prompt || "") ? prompt : undefined }) });
        followJob(res.job.id, { onEnd: (j) => { if (j?.status === "failed") toast(j.message || "Vẽ lại lỗi"); refreshJob(job.id); } });
      } catch (e) { btn.disabled = false; btn.textContent = "🔁 Vẽ lại"; toast(e.message); }
    };
  });
  $(".b-go", box).onclick = async () => {
    $(".b-status", box).textContent = "Đang gửi…";
    try { const j = await api(`/api/jobs/${job.id}/approve`, { method: "POST", body: JSON.stringify({ stage: "visuals" }) }); updateJob(j); followJob(j.id); }
    catch (e) { $(".b-status", box).textContent = ""; toast(e.message); }
  };
  $(".b-cancel", box).onclick = async () => { await api(`/api/jobs/${job.id}/cancel`, { method: "POST" }); refreshJob(job.id); };
  out.append(box);
}

window.renderGate = (job, out) => (job.result?.stage === "visuals" ? storyGate : planGate)(job, out);

// Scenes of a finished video on its review page: redraw one picture or change the music without a full render.
window.sceneStrip = async (job) => {
  const r = job.result || {}, planUrl = job.files?.plan?.url;
  if (!r.video || !planUrl) return null;
  const base = planUrl.replace(/plan\.json$/, "");
  let plan, vis = null;
  try { plan = await (await fetch(planUrl, { cache: "no-store" })).json(); } catch { return null; }
  try { const v = await fetch(base + "visuals.json", { cache: "no-store" }); if (v.ok) vis = (await v.json()).filter((x) => !x.card); } catch { /* older job */ }
  const vf = job.files.video, rel = vf.url.slice("/files/".length), dataRoot = vf.path.slice(0, vf.path.length - rel.length);
  const urlOf = (p) => p && dataRoot && p.startsWith(dataRoot) ? "/files/" + p.slice(dataRoot.length) : null;
  const bust = r.rendered_at ? `?v=${Math.round(r.rendered_at)}` : "";
  const box = document.createElement("div");
  box.className = "card scenes";
  box.innerHTML = `<h3>Cảnh &amp; nhạc</h3><p class="muted">Đổi ảnh một cảnh hoặc đổi nhạc: chỉ dựng lại phần hình (vài chục giây), giọng và phụ đề giữ nguyên. Bản cũ chuyển vào thùng rác.</p>
    <div class="board">${plan.scenes.map((sc, i) => {
      const src = urlOf(vis?.[i]?.visual) || `${base}images/scene_${String(i + 1).padStart(2, "0")}.png`;
      return `<figure data-i="${i}"><img src="${src}${bust}" alt="Cảnh ${i + 1}" loading="lazy" onerror="this.style.visibility='hidden'">
        <figcaption><b>${i + 1}.</b> ${esc(sc.narration.slice(0, 110))}${sc.narration.length > 110 ? "…" : ""}</figcaption>
        <textarea rows="2" class="b-vp">${esc(sc.visual_prompt || "")}</textarea>
        <button class="ghost small b-redo">🔁 Vẽ lại &amp; dựng</button></figure>`;
    }).join("")}</div>
    <div class="actions"><label>Nhạc nền <select class="s-music"><option value="__keep">(giữ nguyên)</option><option value="__none">Không nhạc</option>
      ${(typeof musicTracks !== "undefined" ? musicTracks : []).filter((t) => t.available).map((t) => `<option value="${esc(t.id)}">${esc(t.title)} — ${esc(t.mood)}</option>`).join("")}</select></label>
      <button class="ghost small s-apply">Đổi nhạc &amp; dựng</button><span class="muted s-status"></span></div>`;
  const run = async (body, btn) => {
    btn.disabled = true; const st = $(".s-status", box); st.textContent = "Đang dựng lại…";
    try {
      const res = await api(`/api/jobs/${job.id}/restage`, { method: "POST", body: JSON.stringify(body) });
      followJob(res.job.id, { onEnd: (j) => { btn.disabled = false; st.textContent = j?.status === "done" ? "Xong" : (j?.message || "Lỗi"); if (j?.status === "done") window.openJobPage?.(job.id); } });
    } catch (e) { btn.disabled = false; st.textContent = ""; toast(e.message); }
  };
  $$("figure", box).forEach((fig) => {
    const i = +fig.dataset.i;
    $(".b-redo", fig).onclick = (e) => {
      const p = $(".b-vp", fig).value.trim();
      run({ stage: "visuals", scene: i, prompt: p !== (plan.scenes[i].visual_prompt || "") ? p : undefined }, e.currentTarget);
    };
  });
  $(".s-apply", box).onclick = (e) => {
    const m = $(".s-music", box).value;
    if (m === "__keep") return toast("Chọn bản nhạc khác trước");
    run({ stage: "compose", music: m }, e.currentTarget);
  };
  return box;
};
