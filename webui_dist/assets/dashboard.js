/* AI4BI – Revenue Dashboard + panel Trợ lý AI (vanilla JS, không phụ thuộc thư viện ngoài) */
(() => {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const COL = { prior: "#94a3b8", kpi: "#f59e0b", cash: "#10b981" };
  const ROBOT = '<svg viewBox="0 0 24 24"><path fill="currentColor" d="M12 2a2 2 0 0 1 2 2 2 2 0 0 1-1 1.73V7h4a3 3 0 0 1 3 3v7a3 3 0 0 1-3 3H7a3 3 0 0 1-3-3v-7a3 3 0 0 1 3-3h4V5.73A2 2 0 0 1 10 4a2 2 0 0 1 2-2M9 11.5A1.5 1.5 0 1 0 9 14.5 1.5 1.5 0 0 0 9 11.5m6 0a1.5 1.5 0 1 0 0 3 1.5 1.5 0 0 0 0-3"/></svg>';
  const state = { mode: "qtd", month: null, year: null, meta: null, data: null };

  // ------------------------------------------------------------ định dạng (vi-VN)
  const nf = (v, d = 2) => v == null ? "—" : Number(v).toLocaleString("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d });
  const ty = (v, d = 2) => v == null ? "—" : nf(v / 1e9, d);
  const pct = (v, d = 1) => v == null ? "—" : nf(v * 100, d) + "%";
  function chg(c, kind, hib = true) {
    if (c == null) return { txt: "—", cls: "neu" };
    const good = (c > 0) === hib;
    const txt = kind === "pts" ? `${c >= 0 ? "+" : ""}${nf(c * 100, 1)} điểm %` : `${c >= 0 ? "+" : ""}${nf(c * 100, 1)}%`;
    return { txt, cls: Math.abs(c) < 1e-9 ? "neu" : good ? "pos" : "neg" };
  }
  const fmtVal = (v, f) => f === "pct" ? pct(v) : f === "money" ? ty(v) : nf(v, 0);
  const unit = (f) => f === "money" ? "<small>tỷ</small>" : "";
  const askBtn = (q, alert) => q ? `<button class="ask ${alert ? "alert" : ""}" data-ask="${esc(q)}" data-tip="Hỏi AI: ${esc(q)}" aria-label="Hỏi AI">${ROBOT}</button>` : "";

  // ------------------------------------------------------------ SVG helpers
  function niceMax(v) {
    if (v <= 0) return 1;
    const p = Math.pow(10, Math.floor(Math.log10(v))), n = v / p;
    return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 2.5 ? 2.5 : n <= 5 ? 5 : 10) * p;
  }

  function donut(share, color, size = 30) {
    const r = size / 2 - 3, c = 2 * Math.PI * r, s = Math.max(0, Math.min(1, share || 0));
    return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}"><circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="#eef2f7" stroke-width="5"/>
      <circle cx="${size / 2}" cy="${size / 2}" r="${r}" fill="none" stroke="${color}" stroke-width="5" stroke-dasharray="${c * s} ${c}" transform="rotate(-90 ${size / 2} ${size / 2})" stroke-linecap="round"/></svg>`;
  }

  /** Cột [Kỳ trước | Thực hiện | KPI] dùng CHUNG trục Y cho mọi thẻ chi nhánh (so sánh được quy mô). */
  function branchBars(b, ymax) {
    const W = 360, H = 170, T = 22, B = 26, L = 8, R = 8;
    const items = [
      { k: "Kỳ trước", v: b.prior, c: COL.prior },
      { k: "Thực hiện", v: b.actual, c: b.color },
      { k: "KPI", v: b.kpi, c: COL.kpi },
    ];
    const ih = H - T - B, slot = (W - L - R) / 3, bw = slot * 0.5;
    let g = `<line x1="${L}" x2="${W - R}" y1="${T + ih}" y2="${T + ih}" stroke="#cbd5e1"/>`;
    items.forEach((it, i) => {
      const x = L + slot * i + (slot - bw) / 2;
      if (it.v == null) {
        g += `<text x="${x + bw / 2}" y="${T + ih - 6}" text-anchor="middle" font-size="11" fill="#94a3b8">không có</text>`;
      } else {
        const h = Math.max(2, (it.v / ymax) * ih), y = T + ih - h;
        g += `<rect x="${x}" y="${y}" width="${bw}" height="${h}" rx="4" fill="${it.c}"><title>${it.k}: ${ty(it.v)} tỷ</title></rect>`;
        g += `<text x="${x + bw / 2}" y="${y - 6}" text-anchor="middle" font-size="12" font-weight="700" fill="#0f172a">${ty(it.v)}</text>`;
      }
      g += `<text x="${x + bw / 2}" y="${H - 8}" text-anchor="middle" font-size="11.5" fill="#475569">${it.k}</text>`;
    });
    return `<svg class="bars" viewBox="0 0 ${W} ${H}" role="img" aria-label="Kỳ trước, Thực hiện, KPI">${g}</svg>`;
  }

  /** Cột chồng doanh thu theo chi nhánh từng tháng + đường dòng tiền thực thu. */
  function trendChart(t) {
    const W = 760, H = 270, L = 46, R = 14, T = 16, B = 30;
    const n = t.labels.length, ih = H - T - B, iw = W - L - R, slot = iw / n, bw = slot * 0.62;
    const tot = t.total.map((v) => v || 0), max = niceMax(Math.max(...tot, ...t.cash.map((v) => v || 0)) * 1.05);
    const y = (v) => T + ih - (v / max) * ih;
    const hl = new Set(t.highlight);
    let g = "";
    for (let i = 0; i <= 4; i++) {
      const v = (max / 4) * i;
      g += `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" stroke="${i ? "#eef2f7" : "#cbd5e1"}"/>`;
      g += `<text x="${L - 6}" y="${y(v) + 4}" text-anchor="end" font-size="10.5" fill="#64748b">${nf(v / 1e9, 0)}</text>`;
    }
    t.labels.forEach((lb, i) => {
      const x = L + slot * i + (slot - bw) / 2, m = i + 1;
      if (hl.has(m)) g += `<rect x="${L + slot * i + 1}" y="${T}" width="${slot - 2}" height="${ih}" fill="#eff6ff" rx="4"/>`;
      let acc = 0;
      t.series.forEach((s) => {
        const v = s.values[i] || 0, y1 = y(acc + v), y0 = y(acc);
        g += `<rect x="${x}" y="${y1}" width="${bw}" height="${Math.max(0, y0 - y1)}" fill="${s.color}" opacity="${hl.has(m) ? 1 : .55}"><title>${esc(s.name)} ${lb}: ${ty(v)} tỷ</title></rect>`;
        acc += v;
      });
      const top = Math.min(y(acc), t.cash[i] == null ? Infinity : y(t.cash[i]) - 6);
      g += `<text x="${x + bw / 2}" y="${top - 6}" text-anchor="middle" stroke="#fff" stroke-width="3" paint-order="stroke" font-size="10" font-weight="${hl.has(m) ? 800 : 600}" fill="#334155">${ty(acc, 1)}</text>`;
      g += `<text x="${x + bw / 2}" y="${H - 10}" text-anchor="middle" font-size="11" font-weight="${hl.has(m) ? 800 : 500}" fill="${hl.has(m) ? "#1d4ed8" : "#64748b"}">${lb}</text>`;
    });
    const pts = t.cash.map((v, i) => v == null ? null : [L + slot * i + slot / 2, y(v)]).filter(Boolean);
    g += `<polyline fill="none" stroke="${COL.cash}" stroke-width="2.5" stroke-dasharray="6 4" points="${pts.map((p) => p.join(",")).join(" ")}"/>`;
    pts.forEach((p, i) => g += `<circle cx="${p[0]}" cy="${p[1]}" r="3.5" fill="#fff" stroke="${COL.cash}" stroke-width="2"><title>Thực thu ${t.labels[i]}: ${ty(t.cash[i])} tỷ</title></circle>`);
    return `<svg class="trend" viewBox="0 0 ${W} ${H}" role="img" aria-label="Xu hướng doanh thu theo tháng">${g}</svg>`;
  }

  // ------------------------------------------------------------ render
  function renderKpis(d) {
    const tot = d.kpis[0], st = d.total_status, tg = d.header.time_gone;
    const kp = tot.extra.find((e) => e.label === "% KPI")?.value, kv = tot.extra.find((e) => e.label === "KPI")?.value;
    const c = chg(tot.change, tot.change_kind, tot.higher_is_better);
    const total = `<div class="card total">${askBtn(tot.ask, st.code === "bad")}
      <div class="kc-label">Tổng công ty · ${esc(tot.label)}</div>
      <div class="kc-value">${ty(tot.value)}${unit("money")}</div>
      <div class="kc-chg"><span class="status st-${st.code}">${esc(st.label)}</span>
        <span class="${c.cls}">${c.txt}</span><span class="muted">${d.period.prior_label ? "so với " + esc(d.period.prior_label) : "không có kỳ so sánh"}</span></div>
      <div class="progress"><div class="bar" style="width:${Math.min(100, (kp || 0) * 100)}%"></div><div class="tg" style="left:${Math.min(100, tg * 100)}%"></div></div>
      <div class="progress-lbl"><span>% KPI <b>${pct(kp)}</b> · KPI ${ty(kv)} tỷ</span><span>Tiến độ thời gian ${pct(tg, 0)}</span></div></div>`;
    const rest = d.kpis.slice(1).map((k) => {
      const c2 = chg(k.change, k.change_kind, k.higher_is_better);
      return `<div class="card">${askBtn(k.ask, c2.cls === "neg")}
        <div class="kc-label">${esc(k.label)}</div>
        <div class="kc-value">${fmtVal(k.value, k.format)}${unit(k.format)}</div>
        <div class="kc-chg"><span class="${c2.cls}">${c2.txt}</span><span class="muted">${d.period.prior_label ? "vs kỳ trước" : ""}</span></div>
        <div class="kc-extra">${k.extra.map((e) => `<span>${esc(e.label)} <b>${fmtVal(e.value, e.format)}${e.format === "money" ? " tỷ" : ""}</b></span>`).join("")}</div></div>`;
    }).join("");
    $("#kpiRow").innerHTML = total + rest;
  }

  function renderBranches(d) {
    const ymax = niceMax(Math.max(...d.branches.flatMap((b) => [b.actual || 0, b.prior || 0, b.kpi || 0])) * 1.12);
    $("#branchRow").innerHTML = d.branches.map((b) => {
      const g = chg(b.growth, "pct"), k = b.kpi_pct;
      return `<div class="card branch">${askBtn(b.ask, b.status.code === "bad")}
        <div class="bc-head"><span class="bc-name" style="background:${b.color}">${esc(b.name)}</span>
          <span class="bc-donut" title="Tỷ trọng doanh thu">${donut(b.share, b.color)}${pct(b.share)}</span></div>
        <div class="bc-ann"><span class="${g.cls}">${g.txt} <span class="neu">kỳ trước</span></span><span class="pipe">|</span>
          <span class="${k == null ? "neu" : k >= 1 ? "pos" : "neg"}">${pct(k)} <span class="neu">KPI</span></span>
          <span class="status st-${b.status.code}">${esc(b.status.label)}</span></div>
        ${branchBars(b, ymax)}
        <div class="bc-foot"><span>Biên LN gộp <b>${pct(b.margin)}</b></span><span>Đơn vị: tỷ đồng</span></div></div>`;
    }).join("");
  }

  function renderTrend(d) {
    const t = d.trend;
    $("#trendCard").innerHTML = `${askBtn(t.ask)}
      <div class="pc-title">Xu hướng doanh thu thuần theo tháng <small>tỷ đồng · kỳ đang xem được tô đậm</small></div>
      <div class="pc-legend">${t.series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}
        <span><i style="background:${COL.cash}"></i>Dòng tiền thực thu (đường đứt)</span></div>
      ${trendChart(t)}`;
  }

  function renderGroups(d) {
    const max = Math.max(...d.groups.map((g) => g.share || 0), 0.0001);
    $("#groupCard").innerHTML = `${askBtn(d.groups_ask)}
      <div class="pc-title">Theo nhóm hàng <small>${esc(d.period.label)}</small></div>
      <table class="gt"><thead><tr><th>Nhóm hàng</th><th class="r">Doanh thu (tỷ)</th><th class="r">Biên LN gộp</th><th class="r">Tỷ lệ CK</th></tr></thead><tbody>
      ${d.groups.map((g) => `<tr><td>${esc(g.name)}<div class="share-bar"><span style="width:${(g.share / max) * 100}%"></span></div></td>
        <td class="r">${ty(g.actual)}<br><small class="neu">${pct(g.share)}</small></td><td class="r">${pct(g.margin)}</td><td class="r">${pct(g.discount)}</td></tr>`).join("")}
      </tbody></table>`;
  }

  function renderTop(el, title, rows, ask) {
    const max = Math.max(...rows.map((r) => r.value || 0), 1);
    const sum = rows.reduce((a, r) => a + (r.share || 0), 0);
    el.innerHTML = `${askBtn(ask)}
      <div class="pc-title">${esc(title)} <small>Top ${rows.length} chiếm ${pct(sum)} doanh thu kỳ</small></div>
      ${rows.map((r, i) => `<div class="rk"><span class="n">${i + 1}</span><span class="nm" title="${esc(r.name)}">${esc(r.name)}</span>
        <span class="v">${ty(r.value)}<small>${pct(r.share)}</small></span><div class="bar"><span style="width:${(r.value / max) * 100}%"></span></div></div>`).join("")}`;
  }

  function render(d) {
    $("#periodLbl").textContent = d.period.label + (d.period.prior_label ? ` · so với ${d.period.prior_label}` : "");
    $("#hTg").textContent = pct(d.header.time_gone, 0);
    $("#kpiNote").textContent = [d.period.kpi_note, d.period.prior_label ? null : "Bộ dữ liệu mẫu chỉ có năm 2025 nên YTD không có kỳ so sánh"].filter(Boolean).join(" · ");
    renderKpis(d); renderBranches(d); renderTrend(d); renderGroups(d);
    renderTop($("#topCust"), "Top khách hàng", d.top_customers, d.top_customers_ask);
    renderTop($("#topProd"), "Top sản phẩm", d.top_products, d.top_products_ask);
  }

  async function load() {
    $("#wrap").style.opacity = .55;
    try {
      const r = await fetch(`/api/v1/dashboard?mode=${state.mode}&month=${state.month}&year=${state.year}`);
      if (!r.ok) throw new Error((await r.json()).detail || r.status);
      state.data = await r.json();
      render(state.data);
    } catch (e) {
      $("#kpiRow").innerHTML = `<div class="card skeleton">Không tải được dữ liệu: ${esc(e.message)}</div>`;
    } finally { $("#wrap").style.opacity = 1; }
  }

  // ------------------------------------------------------------ panel chatbot
  const frame = $("#cpFrame");
  let ready = false, queue = [];
  function openPanel(question) {
    document.body.classList.add("cp-open");
    $("#cpanel").setAttribute("aria-hidden", "false");
    if (frame.src === "about:blank" || !frame.src.includes("/chat")) frame.src = "/chat?embed=1";
    if (question) {
      const msg = { type: "ai4bi:ask", question, bod: $("#cpBod").checked };
      ready ? frame.contentWindow.postMessage(msg, location.origin) : queue.push(msg);
    }
  }
  function closePanel() { document.body.classList.remove("cp-open"); $("#cpanel").setAttribute("aria-hidden", "true"); }
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || !e.data) return;
    if (e.data.type === "ai4bi:close") { closePanel(); return; }
    if (e.data.type === "ai4bi:ready") {
      ready = true;
      frame.contentWindow.postMessage({ type: "ai4bi:bod", bod: $("#cpBod").checked }, location.origin);
      queue.splice(0).forEach((m) => frame.contentWindow.postMessage(m, location.origin));
    }
  });
  $("#fab").addEventListener("click", () => openPanel());
  $("#cpClose").addEventListener("click", closePanel);
  $("#cpBackdrop").addEventListener("click", closePanel);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closePanel(); });
  $("#cpBod").addEventListener("change", () => ready && frame.contentWindow.postMessage({ type: "ai4bi:bod", bod: $("#cpBod").checked }, location.origin));
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-ask]");
    if (b) openPanel(b.dataset.ask);
  });

  // ------------------------------------------------------------ control bar
  $("#modeSeg").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-mode]");
    if (!b || b.dataset.mode === state.mode) return;
    state.mode = b.dataset.mode;
    document.querySelectorAll("#modeSeg button").forEach((x) => x.classList.toggle("on", x === b));
    load();
  });
  $("#selMonth").addEventListener("change", (e) => { state.month = +e.target.value; load(); });
  $("#selYear").addEventListener("change", (e) => { state.year = +e.target.value; load(); });

  async function init() {
    const meta = await fetch("/api/v1/dashboard/meta").then((r) => r.json());
    state.meta = meta; state.year = meta.default_year; state.month = meta.default_month;
    $("#selYear").innerHTML = meta.years.map((y) => `<option ${y === state.year ? "selected" : ""}>${y}</option>`).join("");
    const last = meta.default_month;
    $("#selMonth").innerHTML = Array.from({ length: last }, (_, i) => i + 1).map((m) => `<option value="${m}" ${m === state.month ? "selected" : ""}>Tháng ${m}</option>`).join("");
    $("#hCompany").textContent = meta.company || "";
    $("#hEnd").textContent = meta.data_end.split("-").reverse().join("/");
    const p = new URLSearchParams(location.search);
    if (p.get("mode") && meta.modes.includes(p.get("mode"))) {
      state.mode = p.get("mode");
      document.querySelectorAll("#modeSeg button").forEach((x) => x.classList.toggle("on", x.dataset.mode === state.mode));
    }
    if (p.get("month")) { state.month = Math.min(last, Math.max(1, +p.get("month"))); $("#selMonth").value = state.month; }
    await load();
  }
  init();
})();
