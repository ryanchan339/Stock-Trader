/* =============================================================================
   Stock Trader dashboard
   Renders docs/data/dashboard.json. Every displayed number comes from that file
   so the page can never disagree with what the pipeline actually did.
   ============================================================================= */

"use strict";

const TABS = ["overview", "performance", "activity", "learn", "glossary"];
const TAB_CHARTS = { overview: ["liveChart"], performance: ["backtestChart", "wfChart"] };

let DATA = null;
let activeTab = "overview";
let orderFilter = "all";

const charts = {};
const chartAttempted = new Set();

/* ----------------------------------- utils ----------------------------------- */

const DASH = "–";

const isNum = (x) => typeof x === "number" && isFinite(x);

function esc(value) {
  return String(value == null ? "" : value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function pct(x, digits) {
  if (!isNum(x)) return DASH;
  return (x * 100).toFixed(digits == null ? 2 : digits) + "%";
}

function signed(text, x) {
  if (!isNum(x) || text === DASH) return text;
  return x >= 0 ? "+" + text : text;
}

function money(x, digits) {
  if (!isNum(x)) return DASH;
  return "$" + Number(x).toLocaleString(undefined, {
    minimumFractionDigits: digits == null ? 0 : digits,
    maximumFractionDigits: digits == null ? 0 : digits,
  });
}

function int(x) {
  return isNum(x) ? Math.round(x).toLocaleString() : DASH;
}

function num(x, digits) {
  return isNum(x) ? x.toFixed(digits == null ? 2 : digits) : DASH;
}

function stamp(iso) {
  if (!iso || typeof iso !== "string") return DASH;
  return iso.slice(0, 16).replace("T", " ");
}

function signClass(x) {
  if (!isNum(x)) return "";
  return x >= 0 ? "up" : "down";
}

function getPath(root, path) {
  return String(path).split(".").reduce(
    (acc, key) => (acc == null ? undefined : acc[key]),
    root
  );
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function daysSince(iso) {
  const then = Date.parse(iso);
  if (isNaN(then)) return null;
  return (Date.now() - then) / 86400000;
}

/* ------------------------------ data-live binding ------------------------------ */

function formatValue(value, fmt, withSign) {
  let text;
  switch (fmt) {
    case "pct": text = pct(value, 2); break;
    case "pct1": text = pct(value, 1); break;
    case "pct0": text = pct(value, 0); break;
    case "money": text = money(value); break;
    case "money2": text = money(value, 2); break;
    case "int": text = int(value); break;
    case "num2": text = num(value, 2); break;
    case "datetime": text = stamp(value); break;
    default:
      if (isNum(value)) text = value.toLocaleString();
      else if (typeof value === "string" && value) text = value;
      else if (typeof value === "boolean") text = value ? "Yes" : "No";
      else text = DASH;
  }
  return withSign ? signed(text, value) : text;
}

function fillLiveValues() {
  document.querySelectorAll("[data-live]").forEach((el) => {
    const value = getPath(DATA, el.dataset.live);
    el.textContent = formatValue(value, el.dataset.fmt, el.dataset.sign === "true");
  });
}

/* --------------------------------- derived data --------------------------------- */

function augment(d) {
  const derived = {};

  const universeSize = d.strategy && d.strategy.universe_size;
  derived.universe_peers = isNum(universeSize) ? universeSize - 1 : null;

  const live = d.live || [];
  if (live.length) {
    const first = live[0];
    const last = live[live.length - 1];
    derived.live_start = first.date;
    derived.live_end = last.date;
    derived.live_runs = live.length;
    derived.live_change = first.equity ? last.equity / first.equity - 1 : null;
    derived.live_spy_change = first.spy ? last.spy / first.spy - 1 : null;
  }

  const baselines = (d.backtest && d.backtest.baseline_metrics) || [];
  const momentum = baselines.filter((row) => row.key === "momentum_20d")[0];
  derived.momentum_baseline_return = momentum ? momentum.total_return : null;

  const orders = d.orders || [];
  derived.orders_total = orders.length;
  derived.orders_placed = orders.filter((o) => o.placed).length;
  derived.orders_planned = orders.length - derived.orders_placed;

  // Baselines are refreshed by a separate script, so they can lag the model's
  // own backtest. Surfacing the gap keeps the comparison honest.
  const strategySeries = (d.backtest && d.backtest.strategy) || [];
  const baselineSeries = Object.keys((d.backtest && d.backtest.baselines) || {})
    .map((key) => d.backtest.baselines[key])
    .filter((series) => series && series.length);
  derived.backtest_end = strategySeries.length ? strategySeries[strategySeries.length - 1].date : null;
  derived.baseline_end = baselineSeries.length
    ? baselineSeries.map((s) => s[s.length - 1].date).sort()[0]
    : null;

  d.derived = derived;
}

/* ------------------------------------ theme ------------------------------------ */

function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

function setTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  try { localStorage.setItem("st-theme", theme); } catch (e) { /* private mode */ }

  Object.keys(charts).forEach((id) => {
    charts[id].destroy();
    delete charts[id];
  });
  chartAttempted.clear();
  renderChartsFor(activeTab);
}

/* ------------------------------------- tabs ------------------------------------- */

function activateTab(name, options) {
  if (TABS.indexOf(name) === -1) name = "overview";
  activeTab = name;

  TABS.forEach((tab) => {
    const button = document.getElementById("tab-" + tab);
    const panel = document.getElementById("panel-" + tab);
    const on = tab === name;
    if (button) button.setAttribute("aria-selected", on ? "true" : "false");
    if (button) button.tabIndex = on ? 0 : -1;
    if (panel) panel.hidden = !on;
  });

  if (window.location.hash.slice(1) !== name) {
    history.replaceState(null, "", "#" + name);
  }
  if (options && options.scrollTop) {
    window.scrollTo({ top: 0, behavior: "auto" });
  }
  if (options && options.focus) {
    const button = document.getElementById("tab-" + name);
    if (button) button.focus();
  }
  renderChartsFor(name);
}

function initTabs() {
  const buttons = TABS.map((tab) => document.getElementById("tab-" + tab)).filter(Boolean);

  buttons.forEach((button) => {
    button.addEventListener("click", () => activateTab(button.dataset.tab, { scrollTop: true }));
    button.addEventListener("keydown", (event) => {
      const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      event.preventDefault();
      const index = TABS.indexOf(button.dataset.tab);
      const next = TABS[(index + step + TABS.length) % TABS.length];
      activateTab(next, { focus: true });
    });
  });

  document.addEventListener("click", (event) => {
    if (!event.target || typeof event.target.closest !== "function") return;
    const goto = event.target.closest("[data-goto]");
    if (goto) {
      activateTab(goto.dataset.goto, { scrollTop: true });
      return;
    }
    const scrollTo = event.target.closest("[data-scroll]");
    if (scrollTo) {
      const target = document.getElementById(scrollTo.dataset.scroll);
      if (target) target.scrollIntoView({ block: "start" });
    }
  });

  window.addEventListener("hashchange", () => {
    activateTab(window.location.hash.slice(1) || "overview");
  });

  activateTab(window.location.hash.slice(1) || "overview");
}

/* ------------------------------------ charts ------------------------------------ */

function chartTheme() {
  return {
    strategy: cssVar("--accent") || "#5b95ff",
    spy: "#f59e0b",
    baselines: [cssVar("--accent-2") || "#a78bfa", cssVar("--green") || "#34d399", cssVar("--red") || "#f87171"],
    grid: currentTheme() === "light" ? "rgba(15,25,40,0.08)" : "rgba(255,255,255,0.07)",
    text: cssVar("--muted") || "#90a0b3",
    surface: cssVar("--surface") || "#111823",
    border: cssVar("--border") || "#232f3f",
    strong: cssVar("--text-strong") || "#fff",
  };
}

function applyChartDefaults(theme) {
  Chart.defaults.color = theme.text;
  Chart.defaults.borderColor = theme.grid;
  Chart.defaults.font.family = cssVar("--font") || "sans-serif";
  Chart.defaults.font.size = 12;
}

function tooltipStyle(theme) {
  return {
    backgroundColor: theme.surface,
    titleColor: theme.strong,
    bodyColor: theme.text,
    borderColor: theme.border,
    borderWidth: 1,
    padding: 10,
    cornerRadius: 8,
    boxPadding: 4,
    usePointStyle: true,
  };
}

function indexTo100(values) {
  const base = values.filter((v) => isNum(v) && v !== 0)[0];
  return values.map((v) => (isNum(v) && base ? (v / base) * 100 : null));
}

function replaceCanvasWithNote(canvasId, message) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const wrap = canvas.closest(".chart-wrap") || canvas;
  const note = document.createElement("p");
  note.className = "hint";
  note.textContent = message;
  wrap.replaceWith(note);
}

function lineChart(canvasId, labels, datasets, valueFormatter) {
  const theme = chartTheme();
  applyChartDefaults(theme);
  const canvas = document.getElementById(canvasId);
  if (!canvas) return null;

  return new Chart(canvas, {
    type: "line",
    data: { labels: labels, datasets: datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      elements: { point: { radius: 0, hitRadius: 12 }, line: { borderWidth: 2, tension: 0.15 } },
      plugins: {
        legend: { labels: { usePointStyle: true, boxWidth: 8, padding: 16 } },
        tooltip: Object.assign(tooltipStyle(theme), {
          callbacks: {
            label: (ctx) => " " + ctx.dataset.label + ": " + valueFormatter(ctx.parsed.y),
          },
        }),
      },
      scales: {
        x: {
          ticks: { maxTicksLimit: 8, autoSkip: true, autoSkipPadding: 18, maxRotation: 0 },
          grid: { display: false },
        },
        y: { grid: { color: theme.grid }, ticks: { callback: (v) => valueFormatter(v) } },
      },
    },
  });
}

function buildLiveChart() {
  const live = DATA.live || [];
  if (live.length < 2) {
    replaceCanvasWithNote(
      "liveChart",
      "Not enough runs yet — the live curve appears once a couple of reweights have been recorded."
    );
    return null;
  }
  const theme = chartTheme();
  return lineChart(
    "liveChart",
    live.map((p) => p.date),
    [
      {
        label: "Paper account",
        data: indexTo100(live.map((p) => p.equity)),
        borderColor: theme.strategy,
        backgroundColor: theme.strategy,
        fill: false,
      },
      {
        label: "SPY (buy & hold)",
        data: indexTo100(live.map((p) => p.spy)),
        borderColor: theme.spy,
        backgroundColor: theme.spy,
        fill: false,
      },
    ],
    (v) => (isNum(v) ? v.toFixed(1) : DASH)
  );
}

function buildBacktestChart() {
  const backtest = DATA.backtest || {};
  const strategy = backtest.strategy || [];
  if (strategy.length < 2) {
    replaceCanvasWithNote("backtestChart", "No backtest equity curve has been generated yet.");
    return null;
  }
  const theme = chartTheme();
  const labels = strategy.map((p) => p.date);

  // Baselines can cover a shorter window, so align them onto the strategy's dates.
  const alignTo = (series) => {
    const map = {};
    series.forEach((p) => { map[p.date] = p.equity; });
    return labels.map((date) => (date in map ? map[date] : null));
  };

  const datasets = [
    {
      label: "Model blend (this strategy)",
      data: strategy.map((p) => p.equity),
      borderColor: theme.strategy,
      backgroundColor: theme.strategy,
      borderWidth: 2.4,
    },
  ];
  const baselines = backtest.baselines || {};
  Object.keys(baselines).forEach((name, i) => {
    datasets.push({
      label: name,
      data: alignTo(baselines[name]),
      borderColor: theme.baselines[i % theme.baselines.length],
      backgroundColor: theme.baselines[i % theme.baselines.length],
      borderWidth: 1.4,
      borderDash: [4, 3],
      spanGaps: false,
    });
  });

  return lineChart("backtestChart", labels, datasets, (v) => money(v));
}

function buildWalkForwardChart() {
  const folds = (DATA.walk_forward || {}).folds || [];
  if (!folds.length) {
    replaceCanvasWithNote("wfChart", "No walk-forward results have been generated yet.");
    return null;
  }
  const theme = chartTheme();
  applyChartDefaults(theme);
  const canvas = document.getElementById("wfChart");
  if (!canvas) return null;

  return new Chart(canvas, {
    type: "bar",
    data: {
      labels: folds.map((f) => String(f.test_year)),
      datasets: [
        {
          label: "Strategy that year",
          data: folds.map((f) => (isNum(f.backtest_total_return) ? f.backtest_total_return * 100 : null)),
          backgroundColor: theme.strategy,
          borderRadius: 4,
        },
        {
          label: "SPY that year",
          data: folds.map((f) => (isNum(f.backtest_benchmark_return) ? f.backtest_benchmark_return * 100 : null)),
          backgroundColor: theme.spy,
          borderRadius: 4,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { mode: "index", intersect: false },
      plugins: {
        legend: { labels: { usePointStyle: true, boxWidth: 8, padding: 16 } },
        tooltip: Object.assign(tooltipStyle(theme), {
          callbacks: {
            label: (ctx) => " " + ctx.dataset.label + ": " + (isNum(ctx.parsed.y) ? ctx.parsed.y.toFixed(1) + "%" : DASH),
          },
        }),
      },
      scales: {
        x: { grid: { display: false } },
        y: { grid: { color: theme.grid }, ticks: { callback: (v) => v + "%" } },
      },
    },
  });
}

const chartFactories = {
  liveChart: buildLiveChart,
  backtestChart: buildBacktestChart,
  wfChart: buildWalkForwardChart,
};

function renderChartsFor(tab) {
  if (!DATA || typeof Chart === "undefined") return;
  (TAB_CHARTS[tab] || []).forEach((id) => {
    if (chartAttempted.has(id)) return;
    chartAttempted.add(id);
    const chart = chartFactories[id]();
    if (chart) charts[id] = chart;
  });
}

/* ---------------------------------- renderers ---------------------------------- */

function cardHtml(card) {
  return (
    '<div class="card">' +
    '<div class="label">' + esc(card.label) + "</div>" +
    '<div class="value ' + (card.cls || "") + '">' + esc(card.value) + "</div>" +
    (card.sub ? '<div class="sub">' + esc(card.sub) + "</div>" : "") +
    "</div>"
  );
}

function renderCards(elementId, cards) {
  const host = document.getElementById(elementId);
  if (host) host.innerHTML = cards.map(cardHtml).join("");
}

function renderHeadlineCards() {
  const h = DATA.headline || {};
  renderCards("headline-cards", [
    {
      label: "Strategy return",
      value: signed(pct(h.total_return, 1), h.total_return),
      cls: signClass(h.total_return),
      sub: "simulated, not real money",
    },
    {
      label: "SPY return",
      value: signed(pct(h.benchmark_return, 1), h.benchmark_return),
      sub: "doing nothing at all",
    },
    {
      label: "Excess vs SPY",
      value: signed(pct(h.excess_return, 1), h.excess_return),
      cls: signClass(h.excess_return),
      sub: "the part that justifies the effort",
    },
    {
      label: "Max drawdown",
      value: pct(h.max_drawdown, 1),
      cls: "down",
      sub: "worst fall from a peak",
    },
    {
      label: "Sharpe ratio",
      value: num(h.sharpe, 2),
      sub: "reward per unit of bumpiness",
    },
    {
      label: "Model accuracy",
      value: pct(h.accuracy, 1),
      sub: "a coin flip is 50%",
    },
  ]);
}

function renderFreshness() {
  const pill = document.getElementById("freshness");
  const label = document.getElementById("generated");
  if (!label) return;
  const generated = DATA.generated_at;
  // The "updated" prefix and the clock time collapse away on narrow screens,
  // leaving just the date so the header stays on one line.
  const parts = stamp(generated).split(" ");
  label.innerHTML =
    '<span class="hide-sm">updated </span>' + esc(parts[0]) +
    (parts[1] ? '<span class="hide-sm"> ' + esc(parts[1]) + " UTC</span>" : "");
  const age = daysSince(generated);
  if (pill && age != null && age > 10) {
    pill.classList.add("is-stale");
    pill.title = "This data is " + Math.floor(age) + " days old; the weekly reweight may not have run.";
  }
}

function renderRegimePill() {
  const pill = document.getElementById("regime-pill");
  if (!pill) return;
  const current = DATA.current || {};
  if (current.risk_off == null) {
    pill.hidden = true;
    return;
  }
  pill.textContent = current.risk_off
    ? "Risk-off: market below its trend, exposure halved"
    : "Risk-on: market above its trend";
}

function renderHoldings() {
  const host = document.getElementById("holdings");
  if (!host) return;
  const current = DATA.current || {};
  const holdings = current.holdings || {};
  const picks = current.picks || [];

  let rows = picks.map((pick) => ({
    rank: pick.rank,
    symbol: pick.symbol,
    close: pick.close,
    move: pick.return_20d,
    vol: pick.volatility_20d,
    raw: pick.target_weight,
    final: holdings[pick.symbol] == null ? null : holdings[pick.symbol],
  }));

  if (!rows.length) {
    rows = Object.keys(holdings)
      .sort((a, b) => holdings[b] - holdings[a])
      .map((symbol, i) => ({
        rank: i + 1, symbol: symbol, close: null, move: null, vol: null,
        raw: null, final: holdings[symbol],
      }));
  }

  if (!rows.length) {
    host.innerHTML = '<p class="hint">No current targets.</p>';
    return;
  }

  const maxWeight = Math.max.apply(null, rows.map((r) => (isNum(r.final) ? r.final : 0)).concat([0.01]));
  // First row whose raw target was actually trimmed — not simply rows[0], which
  // may be a pick the safety rules excluded outright.
  const capped = rows.filter(
    (r) => isNum(r.raw) && isNum(r.final) && Math.abs(r.raw - r.final) > 1e-6
  )[0];

  host.innerHTML =
    '<div class="table-scroll"><table><thead><tr>' +
    "<th>#</th><th>Symbol</th><th class=\"num\">Price</th>" +
    '<th class="num">Last month</th><th class="num">Jumpiness</th>' +
    '<th class="num">Final weight</th><th class="weight-cell"></th>' +
    "</tr></thead><tbody>" +
    rows.map((row) => {
      const width = isNum(row.final) ? Math.max(3, (row.final / maxWeight) * 100) : 0;
      const weightText = isNum(row.final)
        ? pct(row.final, 1)
        : '<span class="badge warn">EXCLUDED</span>';
      return (
        "<tr>" +
        '<td><span class="rank-pip">' + esc(row.rank == null ? "?" : row.rank) + "</span></td>" +
        '<td class="sym">' + esc(row.symbol) + "</td>" +
        '<td class="num">' + money(row.close, 2) + "</td>" +
        '<td class="num ' + (isNum(row.move) ? (row.move >= 0 ? "side-buy" : "side-sell") : "") + '">' +
          signed(pct(row.move, 1), row.move) + "</td>" +
        '<td class="num">' + pct(row.vol, 1) + "</td>" +
        '<td class="num">' + weightText + "</td>" +
        '<td class="weight-cell"><span class="weight-bar" style="width:' + width.toFixed(1) + '%"></span></td>' +
        "</tr>"
      );
    }).join("") +
    "</tbody></table></div>";

  const notes = [];
  if (capped) {
    notes.push(
      "Each pick's raw target was " + pct(capped.raw, 0) +
      ", trimmed to " + pct(capped.final, 0) + " by the per-stock cap."
    );
  }
  const invested = rows.reduce((total, r) => total + (isNum(r.final) ? r.final : 0), 0);
  notes.push(pct(invested, 0) + " of the account invested, the rest held as cash.");
  const excluded = (current.excluded || []).length;
  if (excluded) notes.push(excluded + " pick(s) were rejected by the safety rules.");
  notes.push('"Jumpiness" is the typical size of a daily move over the last 20 trading days.');

  const note = document.createElement("p");
  note.className = "hint chart-foot";
  note.textContent = notes.join(" ");
  host.appendChild(note);
}

function renderLiveFoot() {
  const foot = document.getElementById("live-foot");
  if (!foot) return;
  const d = DATA.derived || {};
  if (!isNum(d.live_change)) { foot.textContent = ""; return; }
  foot.textContent =
    "From " + d.live_start + " to " + d.live_end + " (" + d.live_runs + " recorded reweights): " +
    "paper account " + signed(pct(d.live_change, 2), d.live_change) + ", " +
    "SPY " + signed(pct(d.live_spy_change, 2), d.live_spy_change) + ". " +
    "This window is very short — treat it as a sanity check that the plumbing works, not as evidence.";
}

function renderBacktestFoot() {
  const foot = document.getElementById("backtest-foot");
  if (!foot) return;
  const d = DATA.derived || {};
  const parts = ["A 5 basis-point slippage cost is charged on every simulated trade."];
  if (d.backtest_end && d.baseline_end && d.baseline_end < d.backtest_end) {
    parts.push(
      "The baseline curves were last recomputed on " + d.baseline_end +
      " while the strategy curve runs to " + d.backtest_end +
      ", so the dashed lines stop early and their totals cover a shorter window."
    );
  }
  foot.textContent = parts.join(" ");
}

function renderBaselineTable() {
  const host = document.getElementById("baseline-table");
  if (!host) return;
  const h = DATA.headline || {};
  const baselines = (DATA.backtest || {}).baseline_metrics || [];

  const rows = [{
    label: "Model blend",
    blurb: "This strategy: " + pct(DATA.strategy ? DATA.strategy.model_weight : null, 0) +
           " model + " + pct(DATA.strategy ? DATA.strategy.momentum_weight : null, 0) + " momentum.",
    total_return: h.total_return,
    benchmark_return: h.benchmark_return,
    excess_return: h.excess_return,
    max_drawdown: h.max_drawdown,
    sharpe: h.sharpe,
    self: true,
  }].concat(baselines);

  if (rows.length === 1 && !isNum(h.total_return)) {
    host.innerHTML = '<p class="hint">No comparison metrics available yet.</p>';
    return;
  }

  host.innerHTML =
    '<div class="table-scroll"><table><thead><tr>' +
    "<th>Strategy</th><th class=\"num\">Return</th><th class=\"num\">SPY, same window</th>" +
    '<th class="num">Excess</th><th class="num">Max drawdown</th><th class="num">Sharpe</th>' +
    "</tr></thead><tbody>" +
    rows.map((row) => (
      "<tr" + (row.self ? ' class="is-highlight"' : "") + ">" +
      "<td><strong>" + esc(row.label) + "</strong>" +
        (row.blurb ? '<div class="hint">' + esc(row.blurb) + "</div>" : "") + "</td>" +
      '<td class="num">' + signed(pct(row.total_return, 1), row.total_return) + "</td>" +
      '<td class="num">' + signed(pct(row.benchmark_return, 1), row.benchmark_return) + "</td>" +
      '<td class="num ' + (isNum(row.excess_return) ? (row.excess_return >= 0 ? "side-buy" : "side-sell") : "") + '">' +
        signed(pct(row.excess_return, 1), row.excess_return) + "</td>" +
      '<td class="num">' + pct(row.max_drawdown, 1) + "</td>" +
      '<td class="num">' + num(row.sharpe, 2) + "</td>" +
      "</tr>"
    )).join("") +
    "</tbody></table></div>" +
    '<p class="hint chart-foot">Each row is the same simulation with a different way of choosing the 5 stocks. ' +
    "The SPY column differs between rows only because the baselines were last recomputed on an earlier date.</p>";
}

function renderWalkForward() {
  const wf = DATA.walk_forward || {};
  const aggregate = wf.aggregate || {};
  const folds = wf.folds || [];

  renderCards("wf-cards", [
    {
      label: "Years tested",
      value: isNum(aggregate.folds) ? String(aggregate.folds) : DASH,
      sub: "each trained on the past only",
    },
    {
      label: "Avg excess vs SPY",
      value: signed(pct(aggregate.mean_excess_return, 1), aggregate.mean_excess_return),
      cls: signClass(aggregate.mean_excess_return),
      sub: "per tested year",
    },
    {
      label: "Years it beat SPY",
      value: isNum(aggregate.positive_excess_folds)
        ? aggregate.positive_excess_folds + " of " + (aggregate.folds || DASH)
        : DASH,
      sub: "coin flip would give half",
    },
    {
      label: "Worst drawdown",
      value: pct(aggregate.worst_max_drawdown, 1),
      cls: "down",
      sub: "deepest fall in any year",
    },
    {
      label: "Avg ROC AUC",
      value: num(aggregate.mean_roc_auc, 3),
      sub: "0.50 = no ranking skill",
    },
  ]);

  const host = document.getElementById("wf-table");
  if (!host) return;
  if (!folds.length) {
    host.innerHTML = '<p class="hint">No walk-forward folds recorded yet.</p>';
    return;
  }

  host.innerHTML =
    '<div class="table-scroll"><table><thead><tr>' +
    '<th>Test year</th><th class="num">Strategy</th><th class="num">SPY</th><th class="num">Excess</th>' +
    '<th class="num">Max drawdown</th><th class="num">Sharpe</th><th class="num">ROC AUC</th>' +
    "</tr></thead><tbody>" +
    folds.map((fold) => (
      "<tr>" +
      '<td class="sym">' + esc(fold.test_year) + "</td>" +
      '<td class="num">' + signed(pct(fold.backtest_total_return, 1), fold.backtest_total_return) + "</td>" +
      '<td class="num">' + signed(pct(fold.backtest_benchmark_return, 1), fold.backtest_benchmark_return) + "</td>" +
      '<td class="num ' + (isNum(fold.backtest_excess_return) ? (fold.backtest_excess_return >= 0 ? "side-buy" : "side-sell") : "") + '">' +
        signed(pct(fold.backtest_excess_return, 1), fold.backtest_excess_return) + "</td>" +
      '<td class="num">' + pct(fold.backtest_max_drawdown, 1) + "</td>" +
      '<td class="num">' + num(fold.backtest_sharpe, 2) + "</td>" +
      '<td class="num">' + num(fold.roc_auc, 3) + "</td>" +
      "</tr>"
    )).join("") +
    "</tbody></table></div>" +
    '<p class="hint chart-foot">The most recent year is usually a partial one — it only covers the days recorded so far.</p>';
}

function renderVerdictCards() {
  const h = DATA.headline || {};
  const aggregate = (DATA.walk_forward || {}).aggregate || {};
  renderCards("verdict-cards", [
    {
      label: "Prediction accuracy",
      value: pct(h.accuracy, 1),
      sub: "coin flip = 50.0%",
    },
    {
      label: "Ranking skill",
      value: num(h.roc_auc, 3),
      sub: "no skill = 0.500",
    },
    {
      label: "Honest test: excess",
      value: signed(pct(aggregate.mean_excess_return, 1), aggregate.mean_excess_return),
      cls: signClass(aggregate.mean_excess_return),
      sub: "avg per walk-forward year",
    },
    {
      label: "Honest test: hit rate",
      value: isNum(aggregate.positive_excess_folds)
        ? aggregate.positive_excess_folds + " of " + (aggregate.folds || DASH)
        : DASH,
      sub: "years that beat SPY",
    },
  ]);
}

/* ---------------------------------- activity ---------------------------------- */

function renderActivityCards() {
  const current = DATA.current || {};
  const derived = DATA.derived || {};
  renderCards("activity-cards", [
    { label: "Paper equity", value: money(current.equity), sub: "pretend dollars" },
    {
      label: "Latest run",
      value: current.mode === "submit" ? "Submitted" : "Dry run",
      sub: stamp(current.as_of) + " UTC",
    },
    { label: "Account type", value: current.paper === false ? "LIVE" : "Paper", cls: current.paper === false ? "down" : "", sub: "no real money if paper" },
    { label: "Orders recorded", value: int(derived.orders_total), sub: derived.orders_placed + " placed / " + derived.orders_planned + " planned" },
    { label: "Reweights logged", value: int(derived.live_runs), sub: "one per recorded market date" },
  ]);
}

function orderSize(order) {
  if (isNum(order.notional)) return money(order.notional);
  if (isNum(order.qty)) return num(order.qty, 2) + " sh";
  return DASH;
}

const ORDER_REASONS = {
  exit_non_target: "no longer in the top picks",
  raise_to_target: "top up to target weight",
  trim_to_target: "trim down to target weight",
};

function renderOrders() {
  const host = document.getElementById("orders");
  if (!host) return;
  const all = DATA.orders || [];
  const orders = all.filter((order) =>
    orderFilter === "all" ? true : orderFilter === "placed" ? order.placed : !order.placed
  );

  if (!all.length) {
    host.innerHTML = '<p class="hint">No orders recorded yet.</p>';
    return;
  }
  if (!orders.length) {
    host.innerHTML = '<p class="hint">No ' + esc(orderFilter) + " orders recorded yet.</p>";
    return;
  }

  host.innerHTML =
    '<div class="scroll"><table><thead><tr>' +
    "<th>Time (UTC)</th><th>Status</th><th>Symbol</th><th>Side</th>" +
    '<th class="num">Size</th><th>Why</th>' +
    "</tr></thead><tbody>" +
    orders.map((order) => (
      "<tr>" +
      "<td>" + esc(stamp(order.time)) + "</td>" +
      '<td><span class="badge ' + (order.placed ? "placed" : "planned") + '">' +
        (order.placed ? "PLACED" : "PLANNED") + "</span></td>" +
      '<td class="sym">' + esc(order.symbol == null ? DASH : order.symbol) + "</td>" +
      '<td class="side-' + esc(order.side) + '">' + esc((order.side || "").toUpperCase()) + "</td>" +
      '<td class="num">' + orderSize(order) + "</td>" +
      "<td>" + esc(ORDER_REASONS[order.reason] || String(order.reason || "").replace(/_/g, " ")) + "</td>" +
      "</tr>"
    )).join("") +
    "</tbody></table></div>" +
    '<p class="hint chart-foot">Showing ' + orders.length + " of " + all.length +
    " recorded order events, newest first.</p>";
}

function initOrderFilter() {
  document.querySelectorAll("[data-order-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      orderFilter = button.dataset.orderFilter;
      document.querySelectorAll("[data-order-filter]").forEach((other) => {
        other.classList.toggle("is-active", other === button);
      });
      renderOrders();
    });
  });
}

