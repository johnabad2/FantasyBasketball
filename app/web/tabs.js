// The rest of the tabs (Free Agents & Trades, Trade Lab, League, Player, Compare, News, Activity).
// Uses the helpers in app.js ($, esc, num, signed, table, card, playerCell, charts, post).
"use strict";

const api = async (url) => (await fetch(url)).json();
const BLUE = "#3b82f6", RED = "#f87171", GREEN = "#4ade80", AMBER = "#fbbf24";
const tipStyle = { backgroundColor: "#1a2230", borderColor: "#2c3747", textStyle: { color: "#e8edf4" } };

/** Open any player's page (used by clickable names everywhere). */
function openPlayer(name) {
  playerQuery = name;
  location.hash = "player";
  document.getElementById("view").dataset.key = "";
  render();
}
document.addEventListener("click", (e) => {
  const a = e.target.closest("[data-player]");
  if (a) { e.preventDefault(); openPlayer(a.dataset.player); }
});
const link = (name) => `<a href="#" class="plink" data-player="${esc(name)}">${esc(name)}</a>`;
const facedLink = (id, name, sub = "") => `<div class="who"><div class="face sm" style="background-image:url(/photo/${id})"></div>
  <div>${link(name)}${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div></div>`;
const statusPill = (status, tag) => `<span class="pill ${tag || ""}">${esc(status)}</span>`;

// ------------------------------------------------------------------ Free Agents & Trades

function renderFreeAgents(view) {
  const f = state.fa;
  view.innerHTML = `
    <section class="split even">
      <div class="card flush"><div class="pad"><h2>Free agents · ${f.upgrades} would upgrade your roster</h2>
        <div class="sub">worth = pts per game · green = better than one of yours, per game and over 30 days</div></div>
        <div id="fa-table" class="tall"></div></div>
      <div class="stack">
        <div class="card flush"><div class="pad"><h2>Trade ideas</h2>
          <div class="sub">Better players whose recent form makes the swap look fair to them.</div></div><div id="fa-ideas"></div></div>
        <div class="card flush"><div class="pad"><h2>Trade partners</h2>
          <div class="sub">Fair swaps that fix a position imbalance (2–3 per position). Click one to open it in Trade Lab.</div></div>
          <div id="fa-partners"></div></div>
        <div class="card flush"><div class="pad"><h2>Streamers: best pickups for the next 7 days</h2>
          <div class="sub">Only drops that also win over the rest of the season · light = games on days you have empty slots</div></div>
          <div id="fa-streams"></div></div>
      </div>
    </section>`;
  table($("#fa-table"), [
    { key: "name", label: "Player", num: false, html: (p) => facedLink(p.id, p.name, `${p.pos} · ${p.nba}`) },
    { key: "status", label: "Status", num: false, html: (p) => p.waivers ? statusPill(`waivers ${p.waivers}`, "shaky")
      : statusPill(p.status, p.status_tag) },
    { key: "worth", label: "Worth", num: true, html: (p) => num(p.worth) },
    { key: "next30", label: "Next 30d", num: true, html: (p) => num(p.next30, 0) },
    { key: "drop", label: "Drop for him", num: false, html: (p) => p.drop ? esc(p.drop) : "" },
    { key: "gain", label: "Gain", num: true, html: (p) => p.gain ? signed(p.gain) : "", cls: () => "good" },
  ], f.free_agents, { rowClass: (p) => p.drop ? "up" : "" });

  if (f.ideas.length) {
    table($("#fa-ideas"), [
      { key: "team", label: "Team", num: false },
      { key: "send", label: "You send", num: false, html: (r) => `${link(r.send)} <span class="muted">${num(r.send_base)}</span>` },
      { key: "get", label: "You get", num: false, html: (r) => `${link(r.get)} <span class="muted">${num(r.get_base)}</span>` },
      { key: "gain", label: "Gain/g", num: true, html: (r) => signed(r.gain), cls: () => "good" },
    ], f.ideas);
  } else {
    $("#fa-ideas").innerHTML = `<div class="empty small">No trade ideas yet: needs ${f.min_games}+ games played, so check back in a week or two.</div>`;
  }
  if (f.partners.length) {
    table($("#fa-partners"), [
      { key: "team", label: "Team", num: false },
      { key: "send", label: "You send", num: false },
      { key: "get", label: "You get", num: false },
      { key: "fixes", label: "Fixes", num: false, cls: (r) => r.both ? "good" : "" },
      { key: "me", label: "You", num: true, html: (r) => signed(r.me) },
      { key: "them", label: "Them", num: true, html: (r) => signed(r.them) },
    ], f.partners, { rowClass: () => "clickable" });
    $("#fa-partners").querySelectorAll("tbody tr").forEach((tr) => tr.onclick = () => {
      const p = f.partners[tr.dataset.i];
      openTrade(state.rosters.find((t) => t.me).id, p.team_id, [p.send_id], [p.get_id]);
    });
  } else {
    $("#fa-partners").innerHTML = `<div class="empty small">No fair fixes found right now.</div>`;
  }
  renderStreamers($("#fa-streams"), state.week && state.week.streamers);
}

