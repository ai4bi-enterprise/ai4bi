// Khung ứng dụng: menu, bộ chọn kỳ / đơn vị / vai trò, định tuyến, trang dashboard và trang chat.
import { blocksGrid, destroyCharts, esc, insightsCard, kpiGrid, redrawAll } from "./render.js";
import { createChat } from "./chat.js";
import { api as rawApi, download as rawDownload, toLogin } from "./api.js";

const $ = (s) => document.querySelector(s);
const store = {
  get: (k, d) => { try { return JSON.parse(localStorage.getItem("ai4bi." + k)) ?? d; } catch { return d; } },
  set: (k, v) => { try { localStorage.setItem("ai4bi." + k, JSON.stringify(v)); } catch { /* chế độ riêng tư */ } },
};
const ICONS = { home: "⌂", trend: "↗", box: "▦", pnl: "≡", scale: "⚖", cash: "₫", chat: "✦" };
const S = { meta: null, user: store.get("user", null), period: store.get("period", null), org: "", module: "overview" };

// Chỉ chế độ demo mới gửi X-Demo-User; chế độ đăng nhập dùng cookie httpOnly (api.js tự refresh 1 lần).
const demoHeader = () => (S.meta?.auth?.mode === "password" || !S.user ? {} : { "X-Demo-User": S.user });
const api = (path, opts = {}) => rawApi(path, opts, demoHeader());
const download = (path, body) => rawDownload(path, body, demoHeader());

// ------------------------------------------------------------ tài khoản
async function signOut(all) {
  await rawApi(all ? "/api/auth/logout-all" : "/api/auth/logout", { method: "POST" }).catch(() => {});
  location.href = "/login";
}
$("#logoutBtn").onclick = () => signOut(false);
$("#logoutAllBtn").onclick = () => signOut(true);
$("#pwBtn").onclick = () => { $("#pwForm").reset(); $("#pwMsg").textContent = ""; $("#pwDialog").showModal(); };
$("#pwForm").addEventListener("submit", async (e) => {
  if (e.submitter?.value !== "ok") return;
  e.preventDefault();
  const f = e.target;
  if (f.new_password.value !== f.confirm.value) { $("#pwMsg").textContent = "Hai lần nhập mật khẩu mới không khớp."; return; }
  try {
    await rawApi("/api/auth/change-password", { method: "POST", body: JSON.stringify({
      current_password: f.current_password.value, new_password: f.new_password.value }) });
    $("#pwDialog").close();
    f.reset();
  } catch (err) { $("#pwMsg").textContent = err.message; }
});
// Quyền thay đổi (vai trò, phạm vi, khoá tài khoản) -> tải lại trang (Blueprint 14.3: kiểm mỗi 30 giây)
function watchPermissions() {
  setInterval(async () => {
    if (document.hidden) return;
    try {
      const me = await rawApi("/api/auth/me");
      if (me.perm_version !== S.meta.auth.perm_version || me.must_change_password) location.reload();
    } catch { /* api.js đã xử lý 401 */ }
  }, 30000);
}

// ------------------------------------------------------------ thông báo hệ thống (Blueprint 10.3 announcements, 15)
// Có thông báo chưa xem / chưa xác nhận thì tự mở khi vào trang; nội dung là văn bản thuần, luôn escape.
const ANN = { items: [] };
const when = (iso) => new Date(iso).toLocaleString("vi-VN", { dateStyle: "short", timeStyle: "short" });
const pendingAnn = () => ANN.items.filter((a) => !a.seen_at || (a.require_ack && !a.acked_at));

function annCount() {
  const n = pendingAnn().length;
  $("#annBtn").hidden = !ANN.items.length;
  $("#annCount").hidden = !n;
  $("#annCount").textContent = n;
}

function renderAnn() {
  $("#annList").innerHTML = ANN.items.map((a) => `<article class="ann-item ${a.level === "important" ? "important" : ""} ${a.seen_at ? "" : "new"}" data-id="${a.id}">
    <h3>${esc(a.title)}${a.level === "important" ? ` <span class="tag warn">Quan trọng</span>` : ""}</h3>
    <p>${esc(a.body)}</p>
    <div class="ann-foot muted small">${when(a.starts_at)}
      ${a.require_ack ? (a.acked_at ? `<span class="tag good">Đã xác nhận ${when(a.acked_at)}</span>`
        : `<button type="button" class="btn-ai" data-ack>Tôi đã đọc và xác nhận</button>`) : ""}</div></article>`).join("")
    || `<p class="muted">Không có thông báo.</p>`;
  annCount();
}