function riskRules() {
  const strategy = DATA.strategy || {};
  const controls = strategy.risk_controls || {};
  const rules = [];

  if (isNum(controls.max_position_weight)) {
    rules.push({
      name: "Position cap",
      text: "Never put more than " + pct(controls.max_position_weight, 0) +
            " of the account into any single stock, however good it looks.",
    });
  }
  if (isNum(controls.max_gross_exposure)) {
    rules.push({
      name: "Total exposure cap",
      text: "Never have more than " + pct(controls.max_gross_exposure, 0) +
            " of the account invested at once — the rest sits in cash as a shock absorber.",
    });
  }
  if (isNum(controls.min_price)) {
    rules.push({
      name: "Penny-stock filter",
      text: "Skip anything trading below " + money(controls.min_price, 0) +
            " a share. Very cheap stocks behave badly and cost more to trade.",
    });
  }
  if (isNum(controls.max_volatility_20d)) {
    rules.push({
      name: "Wildness filter",
      text: "Skip anything whose typical daily move over the last 20 days was bigger than " +
            pct(controls.max_volatility_20d, 0) + ".",
    });
  }
  if (isNum(controls.risk_off_spy_ma_days) && isNum(controls.risk_off_exposure_multiplier)) {
    const reduced = isNum(controls.max_gross_exposure)
      ? " (down to " + pct(controls.max_gross_exposure * controls.risk_off_exposure_multiplier, 1) + ")"
      : "";
    rules.push({
      name: "Risk-off switch",
      text: "If SPY closes below its own " + controls.risk_off_spy_ma_days +
            "-day average — a crude 'the market is trending down' signal — cut the maximum invested amount by " +
            pct(1 - controls.risk_off_exposure_multiplier, 0) + reduced + ".",
    });
  }
  if (isNum(strategy.min_notional)) {
    rules.push({
      name: "Don't fuss",
      text: "Ignore any adjustment smaller than " + money(strategy.min_notional, 0) +
            ". Nudging a position by a few dollars is not worth a trade.",
    });
  }
  return rules;
}

