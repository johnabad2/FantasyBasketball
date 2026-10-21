// Fantasy Hoops web app: fetches /api/state from the local Python server and draws the current tab.
"use strict";

const TABS = [
  ["standings", "Standings"], ["my-team", "My Team"], ["week", "Week Ahead"], ["schedule", "Schedule"],
  ["free-agents", "Free Agents & Trades"], ["trade-lab", "Trade Lab"], ["league", "League"], ["player", "Player"],
  ["compare", "Compare"], ["news", "News"], ["activity", "Activity"],
];
const READY = new Set(["standings", "my-team"]);  // tabs moved over from the desktop app so far
const ME = "#f26b1d", OTHER = "#4b5563", MUTED = "#8ea0b8", GRID = "#1c2636";

let state = null;
let charts = [];
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const num = (v, d = 1) => (v === null || v === undefined ? "-" : Number(v).toLocaleString(undefined,
  { minimumFractionDigits: d, maximumFractionDigits: d }));
const signed = (v, d = 1) => (v === null || v === undefined ? "-" : (v > 0 ? "+" : "") + num(v, d));

// ------------------------------------------------------------------ data

async function poll() {
  try {
    state = await (await fetch("/api/state")).json();
    render();
  } catch (e) {
    $("#status").textContent = "Can't reach the app's server.";
  }
  // fast while ESPN is loading or roles are still coming in, otherwise just pick up auto-refreshes
  const waiting = !state || state.loading || (state.my_team && state.my_team.roster.some((p) => p.role === null));
  setTimeout(poll, waiting ? 2000 : 30000);
}

async function post(url, body) {
  await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  setTimeout(poll, 300);
}

$("#refresh").onclick = () => { $("#refresh").disabled = true; post("/api/refresh"); };

// ------------------------------------------------------------------ shell

function currentTab() {
  const t = location.hash.slice(1);
  return TABS.some(([id]) => id === t) ? t : "standings";
}
window.addEventListener("hashchange", render);
window.addEventListener("resize", () => charts.forEach((c) => c.resize()));

function render() {
  const tab = currentTab();
  $("#tabs").innerHTML = TABS.map(([id, name]) =>
    `<a href="#${id}" class="${id === tab ? "on" : ""} ${READY.has(id) ? "" : "soon"}">${esc(name)}</a>`).join("");
  if (!state) return;
  if (state.header) {
    $("#league").textContent = state.header.league;
    $("#league-sub").textContent = "Fantasy Hoops" + (state.header.demo ? " · demo league" : "");
  }
  $("#status").textContent = state.loading ? "Loading from ESPN…" :
    state.error ? state.error : `Updated ${state.updated} · auto-refresh every ${state.refresh_min} min`;
  $("#status").className = "status" + (state.error ? " bad" : "");
  $("#refresh").disabled = state.loading;

  const view = $("#view");
  const key = tab + "|" + (state.updated || "") + "|" + JSON.stringify(state.my_team ? state.my_team.roster.map((p) => p.role) : []);
  if (view.dataset.key === key) return;  // nothing new: keep the page (and its sorting, scroll) as it is
  view.dataset.key = key;
  charts.forEach((c) => c.dispose());
  charts = [];
  if (!state.standings) {
    view.innerHTML = `<div class="card empty">${state.error ? esc(state.error) : "Loading your league from ESPN…"}</div>`;
    return;
  }
  ({ "standings": renderStandings, "my-team": renderMyTeam }[tab] || renderSoon)(view, tab);
}

function renderSoon(view, tab) {
  const name = TABS.find(([id]) => id === tab)[1];
  view.innerHTML = `<div class="card empty"><h2>${esc(name)}</h2>This tab is still in the desktop app
    (<code>app\\main.py</code>). It's next in line to move over.</div>`;
}

// ------------------------------------------------------------------ tables