// ------------------------------------------------------------------ Trade Lab

let lab = null;  // {a, b, send: [], get: []}

function openTrade(a, b, send, get) {
  lab = { a, b, send, get };
  location.hash = "trade-lab";
}

function renderTradeLab(view) {
  const rosters = state.rosters;
  if (!lab) {
    const me = rosters.find((t) => t.me);
    lab = { a: me.id, b: rosters.find((t) => !t.me).id, send: [], get: [] };
  }
  const opts = (sel) => rosters.map((t) => `<option value="${t.id}" ${t.id === sel ? "selected" : ""}>${esc(t.name)}${t.me ? " (you)" : ""}</option>`).join("");
  view.innerHTML = `
    <section class="toolbar">Team <select id="lab-a">${opts(lab.a)}</select> <b>trades with</b>
      <select id="lab-b">${opts(lab.b)}</select> <button class="btn" id="lab-clear">Clear picks</button>
      <span class="sub">Click players to tick them.</span></section>
    <section class="split even">
      <div class="card flush"><div class="pad side-a"><h2 id="lab-a-head"></h2></div><div id="lab-a-list"></div></div>
      <div class="card flush"><div class="pad side-b"><h2 id="lab-b-head"></h2></div><div id="lab-b-list"></div></div>
    </section>
    <section class="split three">
      <div class="card" id="lab-summary"><div class="muted">Working it out…</div></div>
      <div class="card flush"><div class="pad"><h2 id="lab-sugg-head">Suggestions</h2><div class="sub">Click one to load it.</div></div>
        <div id="lab-sugg"></div></div>
      <div class="card"><h2>Lineup strength</h2><div class="sub">fp/g of each team's starters · ○ before ● after</div>
        <div class="chart mid" id="lab-chart"></div></div>
    </section>`;
  $("#lab-a").onchange = () => { lab.a = Number($("#lab-a").value); if (lab.a === lab.b) lab.b = rosters.find((t) => t.id !== lab.a).id; lab.send = []; lab.get = []; renderTradeLab(view); };
  $("#lab-b").onchange = () => { lab.b = Number($("#lab-b").value); lab.get = []; if (lab.a === lab.b) { lab.a = rosters.find((t) => t.id !== lab.b).id; lab.send = []; } renderTradeLab(view); };
  $("#lab-clear").onclick = () => { lab.send = []; lab.get = []; renderTradeLab(view); };
  drawLabLists();
  runTrade();
}

function drawLabLists() {
  const A = state.rosters.find((t) => t.id === lab.a), B = state.rosters.find((t) => t.id === lab.b);
  $("#lab-a-head").textContent = `${A.name} sends${A.me ? " (you)" : ""}`;
  $("#lab-b-head").textContent = `${B.name} sends`;
  [["#lab-a-list", A, "send"], ["#lab-b-list", B, "get"]].forEach(([sel, team, key]) => {
    table($(sel), [
      { key: "tick", label: "", num: false, html: (p) => lab[key].includes(p.id) ? "✔" : "" },
      { key: "name", label: "Player", num: false, html: (p) => `${esc(p.name)} <span class="muted">${esc(p.pos)} · ${esc(p.nba)}</span>` },
      { key: "status", label: "Status", num: false, html: (p) => statusPill(p.status, p.status_tag) },
      { key: "base", label: "Base", num: true, html: (p) => num(p.base) },
      { key: "l15", label: "L15", num: true, html: (p) => num(p.l15) },
    ], team.players, { rowClass: (p) => "clickable " + (lab[key].includes(p.id) ? "me" : "") });
    $(sel).querySelectorAll("tbody tr").forEach((tr) => tr.onclick = () => {
      const p = team.players[tr.dataset.i];
      lab[key] = lab[key].includes(p.id) ? lab[key].filter((x) => x !== p.id) : [...lab[key], p.id];
      drawLabLists();
      runTrade();
    });
  });
}