function renderRiskControls() {
  const rules = riskRules();

  const host = document.getElementById("risk-controls");
  if (host) {
    host.innerHTML = rules.length
      ? '<div class="table-scroll"><table><thead><tr><th>Rule</th><th>What it does</th></tr></thead><tbody>' +
        rules.map((rule) =>
          "<tr><td><strong>" + esc(rule.name) + "</strong></td><td>" + esc(rule.text) + "</td></tr>"
        ).join("") +
        "</tbody></table></div>"
      : '<p class="hint">No risk controls recorded in the latest plan.</p>';
  }

  const list = document.getElementById("rules-list");
  if (list) {
    list.innerHTML = rules.map((rule) =>
      "<li><strong>" + esc(rule.name) + ".</strong> " + esc(rule.text) + "</li>"
    ).join("");
  }
}

function renderRegime() {
  const host = document.getElementById("regime");
  if (!host) return;
  const regime = (DATA.current || {}).market_regime || {};
  if (!regime.status) {
    host.innerHTML = '<p class="hint">No market regime recorded in the latest plan.</p>';
    return;
  }

  const verdict = regime.risk_off
    ? "SPY is BELOW its average — risk-off. The strategy is allowed to invest only half as much."
    : "SPY is ABOVE its average — risk-on. Normal exposure allowed.";

  const rows = [
    ["Benchmark", esc(regime.benchmark || DASH)],
    ["Market date", esc(regime.date || DASH)],
    ["SPY close", money(regime.close, 2)],
    [regime.moving_average_days + "-day average", money(regime.moving_average, 2)],
    ["Data check", esc(regime.status === "ok" ? "ok" : regime.status)],
    ["Verdict", esc(verdict)],
  ];

  host.innerHTML =
    '<div class="table-scroll"><table><tbody>' +
    rows.map((row) => "<tr><td>" + row[0] + '</td><td class="sym">' + row[1] + "</td></tr>").join("") +
    "</tbody></table></div>";
}