/** A sortable table. cols: [{key, label, num?, html?(row) , cls?}]; rowClass(row) adds a class per row. */
function table(el, cols, rows, { sort = null, desc = true, rowClass = () => "" } = {}) {
  let by = sort, down = desc;
  const draw = () => {
    const sorted = by ? [...rows].sort((a, b) => {
      const x = a[by], y = b[by];
      if (x === y) return 0;
      if (x === null || x === undefined || x === "") return 1;
      if (y === null || y === undefined || y === "") return -1;
      return (typeof x === "number" ? x - y : String(x).localeCompare(String(y))) * (down ? -1 : 1);
    }) : rows;
    el.innerHTML = `<div class="scroll"><table><thead><tr>${cols.map((c) =>
      `<th data-k="${c.key}" class="${c.num ? "n" : ""} ${by === c.key ? "sorted" + (down ? "" : " asc") : ""}">${esc(c.label)}</th>`).join("")}
      </tr></thead><tbody>${sorted.map((r) => `<tr class="${rowClass(r)}">${cols.map((c) =>
      `<td class="${c.num ? "n" : ""} ${c.cls ? c.cls(r) : ""}">${c.html ? c.html(r) : esc(r[c.key] ?? "-")}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>`;
    el.querySelectorAll("th").forEach((th) => th.onclick = () => {
      const k = th.dataset.k;
      if (by === k) down = !down; else { by = k; down = cols.find((c) => c.key === k).num !== false; }
      draw();
    });
  };
  draw();
}

// ------------------------------------------------------------------ Standings

function renderStandings(view) {
  const s = state.standings, teams = s.teams;
  const mine = teams.find((t) => t.me), top = teams[0];
  const place = teams.indexOf(mine) + 1;
  const projPlace = mine.proj_rank;
  view.innerHTML = `
    <section class="cards">
      ${card("Leader", esc(top.team), `${num(top.points, 0)} pts`)}
      ${card("You", `#${place} of ${teams.length}`, `${num(mine.points, 1)} pts · ${mine.behind ? num(mine.behind, 1) + " behind" : "in first"}`)}
      ${card("Scored today", signed(mine.today), "your team, live")}
      ${card("Projected finish", projPlace ? `#${projPlace}` : "-", mine.proj_final ? `${num(mine.proj_final, 0)} pts` : "")}
    </section>
    <section class="card flush" id="standings-table"></section>
    <div class="note">Points include games in progress · today = scored so far today · proj final = projected season total</div>
    <section class="card"><div class="k">Rank by week</div><div class="sub">hover a week for its table</div>
      <div class="chart" id="rank-chart"></div></section>`;

  const most = Math.max(...teams.map((t) => t.points), 1);
  teams.forEach((t, i) => t.place = i + 1);
  table($("#standings-table"), [
    { key: "place", label: "#", num: true },
    { key: "team", label: "Team", num: false },
    { key: "owner", label: "Owner", num: false },
    { key: "points", label: "Points", num: true, html: (t) => num(t.points) },
    { key: "bar", label: "", html: (t) => `<div class="bar"><i style="width:${100 * t.points / most}%"></i></div>` },
    { key: "today", label: "Today", num: true, html: (t) => signed(t.today) },
    { key: "behind", label: "Behind", num: true, html: (t) => t.behind ? num(t.behind) : "-" },
    { key: "proj_final", label: "Proj final", num: true, html: (t) => num(t.proj_final, 0) },
    { key: "proj_rank", label: "Proj rank", num: true, html: (t) => t.proj_rank ? "#" + t.proj_rank : "-" },
  ], teams, { rowClass: (t) => t.me ? "me" : "" });

  rankChart($("#rank-chart"), s);
}

function card(k, v, sub) {
  return `<div class="card"><div class="k">${k}</div><div class="v">${v}</div><div class="sub">${sub}</div></div>`;
}

function rankChart(el, s) {
  if (!s.weeks.length) { el.innerHTML = `<div class="empty">No games played yet.</div>`; return; }
  const names = Object.keys(s.weeks[0].ranks).sort((a, b) => (a === s.me) - (b === s.me));  // yours drawn last (on top)
  const chart = echarts.init(el);
  charts.push(chart);
  chart.setOption({
    grid: { left: 36, right: 230, top: 16, bottom: 30 },
    tooltip: {
      trigger: "axis", backgroundColor: "#1a2230", borderColor: "#2c3747", textStyle: { color: "#e8edf4" },
      formatter: (items) => {
        const w = s.weeks[items[0].dataIndex];
        const order = Object.keys(w.ranks).sort((a, b) => w.ranks[a] - w.ranks[b]);
        return `<b>Week ${w.week}</b> <span style="color:${MUTED}">${w.start} – ${w.end}</span><br>` + order.map((n) =>
          `<span style="color:${n === s.me ? ME : "#c4cdd9"};${n === s.me ? "font-weight:700" : ""}">${w.ranks[n]}. ${esc(n)}
           — ${num(w.totals[n])} <span style="color:${MUTED}">(+${num(w.gained[n])})</span></span>`).join("<br>");
      },
    },
    xAxis: { type: "category", data: s.weeks.map((w) => `Week ${w.week}`), axisLabel: { color: MUTED },
             axisLine: { lineStyle: { color: "#243044" } } },
    yAxis: { type: "value", inverse: true, min: 1, max: names.length, interval: 1, axisLabel: { color: MUTED },
             splitLine: { lineStyle: { color: GRID } } },
    series: names.map((n) => {
      const me = n === s.me;
      return {
        name: n, type: "line", smooth: true, data: s.weeks.map((w) => w.ranks[n]), symbolSize: me ? 11 : 7, z: me ? 10 : 1,
        lineStyle: { width: me ? 4 : 2, color: me ? ME : OTHER, shadowBlur: me ? 14 : 0, shadowColor: ME },
        itemStyle: { color: me ? ME : OTHER }, emphasis: { focus: "series" },
        endLabel: { show: true, formatter: (p) => `${p.value}. ${n}`, color: me ? "#fff" : MUTED, fontWeight: me ? "bold" : "normal" },
      };
    }),
  });
}

// ------------------------------------------------------------------ My Team

function renderMyTeam(view) {
  const m = state.my_team;
  view.innerHTML = `
    <section class="cards">
      ${card("Your team", esc(m.team), `${num(m.points)} pts`)}
      ${card("Rank", `#${m.rank} of ${m.of}`, "points include games in progress")}
      ${card("Lineup today", num(m.lineup_today), "projected points from your starters")}
      ${card("Projected final", m.proj_final ? num(m.proj_final, 0) : "-", "season total")}
    </section>
    ${m.pending.map((p) => `<div class="banner">Pending trade, already counted in plans and alerts:
      ${esc(p.out.join(", "))} → ${esc(p.in.join(", "))} (goes through ${esc(p.when)})</div>`).join("")}
    <section class="card"><h2>Alerts</h2><div class="alerts">${m.alerts.length ? m.alerts.map((a) =>
      `<div class="alert ${a.priority}"><div class="t"><span class="lab">${a.label}</span>${esc(a.title)}</div>
       ${a.detail.map((l) => `<div class="d">${esc(l)}</div>`).join("")}</div>`).join("") :
      `<div class="muted">Nothing to do right now.</div>`}</div></section>
    <section class="card flush" id="roster"></section>
    <div class="note">☆ keep = never suggested as a drop or trade bait · base = pts per game · L15 = last 15 days ·
      NBA role = recent starts · minutes = usual (change over the last 7 days) · vs proj = beating or missing ESPN's projection</div>`;

  const statusPill = (p) => `<span class="pill ${p.status_tag}">${esc(p.status)}</span>`;
  table($("#roster"), [
    { key: "kept", label: "Keep", num: false, html: (p) =>
      `<button class="star ${p.kept ? "on" : ""}" data-id="${p.id}" data-keep="${!p.kept}" title="${p.kept ? "Kept: click to unkeep" : "Keep him"}">${p.kept ? "★" : "☆"}</button>` },
    { key: "name", label: "Player", num: false, html: (p) =>
      `<div class="who"><div class="face" style="background-image:url(/photo/${p.id})"></div><div>${esc(p.name)}<div class="sub">${esc(p.pos)} · ${esc(p.nba)}</div></div></div>` },
    { key: "lineup", label: "Your lineup", num: false, html: (p) =>
      `<span class="pill ${p.starting ? "start" : ""}">${esc(p.starting ? "starting " + p.lineup : p.lineup)}</span>` },
    { key: "game", label: "Today", num: false, html: (p) => p.game ? esc(p.game) : `<span class="muted">no game</span>` },
    { key: "proj_today", label: "Proj today", num: true, html: (p) => num(p.proj_today) },
    { key: "role", label: "NBA role", num: false, html: (p) => p.role === null ? `<span class="muted">loading…</span>` : esc(p.role || "-") },
    { key: "status", label: "Status", num: false, html: statusPill },
    { key: "gp", label: "GP", num: true },
    { key: "minutes", label: "Minutes", num: true },
    { key: "base", label: "Base", num: true, html: (p) => num(p.base) },
    { key: "l15", label: "L15", num: true, html: (p) => num(p.l15) },
    { key: "trend", label: "Trend", num: true, html: (p) => p.trend === null ? "-" : signed(p.trend, 0) + "%",
      cls: (p) => p.form === "hot" ? "good" : p.form === "cold" ? "bad" : "" },
    { key: "vs_proj", label: "Vs proj", num: true, html: (p) => signed(p.vs_proj),
      cls: (p) => p.vs_proj > 0 ? "good" : p.vs_proj < 0 ? "bad" : "" },
    { key: "fp_total", label: "FP total", num: true, html: (p) => num(p.fp_total, 0) },
  ], m.roster, { rowClass: (p) => p.status_tag === "out" ? "out" : "" });

  $("#roster").addEventListener("click", (e) => {
    const b = e.target.closest(".star");
    if (!b) return;
    b.disabled = true;
    post("/api/keep", { id: Number(b.dataset.id), keep: b.dataset.keep === "true" });
  });
}

render();
poll();