let tradeSeq = 0;
async function runTrade() {
  const seq = ++tradeSeq;
  const res = await fetch("/api/trade", { method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(lab) });
  const r = await res.json();
  if (seq !== tradeSeq || !$("#lab-summary")) return;  // a newer pick came in meanwhile
  const side = (s, cls) => `<div class="side ${cls}"><b>${esc(s.team)}</b>
    <div>Lineup ${num(s.before)} → ${num(s.after)} fp/g (<span class="${s.delta >= 0 ? "good" : "bad"}">${signed(s.delta)}</span>) · rank #${s.rank_before} → #${s.rank_after}</div>
    ${s.next30 ? `<div>Next 30 days: ${num(s.next30[0], 0)} → ${num(s.next30[1], 0)} pts (<span class="${s.next30[1] >= s.next30[0] ? "good" : "bad"}">${signed(s.next30[1] - s.next30[0], 0)}</span>) · season ${signed(s.season, 0)}</div>`
      : `<div>About ${signed(s.season, 0)} season points</div>`}
    ${s.fixed ? `<div class="good">Fixes ${esc(s.fixed)}</div>` : ""}${s.created ? `<div class="warn">Creates: ${esc(s.created)}</div>` : ""}
    ${s.gives.length || s.gets.length ? `<div class="muted">Gives: ${esc(s.gives.join(", ") || "nothing")}<br>Gets: ${esc(s.gets.join(", ") || "nothing")}</div>` : ""}
    ${s.positions.length ? `<div class="muted">Positions: ${esc(s.positions.join(", "))}</div>` : ""}
    ${s.roster[0] !== s.roster[1] ? `<div class="muted">Roster ${s.roster[0]} → ${s.roster[1]}${s.drop ? `: would need to drop someone (lowest: ${esc(s.drop)})` : ""}</div>` : ""}</div>`;
  $("#lab-summary").innerHTML = `<div class="verdict">${esc(r.verdict)}</div>${side(r.sides[0], "side-a")}${side(r.sides[1], "side-b")}`;
  $("#lab-sugg-head").textContent = r.suggest_title;
  const short = (names) => names.map((n) => n.split(" ").length > 1 ? `${n[0]}. ${n.split(" ").slice(1).join(" ")}` : n).join(", ");
  if (r.suggestions.length) {
    table($("#lab-sugg"), [
      { key: "s", label: "Sends", num: false, html: (o) => esc(short(o.send_names)) },
      { key: "g", label: "Gets", num: false, html: (o) => esc(short(o.get_names)) },
      { key: "da", label: "You", num: true, html: (o) => signed(o.da) },
      { key: "db", label: "Them", num: true, html: (o) => signed(o.db) },
    ], r.suggestions, { rowClass: (o) => "clickable " + (Math.min(o.da, o.db) >= -0.05 ? "up" : "") });
    $("#lab-sugg").querySelectorAll("tbody tr").forEach((tr) => tr.onclick = () => {
      const o = r.suggestions[tr.dataset.i];
      lab.send = o.send; lab.get = o.get; drawLabLists(); runTrade();
    });
  } else {
    $("#lab-sugg").innerHTML = `<div class="empty small">No trade changes either lineup.</div>`;
  }
  strengthChart($("#lab-chart"), r.strength);
}

function strengthChart(el, rows) {
  let chart = echarts.getInstanceByDom(el);
  if (!chart) { chart = echarts.init(el); charts.push(chart); }
  const colors = [BLUE, ME];
  const names = rows.map((r) => r.team).reverse();
  const vals = rows.flatMap((r) => [r.before, r.after]);
  const lo = Math.min(...vals), hi = Math.max(...vals), pad = (hi - lo) * 0.1 || 1;
  chart.setOption({
    grid: { left: 150, right: 108, top: 6, bottom: 24 },
    tooltip: { ...tipStyle, trigger: "item", formatter: (p) => `${esc(p.data.team)}<br>${num(p.data.before)} → ${num(p.data.after)} fp/g` },
    xAxis: { type: "value", min: Math.floor(lo - pad), max: Math.ceil(hi + pad), axisLabel: { color: MUTED },
             splitLine: { lineStyle: { color: GRID } } },
    yAxis: [
      { type: "category", data: names, axisLabel: { color: (v) => MUTED, fontSize: 11 }, axisLine: { show: false }, axisTick: { show: false } },
      // each team's strength after the trade on the right, with the change for the two teams trading
      { type: "category", data: names, position: "right", axisLine: { show: false }, axisTick: { show: false },
        axisLabel: { fontSize: 11, formatter: (team) => {
          const r = rows.find((x) => x.team === team), d = r.after - r.before;
          return r.side === null ? `{o|${num(r.after)}}` : `{t|${num(r.after)}} {${d >= 0 ? "g" : "b"}|(${signed(d)})}`;
        }, rich: { o: { color: MUTED }, t: { color: "#e8edf4", fontWeight: "bold" }, g: { color: GREEN }, b: { color: RED } } } },
    ],
    series: [
      { type: "scatter", symbolSize: 10, itemStyle: { color: "transparent", borderWidth: 2 },
        data: rows.map((r) => ({ value: [r.before, r.team], team: r.team, before: r.before, after: r.after,
          itemStyle: { borderColor: r.side === null ? OTHER : colors[r.side] } })) },
      { type: "scatter", symbolSize: 11, data: rows.map((r) => ({ value: [r.after, r.team], team: r.team, before: r.before, after: r.after,
          itemStyle: { color: r.side === null ? OTHER : colors[r.side], shadowBlur: r.side === null ? 0 : 10,
                       shadowColor: r.side === null ? "transparent" : colors[r.side] } })) },
    ],
  }, true);
}