function renderPickCompare() {
  const host = document.getElementById("pick-compare");
  if (!host) return;
  const current = DATA.current || {};
  const blend = current.picks || [];
  const momentum = current.momentum_picks || [];

  if (!blend.length && !momentum.length) {
    host.innerHTML = '<p class="hint">No recommendations recorded yet.</p>';
    return;
  }

  const momentumSymbols = {};
  momentum.forEach((pick) => { momentumSymbols[pick.symbol] = true; });
  const blendSymbols = {};
  blend.forEach((pick) => { blendSymbols[pick.symbol] = true; });

  const rowsFor = (picks, otherSet) =>
    picks.map((pick) =>
      "<tr>" +
      '<td><span class="rank-pip">' + esc(pick.rank) + "</span></td>" +
      '<td class="sym">' + esc(pick.symbol) + "</td>" +
      '<td class="num">' + signed(pct(pick.return_20d, 1), pick.return_20d) + "</td>" +
      "<td>" + (otherSet[pick.symbol] ? '<span class="badge planned">SHARED</span>' : '<span class="badge warn">ONLY HERE</span>') + "</td>" +
      "</tr>"
    ).join("");

  const overlap = blend.filter((pick) => momentumSymbols[pick.symbol]).length;

  host.innerHTML =
    '<div class="clue-grid">' +
      '<div class="clue"><h5>Blend (what it trades)</h5>' +
        '<div class="table-scroll"><table><thead><tr><th>#</th><th>Symbol</th><th class="num">Last month</th><th></th></tr></thead>' +
        "<tbody>" + rowsFor(blend, momentumSymbols) + "</tbody></table></div></div>" +
      '<div class="clue"><h5>Pure momentum (no model)</h5>' +
        '<div class="table-scroll"><table><thead><tr><th>#</th><th>Symbol</th><th class="num">Last month</th><th></th></tr></thead>' +
        "<tbody>" + rowsFor(momentum, blendSymbols) + "</tbody></table></div></div>" +
    "</div>" +
    '<p class="hint">' + overlap + " of " + (blend.length || 0) +
    " picks are identical. The machine learning only changed the rest.</p>";
}

