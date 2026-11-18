// Client API dùng chung (Blueprint 14.1): gặp 401 thì gọi /api/auth/refresh đúng MỘT lần rồi thử lại;
// refresh bị từ chối mới chuyển về /login. Cookie httpOnly do trình duyệt tự gửi – JS không giữ token.
let refreshing = null;

function refreshOnce() {
  refreshing ??= fetch("/api/auth/refresh", { method: "POST", credentials: "same-origin" })
    .then((r) => r.ok)
    .catch(() => false)
    .finally(() => setTimeout(() => (refreshing = null), 0));
  return refreshing;
}

export function toLogin() {
  const next = location.pathname + location.hash;
  location.href = "/login" + (next && next !== "/" ? "?next=" + encodeURIComponent(next) : "");
}

export async function api(path, opts = {}, extraHeaders = {}) {
  const run = () => fetch(path, {
    ...opts, credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...extraHeaders, ...(opts.headers || {}) },
  });
  let r = await run();
  if (r.status === 401 && !path.startsWith("/api/auth/")) {
    if (await refreshOnce()) r = await run();
    if (r.status === 401) { toLogin(); throw Object.assign(new Error("Phiên đăng nhập đã hết hạn"), { status: 401 }); }
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw Object.assign(new Error(data.detail || "Hệ thống đang bận, vui lòng thử lại."), { status: r.status });
  return data;
}

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// POST rồi tải file trả về (xuất Excel, chia sẻ HTML); cùng cơ chế refresh 1 lần như api().
export async function download(path, body, extraHeaders = {}) {
  const run = () => fetch(path, { method: "POST", credentials: "same-origin", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json", ...extraHeaders } });
  let r = await run();
  if (r.status === 401) {
    if (await refreshOnce()) r = await run();
    if (r.status === 401) { toLogin(); throw Object.assign(new Error("Phiên đăng nhập đã hết hạn"), { status: 401 }); }
  }
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw Object.assign(new Error(data.detail || "Không tạo được file, vui lòng thử lại."), { status: r.status });
  }
  const name = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "")?.[1] || "AI4BI";
  saveBlob(await r.blob(), name);
}

export function saveBlob(blob, name) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// [preview-v1] nút liên hệ mua bản Pro (Zalo / Viber / WhatsApp / email) – dùng ở thanh bên, khung xem trước, trang đăng nhập
export function contactLinks(c) {
  if (!c) return "";
  const a = (href, label, title) => `<a class="chip-link" href="${esc(href)}" target="_blank" rel="noopener" title="${esc(title)}">${label}</a>`;
  return `<span class="muted small">Liên hệ mua Pro: <b>${esc(c.phone)}</b></span><div>`
    + a(c.zalo, "Zalo", "Nhắn Zalo " + c.phone) + a(c.viber, "Viber", "Nhắn Viber " + c.phone)
    + a(c.whatsapp, "WhatsApp", "Nhắn WhatsApp " + c.phone) + a(c.mailto, "Email", c.email) + "</div>";
}