// ------------------------------------------------------------------ League

let leagueTeam = null, leagueSplit = "";

async function renderLeague(view) {
  const teams = state.teams;
  leagueTeam = leagueTeam ?? teams.find((t) => t.me).id;
  view.innerHTML = `
    <section class="toolbar">Team <select id="lg-team">${teams.map((t) => `<option value="${t.id}" ${t.id === leagueTeam ? "selected" : ""}>${esc(t.name)}${t.me ? " (you)" : ""}</option>`).join("")}</select>
      Stats <select id="lg-split"><option value="">season (projected before games)</option></select>
      <span class="sub" id="lg-info"></span></section>
    <section class="card flush" id="lg-table"><div class="empty">Loading…</div></section>
    <div class="note">* = ESPN projection (no games yet) · click a player for his page</div>`;
  $("#lg-team").onchange = () => { leagueTeam = Number($("#lg-team").value); renderLeague(view); };
  const r = await api(`/api/league?team=${leagueTeam}&split=${leagueSplit}`);
  if (r.error) { $("#lg-table").innerHTML = `<div class="empty">${esc(r.error)}</div>`; return; }
  $("#lg-split").innerHTML = `<option value="">season (projected before games)</option>` +
    r.splits.map((s) => `<option value="${s.key}" ${s.key === leagueSplit ? "selected" : ""}>${esc(s.label)}</option>`).join("");
  $("#lg-split").onchange = () => { leagueSplit = $("#lg-split").value; renderLeague(view); };
  $("#lg-info").textContent = `${r.owner} · ${num(r.points)} pts · #${r.rank}`;
  table($("#lg-table"), [
    { key: "name", label: "Player", num: false, html: (p) => facedLink(p.id, p.name, `${p.pos} · ${p.nba} · ${p.slot}${p.projected ? " · *" : ""}`) },
    { key: "status", label: "Status", num: false, html: (p) => statusPill(p.status, p.status_tag) },
    { key: "gp", label: "GP", num: true },
    { key: "fp_total", label: "FP tot", num: true, html: (p) => num(p.fp_total, 0) },
    { key: "fpg", label: "FP/G", num: true, html: (p) => `<b>${num(p.fpg)}</b>` },
    { key: "proj", label: "Proj/G", num: true, html: (p) => num(p.proj) },
    { key: "vs_proj", label: "Vs proj", num: true, html: (p) => signed(p.vs_proj), cls: (p) => p.vs_proj > 0 ? "good" : p.vs_proj < 0 ? "bad" : "" },
    ...r.box_cols.map((c, i) => ({ key: "b" + i, label: c, num: true, html: (p) => esc(p.box[i]) })),
  ], r.rows.map((p) => ({ ...p, ...Object.fromEntries(p.box.map((v, i) => ["b" + i, parseFloat(v) || 0])) })));
}

// ------------------------------------------------------------------ Player

let playerQuery = "", playerStat = "FP", playerRange = "all";

