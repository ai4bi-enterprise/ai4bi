// Render KHỐI dùng chung cho dashboard và chatbot (Blueprint P2: cùng khuôn KPI + biểu đồ + bảng).
const NF = (d) => new Intl.NumberFormat("vi-VN", { minimumFractionDigits: d, maximumFractionDigits: d });

export const fmt = {
  money(v) {
    if (v == null) return "–";
    const a = Math.abs(v);
    if (a >= 1e12) return NF(2).format(v / 1e12) + " nghìn tỷ";
    if (a >= 1e9) return NF(a >= 1e11 ? 1 : 2).format(v / 1e9) + " tỷ";
    if (a >= 1e6) return NF(1).format(v / 1e6) + " triệu";
    return NF(0).format(v) + " đ";
  },
  pct: (v) => (v == null ? "–" : NF(1).format(v * 100) + "%"),
  signed_pct: (v) => (v == null ? "–" : (v >= 0 ? "+" : "") + NF(1).format(v * 100) + "%"),
  number: (v) => (v == null ? "–" : NF(0).format(v)),
  days: (v) => (v == null ? "–" : NF(0).format(v) + " ngày"),
  times: (v) => (v == null ? "–" : NF(2).format(v) + " lần"),
  axis(v, kind) {
    if (kind === "pct") return NF(0).format(v * 100) + "%";
    if (kind === "days") return NF(0).format(v);
    if (kind === "money") {
      const a = Math.abs(v);
      return a >= 1e9 ? NF(a >= 1e10 ? 0 : 1).format(v / 1e9) + " tỷ" : a >= 1e6 ? NF(0).format(v / 1e6) + " tr" : NF(0).format(v);
    }
    return NF(0).format(v);
  },
};
export const fv = (v, kind) => (fmt[kind] || fmt.number)(v);

export const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const ask = (q) => q && window.dispatchEvent(new CustomEvent("ai4bi:ask", { detail: q }));

// ------------------------------------------------------------ KPI + nhận định
export function kpiGrid(kpis) {
  const el = document.createElement("div");
  el.className = "kpis";
  for (const k of kpis) {
    const c = document.createElement("div");
    c.className = "kpi";
    c.tabIndex = 0;
    c.title = k.ask ? "Hỏi AI: " + k.ask : "";
    const toneLabel = { good: "Tốt", warn: "Theo dõi", bad: "Cảnh báo" }[k.tone];
    c.innerHTML = `
      ${toneLabel ? `<span class="kpi-dot dot-${k.tone}" title="${toneLabel}" aria-label="${toneLabel}"></span>` : ""}
      <div class="kpi-label">${esc(k.label)}</div>
      <div class="kpi-value" title="${esc(k.display)}">${esc(k.display)}</div>
      ${k.sub ? `<div class="kpi-sub">${esc(k.sub)}</div>` : ""}
      <div class="kpi-cmp">${(k.compares || []).map((x) => `<span>${esc(x.label)}: <b class="tone-${x.tone}">${esc(x.delta_display)}</b></span>`).join("")}</div>`;
    c.addEventListener("click", () => ask(k.ask));
    c.addEventListener("keydown", (e) => e.key === "Enter" && ask(k.ask));
    el.append(c);
  }
  return el;
}

export function insightsCard(items, title = "Nhận định tự động") {
  const el = document.createElement("div");
  el.className = "insights";
  const lab = { good: "Tốt", warn: "Theo dõi", bad: "Cảnh báo", info: "Thông tin" };
  el.innerHTML = `<h3>${esc(title)}</h3><ul>${items.map((i) => `<li><span class="lvl lvl-${i.level}">${lab[i.level] || "•"}</span><span>${esc(i.text)}</span></li>`).join("")}</ul>`;
  return el;
}