/* ------------------------------------ learn ------------------------------------ */

function renderUniverse() {
  const host = document.getElementById("universe-chips");
  if (!host) return;
  const universe = (DATA.strategy || {}).universe || [];
  host.innerHTML = universe.map((symbol) => '<span class="chip">' + esc(symbol) + "</span>").join("");
}

function renderMetricGuide() {
  const host = document.getElementById("metric-guide");
  if (!host) return;
  const h = DATA.headline || {};
  const aggregate = (DATA.walk_forward || {}).aggregate || {};

  const entries = [
    {
      term: "Return",
      text: "How much a pretend $100,000 grew over the test period, in percent. A return of 100% means it doubled.",
      now: "backtest: " + signed(pct(h.total_return, 1), h.total_return),
    },
    {
      term: "SPY return",
      text: "What you would have made over the same days by buying the whole market once and never touching it again. This is the thing to beat.",
      now: "backtest: " + signed(pct(h.benchmark_return, 1), h.benchmark_return),
    },
    {
      term: "Excess return",
      text: "Return minus SPY return. It is the only number that justifies doing any of this. If it is negative, the strategy lost to doing nothing.",
      now: "backtest: " + signed(pct(h.excess_return, 1), h.excess_return) +
           "  ·  honest test: " + signed(pct(aggregate.mean_excess_return, 1), aggregate.mean_excess_return) + " per year",
    },
    {
      term: "Max drawdown",
      text: "The worst fall from a previous high point. −20% means that at some moment the account was worth a fifth less than its best-ever value. This is the number that decides whether a human could actually have stuck with the strategy.",
      now: "backtest: " + pct(h.max_drawdown, 1) + "  ·  worst tested year: " + pct(aggregate.worst_max_drawdown, 1),
    },
    {
      term: "Sharpe ratio",
      text: "Return divided by how bumpy the ride was, scaled to a year. Roughly 'reward per unit of stress'. Above 1 is usually called good, but it is easy to inflate over a short or lucky stretch.",
      now: "backtest: " + num(h.sharpe, 2),
    },
    {
      term: "Accuracy",
      text: "Out of all the individual yes/no predictions the model made, the fraction it got right. 50% is exactly a coin flip, so anything near 50% means individual predictions are close to worthless on their own.",
      now: "now: " + pct(h.accuracy, 1),
    },
    {
      term: "Precision",
      text: "When the model said 'yes, this will beat the market', how often was it actually right?",
      now: "now: " + pct(h.precision, 1),
    },
    {
      term: "ROC AUC",
      text: "How well the model sorts winners above losers, from 0.5 (no better than shuffling) to 1.0 (perfect). It is a fairer measure than accuracy because it ignores where you draw the yes/no line and only looks at the ordering.",
      now: "now: " + num(h.roc_auc, 3) + "  ·  honest test avg: " + num(aggregate.mean_roc_auc, 3),
    },
    {
      term: "Target weight",
      text: "The share of the account meant to be sitting in one stock. 15% of a $100,000 account is $15,000 of that stock.",
      now: "per pick now: " + pct((DATA.strategy || {}).raw_target_weight, 0) + " raw",
    },
  ];

  host.className = "metric-guide";
  host.innerHTML = entries.map((entry) => (
    '<dl class="metric-row"><dt>' + esc(entry.term) + "</dt>" +
    "<dd>" + esc(entry.text) +
    (entry.now ? '<br /><span class="now">' + esc(entry.now) + "</span>" : "") +
    "</dd></dl>"
  )).join("");
}