async function renderPlayer(view) {
  view.innerHTML = `
    <section class="toolbar"><input id="pl-q" placeholder="Any NBA player: name or nickname (e.g. kd, the king)" value="${esc(playerQuery)}">
      <button class="btn primary" id="pl-go">Search</button><button class="btn" id="pl-cmp">Compare with…</button></section>
    <div id="pl-body">${playerQuery ? `<div class="card empty">Looking up ${esc(playerQuery)}…</div>` :
      `<div class="card empty">Search for any NBA player, or click a player's name anywhere in the app.</div>`}</div>`;
  const go = () => { playerQuery = $("#pl-q").value.trim(); if (playerQuery) renderPlayer(view); };
  $("#pl-go").onclick = go;
  $("#pl-q").onkeydown = (e) => { if (e.key === "Enter") go(); };
  $("#pl-cmp").onclick = () => { compareQ = [playerQuery || "", ""]; location.hash = "compare"; };
  if (!playerQuery) return;
  const p = await api(`/api/player?q=${encodeURIComponent(playerQuery)}`);
  if (!$("#pl-body")) return;
  if (p.error) { $("#pl-body").innerHTML = `<div class="card empty">${esc(p.error)}</div>`; return; }
  $("#pl-body").innerHTML = `
    <section class="player-head card">
      <div class="photo" style="background-image:url(/photo/${p.id})"></div>
      <div><h1>${esc(p.name)}</h1>
        ${p.nicknames.length ? `<div class="sub">“${p.nicknames.map(esc).join("”, “")}”</div>` : ""}
        <div class="facts"><span class="pill">${esc(p.pos)} · ${esc(p.nba)}</span>${statusPill(p.status, p.status_tag)}
          <span class="pill">${esc(p.owner)}</span></div></div>
      <div class="grow" id="pl-splits"></div>
    </section>
    <section class="split wide">
      <div class="card"><div class="row-between"><div id="pl-stats" class="chips"></div>
          <select id="pl-range"><option value="all">All games</option><option value="10">Last 10</option><option value="20">Last 20</option></select></div>
        <div class="chart" id="pl-chart"></div></div>
      <div class="card flush"><div class="pad"><h2>Points by stat</h2><div class="sub">${esc(p.breakdown_label)} · click to chart</div></div>
        <div id="pl-break"></div></div>
    </section>`;
  table($("#pl-splits"), [
    { key: "label", label: "Split", num: false }, { key: "gp", label: "GP", num: true },
    { key: "fpg", label: "FP/G", num: true, html: (s) => `<b>${num(s.fpg)}</b>` },
    ...p.box_cols.map((c, i) => ({ key: "b" + i, label: c, num: true, html: (s) => esc(s.box[i]) })),
  ], p.splits);
  table($("#pl-break"), [
    { key: "stat", label: "Stat", num: false }, { key: "per_game", label: "Per game", num: true, html: (r) => num(r.per_game) },
    { key: "pts", label: "Pts each", num: true, html: (r) => signed(r.pts, 0) },
    { key: "fp", label: "Fantasy pts", num: true, html: (r) => signed(r.fp), cls: (r) => r.fp >= 0 ? "good" : "bad" },
  ], p.breakdown, { rowClass: () => "clickable" });
  $("#pl-break").querySelectorAll("tbody tr").forEach((tr) => tr.onclick = () => {
    const stat = tr.cells[0].textContent.trim();
    if (p.chart_stats.includes(stat)) { playerStat = stat; drawStats(); drawGames(p); }
  });
  const drawStats = () => {
    $("#pl-stats").innerHTML = p.chart_stats.map((s) => `<button class="chip ${s === playerStat ? "on" : ""}" data-s="${s}">${s === "FP" ? "Fantasy pts" : s}</button>`).join("");
    $("#pl-stats").querySelectorAll(".chip").forEach((b) => b.onclick = () => { playerStat = b.dataset.s; drawStats(); drawGames(p); });
  };
  $("#pl-range").value = playerRange;
  $("#pl-range").onchange = () => { playerRange = $("#pl-range").value; drawGames(p); };
  drawStats();
  drawGames(p);
}

function drawGames(p) {
  const el = $("#pl-chart");
  if (!p.games || !p.games.length) { el.innerHTML = `<div class="empty">${esc(p.games ? "No games yet." : p.games_label)}</div>`; return; }
  const games = playerRange === "all" ? p.games : p.games.slice(-Number(playerRange));
  const day = (iso, long) => new Date(iso).toLocaleDateString(undefined, long ? { weekday: "short", day: "numeric", month: "short" }
                                                                         : { day: "numeric", month: "short" });
  const vals = games.map((g) => g[playerStat] ?? 0);
  const avg = vals.reduce((a, b) => a + b, 0) / vals.length;
  const fewer = (p.rules[playerStat] ?? 0) < 0;  // e.g. turnovers cost points: blue still means good for you
  const good = (v) => (v >= avg) !== fewer;
  let chart = echarts.getInstanceByDom(el);
  if (!chart) { el.innerHTML = ""; chart = echarts.init(el); charts.push(chart); }
  chart.setOption({
    grid: { left: 40, right: 70, top: 30, bottom: 40 },
    title: { text: `${p.stat_names[playerStat]} per game${fewer ? " (fewer is better for you)" : ""}`, left: 0, top: 0,
             textStyle: { color: "#e8edf4", fontSize: 13 } },
    tooltip: { ...tipStyle, trigger: "axis", formatter: (items) => {
      const g = games[items[0].dataIndex];
      return `<b>${esc(day(g.date, true))}</b> · ${esc(g.opp)} · ${esc(g.result)}${g.playoffs ? " · playoffs" : ""}<br>
        ${num(g[playerStat])} ${playerStat === "FP" ? "fantasy pts" : playerStat} (${signed(g[playerStat] - avg)} vs avg)<br>
        <span style="color:${MUTED}">${num(g.PTS, 0)} pts ${num(g.REB, 0)} reb ${num(g.AST, 0)} ast ${num(g.STL, 0)} stl ${num(g.BLK, 0)} blk ${num(g.TO, 0)} to · ${num(g.MIN, 0)} min</span>`;
    } },
    xAxis: { type: "category", data: games.map((g) => day(g.date)), axisLabel: { color: MUTED, fontSize: 10 }, axisLine: { lineStyle: { color: "#243044" } } },
    yAxis: { type: "value", axisLabel: { color: MUTED }, splitLine: { lineStyle: { color: GRID } } },
    series: [{ type: "line", smooth: 0.3, symbol: "circle", showSymbol: true, showAllSymbol: true, symbolSize: 10, z: 3,
      data: vals.map((v) => ({ value: v, itemStyle: { color: good(v) ? BLUE : RED, borderColor: "#131a25", borderWidth: 1 } })),
      // the line fades from each dot's colour to the next one's
      lineStyle: { width: 2.5, color: vals.length < 2 ? MUTED : { type: "linear", x: 0, y: 0, x2: 1, y2: 0,
        colorStops: vals.map((v, i) => ({ offset: i / (vals.length - 1), color: good(v) ? BLUE : RED })) } },
      markLine: { symbol: "none", silent: true, data: [{ yAxis: avg }], lineStyle: { color: "#c4cdd9", type: "dashed" },
                  label: { formatter: `avg ${num(avg)}`, color: "#e8edf4", position: "end" } } }],
  }, true);
}

