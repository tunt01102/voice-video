// Web capture (docs/SDD-web-capture.md): the capture gate shown when a Motion or Tạo mới job waits at stage
// "capture", and the "Website của tôi" card in tab Hệ thống (own sites, saved site references). Uses app.js globals.

const KIND_VI = { product: "Ảnh sản phẩm", image: "Ảnh trên trang", block: "Chụp khối sản phẩm", hero: "Chụp màn hình",
  upload: "Ảnh bạn tải lên" };
const KEY_VI = { name: "Tên", description: "Mô tả", price: "Giá", brand: "Thương hiệu", heading: "Tiêu đề trên trang", line: "Dòng chữ" };
const MISSING_VI = { name: "tên sản phẩm hoặc trang", sentence: "câu mô tả", price: "giá hoặc con số",
  pictures: "ảnh sản phẩm (ít nhất 2 ảnh lớn)", logo: "logo", clean: "trang mở không lỗi" };
const ACTION_VI = { open_link: "mở link", click: "bấm", scroll: "cuộn", capture_image: "lấy ảnh", capture_block: "chụp khối",
  note_fact: "ghi dữ kiện", done: "xong", invalid: "trả lời sai luật", stop: "dừng" };

const sourceVi = (s) => String(s || "").replace(/^jsonld:/, "dữ liệu cấu trúc · ").replace(/^og:/, "Open Graph · ")
  .replace(/^meta:/, "thẻ meta · ").replace(/^text:/, "chữ trên trang · ").replace(/^agent:/, "trợ lý · ")
  .replace(/^reference$/, "mẫu đã lưu").replace(/^screenshot:/, "chụp màn hình · ").replace(/^page:img$/, "ảnh trên trang")
  .replace(/^header:/, "đầu trang · ").replace(/^link:/, "biểu tượng trang · ");

function keySelect(cur) {
  return `<select data-k>${Object.entries(KEY_VI).map(([k, v]) => `<option value="${k}" ${k === cur ? "selected" : ""}>${v}</option>`).join("")}</select>`;
}

function picCard(p, url, checked) {
  return `<label class="cap-pic"><input type="checkbox" data-file="${esc(p.file)}" ${checked ? "checked" : ""}>
    <img src="${esc(url)}" alt="${esc(p.alt || KIND_VI[p.kind] || "")}" loading="lazy">
    <span><b>${esc(KIND_VI[p.kind] || p.kind)}</b> · ${p.w}×${p.h}</span>
    <small class="muted" title="${esc(p.url || "")}">${esc(sourceVi(p.source))}</small></label>`;
}

