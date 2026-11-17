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