// ------------------------------------------------------------------ Compare

let compareQ = ["", ""], compareSplit = "", compareMode = "fantasy";

async function renderCompare(view) {
  view.innerHTML = `
    <section class="toolbar"><input id="c-a" placeholder="Player 1" value="${esc(compareQ[0])}"> <b>vs</b>
      <input id="c-b" placeholder="Player 2" value="${esc(compareQ[1])}">
      <button class="btn primary" id="c-go">Compare</button>
      <select id="c-split"><option value="">season (projected before games)</option><option value="last_7">last 7 days</option>
        <option value="last_15">last 15 days</option><option value="last_30">last 30 days</option><option value="projected">projected</option></select>
      <span class="seg"><button data-m="raw">Per-game stats</button><button data-m="fantasy">Fantasy points by stat</button></span></section>
    <div id="c-body"><div class="card empty">Type two players (names or nicknames), then press Compare.</div></div>`;
  $("#c-split").value = compareSplit;
  view.querySelectorAll(".seg button").forEach((b) => {
    b.classList.toggle("on", b.dataset.m === compareMode);
    b.onclick = () => { compareMode = b.dataset.m; renderCompare(view); };
  });
  const go = () => { compareQ = [$("#c-a").value.trim(), $("#c-b").value.trim()]; renderCompare(view); };
  $("#c-go").onclick = go;
  $("#c-b").onkeydown = (e) => { if (e.key === "Enter") go(); };
  $("#c-split").onchange = () => { compareSplit = $("#c-split").value; renderCompare(view); };
  if (!compareQ[0] || !compareQ[1]) return;
  $("#c-body").innerHTML = `<div class="card empty">Looking up ${esc(compareQ[0])} and ${esc(compareQ[1])}…</div>`;
  const r = await api(`/api/compare?a=${encodeURIComponent(compareQ[0])}&b=${encodeURIComponent(compareQ[1])}&split=${compareSplit}&mode=${compareMode}`);
  if (!$("#c-body")) return;
  if (r.error) { $("#c-body").innerHTML = `<div class="card empty">${esc(r.error)}</div>`; return; }
  const colors = [BLUE, "#d95926"];
  $("#c-body").innerHTML = `
    <section class="split even">${r.players.map((p, i) => `<div class="card pcard" style="border-left:4px solid ${colors[i]}">
      <div class="photo sm" style="background-image:url(/photo/${p.id})"></div>
      <div><h2>${link(p.name)}</h2><div class="sub">${esc(p.pos)}, ${esc(p.nba)} · ${esc(p.status)}</div><div class="sub">${esc(p.owner)}</div></div></div>`).join("")}</section>
    <section class="split wide">
      <div class="card"><h2>${r.mode === "fantasy" ? "Fantasy points each stat earns per game" : "Per-game stats"}</h2>
        <div class="sub">each axis spans every rostered player in your league · higher is always better</div>
        <div class="chart" id="c-chart"></div></div>
      <div class="card flush"><div class="pad"><h2>Side by side</h2><div class="sub">edge = extra fantasy pts per game</div></div><div id="c-table"></div></div>
    </section>`;
  const last = (n) => n.split(" ").slice(-1)[0];
  table($("#c-table"), [
    { key: "label", label: "Stat", num: false }, { key: "a", label: last(r.players[0].name), num: true, html: (x) => num(x.a) },
    { key: "b", label: last(r.players[1].name), num: true, html: (x) => num(x.b) },
    { key: "edge", label: "Edge", num: false, html: (x) => x.edge === null ? "-" : Math.abs(x.edge) < 0.05 ? "even" :
      `<span style="color:${colors[x.edge > 0 ? 0 : 1]}">${esc(last(r.players[x.edge > 0 ? 0 : 1].name))} +${num(Math.abs(x.edge))}</span>` },
  ], r.rows);
  // parallel coordinates: every axis scaled 0..1 across the league (flipped where fewer is better)
  const scaled = (key, v) => { const [lo, hi] = r.ranges[key]; const t = (v - lo) / (hi - lo); return r.inverted.includes(key) ? 1 - t : t; };
  const chart = echarts.init($("#c-chart"));
  charts.push(chart);
  chart.setOption({
    grid: { left: 30, right: 30, top: 30, bottom: 50 },
    legend: { top: 0, right: 0, textStyle: { color: "#c4cdd9" } },
    tooltip: { ...tipStyle, trigger: "axis", formatter: (items) => {
      const ax = r.axes[items[0].dataIndex];
      return `<b>${esc(ax.label)}</b><br>` + r.players.map((p, i) => `<span style="color:${colors[i]}">${esc(p.name)}: ${num(p.values[ax.key])}</span>`).join("<br>");
    } },
    xAxis: { type: "category", data: r.axes.map((a) => a.label), axisLabel: { color: "#c4cdd9", fontSize: 11, interval: 0 },
             axisLine: { lineStyle: { color: "#243044" } }, splitLine: { show: true, lineStyle: { color: "#243044" } } },
    yAxis: { type: "value", min: -0.05, max: 1.05, show: false },
    series: r.players.map((p, i) => ({ name: p.name, type: "line", symbolSize: 9, symbol: i ? "rect" : "circle",
      data: r.axes.map((a) => scaled(a.key, p.values[a.key] ?? 0)),
      lineStyle: { width: 2.5, color: colors[i], shadowBlur: 8, shadowColor: colors[i] }, itemStyle: { color: colors[i] },
      label: { show: true, formatter: (x) => num(p.values[r.axes[x.dataIndex].key]), color: "#c4cdd9", fontSize: 10,
               position: i ? "bottom" : "top" } })),
  });
}