function openAnn() {
  renderAnn();
  if (!$("#annDlg").open) $("#annDlg").showModal();
  for (const a of ANN.items.filter((x) => !x.seen_at)) {   // mở ra = đã xem; giữ viền "mới" đến khi đóng
    api(`/api/announcements/${a.id}/seen`, { method: "POST" }).then(() => { a.seen_at = new Date().toISOString(); annCount(); }).catch(() => {});
  }
}

async function loadAnn(auto) {
  try { ANN.items = await api("/api/announcements"); } catch { return; }
  annCount();
  if (auto && pendingAnn().length) openAnn();
}

$("#annBtn").onclick = openAnn;
$("#annList").onclick = async (e) => {
  if (e.target.dataset.ack === undefined) return;
  const a = ANN.items.find((x) => x.id === +e.target.closest("[data-id]").dataset.id);
  e.target.disabled = true;
  try {
    await api(`/api/announcements/${a.id}/ack`, { method: "POST" });
    a.seen_at ??= new Date().toISOString();
    a.acked_at = new Date().toISOString();
    renderAnn();
  } catch (err) { e.target.disabled = false; alert(err.message); }
};

// ------------------------------------------------------------ giao diện sáng/tối
function applyTheme(t) {
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}
applyTheme(store.get("theme", null));
$("#themeBtn").onclick = () => {
  const dark = document.documentElement.dataset.theme
    ? document.documentElement.dataset.theme === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  const t = dark ? "light" : "dark";
  store.set("theme", t);
  applyTheme(t);
  redrawAll();
};
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", redrawAll);

// ------------------------------------------------------------ meta, menu, bộ lọc
async function loadMeta() {
  S.meta = await api("/api/meta");
  S.user = S.meta.user.username;
  store.set("user", S.user);
  $("#brandName").textContent = S.meta.company;
  document.title = `AI4BI – ${S.meta.company}`;
  $("#who").innerHTML = `<b>${esc(S.meta.user.display_name)}</b>${esc(S.meta.user.role)} · Phạm vi: ${esc(S.meta.user.scope)}`;
  $("#userSelWrap").hidden = !S.meta.demo_users.length;
  $("#acct").hidden = S.meta.auth?.mode !== "password";
  $("#adminLink").hidden = !S.meta.auth?.is_admin;
  $("#teamLink").hidden = !S.meta.team_log;
  if (S.meta.auth?.email) $("#who").insertAdjacentHTML("beforeend", `<br><span class="muted">${esc(S.meta.auth.email)}</span>`);
  $("#userSel").innerHTML = S.meta.demo_users.map((u) => `<option value="${u.username}" ${u.username === S.user ? "selected" : ""}>${esc(u.display_name)}</option>`).join("");
  const items = S.meta.menu.flatMap((g) => g.items);
  $("#menu").innerHTML = S.meta.menu.map((g) => `<div class="nav-group">${esc(g.group)}</div>` +
    g.items.map((i) => i.locked
      ? `<a class="nav-item locked" href="${esc(S.meta.edition.upgrade_url)}" target="_blank" rel="noopener" title="Có ở bản AI4BI Pro"><span class="nav-ico">${ICONS[i.icon] || "•"}</span>${esc(i.label)}<span class="pro-tag">🔒 PRO</span></a>`
      : `<a class="nav-item" href="#/${i.key}" data-key="${i.key}"><span class="nav-ico">${ICONS[i.icon] || "•"}</span>${esc(i.label)}</a>`).join("")).join("") +
    (S.meta.can_chat ? `<div class="nav-group">Trợ lý AI</div><a class="nav-item" href="#/chat" data-key="chat"><span class="nav-ico">✦</span>Hỏi đáp dữ liệu</a>` : "") +
    (S.meta.data?.upload ? `<div class="nav-group">Dữ liệu</div><a class="nav-item" href="/data"><span class="nav-ico">⬆</span>Dữ liệu của tôi${S.meta.data.active === "user" ? "" : '<span class="pro-tag">MẪU</span>'}</a>` : "");
  $("#chatToggle").hidden = !S.meta.can_chat;
  S.items = items.filter((i) => !i.locked);
  const lite = S.meta.edition?.name === "lite";
  $("#editionTag").hidden = !lite;
  $("#upgrade").hidden = !lite;
  if (lite) {
    $("#upgradePro").href = S.meta.edition.upgrade_url;
    $("#upgradeDemo").href = S.meta.edition.contact_url;
  }
  // kỳ
  const { latest, first } = S.meta;
  const dataKey = `${S.meta.data?.active || "demo"}|${S.meta.source}`;   // đổi bộ dữ liệu -> về kỳ mới nhất của bộ mới
  if (store.get("dataKey", null) !== dataKey) { S.period = null; store.set("dataKey", dataKey); }
  if (!S.period || S.period.year * 100 + S.period.month > latest.year * 100 + latest.month) S.period = { ...latest, mode: "quarter" };
  $("#yearSel").innerHTML = Array.from({ length: latest.year - first.year + 1 }, (_, i) => latest.year - i).map((y) => `<option>${y}</option>`).join("");
  // đơn vị (đã lọc theo phạm vi ở backend)
  let opts = `<option value="">Toàn ${S.meta.user.scope === "Toàn bộ" ? "công ty" : "phạm vi"}</option>`;
  for (const [region, orgs] of Object.entries(S.meta.org_tree)) {
    opts += `<optgroup label="${esc(region)}">${Object.keys(S.meta.org_tree).length > 1 ? `<option value="region:${esc(region)}">${esc(region)} (cả khu vực)</option>` : ""}
      ${orgs.map((o) => `<option value="org:${esc(o)}">${esc(o)}</option>`).join("")}</optgroup>`;
  }
  $("#orgSel").innerHTML = opts;
  S.org = "";
  syncControls();
}

