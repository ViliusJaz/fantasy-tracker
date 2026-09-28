"use strict";

const app = document.getElementById("app");
const switcher = document.getElementById("league-switch");
const modal = document.getElementById("player-modal");
const modalBody = document.getElementById("player-modal-body");
const REFRESH_LIVE_MS = 60_000;

let leaguesCache = null;
let refreshTimer = null;
let renderToken = 0;
let modalToken = 0;

// ------------------------------------------------------------------ helpers

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

const fmt = (n) => (n === null || n === undefined ? "–" : Number(n).toFixed(2).replace(/\.?0+$/, ""));
const fmt1 = (n) => (n === null || n === undefined ? "–" : Number(n).toFixed(1).replace(/\.0$/, ""));

const roundLabel = (r) => `${r + 1} turas`;

const pad = (n) => String(n).padStart(2, "0");
function when(iso) {
  const d = new Date(iso);
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
const shortDay = (isoDay) => (isoDay ? isoDay.slice(5) : "");

const POS = { guard: "G", forward: "F", center: "C" };
const POS_LONG = { guard: "Gynėjas", forward: "Puolėjas", center: "Centras" };
const FORMAT = { head_to_head: "Head-to-head", classic: "Pagal taškus" };
const SEVERITY = { out: "bad", doubtful: "warn", uncertain: "warn", questionable: "warn", "game-time": "mild", expected: "mild" };

// Box-score columns shown in every player table.
const STAT_COLS = [
  ["min", "MIN", "Minutės"],
  ["pts", "TŠK", "Taškai"],
  ["reb", "AK", "Atkovoti kamuoliai"],
  ["ast", "RP", "Rezultatyvūs perdavimai"],
  ["stl", "PR", "Perimti kamuoliai"],
  ["blk", "BL", "Blokuoti metimai"],
  ["tov", "KL", "Klaidos"],
  ["p2", "2T", "Dvitaškiai"],
  ["p3", "3T", "Tritaškiai"],
  ["ft", "BM", "Baudų metimai"],
  ["eff", "NB", "Naudingumo balas"],
];

function statHeads(extraCls = "") {
  return STAT_COLS.map(([, abbr, title]) => `<th class="num stat ${extraCls}" title="${title}">${abbr}</th>`).join("");
}

// mode "round": totals of one round (made/attempted); mode "avg": season averages (shooting as %).
function statCells(line, mode) {
  if (!line) return STAT_COLS.map(() => '<td class="num stat dim">–</td>').join("");
  const shot = (m, a) => {
    if (mode === "round") return `${m}/${a}`;
    return a ? `${Math.round((m / a) * 100)}%` : "–";
  };
  const val = (key) => {
    if (key === "p2") return shot(line.p2m, line.p2a);
    if (key === "p3") return shot(line.p3m, line.p3a);
    if (key === "ft") return shot(line.ftm, line.fta);
    if (key === "min") return mode === "round" ? Math.round(line.min) : fmt1(line.min);
    return mode === "round" ? fmt(line[key]) : fmt1(line[key]);
  };
  return STAT_COLS.map(([key]) => `<td class="num stat">${val(key)}</td>`).join("");
}

async function api(path, opts = {}) {
  const res = await fetch(path, {
    ...opts,
    headers: opts.body ? { "Content-Type": "application/json" } : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Klaida ${res.status}`);
  return data;
}

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, "");
  const [path, qs] = raw.split("?");
  return { parts: path.split("/").filter(Boolean), params: new URLSearchParams(qs || "") };
}

function setView(html) {
  app.innerHTML = html;
}

function stateBox(msg, isError = false) {
  return `<div class="state${isError ? " error" : ""}">${esc(msg)}</div>`;
}

function skeletonTable(rows = 8) {
  return `<div class="card">${'<div class="skeleton"></div>'.repeat(rows)}</div>`;
}

function stamp() {
  const d = new Date();
  return `Atnaujinta ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// Injury status exactly as the BasketNews report words it ("Out", "Game-time", ...).
function injuryBadge(injury) {
  if (!injury) return "";
  const cls = SEVERITY[injury.status] || "mild";
  const title = [injury.labelLt, injury.return && `grįžimas: ${injury.return}`, injury.reasonLt || injury.comment].filter(Boolean).join(" · ");
  return `<span class="inj ${cls}" title="${esc(title)}">${esc(injury.label)}</span>`;
}

function avatar(p, size = "") {
  return p.photo
    ? `<img class="avatar ${size}" src="${esc(p.photo)}" alt="" loading="lazy" onerror="this.style.visibility='hidden'">`
    : `<span class="avatar ${size}"></span>`;
}

function clubCell(club) {
  if (!club) return "–";
  const logo = club.logo ? `<img src="${esc(club.logo)}" alt="" loading="lazy" onerror="this.remove()">` : "";
  return `<div class="club">${logo}${esc(club.abbr)}</div>`;
}

function gameCell(games) {
  if (!games || !games.length) return '<span class="dim">Nežaidžia</span>';
  return games.map((g) => {
    const vs = `${g.home ? "vs" : "@"} ${esc(g.opponent)}`;
    if (g.canceled) return `<div class="game done">${vs} <span class="when">atšauktos</span></div>`;
    if (g.live) return `<div class="game">${vs} <span class="live-dot">${g.score[0]}:${g.score[1]} LIVE</span></div>`;
    if (g.completed) {
      const res = g.score[0] > g.score[1] ? "W" : "L";
      return `<div class="game done">${vs} <span class="when">${res} ${g.score[0]}:${g.score[1]}</span></div>`;
    }
    return `<div class="game">${vs}<span class="when">${when(g.at)}</span></div>`;
  }).join("");
}

// ------------------------------------------------------------------ line chart (inline SVG)

const CHART_TEAM = "#3987e5";
const CHART_AVG = "#d95926";

function niceStep(range, target) {
  const raw = range / target;
  const mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw;
}

/* series: [{name, color, values: [{x, y}]}]; xs: category labels in order. */
function lineChart(id, { xs, series, invert = false, yMin, yMax, integer = false, format = fmt }) {
  const W = 560, H = 220, L = 44, R = 16, T = 14, B = 30;
  const plotW = W - L - R, plotH = H - T - B;
  let lo = yMin, hi = yMax;
  const all = series.flatMap((s) => s.values.map((v) => v.y)).filter((v) => v !== null && v !== undefined);
  if (lo === undefined) lo = Math.min(0, ...all);
  if (hi === undefined) hi = Math.max(1, ...all);
  let step = integer ? Math.max(1, Math.ceil((hi - lo) / 7)) : niceStep(hi - lo || 1, 4);
  if (!integer) hi = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let v = lo; v <= hi + 1e-9; v += step) ticks.push(v);
  const x = (i) => (xs.length === 1 ? L + plotW / 2 : L + (plotW * i) / (xs.length - 1));
  const y = (v) => {
    const t = (v - lo) / (hi - lo || 1);
    return invert ? T + plotH * t : T + plotH * (1 - t);
  };
  const grid = ticks.map((v) => `<line x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}" class="grid-line"/>
    <text x="${L - 8}" y="${y(v) + 4}" class="tick" text-anchor="end">${integer ? v : fmt(v)}</text>`).join("");
  const xlabels = xs.map((lbl, i) => `<text x="${x(i)}" y="${H - 8}" class="tick" text-anchor="middle">${esc(lbl)}</text>`).join("");
  const lines = series.map((s) => {
    const pts = s.values.map((v, i) => (v.y === null || v.y === undefined ? null : [x(i), y(v.y)]));
    let d = "", pen = false;
    pts.forEach((p) => { if (!p) { pen = false; return; } d += `${pen ? "L" : "M"}${p[0].toFixed(1)},${p[1].toFixed(1)}`; pen = true; });
    const dots = pts.map((p) => (p ? `<circle cx="${p[0]}" cy="${p[1]}" r="4.5" fill="${s.color}" class="dot"/>` : "")).join("");
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>${dots}`;
  }).join("");
  const bands = xs.map((_, i) => {
    const x0 = xs.length === 1 ? L : Math.max(L, x(i) - plotW / (2 * (xs.length - 1)));
    const x1 = xs.length === 1 ? W - R : Math.min(W - R, x(i) + plotW / (2 * (xs.length - 1)));
    return `<rect x="${x0}" y="${T}" width="${x1 - x0}" height="${plotH}" fill="transparent" data-i="${i}" tabindex="0"/>`;
  }).join("");
  const legend = series.length > 1
    ? `<div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${esc(s.name)}</span>`).join("")}</div>` : "";
  const html = `${legend}<div class="chart-wrap" id="${id}">
    <svg viewBox="0 0 ${W} ${H}" role="img">${grid}${xlabels}
      <line class="crosshair" x1="0" x2="0" y1="${T}" y2="${T + plotH}" style="display:none"/>${lines}${bands}</svg>
    <div class="chart-tip" hidden></div></div>`;

  const bind = () => {
    const wrap = document.getElementById(id);
    if (!wrap) return;
    const svg = wrap.querySelector("svg");
    const tip = wrap.querySelector(".chart-tip");
    const cross = wrap.querySelector(".crosshair");
    const show = (i) => {
      const px = x(i);
      cross.setAttribute("x1", px); cross.setAttribute("x2", px); cross.style.display = "";
      tip.replaceChildren();
      const head = document.createElement("div");
      head.className = "tip-head"; head.textContent = xs[i];
      tip.append(head);
      series.forEach((s) => {
        const v = s.values[i]?.y;
        const row = document.createElement("div");
        row.className = "tip-row";
        const key = document.createElement("i"); key.style.background = s.color;
        const val = document.createElement("strong"); val.textContent = v === null || v === undefined ? "–" : format(v);
        const name = document.createElement("span"); name.textContent = s.name;
        row.append(key, val, name);
        tip.append(row);
      });
      tip.hidden = false;
      const rect = svg.getBoundingClientRect();
      const left = (px / W) * rect.width;
      tip.style.left = `${Math.min(Math.max(left, 70), rect.width - 70)}px`;
    };
    const hide = () => { tip.hidden = true; cross.style.display = "none"; };
    wrap.querySelectorAll("rect[data-i]").forEach((r) => {
      r.addEventListener("pointerenter", () => show(+r.dataset.i));
      r.addEventListener("focus", () => show(+r.dataset.i));
      r.addEventListener("blur", hide);
    });
    svg.addEventListener("pointerleave", hide);
  };
  return { html, bind };
}

// ------------------------------------------------------------------ nav

async function loadLeagues(force = false) {
  if (!leaguesCache || force) leaguesCache = (await api("/api/leagues")).leagues;
  return leaguesCache;
}

function renderSwitcher(activeId) {
  const items = (leaguesCache || []).map(
    (l) => `<a class="chip${l.league.id === activeId ? " active" : ""}" href="#/l/${l.league.id}">${esc(l.league.title)}</a>`
  );
  switcher.innerHTML = `<a class="chip${activeId ? "" : " active"}" href="#/">Visos lygos</a>${items.join("")}`;
}

function leagueHeader(league, tab) {
  const tabs = [
    ["standings", "Lentelė", `#/l/${league.id}`],
    ["rounds", league.format === "head_to_head" ? "Mačai" : "Turai", `#/l/${league.id}/rounds`],
    ["records", "Sezono rekordai", `#/l/${league.id}/records`],
    ["free-agents", "Laisvieji agentai", `#/l/${league.id}/free-agents`],
  ];
  const status = league.roundStarted
    ? `<span class="badge live">Vyksta ${roundLabel(league.currentRound)}</span>`
    : `<span>Kitas: ${roundLabel(league.currentRound)} iš ${league.totalRounds}</span>`;
  return `
    <div class="league-head">
      <div>
        <h1 class="page-title">${esc(league.title)}</h1>
        <div class="meta-line">
          <span class="badge format">${FORMAT[league.format] || esc(league.format)}</span>
          <span>${esc(league.competition)}</span><span>·</span>
          <span>${league.teamsCount ?? ""} komandos</span><span>·</span>
          ${status}
        </div>
      </div>
      <a class="ext-link" href="${esc(league.url)}" target="_blank" rel="noopener">BasketNews ↗</a>
    </div>
    <nav class="tabs">
      ${tabs.map(([id, label, href]) => `<a class="tab${id === tab ? " active" : ""}" href="${href}">${label}</a>`).join("")}
    </nav>`;
}

function roundSelect(from, to, selected, labelFn = roundLabel) {
  if (to < from) return "";
  const opts = [];
  for (let r = to; r >= from; r--) {
    opts.push(`<option value="${r}"${r === selected ? " selected" : ""}>${labelFn(r)}</option>`);
  }
  return `<select class="select" id="round-select" aria-label="Turas">${opts.join("")}</select>`;
}

function bindRoundSelect(baseHash) {
  const sel = document.getElementById("round-select");
  if (sel) sel.addEventListener("change", () => (location.hash = `${baseHash}?r=${sel.value}`));
}

function roundArrows(base, r, first, last, labelFn = roundLabel) {
  const prev = r > first ? `<a class="btn nav" href="${base}?r=${r - 1}" aria-label="Ankstesnis turas">‹</a>` : '<span class="btn nav disabled">‹</span>';
  const next = r < last ? `<a class="btn nav" href="${base}?r=${r + 1}" aria-label="Kitas turas">›</a>` : '<span class="btn nav disabled">›</span>';
  return `${prev}${roundSelect(first, last, r, labelFn)}${next}`;
}

function scheduleRefresh(live) {
  clearTimeout(refreshTimer);
  if (live) refreshTimer = setTimeout(() => route(true), REFRESH_LIVE_MS);
}

// ------------------------------------------------------------------ home

async function renderHome(token) {
  renderSwitcher(null);
  setView(`<h1 class="page-title">Mano lygos</h1><p class="page-sub">Pasirink lygą, kad matytum turnyrinę lentelę.</p>
    <div class="league-grid">${'<div class="league-card"><div class="skeleton" style="border:0"></div></div>'.repeat(2)}</div>`);
  let leagues;
  try {
    leagues = await loadLeagues(true);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  renderSwitcher(null);

  const cards = leagues.map((l) => {
    const lg = l.league;
    if (l.error) {
      return `<div class="league-card"><h3>${esc(lg.title)}</h3><div class="form-msg err">${esc(l.error)}</div>
        <button class="remove" data-remove="${lg.id}" title="Pašalinti">×</button></div>`;
    }
    const leader = l.leader
      ? `${esc(l.leader.team.title)} · ${lg.format === "head_to_head" ? `${l.leader.wins}-${l.leader.losses}` : `${fmt(l.leader.pointsTotal)} tšk.`}`
      : "–";
    const mine = l.mine
      ? `<div><div class="stat-label">Mano vieta</div><div class="stat-value">${l.mine.position} / ${l.teams}</div></div>`
      : "";
    return `
      <a class="league-card" href="#/l/${lg.id}">
        <div>
          <h3>${esc(lg.title)}</h3>
          <div class="meta-line" style="margin-top:6px">
            <span class="badge format">${FORMAT[lg.format] || esc(lg.format)}</span>
            <span>${esc(lg.competition)}</span>
          </div>
        </div>
        <div class="stats">
          <div><div class="stat-label">Lyderis</div><div class="stat-value">${leader}</div></div>
          ${mine}
        </div>
        <div class="meta-line">${l.teams} komandos · ${l.round === null ? "sezonas dar neprasidėjo" : `sužaista ${l.round + 1} tur.`}</div>
        <button class="remove" data-remove="${lg.id}" title="Pašalinti lygą">×</button>
      </a>`;
  });

  cards.push(`
    <div class="league-card add-card">
      <form class="add-form" id="add-form">
        <label for="add-url" style="font-weight:600">Pridėti lygą</label>
        <input class="input" id="add-url" placeholder="https://fantasy.basketnews.com/fantasy-leagues/…" autocomplete="off">
        <button class="btn primary" type="submit">Pridėti</button>
        <div class="form-msg" id="add-msg"></div>
      </form>
    </div>`);

  setView(`<h1 class="page-title">Mano lygos</h1><p class="page-sub">Pasirink lygą, kad matytum turnyrinę lentelę.</p>
    <div class="league-grid">${cards.join("")}</div>`);

  document.getElementById("add-form").addEventListener("submit", async (ev) => {
    ev.preventDefault();
    const input = document.getElementById("add-url");
    const msg = document.getElementById("add-msg");
    msg.className = "form-msg";
    msg.textContent = "Tikrinama…";
    try {
      await api("/api/leagues", { method: "POST", body: JSON.stringify({ url: input.value }) });
      route();
    } catch (e) {
      msg.className = "form-msg err";
      msg.textContent = e.message;
    }
  });

  app.querySelectorAll("[data-remove]").forEach((btn) =>
    btn.addEventListener("click", async (ev) => {
      ev.preventDefault();
      ev.stopPropagation();
      if (!confirm("Pašalinti šią lygą iš sąrašo?")) return;
      await api(`/api/leagues/${btn.dataset.remove}`, { method: "DELETE" });
      route();
    })
  );
}

// ------------------------------------------------------------------ standings

function moveMark(gained) {
  if (!gained) return "";
  return gained > 0 ? `<span class="move up">▲${gained}</span>` : `<span class="move down">▼${-gained}</span>`;
}

function standingsTable(data) {
  const { league, rows, myTeamId, hasTies, live } = data;
  const h2h = league.format === "head_to_head";
  const head = `
    <tr>
      <th class="rank">#</th><th>Komanda</th>
      ${h2h ? `<th class="ctr">P</th><th class="ctr">Pr</th>${hasTies ? '<th class="ctr">L</th>' : ""}` : ""}
      <th class="num">Taškai</th><th class="num">Šio turo</th><th class="num" title="Pagrindinio penketo žaidėjai, kuriems dar liko žaisti šį turą">Liko</th>
    </tr>`;
  const body = rows
    .map((r) => `
      <tr class="${r.team.id === myTeamId ? "mine" : ""}">
        <td class="rank">${r.position}${moveMark(r.positionGained)}</td>
        <td><a class="team-name" href="#/l/${league.id}/t/${r.team.id}">${esc(r.team.title)}</a><span class="owner">${esc(r.team.owner)}</span></td>
        ${h2h ? `<td class="ctr wins">${r.wins}</td><td class="ctr">${r.losses}</td>${hasTies ? `<td class="ctr">${r.ties}</td>` : ""}` : ""}
        <td class="num">${fmt(r.pointsTotal)}</td>
        <td class="num">${fmt(r.pointsRound)}</td>
        <td class="num${live && r.left ? " left-live" : ""}">${r.left}</td>
      </tr>`)
    .join("");
  return `<div class="card table-scroll"><table class="grid"><thead>${head}</thead><tbody>${body}</tbody></table></div>`;
}

async function renderStandings(fid, params, token, silent) {
  const round = params.get("r");
  if (!silent) setView(skeletonTable());
  let data;
  try {
    data = await api(`/api/league/${fid}/standings${round !== null ? `?round=${round}` : ""}`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league } = data;
  const caption = data.round === null
    ? "Sezonas dar neprasidėjo"
    : data.live ? `Vyksta ${roundLabel(data.round)}` : `Po ${data.round + 1} turo`;
  setView(`
    ${leagueHeader(league, "standings")}
    <div class="toolbar">
      <div class="toolbar-left">
        ${data.round !== null ? roundSelect(league.firstRound, league.latestRound, data.round) : ""}
        <span>${caption}</span>
      </div>
      <span class="updated">${stamp()}</span>
    </div>
    ${standingsTable(data)}
    <p class="note">Paspausk ant komandos pavadinimo, kad pamatytum jos sudėtį.${data.myTeamId ? "" : " Savo komandą gali pažymėti jos puslapyje (☆)."}</p>`);
  bindRoundSelect(`#/l/${fid}`);
  scheduleRefresh(data.live);
}

// ------------------------------------------------------------------ rounds / matchups

async function renderRounds(fid, params, token, silent) {
  const round = params.get("r");
  if (!silent) setView(skeletonTable(4));
  let data;
  try {
    data = await api(`/api/league/${fid}/rounds${round !== null ? `?round=${round}` : ""}`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league } = data;
  const myTeamId = (leaguesCache || []).find((l) => l.league.id === fid)?.mine?.team.id;
  const played = data.round < league.currentRound || data.live;
  let content;

  if (league.format === "head_to_head") {
    const cards = data.matchups.map((m) => {
      const side = (t, cls) => t
        ? `<div class="side ${cls}"><a href="#/l/${fid}/t/${t.id}?r=${data.round}">${esc(t.title)}</a><span class="owner">${esc(t.owner)}</span></div>`
        : `<div class="side ${cls}"><span class="dim">Lygos vidurkis</span></div>`;
      const s1 = played ? fmt(m.score1) : "–";
      const s2 = played ? fmt(m.score2) : "–";
      const w1 = played && !data.live && m.score1 > m.score2;
      const w2 = played && !data.live && m.score2 > m.score1;
      const mine = [m.team1?.id, m.team2?.id].includes(myTeamId);
      return `<div class="matchup${mine ? " mine" : ""}">
        ${side(m.team1, "")}
        <div class="score"><span class="${w1 ? "win" : ""}">${s1}</span><span class="sep">:</span><span class="${w2 ? "win" : ""}">${s2}</span></div>
        ${side(m.team2, "right")}
      </div>`;
    });
    content = cards.length ? `<div class="matchups">${cards.join("")}</div>` : stateBox("Šiam turui mačų nėra.");
  } else {
    const rows = data.rows.map((r, i) => `
      <tr class="${r.team.id === myTeamId ? "mine" : ""}">
        <td class="rank">${i + 1}</td>
        <td><a class="team-name" href="#/l/${fid}/t/${r.team.id}?r=${data.round}">${esc(r.team.title)}</a><span class="owner">${esc(r.team.owner)}</span></td>
        <td class="num">${fmt(r.pointsRound)}</td>
        <td class="num">${fmt(r.pointsTotal)}</td>
        <td class="num">${r.position}</td>
      </tr>`).join("");
    content = `<div class="card table-scroll"><table class="grid">
      <thead><tr><th class="rank">#</th><th>Komanda</th><th class="num">Turo taškai</th><th class="num">Iš viso</th><th class="num">Vieta</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  const lastSelectable = league.format === "head_to_head" ? league.totalRounds - 1 : league.latestRound;
  const caption = data.live ? `<span class="badge live">Vyksta</span>` : played ? "" : "<span>Dar nežaista</span>";
  setView(`
    ${leagueHeader(league, "rounds")}
    <div class="toolbar">
      <div class="toolbar-left">${roundSelect(league.firstRound, lastSelectable, data.round)} ${caption}</div>
      <span class="updated">${stamp()}</span>
    </div>
    ${content}`);
  bindRoundSelect(`#/l/${fid}/rounds`);
  scheduleRefresh(data.live);
}

// ------------------------------------------------------------------ season records

function awardCards(cards) {
  return `<div class="awards">${cards.map((c) => {
    const target = c.playerId ? `data-player="${c.playerId}"` : c.teamId ? `data-team="${c.teamId}"` : "";
    return `<div class="award${target ? " clickable" : ""}" ${target}>
      <div class="award-title">${c.icon ? `<span aria-hidden="true">${c.icon}</span> ` : ""}${esc(c.title)}</div>
      <div class="award-main"><span class="award-name">${esc(c.name)}</span>${c.value !== "" ? `<span class="award-value">${esc(c.value)}</span>` : ""}</div>
      ${c.sub ? `<div class="award-sub">${esc(c.sub)}</div>` : ""}
    </div>`;
  }).join("")}</div>`;
}

function formTable(data) {
  const h2h = data.league.format === "head_to_head";
  const rows = data.form.map((f) => {
    const chips = h2h
      ? f.last.map((x) => `<span class="res ${x.result}" title="${roundLabel(x.round)}: ${fmt(x.points)} : ${fmt(x.against)} prieš ${esc(x.opponent)}">${{ W: "P", L: "Pr", T: "L" }[x.result]}</span>`).join("")
      : f.last.map((x) => `<span class="pts-chip" title="${roundLabel(x.round)}">${fmt(x.points)}</span>`).join("");
    const streak = h2h && f.streak.kind ? `${{ W: "P", L: "Pr", T: "L" }[f.streak.kind]}${f.streak.length}` : "–";
    return `<tr>
      <td class="rank">${f.position}</td>
      <td><a class="team-name" href="#/l/${data.league.id}/t/${f.team.id}">${esc(f.team.title)}</a></td>
      <td><div class="chips">${chips || '<span class="dim">–</span>'}</div></td>
      ${h2h ? `<td class="num">${streak}</td><td class="num">${f.longestWin}</td><td class="num">${f.longestLoss}</td>` : ""}
      <td class="num">${fmt(f.avg)}</td><td class="num">${fmt(f.best)}</td><td class="num">${fmt(f.worst)}</td>
    </tr>`;
  }).join("");
  return `<div class="card table-scroll"><table class="grid form-table">
    <thead><tr><th class="rank">#</th><th>Komanda</th><th>Paskutiniai 5</th>
      ${h2h ? '<th class="num">Serija</th><th class="num" title="Ilgiausia pergalių serija">Ilg. P</th><th class="num" title="Ilgiausia pralaimėjimų serija">Ilg. Pr</th>' : ""}
      <th class="num">Vid.</th><th class="num">Geriausias</th><th class="num">Blogiausias</th></tr></thead>
    <tbody>${rows}</tbody></table></div>`;
}

async function renderRecords(fid, params, token, silent) {
  const round = params.get("r");
  if (!silent) setView(skeletonTable(4));
  let data;
  try {
    data = await api(`/api/league/${fid}/records${round !== null ? `?round=${round}` : ""}`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league } = data;
  if (!data.finished.length) {
    setView(`${leagueHeader(league, "records")}${stateBox("Dar nesužaistas nė vienas turas – apdovanojimai atsiras po pirmojo turo.")}`);
    return;
  }
  const first = data.finished[0], last = data.finished[data.finished.length - 1];
  const done = last + 1;
  const missing = data.missingLineups.length
    ? `<p class="note warn-note">Neturime ${data.missingLineups.map((r) => r + 1).join(", ")} turo sudėčių, todėl kapitonų, MVP ir „prarasta dėl sudėties“ skaičiavimuose tie turai neįtraukti.</p>` : "";
  setView(`
    ${leagueHeader(league, "records")}
    ${missing}
    <div class="section-head">
      <h2 class="section-title">${data.round + 1} turo apdovanojimai</h2>
      <div class="round-nav inline">${roundArrows(`#/l/${fid}/records`, data.round, first, last)}</div>
    </div>
    ${awardCards(data.roundAwards)}
    <h2 class="section-title">Sezono „Oskarai“ <span class="dim small">(po ${done} baigto turo)</span></h2>
    ${awardCards(data.oscars)}
    <h2 class="section-title">Sezono rekordai <span class="dim small">(iki ${done} turo)</span></h2>
    ${awardCards(data.records)}
    <h2 class="section-title">Forma ir serijos</h2>
    ${formTable(data)}
    <p class="note">Rodomi tik jau pasibaigę turai. „Prarasta dėl sudėties“ – kiek taškų komanda būtų surinkusi daugiau, jei tų pačių aktyvių žaidėjų penketą, kapitoną ir 6-ą žaidėją būtų išdėsčiusi optimaliai.</p>`);
  bindRoundSelect(`#/l/${fid}/records`);
  app.querySelectorAll("[data-team]").forEach((el) =>
    el.addEventListener("click", () => (location.hash = `#/l/${fid}/t/${el.dataset.team}`)));
}

// ------------------------------------------------------------------ free agents

const faState = { search: "", pos: "", club: "", healthyOnly: false, sort: "avgPts", dir: -1 };

async function renderFreeAgents(fid, token, silent) {
  if (!silent) setView(skeletonTable());
  let data;
  try {
    data = await api(`/api/league/${fid}/free-agents`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league } = data;
  const clubs = [...new Set(data.players.map((p) => p.club?.abbr).filter(Boolean))].sort();
  const lastLabel = `${data.statsRound + 1} tur.`;

  setView(`
    ${leagueHeader(league, "free-agents")}
    <div class="filters">
      <input class="input" id="fa-search" type="search" placeholder="Ieškoti žaidėjo…" value="${esc(faState.search)}" autocomplete="off">
      <select class="select" id="fa-pos" aria-label="Pozicija">
        <option value="">Visos pozicijos</option>
        <option value="guard">Gynėjai</option><option value="forward">Puolėjai</option><option value="center">Centrai</option>
      </select>
      <select class="select" id="fa-club" aria-label="Klubas">
        <option value="">Visi klubai</option>${clubs.map((c) => `<option>${esc(c)}</option>`).join("")}
      </select>
      <label class="check"><input type="checkbox" id="fa-healthy"${faState.healthyOnly ? " checked" : ""}> Tik sveiki</label>
      <span class="updated" id="fa-count"></span>
    </div>
    <div class="card table-scroll">
      <table class="grid players stats-table">
        <thead><tr>
          <th class="sticky">Žaidėjas</th>
          <th>Būklė</th>
          <th>${roundLabel(league.currentRound)}</th>
          <th class="num sortable" data-sort="avgPts" title="Vidutiniai fantasy taškai">Vid. FP</th>
          <th class="num sortable" data-sort="roundPts" title="Fantasy taškai paskutiniame ture">${lastLabel}</th>
          <th class="num sortable" data-sort="gamesPlayed" title="Sužaistos rungtynės">RUNG</th>
          ${statHeads()}
        </tr></thead>
        <tbody id="fa-body"></tbody>
      </table>
    </div>
    <p class="note">Statistika – sezono vidurkiai (metimai – taiklumo %). Laisvieji agentai – visi ${data.totalPlayers} ${esc(league.competition)} žaidėjai, išskyrus ${data.rosteredPlayers} esančius lygos komandų sudėtyse.
      Traumos – iš <a class="link" href="${esc(data.injuryReportUrl || "#")}" target="_blank" rel="noopener">BasketNews traumų sąrašo</a>. Paspausk ant žaidėjo, kad matytum daugiau.</p>`);

  document.getElementById("fa-pos").value = faState.pos;
  document.getElementById("fa-club").value = clubs.includes(faState.club) ? faState.club : "";

  const draw = () => {
    const q = faState.search.trim().toLowerCase();
    const rows = data.players.filter((p) =>
      (!q || p.name.toLowerCase().includes(q)) &&
      (!faState.pos || p.position === faState.pos) &&
      (!faState.club || p.club?.abbr === faState.club) &&
      (!faState.healthyOnly || !p.injury));
    const key = faState.sort;
    rows.sort((a, b) => {
      const av = a[key] ?? -Infinity, bv = b[key] ?? -Infinity;
      return (av > bv ? 1 : av < bv ? -1 : 0) * faState.dir || a.name.localeCompare(b.name);
    });
    document.getElementById("fa-count").textContent = `${rows.length} žaidėjai`;
    app.querySelectorAll("th.sortable").forEach((th) => {
      th.classList.toggle("sorted", th.dataset.sort === key);
      th.dataset.dir = faState.dir > 0 ? "↑" : "↓";
    });
    document.getElementById("fa-body").innerHTML = rows.map((p) => `
      <tr class="clickable" data-player="${p.id}">
        <td class="sticky"><div class="player">${avatar(p)}<div>
          <span class="player-name">${esc(p.name)}</span>
          <div class="sub">${POS[p.position] || ""} · ${esc(p.club?.abbr || "")}</div></div></div></td>
        <td>${p.injury ? injuryBadge(p.injury) : '<span class="dim">–</span>'}</td>
        <td>${gameCell(p.games)}</td>
        <td class="num pts-strong">${fmt1(p.avgPts)}</td>
        <td class="num">${fmt(p.roundPts)}</td>
        <td class="num">${p.gamesPlayed}</td>
        ${statCells(p.season, "avg")}
      </tr>`).join("") || `<tr><td colspan="${6 + STAT_COLS.length}" class="dim" style="text-align:center">Nėra žaidėjų pagal filtrus</td></tr>`;
  };

  document.getElementById("fa-search").addEventListener("input", (e) => { faState.search = e.target.value; draw(); });
  document.getElementById("fa-pos").addEventListener("change", (e) => { faState.pos = e.target.value; draw(); });
  document.getElementById("fa-club").addEventListener("change", (e) => { faState.club = e.target.value; draw(); });
  document.getElementById("fa-healthy").addEventListener("change", (e) => { faState.healthyOnly = e.target.checked; draw(); });
  app.querySelectorAll("th.sortable").forEach((th) => th.addEventListener("click", () => {
    faState.dir = faState.sort === th.dataset.sort ? -faState.dir : -1;
    faState.sort = th.dataset.sort;
    draw();
  }));
  draw();
}

// ------------------------------------------------------------------ team

const STATE_LABEL = { finished: "Baigtas", live: "Vyksta", upcoming: "Dar neprasidėjo" };

function lineupTable(lineup, state) {
  const upcoming = state === "upcoming";
  const scored = lineup.source !== "roster";
  const rows = lineup.players.map((p) => {
    const finishedGames = p.games.length && p.games.every((g) => g.completed || g.canceled);
    const dnp = !upcoming && finishedGames && !p.roundPlayed;
    const fp = upcoming ? "–" : dnp ? '<span class="dim" title="Nežaidė">DNP</span>' : fmt(p.roundPts);
    const contrib = upcoming || !scored ? "–" : fmt(p.contrib);
    const pill = scored ? `<span class="slot ${p.slot}">${esc(p.slotLabel)}</span>` : `<span class="slot bench">${POS[p.position] || "–"}</span>`;
    return `<tr class="${p.slot} clickable" data-player="${p.id}">
      <td class="sticky"><div class="player">${pill}<div>
        <span class="player-name">${esc(p.name)}</span>${p.captain ? '<span class="cap" title="Kapitonas">C</span>' : ""}
        <div class="sub">${POS[p.position] || ""} · ${esc(p.club?.abbr || "")} ${injuryBadge(p.injury)}</div></div></div></td>
      <td class="num">${fp}</td>
      <td class="num dim">${scored && p.mult !== undefined ? `×${p.mult}` : ""}</td>
      <td class="num pts-strong">${contrib}</td>
      ${upcoming ? statCells(p.season, "avg") : statCells(p.roundLine, "round")}
      <td>${gameCell(p.games)}</td>
      <td class="num">${fmt1(p.avgPts)}</td>
    </tr>`;
  }).join("");
  const s = lineup.scoring;
  const foot = s ? `<tfoot><tr><td class="sticky"><strong>Iš viso</strong></td><td></td><td></td><td class="num pts-strong">${fmt(s.total)}</td><td colspan="${STAT_COLS.length + 2}"></td></tr></tfoot>` : "";
  return `<div class="card table-scroll"><table class="grid roster stats-table">
    <thead><tr><th class="sticky">Žaidėjas</th><th class="num" title="Fantasy taškai">FP</th><th class="num" title="Daugiklis">×</th><th class="num" title="Taškai komandai">Tšk</th>
      ${statHeads()}<th>Rungtynės</th><th class="num" title="Sezono vidurkis (FP)">Vid.</th></tr></thead>
    <tbody>${rows}</tbody>${foot}</table></div>`;
}

function roundSummary(data) {
  const { league, result: res, after, team } = data;
  const state = data.roundState;
  const firstGame = data.lineup.players.flatMap((p) => p.games).filter((g) => !g.completed).map((g) => g.at).sort()[0];
  const stateBadge = state === "live" ? '<span class="badge live">Vyksta</span>'
    : `<span class="badge">${STATE_LABEL[state]}</span>`;
  let main = "";
  if (league.format === "head_to_head" && res.opponent !== undefined) {
    const opp = res.opponent
      ? `<a href="#/l/${league.id}/t/${res.opponent.id}?r=${data.round}">${esc(res.opponent.title)}</a>`
      : '<span class="dim">Lygos vidurkis</span>';
    const score = state === "upcoming" ? '<span class="dim">– : –</span>'
      : `<span class="${res.result === "W" ? "win" : ""}">${fmt(res.points)}</span><span class="sep">:</span><span class="${res.result === "L" ? "win" : ""}">${fmt(res.opponentPoints)}</span>`;
    const verdict = { W: '<span class="res W">Laimėjo</span>', L: '<span class="res L">Pralaimėjo</span>', T: '<span class="res T">Lygiosios</span>' }[res.result] || "";
    main = `<div class="versus">
      <div class="side"><strong>${esc(team.title)}</strong></div>
      <div class="score">${score}</div>
      <div class="side right"><strong>${opp}</strong></div>
    </div><div class="verdict">${verdict}</div>`;
  }
  const tiles = [];
  if (league.format !== "head_to_head" && state !== "upcoming") {
    tiles.push(["Turo taškai", fmt(res.points)], ["Turo vieta", res.roundPosition ?? "–"]);
  }
  if (after) {
    tiles.push(["Vieta po turo", `${after.position}<small> / ${league.teamsCount || ""}</small>`]);
    if (league.format === "head_to_head") tiles.push(["Rekordas po turo", `${after.wins}-${after.losses}${after.ties ? `-${after.ties}` : ""}`]);
  }
  const sc = data.lineup.scoring;
  if (sc) {
    tiles.push(["Optimali sudėtis", fmt(sc.optimal)]);
    tiles.push(["Prarasta dėl sudėties", sc.lost ? `−${fmt(sc.lost)}` : "0"]);
  }
  if (state === "live") tiles.push(["Liko žaisti", res.left ?? 0]);
  const when_ = state === "upcoming" && firstGame ? `<span class="dim">Pirmos rungtynės ${when(firstGame)}</span>` : "";
  return `<div class="card round-card">
    <div class="round-card-head">${stateBadge}${when_}</div>
    ${main}
    ${tiles.length ? `<div class="tiles inner">${tiles.map(([l, v]) => `<div class="tile"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("")}</div>` : ""}
  </div>`;
}

function teamCharts(data) {
  const played = data.history.filter((h) => h.state !== "upcoming" && h.points !== undefined);
  if (!played.length) return { html: "", bind: () => {} };
  const xs = played.map((h) => `${h.round + 1} t.`);
  const pos = lineChart("chart-pos", {
    xs, invert: true, integer: true, yMin: 1, yMax: data.league.teamsCount || Math.max(...played.map((h) => h.position || 1)),
    series: [{ name: "Vieta", color: CHART_TEAM, values: played.map((h) => ({ y: h.position ?? null })) }],
    format: (v) => `${v} vieta`,
  });
  const pts = lineChart("chart-pts", {
    xs, yMin: 0,
    series: [
      { name: data.team.title, color: CHART_TEAM, values: played.map((h) => ({ y: h.points })) },
      { name: "Lygos vidurkis", color: CHART_AVG, values: played.map((h) => ({ y: h.leagueAvg ?? null })) },
    ],
  });
  const html = `<div class="charts">
    <div class="card chart-card"><h3 class="chart-title">Vieta lentelėje pagal turus</h3>${pos.html}</div>
    <div class="card chart-card"><h3 class="chart-title">Taškai per turą</h3>${pts.html}</div>
  </div>`;
  return { html, bind: () => { pos.bind(); pts.bind(); } };
}

function historyTable(data) {
  const { league, history } = data;
  if (!history.length) return "";
  const h2h = league.format === "head_to_head";
  const base = `#/l/${league.id}/t/${data.team.id}`;
  const rows = [...history].reverse().map((h) => {
    const sel = h.round === data.round ? " selected" : "";
    if (h2h) {
      const opp = h.opponent ? esc(h.opponent.title) : '<span class="dim">Lygos vidurkis</span>';
      const res = h.result ? `<span class="res ${h.result}">${{ W: "P", L: "Pr", T: "L" }[h.result]}</span>`
        : h.state === "live" ? '<span class="badge live">Vyksta</span>' : '<span class="dim">Kitas</span>';
      const score = h.state === "upcoming" ? '<span class="dim">–</span>' : `${fmt(h.points)} : ${fmt(h.opponentPoints)}`;
      return `<tr class="clickable${sel}" data-href="${base}?r=${h.round}"><td>${roundLabel(h.round)}</td><td>${opp}</td><td class="num">${score}</td><td class="num">${h.position ?? "–"}</td><td class="num">${res}</td></tr>`;
    }
    return `<tr class="clickable${sel}" data-href="${base}?r=${h.round}"><td>${roundLabel(h.round)}</td><td class="num pts-strong">${fmt(h.points)}</td><td class="num">${h.roundPosition ?? "–"}</td><td class="num">${h.position ?? "–"}</td></tr>`;
  }).join("");
  const head = h2h
    ? '<tr><th>Turas</th><th>Varžovas</th><th class="num">Rezultatas</th><th class="num">Vieta</th><th class="num"></th></tr>'
    : '<tr><th>Turas</th><th class="num">Taškai</th><th class="num">Turo vieta</th><th class="num">Vieta</th></tr>';
  return `<h2 class="section-title">Sezono eiga</h2>
    <div class="card table-scroll"><table class="grid history"><thead>${head}</thead><tbody>${rows}</tbody></table></div>`;
}

async function renderTeam(fid, tid, params, token, silent) {
  const round = params.get("r");
  if (!silent) setView(skeletonTable(6));
  let data;
  try {
    data = await api(`/api/league/${fid}/team/${tid}${round !== null ? `?round=${round}` : ""}`);
  } catch (e) {
    if (token === renderToken) setView(stateBox(e.message, true));
    return;
  }
  if (token !== renderToken) return;
  const { league, standing: s, lineup } = data;
  const h2h = league.format === "head_to_head";
  const r = data.round;
  const base = `#/l/${fid}/t/${tid}`;

  const tracked = (leaguesCache || []).some((l) => l.league.id === fid);
  const star = tracked
    ? `<button class="btn star${data.isMine ? " on" : ""}" id="my-team">${data.isMine ? "★ Mano komanda" : "☆ Pažymėti kaip mano"}</button>` : "";
  const seasonLine = [
    esc(data.team.owner),
    `${s.position} vieta iš ${league.teamsCount || ""}`,
    h2h ? `${s.wins}-${s.losses}${s.ties ? `-${s.ties}` : ""}` : null,
    `${fmt(s.pointsTotal)} tšk.`,
  ].filter(Boolean).map((x) => `<span>${x}</span>`).join("<span>·</span>");
  const label = (x) => `${roundLabel(x)}${x === league.currentRound ? " (dabartinis)" : ""}`;
  const charts = teamCharts(data);
  const statsNote = data.roundState === "upcoming"
    ? "Statistika – sezono vidurkiai (metimai – taiklumo %)."
    : `Statistika – ${r + 1} turo (metimai – pataikyta/mesta).`;

  setView(`
    <a class="back" href="#/l/${fid}">← ${esc(league.title)}</a>
    <div class="team-head">
      <div>
        <h1 class="page-title">${esc(data.team.title)}</h1>
        <div class="meta-line">${seasonLine}</div>
      </div>
      ${star}
    </div>
    ${charts.html}
    <div class="round-nav">
      ${roundArrows(base, r, league.firstRound, league.currentRound, label)}
      ${r !== league.currentRound ? `<a class="link small" href="${base}">Į dabartinį turą</a>` : ""}
    </div>
    ${roundSummary(data)}
    <h2 class="section-title">Sudėtis · ${roundLabel(r)}${lineup.formation && lineup.source !== "roster" ? ` <span class="dim small">formacija ${esc(lineup.formation)}</span>` : ""}</h2>
    ${lineup.note ? `<p class="note warn-note">${esc(lineup.note)}</p>` : ""}
    ${lineup.players.length ? lineupTable(lineup, data.roundState) : stateBox("Sudėtis nepasiekiama.")}
    <p class="note">${statsNote} Daugikliai: penketas ×1, kapitonas ×2, 6-as žaidėjas ×1, B2–B5 ×0.5, Out ×0.</p>
    ${historyTable(data)}`);

  charts.bind();
  bindRoundSelect(base);
  app.querySelectorAll("tr[data-href]").forEach((tr) => tr.addEventListener("click", () => (location.hash = tr.dataset.href)));
  const btn = document.getElementById("my-team");
  if (btn) {
    btn.addEventListener("click", async () => {
      await api(`/api/league/${fid}/my-team`, { method: "POST", body: JSON.stringify({ teamId: data.isMine ? null : tid }) });
      await loadLeagues(true);
      route(true);
    });
  }
  scheduleRefresh(data.roundState === "live");
}

// ------------------------------------------------------------------ player modal

function currentLeagueId() {
  const { parts } = parseHash();
  return parts[0] === "l" ? parts[1] : null;
}

async function openPlayer(pid) {
  const fid = currentLeagueId();
  if (!fid) return;
  const token = ++modalToken;
  modalBody.innerHTML = `<div class="state">Kraunama…</div>`;
  if (!modal.open) modal.showModal();
  let data;
  try {
    data = await api(`/api/league/${fid}/player/${pid}`);
  } catch (e) {
    if (token === modalToken) modalBody.innerHTML = stateBox(e.message, true);
    return;
  }
  if (token !== modalToken) return;
  modalBody.innerHTML = playerView(fid, data);
}

function playerView(fid, data) {
  const { player: p, owner, injury } = data;
  const cur = injury.current;
  const ownerHtml = owner
    ? `<a class="badge" href="#/l/${fid}/t/${owner.id}">Komanda: ${esc(owner.title || "")}</a>`
    : '<span class="badge free">Laisvasis agentas</span>';

  const status = cur
    ? `<div class="status-box ${SEVERITY[cur.status] || "mild"}">
        <div class="status-top"><strong>${esc(cur.label)}</strong>${cur.labelLt ? `<span class="dim">${esc(cur.labelLt)}</span>` : ""}${cur.return ? `<span>Numatomas grįžimas: ${esc(cur.return)}</span>` : ""}</div>
        ${cur.comment ? `<div class="status-comment">${cur.reasonLt ? `${esc(cur.reasonLt)} · ` : ""}<span class="dim">„${esc(cur.comment)}“</span></div>` : ""}
      </div>`
    : `<div class="status-box ok"><strong>Sveikas</strong><span class="dim">Traumų sąraše nėra</span></div>`;

  const season = p.season || {};
  const tiles = [
    ["Vid. FP", fmt1(p.avgPts)], ["Rungt.", p.gamesPlayed], ["Min.", fmt1(season.min)], ["Tšk.", fmt1(season.pts)],
    ["Atk. kam.", fmt1(season.reb)], ["Rez. perd.", fmt1(season.ast)], ["Perimti", fmt1(season.stl)], ["NB", fmt1(season.eff)],
  ];

  const episodes = injury.episodes.map((e) => {
    const reason = e.reasonLt || e.reason || "Priežastis nenurodyta";
    const span = `${shortDay(e.start)} → ${e.ongoing ? "dabar" : shortDay(e.end)}`;
    const missed = e.missedRounds.length ? ` · praleido: ${e.missedRounds.map((r) => `${r + 1} t.`).join(", ")}` : "";
    const updates = e.updates.map((u) => `<li><span class="dim">${shortDay(u.date)}</span> ${esc(u.statusLabel)}${u.return ? ` (${esc(u.return)})` : ""}${u.comment ? ` – ${esc(u.comment)}` : ""}</li>`).join("");
    return `<li class="episode ${e.kind}">
      <div class="ep-head"><span class="ep-kind">${e.kind === "injury" ? "Trauma" : "Kita"}</span><strong>${esc(reason[0].toUpperCase() + reason.slice(1))}</strong></div>
      <div class="ep-meta">${span} · ${e.days} d.${e.ongoing ? " (tęsiasi)" : ""}${missed}</div>
      ${e.reasonLt && e.reason ? `<div class="ep-orig dim">Šaltinis: „${esc(e.reason)}“</div>` : ""}
      <ul class="ep-updates">${updates}</ul>
    </li>`;
  }).join("");

  const log = data.gameLog.map((g) => {
    const games = g.games.map((x) => `${x.home ? "vs" : "@"} ${esc(x.opponent)}${x.score ? ` ${x.score[0] > x.score[1] ? "W" : "L"} ${x.score[0]}:${x.score[1]}` : ""}`).join(", ") || '<span class="dim">–</span>';
    if (g.status === "played") {
      return `<tr><td class="sticky">${roundLabel(g.round)}</td><td>${games}</td><td class="num pts-strong">${fmt(g.fp)}</td>${statCells(g.line, "round")}</tr>`;
    }
    const why = { dnp: `Nežaidė${g.reason ? ` – ${esc(g.reason)}` : ""}`, "no-game": "Komanda nežaidė", pending: "Dar nežaista" }[g.status];
    return `<tr><td class="sticky">${roundLabel(g.round)}</td><td>${games}</td><td colspan="${STAT_COLS.length + 1}" class="${g.status === "dnp" ? "dnp" : "dim"}">${why}</td></tr>`;
  }).join("");

  const next = data.nextGames?.length
    ? `${roundLabel(data.league.currentRound)}: ${data.nextGames.map((g) => `${g.home ? "vs" : "@"} ${esc(g.opponent)} ${g.score ? `${g.score[0]}:${g.score[1]}` : when(g.at)}`).join(", ")}`
    : "";

  return `
    <div class="pm-head">
      ${avatar(p, "lg")}
      <div class="pm-title">
        <h2>${esc(p.name)}</h2>
        <div class="meta-line">${clubCell(p.club)}<span>·</span><span>${POS_LONG[p.position] || ""}</span>${p.number != null ? `<span>·</span><span>#${p.number}</span>` : ""}</div>
        <div class="meta-line" style="margin-top:8px">${ownerHtml}${next ? `<span class="dim small">${next}</span>` : ""}</div>
      </div>
      <button class="close" type="button" data-close aria-label="Uždaryti">×</button>
    </div>
    ${status}
    <div class="tiles compact">${tiles.map(([l, v]) => `<div class="tile"><div class="label">${l}</div><div class="value">${v}</div></div>`).join("")}</div>
    <h3 class="section-title">Traumų istorija</h3>
    <p class="summary">${esc(injury.summary)}</p>
    ${episodes ? `<ul class="episodes">${episodes}</ul>` : ""}
    <p class="note">Istorija kaupiama automatiškai iš <a class="link" href="${esc(injury.reportUrl || "#")}" target="_blank" rel="noopener">BasketNews traumų sąrašo</a> kol veikia programa, o praleisti turai – iš rungtynių statistikos.</p>
    <h3 class="section-title">Turai</h3>
    <div class="card table-scroll"><table class="grid log stats-table">
      <thead><tr><th class="sticky">Turas</th><th>Rungtynės</th><th class="num">FP</th>${statHeads()}</tr></thead>
      <tbody>${log || `<tr><td colspan="${STAT_COLS.length + 3}" class="dim">Dar nėra sužaistų turų</td></tr>`}</tbody>
    </table></div>`;
}

modal.addEventListener("click", (e) => {
  if (e.target === modal || e.target.closest("[data-close]")) modal.close();
  if (e.target.closest("a[href^='#']")) modal.close();
});

app.addEventListener("click", (e) => {
  const row = e.target.closest("[data-player]");
  if (row && !e.target.closest("a")) openPlayer(row.dataset.player);
});

// ------------------------------------------------------------------ router

async function route(silent = false) {
  const token = ++renderToken;
  clearTimeout(refreshTimer);
  if (!silent) window.scrollTo(0, 0);
  const { parts, params } = parseHash();

  if (parts[0] !== "l" || !parts[1]) return renderHome(token);

  const fid = parts[1];
  if (!leaguesCache) {
    try { await loadLeagues(); } catch { /* nav is optional */ }
    if (token !== renderToken) return;
  }
  renderSwitcher(fid);

  if (parts[2] === "t" && parts[3]) return renderTeam(fid, parts[3], params, token, silent);
  if (parts[2] === "rounds") return renderRounds(fid, params, token, silent);
  if (parts[2] === "records") return renderRecords(fid, params, token, silent);
  if (parts[2] === "free-agents") return renderFreeAgents(fid, token, silent);
  return renderStandings(fid, params, token, silent);
}

window.addEventListener("hashchange", () => route());
route();
