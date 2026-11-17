// Trang /data: tải file mẫu, nạp file Excel của khách, xem báo cáo kiểm tra + đối soát, chuyển bộ dữ liệu.
import { api, esc } from "./api.js";

const $ = (s) => document.querySelector(s);
const money = (v) => (v == null ? "—" : Math.round(v).toLocaleString("vi-VN"));
const num = (v) => Number(v || 0).toLocaleString("vi-VN");
const msg = (t) => ($("#msg").textContent = t || "");
const MOD = { sales: "Doanh thu", pnl: "Kết quả kinh doanh", bs: "Bảng cân đối", cashflow: "Lưu chuyển tiền", inventory: "Tồn kho" };
let S = null;

async function guard(fn) {
  msg("");
  try { return await fn(); } catch (e) { msg(e.message); }
}

function renderNow() {
  const mine = S.mine, user = S.active === "user";
  const mods = S.modules.map((m) => MOD[m]).filter(Boolean).join(", ") || "—";
  $("#now").innerHTML = `
    <div class="data-now-head">
      <span class="tag ${user ? "good" : ""}">${user ? "Đang dùng dữ liệu của bạn" : "Đang dùng dữ liệu mẫu"}</span>
      <span class="muted small">Phân hệ có dữ liệu: ${esc(mods)}</span>
    </div>
    ${mine ? `<p><b>${esc(mine.company)}</b> · file ${esc(mine.file)} · nạp lúc ${esc(new Date(mine.uploaded_at).toLocaleString("vi-VN"))}
      · dữ liệu đến ${esc(mine.as_of ? new Date(mine.as_of).toLocaleDateString("vi-VN") : "—")}
      · ${num(mine.rows.bi_sales)} dòng bán hàng</p>` : `<p class="muted">Bạn chưa nạp dữ liệu nào. Làm theo 3 bước dưới đây.</p>`}
    <div class="dlg-actions" style="justify-content:flex-start">
      ${mine && !user ? `<button class="btn-ai" data-act="user">Dùng dữ liệu của tôi</button>` : ""}
      ${user ? `<a class="btn-ai" href="/">Xem dashboard</a><button class="link-btn" data-act="demo">Quay về dữ liệu mẫu</button>` : ""}
      ${mine ? `<button class="link-btn" data-act="delete">Xoá dữ liệu của tôi khỏi máy</button>` : ""}
    </div>`;
}

function renderSpec() {
  $("#spec").innerHTML = S.sheets.map((s) => `<h4>${esc(s.name)} – ${esc(s.title)}${s.required ? ' <span class="tag bad">bắt buộc</span>' : ""}</h4>
    ${s.cols.length ? `<ul class="small">${s.cols.map((c) => `<li>${c.required ? "<b>" : ""}${esc(c.header)}${c.required ? " *</b>" : ""}${c.note ? ` <span class="muted">– ${esc(c.note)}</span>` : ""}</li>`).join("")}</ul>`
      : `<p class="small muted">Tên công ty, Tháng chốt số liệu (MM/YYYY), Kênh trọng tâm – không bắt buộc.</p>`}`).join("")
    + (S.edition === "lite" ? `<p class="small muted">🔒 Bản Pro có thêm sheet KetQuaKD, ChiPhi, CanDoiKT, LuuChuyenTien, TonKho → P&L, Bảng cân đối, Dòng tiền, Tồn kho.</p>` : "");
}

function renderReport(r, name) {
  const sheets = r.sheets?.length ? `<div class="table-wrap"><table class="tbl"><thead><tr><th>Sheet</th><th class="num">Số dòng</th><th class="num">Tổng tiền (đồng)</th><th>Từ – đến</th><th>Ghi chú</th></tr></thead><tbody>` +
    r.sheets.map((s) => `<tr><td><b>${esc(s.sheet)}</b><br><span class="muted small">${esc(s.label)}</span></td><td class="num" data-label="Số dòng">${num(s.rows)}</td>
      <td class="num" data-label="Tổng tiền">${money(s.total)}</td><td data-label="Từ – đến">${s.first ? `${esc(fmtD(s.first))} – ${esc(fmtD(s.last))}` : ""}</td>
      <td class="small muted">${s.orders ? `${num(s.orders)} chứng từ · ${num(s.customers)} khách hàng · ${num(s.products)} sản phẩm` : ""}</td></tr>`).join("") + "</tbody></table></div>" : "";
  const recon = r.recon?.length ? `<p class="small"><b>Đối soát file ↔ CSDL:</b> ` + r.recon.map((x) =>
    `<span class="tag ${x.ok ? "good" : "bad"}">${esc(x.sheet)} ${x.ok ? "khớp" : "LỆCH"} ${num(x.rows_db)} dòng</span>`).join(" ") + "</p>" : "";
  $("#report").innerHTML = `<p class="muted small">File: ${esc(name)}</p>
    ${r.errors.length ? `<div class="data-box bad"><b>${r.errors.length} lỗi – cần sửa file rồi tải lại:</b><ul>${r.errors.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : ""}
    ${r.warnings.length ? `<div class="data-box warn"><b>Lưu ý:</b><ul>${r.warnings.map((e) => `<li>${esc(e)}</li>`).join("")}</ul></div>` : ""}
    ${sheets}${recon}
    ${r.can_activate ? `<div class="data-box good">✓ Dữ liệu hợp lệ. Đối chiếu số dòng và tổng tiền ở trên với báo cáo trong phần mềm kế toán của bạn trước khi dùng.</div>
      <p><button class="btn-ai" id="activate">Dùng dữ liệu này cho dashboard và chatbot</button></p>` : ""}`;
  $("#result").hidden = false;
  $("#activate")?.addEventListener("click", () => guard(async () => {
    await api("/api/data/activate", { method: "POST" });
    location.href = "/";
  }));
}

const fmtD = (s) => (/^\d{4}-\d{2}-\d{2}$/.test(s) ? new Date(s).toLocaleDateString("vi-VN") : s);

async function upload(file) {
  if (!file) return;
  msg("");
  $("#result").hidden = false;
  $("#report").innerHTML = `<p class="typing">Đang đọc và kiểm tra ${esc(file.name)}</p>`;
  try {
    const r = await api("/api/data/upload", { method: "POST", body: file,
      headers: { "Content-Type": "application/octet-stream", "x-file-name": encodeURIComponent(file.name) } });
    renderReport(r, file.name);
  } catch (e) {
    $("#report").innerHTML = `<div class="data-box bad">${esc(e.message)}</div>`;
  }
}

$("#file").onchange = (e) => upload(e.target.files[0]);
const drop = $("#drop");
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("on"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("on"); }));
drop.addEventListener("drop", (e) => upload(e.dataTransfer.files[0]));

$("#now").onclick = (e) => {
  const act = e.target.dataset.act;
  if (!act) return;
  if (act === "delete" && !confirm("Xoá dữ liệu của bạn khỏi máy này? Dashboard sẽ quay về dữ liệu mẫu.")) return;
  guard(async () => {
    if (act === "delete") await api("/api/data/mine", { method: "DELETE" });
    else await api(`/api/data/use/${act}`, { method: "POST" });
    S = await api("/api/data/status");
    renderNow();
  });
};

(async () => {
  try {
    S = await api("/api/data/status");
    renderNow();
    renderSpec();
  } catch (e) {
    document.querySelector(".admin").innerHTML = `<div class="error" style="margin:24px">${esc(e.message)}. <a href="/">Về dashboard</a></div>`;
  }
})();