function syncControls() {
  const { latest } = S.meta;
  $("#yearSel").value = S.period.year;
  const maxM = S.period.year === latest.year ? latest.month : 12;
  if (S.period.month > maxM) S.period.month = maxM;
  $("#monthSel").innerHTML = Array.from({ length: maxM }, (_, i) => i + 1).map((m) => `<option value="${m}">Tháng ${m}</option>`).join("");
  $("#monthSel").value = S.period.month;
  document.querySelectorAll("#modeSeg button").forEach((b) => b.classList.toggle("on", b.dataset.mode === S.period.mode));
  store.set("period", S.period);
}

$("#modeSeg").onclick = (e) => { if (e.target.dataset.mode) { S.period.mode = e.target.dataset.mode; syncControls(); route(); } };
$("#yearSel").onchange = (e) => { S.period.year = +e.target.value; syncControls(); route(); };
$("#monthSel").onchange = (e) => { S.period.month = +e.target.value; syncControls(); route(); };
$("#orgSel").onchange = (e) => { S.org = e.target.value; route(); };
$("#userSel").onchange = async (e) => {
  S.user = e.target.value;
  store.set("user", S.user);
  await loadMeta();
  drawerChat.hello();
  location.hash = "#/" + (S.items[0]?.key || "chat");
  route();
};

function filters() {
  const [k, v] = S.org.split(":");
  return k && v ? { [k]: [v] } : {};
}
function periodLabel() {
  const { year, month, mode } = S.period;
  return mode === "quarter" ? `Q${Math.ceil(month / 3)}/${year}${month % 3 ? ` (đến T${month})` : ""}` : mode === "ytd" ? `Lũy kế T1–T${month}/${year}` : `T${month}/${year}`;
}

// ------------------------------------------------------------ trang dashboard
async function renderDashboard(module) {
  const el = $("#content");
  destroyCharts(el);
  const item = S.items.find((i) => i.key === module);
  $("#orgSel").hidden = !item?.org_filter;
  el.innerHTML = `<div class="skeleton"></div><div class="skeleton" style="height:320px"></div>`;
  const q = new URLSearchParams({ year: S.period.year, month: S.period.month, mode: S.period.mode });
  if (item?.org_filter && S.org) q.set(S.org.split(":")[0], S.org.split(":")[1]);
  try {
    const d = await api(`/api/dashboard/${module}?${q}`);
    destroyCharts(el);
    el.innerHTML = `<div class="page-head"><h1>${esc(d.title)}</h1>
      <span class="chip">${esc(d.period.label)}</span>
      ${item?.org_filter && S.org ? `<span class="chip">${esc(S.org.split(":")[1])}</span>` : ""}
      <span class="muted small">Dữ liệu đến ${esc(new Date(S.meta.as_of).toLocaleDateString("vi-VN"))}</span></div>`;
    if (S.meta.data?.upload && S.meta.data.active !== "user")   // đang xem số mẫu: mời nạp dữ liệu thật
      el.insertAdjacentHTML("beforeend", `<div class="data-hint"><span>Đang xem <b>dữ liệu mẫu</b>. Muốn xem số của doanh nghiệp bạn?</span>
        <a class="btn-ai" href="/data">Nạp dữ liệu của tôi</a></div>`);
    if (d.kpis.length) el.append(kpiGrid(d.kpis));
    if (d.insights.length) el.append(insightsCard(d.insights));
    el.append(blocksGrid(d.blocks));
    el.insertAdjacentHTML("beforeend", `<div class="foot">${d.notes.map((n) => `ⓘ ${esc(n)}<br>`).join("")}
      Nguồn dữ liệu${S.meta.data?.active === "user" ? "" : " mẫu"}: ${esc(S.meta.source)}. ${esc(S.meta.note)}</div>`);
  } catch (e) {
    el.innerHTML = `<div class="error">${esc(e.message)}</div>`;
  }
}