/* ----------------------------------- glossary ----------------------------------- */

const GLOSSARY = [
  { id: "backtest", term: "Backtest", def: "Replaying a strategy over historical data to see what it would have done. Cheap to run and dangerously easy to fool yourself with, because you already know how the story ended.", here: "This site's backtest starts at the test-start date and rebalances every few trading days." },
  { id: "baseline", term: "Baseline", def: "A deliberately simple strategy used as a yardstick. If your complicated method cannot beat the simple one, the complexity is not paying for itself.", here: "Three baselines run through identical machinery: top momentum, top relative strength, and lowest volatility." },
  { id: "benchmark", term: "Benchmark", def: "The thing you measure yourself against. Usually 'what would have happened if I had done nothing clever'.", here: "Always SPY." },
  { id: "bps", term: "Basis point (bp)", def: "One hundredth of one percent. 5 basis points = 0.05%. Used because 'the fee went from 0.30% to 0.35%' is ambiguous, while 'rose 5 bps' is not.", here: "Every simulated trade is charged 5 bps to approximate real-world costs." },
  { id: "buy-and-hold", term: "Buy and hold", def: "Buy something once and never trade it again. The lazy option, and it is surprisingly hard to beat after costs." },
  { id: "cross-sectional", term: "Cross-sectional", def: "Comparing many things at one moment in time, rather than one thing across time. 'Who is fastest in the race right now' instead of 'is he faster than last year'.", here: "Every clue is also turned into a cross-sectional rank against the other companies that same day." },
  { id: "drawdown", term: "Drawdown", def: "How far something has fallen from its most recent peak. A max drawdown of −30% means that at some point you were down 30% from your best moment — which is the moment most people quit." },
  { id: "equity", term: "Equity", def: "The total value of an account: cash plus whatever the holdings are currently worth." },
  { id: "equity-curve", term: "Equity curve", def: "A line showing account value over time. The shape matters more than the endpoint: a smooth climb and a violent one can end in the same place." },
  { id: "exposure", term: "Gross exposure", def: "How much of the account is actually invested rather than sitting in cash. 75% exposure means a quarter of the money is on the sidelines.", here: "Capped at the max-gross-exposure limit, and halved when the market is in a downtrend." },
  { id: "feature", term: "Feature", def: "One measurable input given to a model. If the model is a recipe, features are the ingredients. Choosing them is most of the work in practice.", here: "Around 33 features per stock per day, all derived from price and volume alone." },
  { id: "gradient-boosting", term: "Gradient boosting", def: "A way of building a good predictor out of many weak ones. Build a crude flowchart; build a second one that specialises in fixing the first one's errors; then a third; repeat hundreds of times and add up the votes. Excellent on tabular data and completely unlike a chatbot.", here: "The model is a HistGradientBoostingClassifier with 450 rounds." },
  { id: "label", term: "Label", def: "The known right answer attached to a historical example — what the model is trying to learn to reproduce.", here: "1 if the stock beat SPY over the next 5 trading days, 0 otherwise." },
  { id: "large-cap", term: "Large cap", def: "A big company, measured by the total market value of all its shares. Large caps are heavily traded, so orders are easy to fill and prices are less easily pushed around." },
  { id: "market-order", term: "Market order", def: "An instruction to buy or sell immediately at whatever the going price is, rather than naming your price. Fast, certain to execute, and you take whatever price you get." },
  { id: "momentum", term: "Momentum", def: "The observation that things that have been going up recently have a mild tendency to keep going up for a while. Not a law, and it reverses painfully and without warning.", here: "20-day momentum makes up three quarters of the final score." },
  { id: "moving-average", term: "Moving average", def: "The average price over the last N days, recalculated each day. It smooths out the daily noise so the underlying trend is visible. A 100-day moving average reacts slowly; a 10-day one reacts fast." },
  { id: "notional", term: "Notional", def: "The dollar amount of an order, as opposed to the number of shares. 'Buy $5,000 of Apple' rather than 'buy 15 shares'." },
  { id: "overfitting", term: "Overfitting", def: "Learning the noise in your historical data instead of a real pattern. The symptom is a model that looks brilliant on the data it was built with and useless on anything new — like memorising the answers to last year's exam.", here: "This is why the walk-forward test exists and why the headline backtest deserves suspicion." },
  { id: "paper-trading", term: "Paper trading", def: "Trading with pretend money in an account that otherwise behaves like a real one. It tests the plumbing and the discipline, but it cannot test how you feel when actual savings are falling.", here: "Everything on this site is paper trading. No real money is ever involved." },
  { id: "position", term: "Position", def: "A holding in one particular stock — how many shares you own and what they are worth." },
  { id: "precision", term: "Precision", def: "Out of everything the model flagged as a winner, the share that really were winners." },
  { id: "probability", term: "Probability (model output)", def: "A number between 0 and 1 expressing the model's confidence. 0.58 means it leans yes. Treat these as rough leanings, not real odds — they are only as calibrated as the training data allows." },
  { id: "rank", term: "Percentile rank", def: "Where something sits within a group, on a 0-to-1 scale. 0.94 means it beats 94% of the group. Ranking strips out the overall mood of the market and leaves only the comparison." },
  { id: "rebalance", term: "Rebalance", def: "Periodically resetting holdings back to their intended sizes, selling what has drifted up and buying what has drifted down or newly qualified.", here: "Every 5 trading days — roughly weekly." },
  { id: "relative-strength", term: "Relative strength", def: "How a stock has performed compared to the market, rather than on its own. Up 3% while the market rose 6% is weakness, not strength." },
  { id: "risk-off", term: "Risk-off", def: "A defensive mode: reduce how much is invested because conditions look poor. The opposite is risk-on.", here: "Triggered when SPY closes below its own 100-day moving average; the exposure limit is then halved." },
  { id: "roc-auc", term: "ROC AUC", def: "A score from 0.5 to 1.0 for how well a model sorts winners above losers. 0.5 is a shuffled deck. In stock prediction, 0.52 is already considered a real (if tiny) signal — the bar is genuinely that low." },
  { id: "rsi", term: "RSI (Relative Strength Index)", def: "A 0-to-100 gauge of how one-sided recent daily moves have been. Above 70 is traditionally called 'overbought', below 30 'oversold', though these labels predict much less than folklore suggests." },
  { id: "sharpe", term: "Sharpe ratio", def: "Return divided by volatility, annualised: reward per unit of bumpiness. Two strategies can return the same amount while one is far more comfortable to hold; Sharpe is an attempt to capture that difference in one number." },
  { id: "slippage", term: "Slippage", def: "The gap between the price you expected and the price you actually got. Real orders move the market slightly and arrive a moment late; both cost money.", here: "Assumed at 5 basis points per simulated trade, which is optimistic." },
  { id: "spy", term: "SPY", def: "A fund that holds a slice of the 500 largest US companies in one tradeable package. Buying it is the simplest way to own 'the market', which makes it the natural thing to be measured against.", here: "The benchmark for every number on this site, and the input to the risk-off filter." },
  { id: "survivorship", term: "Survivorship bias", def: "Testing on a list of companies chosen today, which quietly excludes everything that collapsed along the way. It flatters every historical result, sometimes enormously.", here: "The company list here was hand-picked recently, so this bias is present." },
  { id: "symbol", term: "Ticker symbol", def: "The short code identifying a stock on an exchange — AAPL for Apple, JPM for JPMorgan. Just a name, carrying no information." },
  { id: "training", term: "Training and test data", def: "Training data is what the model learns from; test data is held back to check it on material it has never seen. Mixing them up is the fastest way to produce impressive and completely fake results.", here: "Everything before the test-start date trains; everything after is used to judge." },
  { id: "universe", term: "Universe", def: "The fixed list of things a strategy is allowed to choose from. Choosing the universe is itself a decision, and often a bigger one than the model." },
  { id: "volatility", term: "Volatility", def: "How much something jumps around, measured by the spread of its daily moves. High volatility means big swings in both directions — it is a measure of turbulence, not of direction.", here: "Anything whose recent daily moves are wilder than the limit is filtered out entirely." },
  { id: "volume", term: "Volume", def: "How many shares changed hands. Unusually high volume means unusual interest — news, panic, or enthusiasm — though it does not say which." },
  { id: "walk-forward", term: "Walk-forward test", def: "Train on everything up to a date, test on the period that follows, then roll forward and repeat. It imitates actually having used the strategy over the years, knowing only what was knowable at each point. Harsher than a plain backtest and far more informative.", here: "Five yearly folds, and the results are notably worse than the headline backtest." },
  { id: "weight", term: "Weight", def: "The share of the account allocated to one holding, as a percentage. Weights are used instead of dollar amounts so the plan works at any account size." },
  { id: "z-score", term: "Z-score", def: "How unusual a value is compared with its own recent history, measured in typical-step sizes. A z-score of 2 means 'about twice as far from normal as this thing usually strays' — a way of saying 'unusual' that works for any quantity." },
];

