const COLORS = {
  strategy: "#4f8cff",
  spy: "#ff9f43",
  baselines: ["#a78bfa", "#2ecc71", "#ff5c5c"],
  grid: "rgba(255,255,255,0.06)",
  text: "#8b98a5",
};

const pct = (x) => (x == null ? "–" : `${(x * 100).toFixed(2)}%`);
const money = (x) => (x == null ? "–" : `$${Number(x).toLocaleString(undefined, { maximumFractionDigits: 0 })}`);
const signClass = (x) => (x == null ? "" : x >= 0 ? "up" : "down");

Chart.defaults.color = COLORS.text;
Chart.defaults.borderColor = COLORS.grid;
Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;

function indexTo100(values) {
  const base = values.find((v) => v != null && v !== 0);
  return values.map((v) => (base ? (v / base) * 100 : null));
}

function renderCards(d) {
  const h = d.headline || {};
  const c = d.current || {};
  const cards = [
    { label: "Paper equity", value: money(c.equity) },
    { label: "Mode", value: (c.mode || "–").replace("_", " ") },
    { label: "Risk-off", value: c.risk_off == null ? "–" : c.risk_off ? "Yes" : "No" },
    { label: "Backtest return", value: pct(h.total_return), cls: signClass(h.total_return) },
    { label: "SPY return", value: pct(h.benchmark_return) },
    { label: "Excess vs SPY", value: pct(h.excess_return), cls: signClass(h.excess_return) },
    { label: "Max drawdown", value: pct(h.max_drawdown), cls: "down" },
    { label: "Sharpe", value: h.sharpe == null ? "–" : h.sharpe.toFixed(2) },
  ];
  document.getElementById("status-cards").innerHTML = cards
    .map((card) => `<div class="card"><div class="label">${card.label}</div><div class="value ${card.cls || ""}">${card.value}</div></div>`)
    .join("");
}

function lineChart(canvasId, labels, datasets) {
  new Chart(document.getElementById(canvasId), {
    type: "line",
    data: { labels, datasets },
    options: {
      responsive: true,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0 }, line: { borderWidth: 2, tension: 0.1 } },
      plugins: { legend: { labels: { usePointStyle: true, boxWidth: 8 } } },
      scales: {
        x: { ticks: { maxTicksLimit: 8, autoSkip: true }, grid: { display: false } },
        y: { grid: { color: COLORS.grid } },
      },
    },
  });
}

function renderLive(d) {
  const live = d.live || [];
  if (live.length < 2) {
    document.getElementById("liveChart").replaceWith(
      Object.assign(document.createElement("p"), {
        className: "hint",
        textContent: "Not enough runs yet — the live curve appears after a couple of reweights.",
      })
    );
    return;
  }
  const labels = live.map((p) => p.date);
  lineChart("liveChart", labels, [
    { label: "Paper account", data: indexTo100(live.map((p) => p.equity)), borderColor: COLORS.strategy },
    { label: "SPY (buy & hold)", data: indexTo100(live.map((p) => p.spy)), borderColor: COLORS.spy },
  ]);
}

function renderBacktest(d) {
  const bt = d.backtest || {};
  const strat = bt.strategy || [];
  if (strat.length < 2) return;
  const labels = strat.map((p) => p.date);
  const byDate = (series) => {
    const map = new Map(series.map((p) => [p.date, p.equity]));
    return labels.map((l) => map.get(l) ?? null);
  };
  const datasets = [{ label: "Model blend", data: strat.map((p) => p.equity), borderColor: COLORS.strategy }];
  Object.entries(bt.baselines || {}).forEach(([name, series], i) => {
    datasets.push({ label: name, data: byDate(series), borderColor: COLORS.baselines[i % COLORS.baselines.length], borderWidth: 1.5 });
  });
  lineChart("backtestChart", labels, datasets);
}

function renderHoldings(d) {
  const holdings = (d.current || {}).holdings || {};
  const rows = Object.entries(holdings).sort((a, b) => b[1] - a[1]);
  if (!rows.length) {
    document.getElementById("holdings").innerHTML = '<p class="hint">No current targets.</p>';
    return;
  }
  document.getElementById("holdings").innerHTML = `
    <table><thead><tr><th>Symbol</th><th class="num">Target weight</th></tr></thead><tbody>
    ${rows.map(([sym, w]) => `<tr><td>${sym}</td><td class="num">${pct(w)}</td></tr>`).join("")}
    </tbody></table>`;
}

function renderOrders(d) {
  const orders = d.orders || [];
  if (!orders.length) {
    document.getElementById("orders").innerHTML = '<p class="hint">No orders recorded yet.</p>';
    return;
  }
  const fmtSize = (o) => (o.notional != null ? money(o.notional) : o.qty != null ? `${Number(o.qty).toFixed(2)} sh` : "–");
  const fmtTime = (t) => (t ? t.slice(0, 16).replace("T", " ") : "–");
  document.getElementById("orders").innerHTML = `
    <div class="scroll"><table><thead><tr>
      <th>Time (UTC)</th><th></th><th>Symbol</th><th>Side</th><th class="num">Size</th><th>Reason</th>
    </tr></thead><tbody>
    ${orders.map((o) => `<tr>
      <td>${fmtTime(o.time)}</td>
      <td><span class="badge ${o.placed ? "placed" : "planned"}">${o.placed ? "PLACED" : "PLANNED"}</span></td>
      <td>${o.symbol ?? "–"}</td>
      <td class="side-${o.side}">${(o.side || "").toUpperCase()}</td>
      <td class="num">${fmtSize(o)}</td>
      <td>${(o.reason || "").replace(/_/g, " ")}</td>
    </tr>`).join("")}
    </tbody></table></div>`;
}

fetch("data/dashboard.json")
  .then((r) => r.json())
  .then((d) => {
    document.getElementById("generated").textContent = "updated " + (d.generated_at || "").slice(0, 16).replace("T", " ") + " UTC";
    renderCards(d);
    renderLive(d);
    renderBacktest(d);
    renderHoldings(d);
    renderOrders(d);
  })
  .catch((e) => {
    document.getElementById("generated").textContent = "failed to load data";
    console.error(e);
  });