// ------------------------------------------------------------ chat
const chatContext = () => ({ ...S.period, filters: filters(), module: S.module });
const drawer = $("#chatDrawer");
const openDrawer = () => { drawer.classList.add("open"); $("#chatCtx").textContent = `Ngữ cảnh: ${periodLabel()}${S.org ? " · " + S.org.split(":")[1] : ""}`; };
const chatOpts = () => ({ api, download, context: chatContext, historyOn: !!S.meta?.chat_history });
let drawerChat = null;
$("#chatToggle").onclick = () => (drawer.classList.contains("open") ? drawer.classList.remove("open") : openDrawer());
$("#chatClose").onclick = () => drawer.classList.remove("open");
let pageChat = null;
window.addEventListener("ai4bi:ask", (e) => (S.module === "chat" && pageChat ? pageChat : drawerChat).send(e.detail));

function renderChatPage() {
  const el = $("#content");
  destroyCharts(el);
  $("#orgSel").hidden = false;
  drawer.classList.remove("open");
  el.innerHTML = `<div class="page-head"><h1>✦ Trợ lý AI</h1><span class="chip">Ngữ cảnh: ${esc(periodLabel())}</span>
    <span class="muted small">${S.meta.llm ? "LLM đang bật – phân tích sâu có kiểm số" : "Chế độ không LLM – hiểu câu bằng luật, số do code tính"}</span>
    <span class="chat-tools push" id="chatToolsPage"></span></div>
    <div class="chat-page"><div class="chat-body" id="chatBodyPage"></div>
    <form class="chat-input" id="chatFormPage"><textarea rows="1" placeholder="VD: Vì sao lợi nhuận quý này thay đổi so với cùng kỳ?" maxlength="500"></textarea><button type="submit" aria-label="Gửi">➤</button></form></div>`;
  pageChat = createChat({ ...chatOpts(), body: $("#chatBodyPage"), form: $("#chatFormPage"), tools: $("#chatToolsPage") });
  pageChat.hello();
}

// ------------------------------------------------------------ định tuyến
function route() {
  const key = location.hash.replace(/^#\//, "") || S.items[0]?.key || "chat";
  S.module = key;
  document.querySelectorAll(".nav-item").forEach((a) => a.classList.toggle("active", a.dataset.key === key));
  $("#sidebar").classList.remove("open");
  $("#scrim").classList.remove("open");
  if (key === "chat" && S.meta.can_chat) return renderChatPage();
  if (!S.items.some((i) => i.key === key)) { location.hash = "#/" + (S.items[0]?.key || "chat"); return; }
  renderDashboard(key);
}
window.addEventListener("hashchange", route);
$("#menuBtn").onclick = () => { $("#sidebar").classList.add("open"); $("#scrim").classList.add("open"); };
$("#scrim").onclick = () => { $("#sidebar").classList.remove("open"); $("#scrim").classList.remove("open"); };

(async () => {
  try {
    await loadMeta();
  } catch (e) {
    if (e.status === 401) return;            // api.js đã chuyển về /login
    if (e.status === 403) return toLogin();  // ví dụ: phải đổi mật khẩu trước
    S.user = null;
    store.set("user", null);
    await loadMeta();
  }
  if (S.meta.auth?.mode === "password") watchPermissions();
  if (S.meta.announcements) {
    loadAnn(true);
    setInterval(() => { if (!document.hidden) loadAnn(false); }, 300000);   // thông báo mới: cập nhật số trên chuông
  }
  drawerChat = createChat({ ...chatOpts(), body: $("#chatBodyDrawer"), form: $("#chatFormDrawer"), onAsk: openDrawer,
    tools: $("#chatToolsDrawer") });
  drawerChat.hello();
  route();
})();
