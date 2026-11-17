// Trợ lý AI: dùng chung cho panel bên phải và trang /chat. Câu trả lời render bằng đúng khối của dashboard.
import { blocksGrid, destroyCharts, esc, kpiGrid, md } from "./render.js";

export function createChat({ body, form, api, context, onAsk }) {
  const input = form.querySelector("textarea");
  const history = [];
  let last = null, busy = false;

  const scroll = () => (body.scrollTop = body.scrollHeight);
  const chips = (list) => {
    const el = document.createElement("div");
    el.className = "chips";
    list.forEach((q) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = q;
      b.onclick = () => send(q);
      el.append(b);
    });
    return el;
  };

  async function hello() {
    destroyCharts(body);
    body.innerHTML = "";
    history.length = 0;
    last = null;
    try {
      const w = await api("/api/chat/welcome");
      const el = document.createElement("div");
      el.className = "msg-bot hello";
      el.innerHTML = `<div class="md"><p><b>Xin chào!</b> Tôi là Trợ lý AI4BI của ${esc(w.company)}.</p>
        <p>Hỏi tôi bằng tiếng Việt tự nhiên – số liệu luôn khớp dashboard và trong phạm vi dữ liệu của bạn.</p></div>`;
      el.append(chips(w.suggestions));
      body.append(el);
    } catch (e) {
      body.innerHTML = `<div class="error">${esc(e.message)}</div>`;
    }
  }

  async function send(q) {
    q = (q || "").trim();
    if (!q || busy) return;
    busy = true;
    onAsk && onAsk();
    const u = document.createElement("div");
    u.className = "msg-user";
    u.textContent = q;
    body.append(u);
    const wait = document.createElement("div");
    wait.className = "typing";
    wait.textContent = "Đang phân tích";
    body.append(wait);
    scroll();
    try {
      const r = await api("/api/chat/ask", { method: "POST", body: JSON.stringify({ question: q, context: context(), history, last }) });
      wait.remove();
      body.append(renderAnswer(r));
      history.push(q);
      last = r.parts?.find((p) => p.metrics?.length || p.block) || last;
    } catch (e) {
      wait.remove();
      body.insertAdjacentHTML("beforeend", `<div class="error">${esc(e.message)}</div>`);
    }
    busy = false;
    scroll();
  }

  function renderAnswer(r) {
    const el = document.createElement("div");
    el.className = "msg-bot";
    el.insertAdjacentHTML("beforeend", `<div class="md">${md(r.answer || "")}</div>`);
    if (r.kpis?.length) el.append(kpiGrid(r.kpis));
    if (r.blocks?.length) el.append(blocksGrid(r.blocks.map((b) => ({ ...b, width: "full", ask: null }))));
    const meta = document.createElement("div");
    meta.className = "msg-meta";
    const lane = r.lane === "deep" ? "Phân tích sâu (LLM + kiểm số)" : "Trả lời nhanh (tính bằng code)";
    meta.innerHTML = `<span>${esc(lane)} · hiểu câu: ${r.parser === "llm" ? "LLM" : "luật"} · ${r.elapsed_ms ?? 0} ms</span>
      ${(r.notes || []).map((n) => `<span>ⓘ ${esc(n)}</span>`).join("")}
      ${r.detail ? `<details><summary>Số liệu chi tiết (do code tính)</summary><div class="md">${md(r.detail)}</div></details>` : ""}
      ${r.sql?.length ? `<details><summary>Xem SQL đã chạy (${r.sql.length})</summary><pre>${esc(r.sql.join(";\n\n"))}</pre></details>` : ""}`;
    el.append(meta);
    if (r.suggestions?.length) el.append(chips(r.suggestions));
    return el;
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value;
    input.value = "";
    input.style.height = "";
    send(q);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); }
  });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 120) + "px"; });
  return { send, hello };
}
