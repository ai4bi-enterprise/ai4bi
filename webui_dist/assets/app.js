/* AI4BI Chatbot – Community WebUI (vanilla JS, không phụ thuộc thư viện ngoài) */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const chat = $("#chat"), input = $("#input"), form = $("#form"), sendBtn = $("#send");
  const bodToggle = $("#bodMode");
  const COLORS = { main: "#2563eb", prior: "#94a3b8", secondary: "#10b981", line: "#f59e0b" };
  const MODEL_LABEL = { sales: "Bán hàng", finance: "Tài chính & Dòng tiền", receivables: "Công nợ" };
  const FMT_LABEL = { money: "VND", pct: "%", qty: "SL" };
  let busy = false;
  const ICON = {
    chart: '<svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M4 20h16v2H2V2h2zm3-3V9h3v8zm5 0V5h3v12zm5 0v-6h3v6z"/></svg>',
    brain: '<svg viewBox="0 0 24 24" width="14" height="14"><path fill="currentColor" d="M12 2a7 7 0 0 0-4 12.74V18a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-3.26A7 7 0 0 0 12 2m2 16h-4v-1h4zm1.3-4.9-.3.2V16H9v-2.7l-.3-.2A5 5 0 1 1 15.3 13.1M10 22h4v1h-4z"/></svg>',
    nhan_xet: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 4.5C7 4.5 2.7 7.6 1 12c1.7 4.4 6 7.5 11 7.5s9.3-3.1 11-7.5c-1.7-4.4-6-7.5-11-7.5M12 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10m0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6"/></svg>',
    danh_gia: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2 1 21h22zm1 16h-2v-2h2zm0-4h-2V9h2z"/></svg>',
    ket_luan: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M9 16.2 4.8 12l-1.4 1.4L9 19 21 7l-1.4-1.4z"/></svg>',
    de_xuat: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M9 21h6v-1H9zm3-19a7 7 0 0 0-4 12.7V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.3A7 7 0 0 0 12 2"/></svg>',
    goi_y: '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M11 18h2v-2h-2zm1-16a10 10 0 1 0 0 20 10 10 0 0 0 0-20m0 18a8 8 0 1 1 0-16 8 8 0 0 1 0 16m0-14a4 4 0 0 0-4 4h2a2 2 0 1 1 4 0c0 2-3 1.75-3 5h2c0-2.25 3-2.5 3-5a4 4 0 0 0-4-4"/></svg>',
  };

  // ------------------------------------------------------------ helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[\s(])_(.+?)_(?=[\s).,]|$)/g, "$1<em>$2</em>");

  function md(text) {
    const out = []; let list = null;
    const close = () => { if (list) { out.push(`</${list}>`); list = null; } };
    for (const raw of String(text).split("\n")) {
      const line = raw.trimEnd();
      let m;
      if (!line.trim()) { close(); continue; }
      if ((m = line.match(/^###\s+(.*)/))) { close(); out.push(`<h3>${inline(m[1])}</h3>`); continue; }
      if ((m = line.match(/^\s*[-•]\s+(.*)/))) { if (list !== "ul") { close(); out.push("<ul>"); list = "ul"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
      if ((m = line.match(/^\s*\d+\.\s+(.*)/))) { if (list !== "ol") { close(); out.push("<ol>"); list = "ol"; } out.push(`<li>${inline(m[1])}</li>`); continue; }
      close(); out.push(`<p>${inline(line)}</p>`);
    }
    close();
    return out.join("");
  }

  const nf = (v, d = 2) => v == null ? "" : Number(v).toLocaleString("vi-VN", { maximumFractionDigits: d });

  // ------------------------------------------------------------ chart (SVG)
  function niceStep(range, ticks = 5) {
    const raw = range / ticks || 1, p = Math.pow(10, Math.floor(Math.log10(raw)));
    const n = raw / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }

  function chartSVG(spec) {
    const W = 760, H = 270, L = 56, R = 48, T = 26;
    const labels = spec.labels || [];
    const rotate = labels.length > 6 && Math.max(...labels.map((l) => String(l).length)) > 8;
    const B = rotate ? 86 : 40;
    const bars = spec.series.filter((s) => s.role !== "line");
    const lines = spec.series.filter((s) => s.role === "line");
    const asLine = spec.kind === "line";
    const vals = bars.flatMap((s) => s.values).filter((v) => v != null);
    let max = Math.max(0, ...vals), min = Math.min(0, ...vals);
    if (max === min) max = min + 1;
    const step = niceStep(max - min);
    max = Math.ceil(max / step) * step; min = Math.floor(min / step) * step;
    const ih = H - T - B, iw = W - L - R;
    const y = (v) => T + ih - ((v - min) / (max - min)) * ih;
    const n = labels.length, bw = iw / n;
    let g = "";
    for (let v = min; v <= max + 1e-9; v += step) {
      g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="${v === 0 ? "#94a3b8" : "#eef2f7"}"/>`;
      g += `<text x="${L - 8}" y="${y(v) + 4}" text-anchor="end" font-size="11" fill="#64748b">${nf(v, 2)}</text>`;
    }
    const showVals = n * Math.max(1, bars.length) <= 16;
    if (asLine) {
      bars.forEach((s, si) => {
        const color = COLORS[s.role] || COLORS.main;
        const pts = s.values.map((v, i) => v == null ? null : [L + bw * i + bw / 2, y(v)]).filter(Boolean);
        g += `<polyline fill="none" stroke="${color}" stroke-width="2.5" points="${pts.map((p) => p.join(",")).join(" ")}"/>`;
        pts.forEach((p, i) => {
          g += `<circle cx="${p[0]}" cy="${p[1]}" r="3.5" fill="#fff" stroke="${color}" stroke-width="2"/>`;
          if (n <= 12 && si === bars.length - 1) g += `<text x="${p[0]}" y="${p[1] - 9}" text-anchor="middle" font-size="10.5" fill="#334155">${nf(s.values[i], 1)}</text>`;
        });
      });
    } else {
      const gw = bw * 0.72, each = gw / Math.max(1, bars.length);
      bars.forEach((s, si) => {
        const color = COLORS[s.role] || COLORS.main;
        s.values.forEach((v, i) => {
          if (v == null) return;
          const x = L + bw * i + (bw - gw) / 2 + each * si + 1;
          const y0 = y(Math.max(0, v)), h = Math.abs(y(v) - y(0));
          g += `<rect x="${x}" y="${y0}" width="${Math.max(2, each - 2)}" height="${Math.max(1, h)}" rx="3" fill="${color}"><title>${esc(s.name)}: ${nf(v)}</title></rect>`;
          if (showVals) g += `<text x="${x + (each - 2) / 2}" y="${v >= 0 ? y0 - 5 : y0 + h + 12}" text-anchor="middle" font-size="10.5" fill="#334155">${nf(v, v >= 100 ? 1 : 2)}</text>`;
        });
      });
    }
    if (lines.length) {
      const lv = lines.flatMap((s) => s.values).filter((v) => v != null);
      const lmax = Math.max(...lv) * 1.15 || 1, lmin = Math.min(0, ...lv);
      const ly = (v) => T + ih - ((v - lmin) / (lmax - lmin)) * ih;
      lines.forEach((s) => {
        const pts = s.values.map((v, i) => v == null ? null : [L + bw * i + bw / 2, ly(v)]).filter(Boolean);
        g += `<polyline fill="none" stroke="${COLORS.line}" stroke-width="2.5" points="${pts.map((p) => p.join(",")).join(" ")}"/>`;
        pts.forEach((p, i) => {
          g += `<circle cx="${p[0]}" cy="${p[1]}" r="4" fill="#fff" stroke="${COLORS.line}" stroke-width="2.5"/>`;
          const t = `${nf(s.values[i], 1)}%`, w = t.length * 6.6 + 8;
          g += `<rect x="${p[0] - w / 2}" y="${p[1] - 24}" width="${w}" height="16" rx="5" fill="#fffbeb" stroke="#fcd34d"/>`;
          g += `<text x="${p[0]}" y="${p[1] - 12}" text-anchor="middle" font-size="10.5" font-weight="700" fill="#b45309">${t}</text>`;
        });
      });
      g += `<text x="${W - R + 6}" y="${T + 4}" font-size="10.5" fill="#b45309">%</text>`;
    }
    labels.forEach((lb, i) => {
      const x = L + bw * i + bw / 2;
      g += rotate
        ? `<text transform="translate(${x},${H - B + 14}) rotate(-35)" text-anchor="end" font-size="11" fill="#475569">${esc(lb)}</text>`
        : `<text x="${x}" y="${H - B + 18}" text-anchor="middle" font-size="11.5" fill="#475569">${esc(lb)}</text>`;
    });
    return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(spec.title)}">${g}</svg>`;
  }

  // ------------------------------------------------------------ insights
  const SEV_CLASS = { good: "st-good", neutral: "st-neutral", warn: "st-warn", bad: "st-bad", none: "st-none" };

  function renderInsights(b) {
    const sec = (key, title, body, full) =>
      `<div class="ins-sec k-${key} ${full ? "full" : ""} ${key === "ket_luan" ? "s-" + esc(b.status) : ""}">
         <div class="ins-title"><span class="ic">${ICON[key] || ""}</span>${esc(title)}</div>${body}</div>`;
    const by = Object.fromEntries((b.sections || []).map((s) => [s.key, s]));
    let html = "";
    if (by.nhan_xet) html += sec("nhan_xet", "Nhận xét", `<ul>${by.nhan_xet.items.map((i) => `<li>${inline(i.text)}</li>`).join("")}</ul>`);
    if (by.danh_gia) html += sec("danh_gia", "Đánh giá", `<ul>${by.danh_gia.items.map((i) =>
      `<li>${i.label ? `<span class="tag ${SEV_CLASS[i.sev] || ""}">${esc(i.label)}</span>` : ""}${inline(i.text)}</li>`).join("")}</ul>`);
    if (by.ket_luan) html += sec("ket_luan", "Kết luận", by.ket_luan.items.map((i) => `<div class="concl">${inline(i.text)}</div>`).join(""), true);
    if (by.de_xuat) html += sec("de_xuat", "Đề xuất", `<ol>${by.de_xuat.items.map((i) => `<li>${inline(i.text)}</li>`).join("")}</ol>`, true);
    if ((b.suggestions || []).length) html += sec("goi_y", "Gợi ý câu hỏi tiếp theo",
      `<div class="sugs">${b.suggestions.map((q) => `<button data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`, true);
    return `<div class="block ins">
      <div class="ins-head"><span class="ttl">PHÂN TÍCH</span>
        <span class="status ${SEV_CLASS[b.status] || "st-none"}">${esc(b.status_label)}</span>
        <span class="basis">${esc(b.basis || "")}</span></div>
      <div class="ins-grid">${html}</div></div>`;
  }

  // ------------------------------------------------------------ blocks
  function renderBlock(b) {
    if (b.type === "text") return `<div class="block md">${md(b.md)}</div>`;
    if (b.type === "insights") return renderInsights(b);
    if (b.type === "table") {
      const hl = new Set(b.highlight_rows || []);
      const head = b.columns.map((c) => `<th class="${c.align === "right" ? "r" : ""}">${esc(c.label)}</th>`).join("");
      const body = b.rows.map((r, ri) => {
        const foot = b.footer && ri === b.rows.length - 1;
        return `<tr class="${foot ? "foot" : ""} ${hl.has(ri) ? "hl" : ""}">` + r.map((v, ci) => {
          const c = b.columns[ci] || {};
          let cls = c.align === "right" ? "r" : "";
          const tn = b.tones?.[ri]?.[ci];
          if (tn) cls += " " + tn;
          return `<td class="${cls}">${esc(v)}</td>`;
        }).join("") + "</tr>";
      }).join("");
      return `<div class="block">${b.title ? `<div class="block-title">${esc(b.title)}</div>` : ""}<div class="tbl-wrap"><table class="tbl"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div></div>`;
    }
    if (b.type === "chart") {
      const legend = b.series.map((s) => `<span><i style="background:${COLORS[s.role] || COLORS.main}"></i>${esc(s.name)}</span>`).join("");
      return `<div class="block chart-box"><div class="chart-title"><span>${esc(b.title)}</span><span class="legend">${legend}</span></div>${chartSVG(b)}</div>`;
    }
    if (b.type === "callout") {
      const cta = b.cta ? `<a class="cta" href="${esc(b.cta.url)}" target="_blank" rel="noopener" data-upgrade="${b.cta.url === "#" ? 1 : 0}">${esc(b.cta.label)} →</a>` : "";
      return `<div class="block callout"><div class="ct">${esc(b.title)}</div><div class="md">${md(b.md)}</div>${cta}</div>`;
    }
    if (b.type === "suggestions") {
      return `<div class="block sugs">${b.items.map((q) => `<button data-q="${esc(q)}">${esc(q)}</button>`).join("")}</div>`;
    }
    return "";
  }

  const MODE_LABEL = { descriptive: "Descriptive · Semantic Layer", bod_demo: "BOD Deep-Dive · Demo", clarify: "Cần làm rõ", chitchat: "Trò chuyện", error: "Lỗi" };

  function addUser(q, bod) {
    $("#welcome")?.remove();
    const el = document.createElement("div");
    el.className = "msg user";
    el.innerHTML = `<div class="bubble-user">${bod ? '<span class="bod-mark">BOD</span>' : ""}${esc(q)}</div>`;
    chat.appendChild(el);
  }

  function addPending(bod) {
    const el = document.createElement("div");
    el.className = "msg";
    el.innerHTML = `<div class="card ${bod ? "bod" : ""}"><div class="card-head">${bod ? ICON.brain + " Đang phân tích quản trị" : ICON.chart + " Đang truy vấn Semantic Layer"}</div><div class="typing"><span></span><span></span><span></span></div></div>`;
    chat.appendChild(el);
    chat.scrollTop = chat.scrollHeight;
    return el;
  }

  function fillAnswer(el, res) {
    const bod = res.mode === "bod_demo";
    const head = bod ? ICON.brain + " AI4BI · BOD Deep-Dive" : ICON.chart + " AI4BI · Trả lời";
    const sql = (res.sql || []).length
      ? `<pre class="sql" hidden>${esc(res.sql.map((s, i) => `-- Truy vấn ${i + 1}\n${s}`).join("\n\n"))}</pre>` : "";
    el.innerHTML = `<div class="card ${bod ? "bod" : ""}">
      <div class="card-head">${head}</div>
      ${(res.blocks || []).map(renderBlock).join("")}
      <div class="meta">
        <span class="badge ${esc(res.mode)}">${esc(MODE_LABEL[res.mode] || res.mode)}</span>
        <span>Router: ${esc(res.router || "-")}</span>
        <span>· ${nf(res.elapsed_ms, 0)} ms</span>
        ${sql ? `<button data-sql>Xem SQL (${res.sql.length})</button>` : ""}
      </div>${sql}</div>`;
    el.scrollIntoView({ block: "start", behavior: "smooth" });
  }

  async function ask(q) {
    q = (q || "").trim();
    if (!q || busy) return;
    busy = true; sendBtn.disabled = true;
    const bod = bodToggle.checked;
    addUser(q, bod);
    const pending = addPending(bod);
    input.value = ""; autosize();
    try {
      const r = await fetch("/api/v1/chat", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: q, bod_mode: bod }) });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || data.blocks?.[0]?.md || `HTTP ${r.status}`);
      fillAnswer(pending, data);
    } catch (e) {
      fillAnswer(pending, { mode: "error", blocks: [{ type: "text", md: `Lỗi: ${e.message}` }], sql: [] });
    } finally {
      busy = false; sendBtn.disabled = false; input.focus();
    }
  }

  // ------------------------------------------------------------ modal (Pro)
  function showUpgrade() {
    $("#modalBody").innerHTML = `
      <h2>AI4BI · So sánh phiên bản</h2>
      <p class="lead">Community miễn phí để trải nghiệm. Pro/Enterprise để đưa vào vận hành trên dữ liệu thật.</p>
      <div class="tbl-wrap"><table class="tbl cmp"><thead><tr><th>Tính năng</th><th>Community</th><th class="pro">Pro Boilerplate</th><th class="pro">Enterprise</th></tr></thead><tbody>
      <tr><td>WebUI hỏi đáp tốc độ cao</td><td>✔ Bản build sẵn</td><td>✔ Full source</td><td>✔ Thương hiệu riêng</td></tr>
      <tr><td>Hỏi đáp qua Semantic Layer</td><td>✔ 3 gói mẫu</td><td>✔ Đầy đủ</td><td>✔ Theo nghiệp vụ riêng</td></tr>
      <tr><td>Nhận xét · Đánh giá · Đề xuất tự động</td><td>✔ Theo quy tắc &amp; ngưỡng YAML</td><td>✔ + ngưỡng theo đơn vị/kỳ</td><td>✔ + chuyên gia phân tích AI</td></tr>
      <tr><td>Cơ sở dữ liệu</td><td>SQLite mẫu</td><td>Postgres, MySQL, SQL Server</td><td>ERP, Oracle, SQL Server, DWH</td></tr>
      <tr><td>BOD Deep-Dive (Root-Cause)</td><td>Kịch bản demo</td><td>✔ Module cơ bản</td><td>✔ AI Planner + Root-Cause Engine</td></tr>
      <tr><td>RBAC &amp; Row-Level Security</td><td>✕</td><td>✔ RBAC</td><td>✔ SSO/AD, phân quyền chi nhánh</td></tr>
      <tr><td>Xuất Excel/PDF, Local LLM</td><td>✕</td><td>✔</td><td>✔ On-Premise 100%</td></tr>
      </tbody></table></div>
      <p style="margin-top:16px">Liên hệ triển khai: xem mục <b>Commercial</b> trong README của repo.</p>`;
    $("#modal").hidden = false;
  }

  async function showSchema() {
    const cat = await fetch("/api/v1/catalog").then((r) => r.json());
    const thr = (m) => m.assess && m.assess.good != null
      ? (m.format === "pct" ? `${m.higher_is_better ? "≥" : "≤"} ${nf(m.assess.good * 100, 1)}% tốt · ${m.higher_is_better ? "<" : ">"} ${nf(m.assess.warn * 100, 1)}% cảnh báo` : "") : "";
    $("#modalBody").innerHTML = `<h2>Semantic Layer</h2><p class="lead">Chatbot chỉ được phép dùng các chỉ số và chiều dưới đây. Ngưỡng đánh giá dùng cho mục Đánh giá.</p>
      <div class="tbl-wrap"><table class="tbl cmp"><thead><tr><th>Chỉ số</th><th>Gói</th><th>Định dạng</th><th>Ngưỡng đánh giá</th><th>Chiều hỗ trợ</th></tr></thead><tbody>
      ${cat.metrics.map((m) => `<tr><td style="text-align:left"><b>${esc(m.label)}</b>${m.derived ? " <small>(dẫn xuất)</small>" : ""}<br><small>${esc(m.description || "")}</small></td><td>${esc(MODEL_LABEL[m.model] || m.model)}</td><td>${esc(FMT_LABEL[m.format] || m.format)}</td><td><small>${esc(thr(m))}</small></td><td style="text-align:left"><small>${esc(m.dimensions.map((d) => cat.dimensions.find((x) => x.key === d)?.label || d).join(", "))}</small></td></tr>`).join("")}
      </tbody></table></div>`;
    $("#modal").hidden = false;
  }

  // ------------------------------------------------------------ init
  function autosize() { input.style.height = "auto"; input.style.height = Math.min(140, input.scrollHeight) + "px"; }

  async function init() {
    try {
      const [health, sug, cat] = await Promise.all([
        fetch("/api/v1/health").then((r) => r.json()),
        fetch("/api/v1/suggestions").then((r) => r.json()),
        fetch("/api/v1/catalog").then((r) => r.json()),
      ]);
      const pill = $("#routerPill");
      const chain = (health.llm || []).map((p) => p.label.replace("Google ", "")).join(" → ");
      pill.textContent = health.router === "llm" ? `LLM: ${chain} → Luật` : "Rule Router · không cần API key";
      pill.title = (health.llm || []).map((p) => `${p.label}: ${p.model}`).join("\n");
      pill.classList.toggle("llm", health.router === "llm");
      if (health.version) $("#version").textContent = "v" + health.version;
      if (health.company) $("#company").textContent = health.company;
      if (health.data_end) $("#dataEnd").textContent = health.data_end.split("-").reverse().join("/");
      $("#chips").innerHTML = sug.quick.map((c) => `<div class="chip ${c.tone}" data-q="${esc(c.text)}"><div class="tag"><span class="dot"></span>${esc(c.group)}</div><div class="q">${esc(c.text)}</div></div>`).join("");
      $("#moreList").innerHTML = sug.more.map((q) => `<button data-q="${esc(q)}">› ${esc(q)}</button>`).join("");
      $("#metricCount").textContent = `${cat.metrics.length} chỉ số`;
      const groups = {};
      cat.metrics.forEach((m) => (groups[m.model] ||= []).push(m));
      $("#catalog").innerHTML = Object.entries(groups).map(([k, ms]) =>
        `<div class="grp">${esc(MODEL_LABEL[k] || k)}</div>` + ms.map((m) =>
          `<div class="m" data-fill="${esc(m.label)} theo chi nhánh Quý 3" title="${m.assess && m.assess.good != null ? "Có ngưỡng đánh giá" : ""}"><span>${esc(m.label)}${m.derived ? ' <span class="d">ƒ</span>' : ""}${m.assess && m.assess.good != null ? '<span class="t">◆</span>' : ""}</span><span class="f">${esc(FMT_LABEL[m.format] || m.format)}</span></div>`).join("")).join("");
    } catch (e) {
      $("#routerPill").textContent = "Không kết nối được backend";
    }
  }

  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-q],[data-fill],[data-sql],[data-upgrade='1']");
    if (!t) return;
    if (t.hasAttribute("data-sql")) { const pre = t.closest(".card").querySelector("pre.sql"); pre.hidden = !pre.hidden; t.textContent = pre.hidden ? t.textContent.replace("Ẩn", "Xem") : t.textContent.replace("Xem", "Ẩn"); return; }
    if (t.dataset.upgrade === "1") { e.preventDefault(); showUpgrade(); return; }
    if (t.dataset.fill) { input.value = t.dataset.fill; autosize(); input.focus(); $("#sidebar").classList.remove("open"); return; }
    if (t.dataset.q) { $("#sidebar").classList.remove("open"); ask(t.dataset.q); }
  });
  form.addEventListener("submit", (e) => { e.preventDefault(); ask(input.value); });
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(input.value); } });
  input.addEventListener("input", autosize);
  bodToggle.addEventListener("change", () => document.body.classList.toggle("bod-on", bodToggle.checked));
  $("#btnUpgrade").addEventListener("click", showUpgrade);
  $("#linkSchema").addEventListener("click", (e) => { e.preventDefault(); showSchema(); });
  $("#modalClose").addEventListener("click", () => ($("#modal").hidden = true));
  $("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") $("#modal").hidden = true; });
  $("#btnMenu").addEventListener("click", () => $("#sidebar").classList.toggle("open"));

  // ---- Chế độ nhúng (panel chatbot trong Dashboard): /chat?embed=1
  const params = new URLSearchParams(location.search);
  if (params.get("embed") === "1") {
    document.body.classList.add("embed");
    window.addEventListener("message", (e) => {
      if (e.origin !== location.origin || !e.data || e.data.type !== "ai4bi:ask") return;
      bodToggle.checked = !!e.data.bod;
      document.body.classList.toggle("bod-on", bodToggle.checked);
      ask(e.data.question);
    });
    window.addEventListener("message", (e) => {
      if (e.origin === location.origin && e.data && e.data.type === "ai4bi:bod") {
        bodToggle.checked = !!e.data.bod;
        document.body.classList.toggle("bod-on", bodToggle.checked);
      }
    });
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") window.parent.postMessage({ type: "ai4bi:close" }, location.origin);
    });
    window.parent.postMessage({ type: "ai4bi:ready" }, location.origin);
  } else if (params.get("q")) {
    setTimeout(() => ask(params.get("q")), 300);
  }
  init();
})();
