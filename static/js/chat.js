// Trợ lý AI: dùng chung cho panel bên phải và trang /chat. Câu trả lời render bằng đúng khối của dashboard.
// Blueprint 13.3: lịch sử theo tài khoản; xuất Excel / CSV; chia sẻ HTML; 👍 / 👎.
import { blocksGrid, blockTable, destroyCharts, esc, kpiGrid, md } from "./render.js";
import { saveBlob } from "./api.js";

export function createChat({ body, form, api, download, context, onAsk, tools, historyOn, exportOn = true }) {
  const input = form.querySelector("textarea");
  const history = [];
  const turns = [];          // [{ q, r }] của cuộc đang mở – dùng cho "Chia sẻ HTML"
  let last = null, busy = false, sessionId = null;

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

  function reset() {
    destroyCharts(body);
    body.innerHTML = "";
    history.length = 0;
    turns.length = 0;
    last = null;
    sessionId = null;
  }

  async function hello() {
    reset();
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

  const userBubble = (q) => {
    const u = document.createElement("div");
    u.className = "msg-user";
    u.textContent = q;
    return u;
  };

  async function send(q) {
    q = (q || "").trim();
    if (!q || busy) return;
    busy = true;
    onAsk && onAsk();
    body.append(userBubble(q));
    const wait = document.createElement("div");
    wait.className = "typing";
    wait.textContent = "Đang phân tích";
    body.append(wait);
    scroll();
    try {
      const r = await api("/api/chat/ask", { method: "POST", body: JSON.stringify({
        question: q, context: context(), history, last, session_id: sessionId }) });
      wait.remove();
      if (r.session_id) sessionId = r.session_id;
      body.append(renderAnswer(r, q));
      history.push(q);
      turns.push({ q, r });
      last = r.parts?.find((p) => p.metrics?.length || p.block) || last;
    } catch (e) {
      wait.remove();
      body.insertAdjacentHTML("beforeend", `<div class="error">${esc(e.message)}</div>`);
    }
    busy = false;
    scroll();
  }

  function renderAnswer(r, q) {
    const el = document.createElement("div");
    el.className = "msg-bot";
    if (r.answer) el.insertAdjacentHTML("beforeend", `<div class="md">${md(r.answer)}</div>`);
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
    if (exportOn && (r.kpis?.length || r.blocks?.length)) el.append(exportBar(r, q));
    if (r.log_id) el.append(feedbackBar(r.log_id));
    if (r.suggestions?.length) el.append(chips(r.suggestions));
    return el;
  }

  // ------------------------------------------------------------ xuất Excel / CSV / chia sẻ HTML một câu trả lời
  const payload = (r) => ({ answer: r.answer || "", kpis: r.kpis || [], blocks: r.blocks || [], notes: r.notes || [] });

  function exportBar(r, q) {
    const bar = document.createElement("div");
    bar.className = "xbar";
    bar.innerHTML = `<button type="button" data-x="xlsx" title="Tải bảng số liệu dạng Excel">⬇ Excel</button>
      <button type="button" data-x="csv" title="Tải bảng số liệu dạng CSV">⬇ CSV</button>
      <button type="button" data-x="html" title="Tải trang HTML để gửi cho người khác">⤴ Chia sẻ HTML</button>
      <span class="muted small" role="status"></span>`;
    bar.onclick = async (e) => {
      const x = e.target.dataset.x;
      if (!x) return;
      const status = bar.querySelector("[role=status]");
      status.textContent = "";
      try {
        if (x === "csv") saveBlob(new Blob(["﻿" + toCsv(r)], { type: "text/csv;charset=utf-8" }), fileName(q, "csv"));
        else if (x === "xlsx") await download("/api/chat/export-xlsx", { question: q, answer: payload(r) });
        else await download("/api/chat/share-log", { title: q, turns: [{ q, r: payload(r) }] });
      } catch (err) { status.textContent = err.message; }
    };
    return bar;
  }

  async function shareAll() {
    if (!turns.length) return alert("Chưa có câu hỏi nào để chia sẻ.");
    await download("/api/chat/share-log", { title: turns[0].q, turns: turns.map((t) => ({ q: t.q, r: t.r && payload(t.r) })) })
      .catch((e) => alert(e.message));
  }

  // 👍 / 👎 (Blueprint 12.5, 13.3): ghi vào nhật ký chatbot để rà soát; 👎 cho phép ghi chú ngắn
  function feedbackBar(id) {
    const bar = document.createElement("div");
    bar.className = "fb";
    bar.innerHTML = `<span class="muted small">Câu trả lời có hữu ích?</span>
      <button type="button" data-v="1" aria-label="Hữu ích" title="Hữu ích">👍</button>
      <button type="button" data-v="-1" aria-label="Chưa đúng / chưa đủ" title="Chưa đúng / chưa đủ">👎</button>`;
    bar.onclick = async (e) => {
      const v = +e.target.dataset.v;
      if (!v) return;
      const note = v < 0 ? (prompt("Câu trả lời chưa đúng hoặc chưa đủ ở đâu? (không bắt buộc)") || "") : "";
      try {
        await api("/api/chat/feedback", { method: "POST", body: JSON.stringify({ log_id: id, vote: v, note }) });
        bar.querySelectorAll("button").forEach((b) => b.classList.toggle("on", +b.dataset.v === v));
        bar.querySelector("span").textContent = "Cảm ơn bạn đã phản hồi";
      } catch (err) { bar.querySelector("span").textContent = err.message; }
    };
    return bar;
  }

  // ------------------------------------------------------------ lịch sử theo tài khoản (/api/chat/sessions)
  async function openSession(id) {
    const s = await api(`/api/chat/sessions/${encodeURIComponent(id)}`);
    reset();
    sessionId = s.id;
    for (const m of s.messages) {
      body.append(userBubble(m.q));
      history.push(m.q);
      turns.push({ q: m.q, r: m.r });
      if (m.r) {
        body.append(renderAnswer(m.r, m.q));
        last = m.r.parts?.find((p) => p.metrics?.length || p.block) || last;
      } else {
        const el = document.createElement("div");
        el.className = "msg-bot";
        el.innerHTML = `<div class="note">Quyền xem dữ liệu của bạn đã thay đổi nên câu trả lời cũ được ẩn.</div>`;
        const b = document.createElement("button");
        b.type = "button";
        b.className = "link-btn";
        b.textContent = "Hỏi lại với quyền hiện tại";
        b.onclick = () => send(m.q);
        el.append(b);
        body.append(el);
      }
    }
    history.splice(0, Math.max(0, history.length - 4));
    scroll();
  }

  function showHistory() {
    historyDialog(api, (id) => openSession(id).catch((e) => alert(e.message)), () => sessionId, () => hello());
  }

  if (tools) {
    tools.innerHTML = `<button type="button" class="icon-btn" data-t="new" title="Cuộc trò chuyện mới">＋</button>
      ${historyOn ? `<button type="button" class="icon-btn" data-t="hist" title="Lịch sử trò chuyện">🕘</button>` : ""}
      ${exportOn ? `<button type="button" class="icon-btn" data-t="share" title="Chia sẻ cả cuộc trò chuyện (HTML)">⤴</button>` : ""}`;
    tools.onclick = (e) => {
      const t = e.target.closest("[data-t]")?.dataset.t;
      if (t === "new") hello();
      else if (t === "hist") showHistory();
      else if (t === "share") shareAll();
    };
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
  return { send, hello, openSession };
}

// ------------------------------------------------------------ CSV (làm ngay trên trình duyệt)
const NUM = ["money", "pct", "signed_pct", "number", "days", "times"];
const csvCell = (v) => {
  let s = v == null ? "" : String(v);
  if (/^[=+\-@\t\r]/.test(s) && typeof v !== "number") s = "'" + s;   // chống chèn công thức khi mở bằng Excel
  return /[",\n;]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export function toCsv(r) {
  const lines = [];
  const row = (cells) => lines.push(cells.map(csvCell).join(","));
  if (r.kpis?.length) {
    row(["Chỉ số", "Giá trị", "Hiển thị"]);
    r.kpis.forEach((k) => row([k.label, k.value, k.display]));
  }
  for (const b of r.blocks || []) {
    const { columns, rows } = blockTable(b);
    if (lines.length) lines.push("");
    row([b.title]);
    row(columns.map((c) => (c.format === "pct" || c.format === "signed_pct" ? `${c.label} (%)` : c.label)));
    rows.forEach((x) => row(columns.map((c) => {
      const v = x[c.key];
      if (typeof v === "number" && (c.format === "pct" || c.format === "signed_pct")) return Math.round(v * 10000) / 100;
      return NUM.includes(c.format) ? v : v ?? "";
    })));
  }
  return lines.join("\r\n");
}

function fileName(q, ext) {
  const s = (q || "tra_loi").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[đĐ]/g, "d")
    .replace(/[^A-Za-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 60) || "tra_loi";
  const d = new Date(), p = (n) => String(n).padStart(2, "0");
  return `AI4BI_${s}_${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}_${p(d.getHours())}${p(d.getMinutes())}.${ext}`;
}

// ------------------------------------------------------------ hộp thoại lịch sử (một hộp dùng chung cho panel và trang /chat)
let dlg = null;
async function historyDialog(api, onOpen, currentId, onCleared) {
  if (!dlg) {
    dlg = document.createElement("dialog");
    dlg.className = "dlg dlg-hist";
    dlg.innerHTML = `<div class="hist-head"><h2>Lịch sử trò chuyện</h2><button type="button" class="icon-btn" data-h="close" aria-label="Đóng">✕</button></div>
      <p class="muted small">Chỉ bạn xem được lịch sử của mình. Giữ tối đa 100 cuộc gần nhất.</p>
      <ul class="hist-list"></ul>
      <div class="dlg-actions"><button type="button" class="link-btn danger" data-h="clear">Xoá toàn bộ lịch sử</button></div>`;
    document.body.append(dlg);
  }
  const list = dlg.querySelector(".hist-list");
  const draw = async () => {
    list.innerHTML = `<li class="muted">Đang tải…</li>`;
    try {
      const rows = await api("/api/chat/sessions");
      list.innerHTML = rows.length ? rows.map((s) => `<li class="${s.id === currentId() ? "on" : ""}">
          <button type="button" class="hist-open" data-id="${esc(s.id)}"><b>${esc(s.title)}</b>
            <span class="muted small">${s.n} câu · ${esc(new Date(s.updated_at).toLocaleString("vi-VN"))}</span></button>
          <button type="button" class="icon-btn" data-ren="${esc(s.id)}" title="Đổi tên">✎</button>
          <button type="button" class="icon-btn" data-del="${esc(s.id)}" title="Xoá">🗑</button></li>`).join("")
        : `<li class="muted">Chưa có cuộc trò chuyện nào.</li>`;
    } catch (e) { list.innerHTML = `<li class="error">${esc(e.message)}</li>`; }
  };
  dlg.onclick = async (e) => {
    const t = e.target.closest("button");
    if (!t) return;
    try {
      if (t.dataset.h === "close") dlg.close();
      else if (t.dataset.id) { dlg.close(); onOpen(t.dataset.id); }
      else if (t.dataset.ren) {
        const cur = t.closest("li").querySelector("b").textContent;
        const title = prompt("Tên mới cho cuộc trò chuyện:", cur);
        if (title && title.trim() && title !== cur) {
          await api(`/api/chat/sessions/${encodeURIComponent(t.dataset.ren)}`, { method: "PATCH", body: JSON.stringify({ title }) });
          draw();
        }
      } else if (t.dataset.del) {
        if (!confirm("Xoá cuộc trò chuyện này? Không khôi phục được.")) return;
        await api(`/api/chat/sessions/${encodeURIComponent(t.dataset.del)}`, { method: "DELETE" });
        if (t.dataset.del === currentId()) onCleared();
        draw();
      } else if (t.dataset.h === "clear") {
        if (!confirm("Xoá TOÀN BỘ lịch sử trò chuyện của bạn? Không khôi phục được.")) return;
        await api("/api/chat/sessions", { method: "DELETE" });
        onCleared();
        draw();
      }
    } catch (err) { alert(err.message); }
  };
  draw();
  dlg.showModal();
}