// ------------------------------------------------------------ bảng
export function tableEl(columns, rows) {
  const wrap = document.createElement("div");
  wrap.className = "tbl-wrap";
  const numeric = (f) => ["money", "pct", "signed_pct", "number", "days", "times"].includes(f);
  const cell = (c, r) => {
    const v = r[c.key];
    if (c.format === "badge") {
      const t = r._tone?.[c.key] || "info";
      return `<td><span class="badge lvl-${t}">${esc(v)}</span></td>`;
    }
    if (!numeric(c.format)) return `<td>${esc(v ?? "–")}</td>`;
    const tone = c.tone && r._tone?.[c.key] ? ` tone-${r._tone[c.key]}` : "";
    const neg = !tone && typeof v === "number" && v < 0 && c.format === "money" ? " neg" : "";
    return `<td class="num${tone}${neg}">${esc(fv(v, c.format))}</td>`;
  };
  wrap.innerHTML = `<table><thead><tr>${columns.map((c) => `<th class="${numeric(c.format) ? "num" : ""}">${esc(c.label)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr class="${r._bold ? "bold" : ""}">${columns.map((c) => cell(c, r)).join("")}</tr>`).join("")}</tbody></table>`;
  return wrap;
}

// ------------------------------------------------------------ biểu đồ
const charts = new Set();
export function destroyCharts(root) {
  for (const ch of [...charts]) if (!root || root.contains(ch.canvas)) { ch.destroy(); charts.delete(ch); }
}

function palette() {
  return { series: [1, 2, 3, 4, 5, 6, 7, 8].map((i) => css(`--s${i}`)), muted: css("--muted"), text: css("--text-2"),
    grid: css("--grid"), pos: css("--pos"), neg: css("--neg"), neutral: css("--neutral"), surface: css("--surface") };
}

function seriesColor(s, i, P) {
  if (s.color === "muted") return P.muted;
  if (s.color === "plan") return P.text;
  if (s.color === "accent") return P.series[1];
  return P.series[i % 8];
}

function chartConfig(b) {
  const P = palette();
  const kind = b.kind;
  const f = b.format || "money";
  const base = {
    responsive: true, maintainAspectRatio: false, animation: { duration: 250 },
    interaction: { mode: kind === "doughnut" ? "nearest" : "index", intersect: false },
    plugins: {
      legend: { display: b.series.length > 1 && kind !== "waterfall", position: "bottom",
        labels: { color: P.text, usePointStyle: true, pointStyle: "rectRounded", boxWidth: 10, boxHeight: 10, padding: 14 } },
      tooltip: { callbacks: { label: (ctx) => {
        const raw = kind === "waterfall" ? b.series[0].data[ctx.dataIndex] : ctx.raw;
        return ` ${ctx.dataset.label || ctx.label}: ${fv(raw, f)}`;
      } } },
    },
  };
  const scale = (axisKind) => ({
    grid: { color: P.grid, drawTicks: false }, border: { display: false },
    ticks: { color: P.text, padding: 6, callback: (v) => fmt.axis(v, axisKind) },
  });
  const cat = { grid: { display: false }, border: { color: P.grid }, ticks: { color: P.text, autoSkip: true, maxRotation: 0 } };

  if (kind === "doughnut") {
    const data = b.series[0].data;
    return { type: "doughnut", data: { labels: b.labels, datasets: [{ data, backgroundColor: b.labels.map((_, i) => P.series[i % 8]),
      borderColor: P.surface, borderWidth: 2 }] },
      options: { ...base, cutout: "62%", plugins: { ...base.plugins, legend: { ...base.plugins.legend, display: true, position: "right" },
        tooltip: { callbacks: { label: (ctx) => {
          const tot = data.reduce((a, x) => a + (x || 0), 0);
          return ` ${ctx.label}: ${fv(ctx.raw, f)} (${fmt.pct(ctx.raw / tot)})`;
        } } } } } };
  }
  if (kind === "waterfall") {
    let run = 0;
    const bars = [], colors = [];
    b.series[0].data.forEach((v, i) => {
      v = v || 0;
      if (b.steps[i] === "total") { bars.push([0, v]); run = v; colors.push(P.neutral); }
      else { bars.push([run, run + v]); run += v; colors.push(v >= 0 ? P.pos : P.neg); }
    });
    return { type: "bar", data: { labels: b.labels, datasets: [{ label: "Giá trị", data: bars, backgroundColor: colors,
      borderRadius: 4, borderSkipped: false, maxBarThickness: 46 }] },
      options: { ...base, plugins: { ...base.plugins, legend: { display: false } },
        scales: { x: { ...cat, ticks: { ...cat.ticks, autoSkip: false, maxRotation: 40, font: { size: 11 } } }, y: scale(f) } } };
  }
  const horizontal = kind === "hbar" || kind === "stacked_hbar";
  const stacked = kind === "stacked" || kind === "stacked_hbar";
  const datasets = b.series.map((s, i) => {
    const color = seriesColor(s, i, P);
    const isLine = s.type === "line" || kind === "line";
    return {
      label: s.name, data: s.data, type: isLine ? "line" : "bar",
      backgroundColor: isLine ? color : color, borderColor: color,
      borderWidth: isLine ? 2 : 0, borderDash: s.color === "plan" ? [6, 4] : undefined,
      pointRadius: isLine ? (s.data.length > 18 ? 0 : 3) : 0, pointHoverRadius: 5, tension: 0.25, spanGaps: true,
      borderRadius: stacked ? 0 : 4, borderSkipped: stacked ? false : "start", maxBarThickness: 38,
      order: isLine ? 0 : 1,
    };
  });
  if (stacked) datasets.forEach((d) => { d.borderColor = P.surface; d.borderWidth = 1; });
  const valueScale = { ...scale(f), stacked };
  const catScale = { ...cat, stacked, ticks: { ...cat.ticks, callback(v) { const l = this.getLabelForValue(v); return horizontal && l.length > 26 ? l.slice(0, 25) + "…" : l; } } };
  const g = b.growth;   // % tăng trưởng so cùng kỳ: hiện cạnh cột kỳ này + trong tooltip
  const options = { ...base, indexAxis: horizontal ? "y" : "x",
    interaction: { mode: "index", intersect: false, axis: horizontal ? "y" : "x" },
    scales: horizontal ? { x: valueScale, y: catScale } : { x: catScale, y: valueScale } };
  if (g?.length) {
    options.layout = { padding: horizontal ? { right: 56 } : { top: 18 } };
    options.plugins = { ...base.plugins, tooltip: { callbacks: { ...base.plugins.tooltip.callbacks,
      footer: (items) => { const v = g[items[0]?.dataIndex]; return v == null ? "" : "Tăng trưởng: " + fmt.signed_pct(v); } } } };
  }
  return { type: "bar", data: { labels: b.labels, datasets }, options, plugins: g?.length ? [growthLabels(g, horizontal, P)] : [] };
}

function growthLabels(g, horizontal, P) {
  return { id: "growthLabels", afterDatasetsDraw(chart) {
    const { ctx } = chart;
    const metas = chart.data.datasets.map((_, i) => chart.getDatasetMeta(i)).filter((m) => !m.hidden);
    if (!metas.length) return;
    ctx.save();
    ctx.font = "600 11px system-ui, sans-serif";
    const base = chart.data.datasets[1]?.data || [];
    g.forEach((v, i) => {
      // không có số cùng kỳ mà kỳ này có -> "Mới"
      const fresh = v == null && base[i] == null && chart.data.datasets[0].data[i] != null;
      if (v == null && !fresh) return;
      // đặt nhãn sau cột dài nhất của nhóm (kỳ này / cùng kỳ)
      const els = metas.map((m) => m.data[i]).filter(Boolean);
      if (!els.length) return;
      ctx.fillStyle = fresh ? P.muted : v >= 0 ? P.pos : P.neg;
      const txt = fresh ? "Mới" : fmt.signed_pct(v);
      if (horizontal) {
        const x = Math.max(...els.map((e) => e.x));
        const y = els.reduce((a, e) => a + e.y, 0) / els.length;
        ctx.textAlign = "left"; ctx.textBaseline = "middle";
        ctx.fillText(txt, x + 6, y);
      } else {
        const y = Math.min(...els.map((e) => e.y));
        const x = els.reduce((a, e) => a + e.x, 0) / els.length;
        ctx.textAlign = "center"; ctx.textBaseline = "bottom";
        ctx.fillText(txt, x, y - 4);
      }
    });
    ctx.restore();
  } };
}

// Khối -> { columns, rows }: dùng cho nút "Bảng" và xuất CSV (backend có bản giống: app/core/export.py block_table)
export function blockTable(b) {
  if (b.type === "table") return { columns: b.columns, rows: b.rows };
  if (b.table) return b.table;   // bảng so sánh đầy đủ do backend dựng
  if (b.kind === "waterfall") {
    return { columns: [{ key: "l", label: "Khoản mục" }, { key: "v", label: "Giá trị", format: b.format }],
      rows: b.labels.map((l, i) => ({ l, v: b.series[0].data[i], _bold: b.steps[i] === "total" })) };
  }
  const columns = [{ key: "l", label: "" }, ...b.series.map((s, i) => ({ key: "s" + i, label: s.name, format: b.format }))];
  return { columns, rows: b.labels.map((l, j) => Object.fromEntries([["l", l], ...b.series.map((s, i) => ["s" + i, s.data[j]])])) };
}

function chartAsTable(b) {
  const t = blockTable(b);
  return tableEl(t.columns, t.rows);
}

export function blockCard(b) {
  const card = document.createElement("section");
  card.className = "card" + (b.width === "full" ? " full" : "");
  card.innerHTML = `<div class="card-head"><h3>${esc(b.title)}</h3><div class="card-tools">
      ${b.type === "chart" ? `<button class="tool" data-act="table" title="Xem dạng bảng">Bảng</button>` : ""}
      ${b.ask ? `<button class="tool" data-act="ask" title="${esc(b.ask)}">✦ Hỏi AI</button>` : ""}</div></div>`;
  const body = document.createElement("div");
  card.append(body);
  let asTable = false;
  const draw = () => {
    destroyCharts(body);
    body.innerHTML = "";
    if (b.type === "table") return body.append(tableEl(b.columns, b.rows));
    if (asTable) return body.append(chartAsTable(b));
    // [empty-chart-v1] mọi giá trị trống -> không vẽ trục rỗng
    if (!b.labels?.length || !b.series?.some((s) => s.data?.some((v) => v != null)))
      return (body.innerHTML = `<div class="note empty-chart">Chưa có dữ liệu cho kỳ / phạm vi này.</div>`);
    const box = document.createElement("div");
    box.className = "chart-box";
    if (b.height) box.style.height = b.height + "px";
    const cv = document.createElement("canvas");
    cv.setAttribute("role", "img");
    cv.setAttribute("aria-label", b.title);
    box.append(cv);
    body.append(box);
    charts.add(new Chart(cv, chartConfig(b)));
  };
  card._redraw = draw;
  draw();
  if (b.note) card.insertAdjacentHTML("beforeend", `<div class="note">ⓘ ${esc(b.note)}</div>`);
  card.querySelector('[data-act="ask"]')?.addEventListener("click", () => ask(b.ask));
  card.querySelector('[data-act="table"]')?.addEventListener("click", (e) => {
    asTable = !asTable;
    e.target.textContent = asTable ? "Biểu đồ" : "Bảng";
    draw();
  });
  return card;
}

export function blocksGrid(blocks) {
  const g = document.createElement("div");
  g.className = "grid";
  blocks.forEach((b) => g.append(blockCard(b)));
  return g;
}

export function redrawAll() {
  document.querySelectorAll(".card").forEach((c) => c._redraw && c._redraw());
}

// Markdown tối giản, an toàn (escape trước rồi mới định dạng)
export function md(text) {
  const lines = esc(text).split("\n");
  let html = "", inList = false;
  for (let line of lines) {
    line = line.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>").replace(/(^|[^*])\*(?!\s)(.+?)\*/g, "$1<i>$2</i>").replace(/`(.+?)`/g, "<code>$1</code>");
    if (/^\s*[-•]\s+/.test(line)) {
      if (!inList) { html += "<ul>"; inList = true; }
      html += `<li>${line.replace(/^\s*[-•]\s+/, "")}</li>`;
    } else {
      if (inList) { html += "</ul>"; inList = false; }
      if (line.trim() === "---") html += "<hr>";
      else if (line.trim()) html += `<p>${line}</p>`;
    }
  }
  return html + (inList ? "</ul>" : "");
}