// ------------------------------------------------------------------ News

let newsScope = "mine";

function renderNews(view) {
  const n = state.news;
  const rows = n.rows.filter((r) => newsScope === "mine" ? r.mine : newsScope === "rostered" ? r.rostered && r.injured : r.injured);
  view.innerHTML = `
    <section class="toolbar">Show <select id="nw-scope"><option value="mine">My team</option>
      <option value="rostered">Every rostered player (injured)</option><option value="all">Every injured NBA player</option></select>
      <span class="sub">Click a player for ESPN's full note and latest news.</span></section>
    <section class="split wide">
      <div class="stack"><div class="card flush" id="nw-table"></div>
        <div class="card" id="nw-detail"><div class="muted">Click a player to see ESPN's full injury note and latest news.</div></div></div>
      <div class="card flush"><div class="pad"><h2>Opportunities</h2>
        <div class="sub">Free agents set to play more: a teammate is out, or his minutes are rising.</div></div><div id="nw-opps"></div></div>
    </section>`;
  $("#nw-scope").value = newsScope;
  $("#nw-scope").onchange = () => { newsScope = $("#nw-scope").value; renderNews(view); };
  table($("#nw-table"), [
    { key: "name", label: "Player", num: false },
    { key: "team", label: "Fantasy team", num: false },
    { key: "status", label: "Status", num: false, html: (r) => statusPill(r.status, r.tag) },
    { key: "injury", label: "Injury", num: false },
    { key: "back", label: "Back", num: false, html: (r) => esc(r.back || "-") },
    { key: "comment", label: "Latest", num: false, cls: () => "clip" },
  ], rows, { rowClass: (r) => "clickable " + (r.mine && newsScope !== "mine" ? "me" : "") });
  $("#nw-table").querySelectorAll("tbody tr").forEach((tr) => tr.onclick = async () => {
    const r = rows[tr.dataset.i];
    $("#nw-detail").innerHTML = `<h2>${link(r.name)}</h2>${r.injured ? `<div><b>${esc(r.status)}</b> · ${esc(r.injury || "injury")}${r.back ? ` · expected back ${esc(r.back)}` : ""}</div>
      <div>${esc(r.comment)}</div><div class="muted">${esc(r.details)}</div>` : ""}<div class="muted" id="nw-items">Loading latest news…</div>`;
    const items = await api(`/api/news?id=${r.id}`);
    if (!$("#nw-items")) return;
    $("#nw-items").outerHTML = items.length ? `<div class="news">${items.map((it) => `<div><span class="muted">${esc(it.when)}</span> <b>${esc(it.headline)}</b>
      ${it.story ? `<div class="muted">${esc(it.story)}</div>` : ""}</div>`).join("")}</div>` : `<div class="muted">No recent news.</div>`;
  });
  if (n.opportunities.length) {
    table($("#nw-opps"), [
      { key: "name", label: "Player", num: false, html: (o) => facedLink(o.id, o.name, o.nba) },
      { key: "usual", label: "Usual", num: true, html: (o) => num(o.usual) },
      { key: "now", label: "Now", num: true, html: (o) => num(o.now), cls: (o) => o.big ? "good" : "" },
      { key: "why", label: "Why", num: false, cls: () => "clip" },
    ], n.opportunities, { sort: "now" });
  } else {
    $("#nw-opps").innerHTML = `<div class="empty small">Nobody right now.</div>`;
  }
}