function renderGlossary(filter) {
  const host = document.getElementById("glossary-list");
  const empty = document.getElementById("glossary-empty");
  const count = document.getElementById("glossary-count");
  if (!host) return;

  const query = (filter || "").trim().toLowerCase();
  const entries = GLOSSARY.filter((entry) =>
    !query ||
    entry.term.toLowerCase().indexOf(query) !== -1 ||
    entry.def.toLowerCase().indexOf(query) !== -1 ||
    (entry.here || "").toLowerCase().indexOf(query) !== -1
  );

  host.innerHTML = entries.map((entry) => (
    '<article class="gloss" id="gloss-' + esc(entry.id) + '">' +
    "<h4>" + esc(entry.term) + "</h4>" +
    "<p>" + esc(entry.def) + "</p>" +
    (entry.here ? '<span class="here"><b>Here:</b> ' + esc(entry.here) + "</span>" : "") +
    "</article>"
  )).join("");

  if (empty) empty.hidden = entries.length > 0;
  if (count) {
    count.textContent = query
      ? entries.length + " of " + GLOSSARY.length + " terms"
      : GLOSSARY.length + " terms, A to Z";
  }
}

function initGlossary() {
  GLOSSARY.sort((a, b) => a.term.localeCompare(b.term));
  renderGlossary("");

  const search = document.getElementById("glossary-search");
  if (search) {
    search.addEventListener("input", () => renderGlossary(search.value));
  }

  const known = {};
  GLOSSARY.forEach((entry) => { known[entry.id] = true; });

  document.querySelectorAll(".term[data-term]").forEach((el) => {
    const id = el.dataset.term;
    if (!known[id]) return;
    const entry = GLOSSARY.filter((g) => g.id === id)[0];
    el.setAttribute("role", "button");
    el.setAttribute("tabindex", "0");
    el.setAttribute("title", entry.def);
    el.setAttribute("aria-label", entry.term + ": see glossary");
    const open = () => showGlossaryTerm(id);
    el.addEventListener("click", open);
    el.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        open();
      }
    });
  });
}