window.captureGate = (job, out) => {
  const r = job.result || {}, c = r.capture_data || {}, f = (job.files || {}).capture_data || {};
  const conf = c.confidence || {}, pics = c.pictures || [], facts = c.facts || [];
  const missing = (conf.missing || []).map((m) => MISSING_VI[m] || m);
  const box = document.createElement("div");
  box.className = "gate cap-gate";
  const steps = c.agent_steps || [];
  box.innerHTML = `<h3>Duyệt ảnh và thông tin lấy từ website</h3>
    <p class="muted">${(c.pages || []).map((p) => `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title || p.url)}</a>`).join(" · ")}</p>
    <p><b>Độ đầy đủ: ${conf.score ?? "?"}/100</b> <span class="muted">(checklist ${conf.checklist ?? "?"}${conf.llm != null ? `, Claude chấm ${conf.llm}` : ""})</span>
      ${missing.length ? ` · còn thiếu: ${missing.map(esc).join(", ")}` : " · đủ để làm video"}</p>
    ${conf.llm_note ? `<p class="muted">${esc(conf.llm_note)}</p>` : ""}
    ${c.reference ? `<p class="muted">Mẫu đã lưu của ${esc(c.reference.domain)}: ${c.reference.stale ? "đã cũ, không dùng" : `khớp ${c.reference.matched}/${c.reference.total}`}</p>` : ""}
    ${steps.length ? `<details><summary>Trợ lý đã tìm thêm (${steps.length} bước)</summary><ol class="cap-steps">${steps.map((s) =>
      `<li>${esc(ACTION_VI[s.action] || s.action)} ${esc(Array.isArray(s.arg) ? s.arg.join(": ") : s.arg ?? "")} — <span class="muted">${esc(s.result || "")}</span></li>`).join("")}</ol></details>` : ""}
    ${(c.warnings || []).length ? `<details><summary>Ghi chú (${c.warnings.length})</summary><ul class="muted">${c.warnings.map((w) => `<li>${esc(w)}</li>`).join("")}</ul></details>` : ""}
    <p class="cap-legal">Chỉ dùng ảnh và logo bạn có quyền sử dụng.</p>
    <h4>Ảnh (tối đa 8, đã đánh dấu sẵn ảnh nên dùng)</h4>
    <div class="cap-pics">${pics.map((p, i) => picCard(p, f.pictures?.[i]?.file?.url || "", p.recommended)).join("") || `<p class="muted">Không lấy được ảnh nào.</p>`}</div>
    <label>Thêm ảnh của bạn<input type="file" class="cap-up" accept="image/*" multiple></label>
    <h4>Logo</h4>
    <div class="cap-logo">${c.logo ? `<label class="cap-pic"><input type="checkbox" class="cap-logo-on" checked>
        <img src="${esc(f.logo?.file?.url || "")}" alt="logo"><span><b>Logo</b> · ${c.logo.w}×${c.logo.h}</span>
        <small class="muted">${esc(sourceVi(c.logo.source))}</small></label>` : `<p class="muted">Không tìm thấy logo đủ lớn.</p>`}
      <label>Logo khác<input type="file" class="cap-logo-up" accept="image/*"></label></div>
    <h4>Thông tin (Claude chỉ dùng những dòng còn đánh dấu; sửa được)</h4>
    <div class="cap-facts">${facts.map((x, i) => `<div class="cap-fact" data-i="${i}"><input type="checkbox" checked aria-label="Dùng dòng này">
        ${keySelect(x.key)}<input data-v value="${esc(x.value)}" aria-label="${esc(KEY_VI[x.key] || x.key)}">
        <small class="muted">${esc(sourceVi(x.source))}</small></div>`).join("") || `<p class="muted">Không lấy được thông tin nào.</p>`}</div>
    <button type="button" class="ghost small cap-add">＋ Thêm một dòng</button>
    ${c.preview ? `<details><summary>Xem cả trang đã chụp</summary><img class="cap-preview" src="${esc(f.preview?.url || "")}" alt="Ảnh chụp cả trang"></details>` : ""}
    <div class="actions"><button class="primary cap-go">Duyệt → viết brief</button>
      <button class="ghost cap-cancel">Huỷ</button><span class="muted cap-st"></span></div>`;
  $(".cap-add", box).onclick = () => $(".cap-facts", box).insertAdjacentHTML("beforeend",
    `<div class="cap-fact"><input type="checkbox" checked aria-label="Dùng dòng này">${keySelect("line")}<input data-v value="" aria-label="Dòng mới"><small class="muted">bạn thêm</small></div>`);
  $(".cap-cancel", box).onclick = () => api(`/api/jobs/${job.id}/cancel`, { method: "POST" }).then(() => refreshJob(job.id), (e) => toast(e.message));
  $(".cap-go", box).onclick = async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true; $(".cap-st", box).textContent = "Đang gửi…";
    try {
      const pictures = $$(".cap-pics input[data-file]", box).filter((i) => i.checked).map((i) => i.dataset.file);
      for (const file of [...$(".cap-up", box).files]) pictures.push(await upload(file));
      if (pictures.length > 8) throw new Error(`Đang chọn ${pictures.length} ảnh — tối đa 8`);
      let logo = c.logo && $(".cap-logo-on", box)?.checked ? c.logo.file : "";
      if ($(".cap-logo-up", box).files[0]) logo = await upload($(".cap-logo-up", box).files[0]);
      const factsOut = $$(".cap-fact", box).filter((d) => $("input[type=checkbox]", d).checked).map((d) => ({
        ...(d.dataset.i != null ? { i: +d.dataset.i } : {}), key: $("[data-k]", d).value, value: $("[data-v]", d).value.trim() }))
        .filter((x) => x.value);
      const j = await api(`/api/jobs/${job.id}/approve`, { method: "POST",
        body: JSON.stringify({ stage: "capture", capture: { pictures, logo, facts: factsOut } }) });
      updateJob(j); followJob(j.id);
    } catch (err) { btn.disabled = false; $(".cap-st", box).textContent = ""; toast(err.message); }
  };
  out.append(box);
};

// ------------------------------------------------------------------ tab Hệ thống: own sites and site references
async function loadWebSites() {
  const card = $("#web-sites");
  if (!card) return;
  try {
    const [own, refs] = await Promise.all([api("/api/webcapture/own-sites"), api("/api/webcapture/sites")]);
    $("#own-sites").value = own.sites.join("\n");
    const day = (t) => (t ? new Date(t * 1000).toLocaleDateString("vi-VN") : "—");
    $("#site-refs").innerHTML = refs.sites.length ? `<table class="cap-refs"><thead><tr><th>Trang</th><th>Lưu</th><th>Dùng lần cuối</th><th>Mục</th><th></th></tr></thead><tbody>${
      refs.sites.map((s) => `<tr><td>${esc(s.domain)}${s.stale ? ' <span class="muted">(cũ)</span>' : ""}</td><td>${day(s.saved_at)}</td>
        <td>${day(s.used_at)}</td><td>${s.items}</td><td><button class="ghost small" data-del="${esc(s.domain)}" aria-label="Xoá mẫu ${esc(s.domain)}">Xoá</button></td></tr>`).join("")
    }</tbody></table>` : `<p class="muted">Chưa có mẫu nào. Mẫu được lưu khi bạn duyệt ảnh và thông tin lấy từ một trang.</p>`;
    $$("#site-refs [data-del]").forEach((b) => b.onclick = async () => {
      try { await api(`/api/webcapture/sites/${encodeURIComponent(b.dataset.del)}`, { method: "DELETE" }); loadWebSites(); }
      catch (e) { toast(e.message); }
    });
  } catch (e) { toast(e.message); }
}
window.loadWebSites = loadWebSites;

$("#own-sites-save")?.addEventListener("click", async () => {
  try {
    const r = await api("/api/webcapture/own-sites", { method: "PUT",
      body: JSON.stringify({ sites: $("#own-sites").value.split("\n").map((x) => x.trim()).filter(Boolean) }) });
    $("#own-sites").value = r.sites.join("\n");
    toast(`Đã lưu ${r.sites.length} trang`);
  } catch (e) { toast(e.message); }
});

if (location.hash === "#system") loadWebSites();