// ------------------------------------------------------------------ Activity

let activityShow = "all";
const AVATAR = ["#2f5a9e", "#b4532a", "#2e7d5b", "#7b4fb0", "#a8326e", "#3d7f99", "#8a6d1f", "#5b6b8c"];
const initials = (name) => name.replace(/'s\b/g, "").split(/\s+/).filter((w) => /^[a-z0-9]/i.test(w)).slice(0, 2).map((w) => w[0]).join("").toUpperCase() || "?";
const avatar = (name) => `<span class="avatar" style="background:${AVATAR[[...name].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR.length]}">${esc(initials(name))}</span>`;

function renderActivity(view) {
  const items = state.activity.filter((x) => activityShow === "all" || x.kind === activityShow);
  view.innerHTML = `
    <section class="toolbar">Show <select id="ac-show"><option value="all">Everything</option><option value="trade">Trades only</option>
      <option value="move">Moves only</option><option value="chat">Chat only</option></select>
      <span class="sub">Chat in blue · click a player for his page</span></section>
    <section class="feed" id="ac-feed"></section>`;
  $("#ac-show").value = activityShow;
  $("#ac-show").onchange = () => { activityShow = $("#ac-show").value; renderActivity(view); };
  let day = null, html = "";
  const when = (iso) => new Date(iso);
  for (const it of items) {
    const t = when(it.when);
    const d = t.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "short" });
    if (d !== day) { day = d; html += `<div class="dayhead">${esc(d)}</div>`; }
    const time = t.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    if (it.kind === "trade") {
      const goes = it.process_at && new Date(it.process_at) > new Date() ?
        `Goes through ${new Date(it.process_at).toLocaleString(undefined, { weekday: "short", hour: "2-digit", minute: "2-digit" })} unless it's vetoed` : "";
      html += `<div class="card trade"><div class="row-between"><div class="thead">⇄ Trade ${it.status === "accepted" ? "Accepted" : "Completed"}</div>
        <span class="muted">${esc(time)}</span></div>${goes ? `<div class="sub">${esc(goes)}</div>` : ""}
        <div class="tsides">${it.sides.map((s) => `<div><div class="who">${avatar(s.team)}<div><b>${esc(s.team)}</b><div class="sub">${esc(s.manager)}</div></div></div>
          ${s.receives.map((p) => `<div class="recv"><div class="face" style="background-image:url(/photo/${p.id})"></div>
            <div><div class="sub">Receives</div>${link(p.name)}<div class="sub">${esc(p.pro)} ${esc(p.pos)}</div></div></div>`).join("")}
          ${s.drops.map((n) => `<div class="recv"><span class="bad">Drops</span> ${link(n)}</div>`).join("")}</div>`).join("")}</div></div>`;
    } else if (it.kind === "move") {
      const cls = it.action.includes("added") ? "good" : it.action.includes("dropped") ? "bad" : "muted";
      html += `<div class="line"><span class="muted">${esc(time)}</span><span>${esc(it.team)}</span><span class="${cls}">${esc(it.action)}</span><span>${link(it.player)}</span></div>`;
    } else {
      html += `<div class="line chat"><span class="muted">${esc(time)}</span><span><b>${esc(it.author)}</b>${it.team ? ` · ${esc(it.team)}` : ""}</span><span class="msg">${esc(it.text)}</span></div>`;
    }
  }
  $("#ac-feed").innerHTML = html || `<div class="card empty">Nothing here yet.</div>`;
}