function showGlossaryTerm(id) {
  const search = document.getElementById("glossary-search");
  if (search) search.value = "";
  renderGlossary("");
  activateTab("glossary");
  const target = document.getElementById("gloss-" + id);
  if (!target) return;
  target.scrollIntoView({ block: "center", behavior: "smooth" });
  target.classList.add("is-flash");
  setTimeout(() => target.classList.remove("is-flash"), 2200);
}

/* ------------------------------------- boot ------------------------------------- */

function renderAll() {
  fillLiveValues();
  renderFreshness();
  renderHeadlineCards();
  renderRegimePill();
  renderHoldings();
  renderLiveFoot();
  renderBacktestFoot();
  renderBaselineTable();
  renderWalkForward();
  renderVerdictCards();
  renderActivityCards();
  renderOrders();
  renderRiskControls();
  renderRegime();
  renderPickCompare();
  renderUniverse();
  renderMetricGuide();
  renderChartsFor(activeTab);
}

function showLoadError() {
  const banner = document.getElementById("load-error");
  if (banner) banner.hidden = false;
  const pill = document.getElementById("freshness");
  const label = document.getElementById("generated");
  if (label) label.textContent = "data unavailable";
  if (pill) pill.classList.add("is-error");
}

function init() {
  const toggle = document.getElementById("theme-toggle");
  if (toggle) {
    toggle.addEventListener("click", () => setTheme(currentTheme() === "dark" ? "light" : "dark"));
  }

  initTabs();
  initOrderFilter();
  initGlossary();

  fetch("data/dashboard.json", { cache: "no-cache" })
    .then((response) => {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    })
    .then((data) => {
      DATA = data;
      augment(DATA);
      renderAll();
    })
    .catch((error) => {
      console.error("Failed to load dashboard data:", error);
      showLoadError();
    });
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", init);
} else {
  init();
}
