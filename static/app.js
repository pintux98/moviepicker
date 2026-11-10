"use strict";

const $app = document.getElementById("app");
const $toast = document.getElementById("toast");
const POLL_MS = 1000;
let session = load("mp");
let state = null, lastKey = "", timer = null, seenMatch = null, genreList = null;
let picked = null, lateGenresSent = false, busy = false, dragging = false, lastScreen = "";

function load(k) { try { return JSON.parse(localStorage.getItem(k)); } catch { return null; } }
function store(k, v) { try { v == null ? localStorage.removeItem(k) : localStorage.setItem(k, JSON.stringify(v)); } catch {} }
function setSession(s) { session = s; store("mp", s); }

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) if (c != null && c !== false) el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  return el;
}
const show = (...nodes) => $app.replaceChildren(...nodes.flat().filter(Boolean));

async function api(method, path, body) {
  const headers = { "Content-Type": "application/json" };
  if (session) headers["X-Player"] = session.pid;
  const r = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
  let d = {};
  try { d = await r.json(); } catch {}
  if (!r.ok) { const e = new Error(d.error || "Something went wrong"); e.status = r.status; throw e; }
  return d;
}

let toastTimer;
function toast(msg, err = false) {
  $toast.textContent = msg;
  $toast.className = err ? "err" : "";
  $toast.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($toast.hidden = true), 2600);
}

async function poll() {
  clearTimeout(timer);
  if (!session) return;
  try {
    state = await api("GET", "/api/rooms/" + session.code);
    onState();
  } catch (e) {
    if (e.status === 404) { setSession(null); return closedScreen(); }
    if (e.status === 403) { setSession(null); return homeScreen(); }
    if (e.status === 429) toast(e.message, true);
  }
  if (session && !document.hidden) timer = setTimeout(poll, POLL_MS);
}
document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });

async function act(type, extra = {}) {
  try {
    await api("POST", `/api/rooms/${session.code}/action`, { type, ...extra });
  } catch (e) {
    if (e.status === 404) { setSession(null); return closedScreen(); }
    toast(e.message, true);
  }
  poll();
}

function onState() {
  const s = state;
  if (s.last_match) {
    if (seenMatch !== null && s.last_match.seq > seenMatch) toast(`It's a match: ${s.last_match.title} (${s.last_match.yes} of ${s.last_match.of} said yes)`);
    seenMatch = s.last_match.seq;
  } else if (seenMatch === null) seenMatch = 0;
  const key = JSON.stringify(s) + "|" + lateGenresSent;
  if (key === lastKey || dragging) return;
  lastKey = key;
  const screen = s.phase + "|" + (s.match ?? "");
  if (screen !== lastScreen) { lastScreen = screen; fadeIn(); }
  ({ lobby: lobbyScreen, genres: genresScreen, swiping: swipeScreen, tournament: tournamentScreen, winner: winnerScreen })[s.phase]?.(s);
}

const SVG = "http://www.w3.org/2000/svg";
const ICONS = {
  x: "M6 6l12 12M18 6 6 18",
  heart: "M12 20.5s-7.4-4.4-9.3-8.9A5.3 5.3 0 0 1 12 6.2a5.3 5.3 0 0 1 9.3 5.4c-1.9 4.5-9.3 8.9-9.3 8.9z",
  star: "M12 2.8l2.8 5.8 6.4.9-4.6 4.5 1.1 6.3L12 17.3l-5.7 3 1.1-6.3-4.6-4.5 6.4-.9z",
  check: "M5 12.5l4.5 4.5L19 7.5",
};
function icon(name) {
  const s = document.createElementNS(SVG, "svg");
  s.setAttribute("viewBox", "0 0 24 24");
  s.setAttribute("aria-hidden", "true");
  s.setAttribute("class", "icon icon-" + name);
  const p = document.createElementNS(SVG, "path");
  p.setAttribute("d", ICONS[name]);
  s.append(p);
  return s;
}
const brand = (cls) => h("img", { class: "brand " + cls, src: "/static/favicon.svg", alt: "" });
const runtime = (m) => (m ? h("span", {}, `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m`) : null);
const stars = (m) => h("span", { class: "rating" }, icon("star"), Number(m.rating).toFixed(1));
const watchLink = (m) => h("a", { class: "watch-link", href: `https://www.themoviedb.org/movie/${encodeURIComponent(m.id)}/watch?locale=RO`,
  target: "_blank", rel: "noopener noreferrer" }, "Where to watch");
const titleYear = (m) => (m.year ? `${m.title} (${m.year})` : m.title);
const poster = (m, cls) => (m.poster ? h("img", { src: m.poster, alt: "", class: cls, loading: "eager" }) : h("div", { class: cls }));
const framedPoster = (m, cls) => h("div", { class: "frame " + cls },
  m.poster ? h("img", { class: "frame-bg", src: m.poster, alt: "", "aria-hidden": "true" }) : null,
  m.poster ? h("img", { class: "frame-img", src: m.poster, alt: "" }) : null);
const provider = (m) => (m.provider ? h("span", { class: "badge" }, "on " + m.provider) : null);
const leave = () => h("button", { class: "ghost", onclick: async () => {
  try { await api("POST", `/api/rooms/${session.code}/action`, { type: "leave" }); } catch {}
  setSession(null); homeScreen();
} }, "Leave room");

function nameInput() {
  return h("input", { id: "name", maxlength: 20, autocomplete: "nickname", placeholder: "e.g. Alex", value: load("mp-name") || "" });
}
function readName() {
  const v = document.getElementById("name").value.trim();
  if (!v) { toast("Pick a name first", true); return null; }
  store("mp-name", v);
  return v;
}
async function enter(promise) {
  if (busy) return;
  busy = true;
  try {
    const d = await promise();
    setSession({ code: d.code, pid: d.pid });
    history.replaceState(null, "", "/r/" + d.code);
    lastKey = ""; seenMatch = null; picked = null; lateGenresSent = false;
    poll();
  } catch (e) { toast(e.message, true); }
  busy = false;
}

function fadeIn() {
  $app.classList.remove("fade");
  void $app.offsetWidth;
  $app.classList.add("fade");
}

function openSheet(m) {
  const close = () => { back.classList.add("closing"); setTimeout(() => back.remove(), 200); document.removeEventListener("keydown", esc); };
  const esc = (e) => { if (e.key === "Escape") close(); };
  const sheet = h("div", { class: "sheet", role: "dialog", "aria-modal": "true", "aria-label": titleYear(m) },
    h("div", { class: "grab", "aria-hidden": "true" }),
    m.poster ? h("img", { class: "sheet-poster", src: m.poster, alt: "" }) : null,
    h("h2", {}, titleYear(m)),
    h("div", { class: "meta center" }, stars(m), runtime(m.runtime), provider(m)),
    h("p", { class: "overview full" }, m.overview),
    m.picked === undefined ? null : h("div", { class: "stats" },
      h("div", { class: "stat" }, h("b", {}, m.picked), "picked"),
      h("div", { class: "stat" }, h("b", {}, m.likes), "liked"),
      h("div", { class: "stat" }, h("b", {}, m.dislikes), "skipped")),
    watchLink(m),
    h("button", { class: "alt", onclick: () => close() }, "Close"));
  const back = h("div", { class: "backdrop", onclick: (e) => { if (e.target === back) close(); } }, sheet);
  let y0 = null;
  sheet.addEventListener("pointerdown", (e) => { y0 = e.clientY; });
  sheet.addEventListener("pointerup", (e) => { if (y0 !== null && e.clientY - y0 > 80) close(); y0 = null; });
  document.addEventListener("keydown", esc);
  document.body.append(back);
  sheet.querySelector("button").focus();
}

function statsSection(stats) {
  if (!stats || !stats.top.length) return null;
  const row = (m, i) => h("button", { class: "rank-row", onclick: () => openSheet(m) },
    h("span", { class: "rank" }, i + 1),
    poster(m, "mini-poster"),
    h("span", { class: "stack tight" }, h("strong", {}, titleYear(m)),
      h("span", { class: "muted left" }, `picked ${m.picked}×` + (m.dislikes ? ` · skipped ${m.dislikes}×` : ""))));
  const tile = (m) => h("button", { class: "strip-item", onclick: () => openSheet(m), "aria-label": titleYear(m) }, poster(m, "strip-poster"));
  return h("section", { class: "stats-home" },
    h("h3", {}, "Most picked"),
    h("div", { class: "rank-list" }, stats.top.map(row)),
    h("h3", {}, "Picked lately"),
    h("div", { class: "strip" }, stats.recent.map(tile)));
}

async function homeScreen() {
  history.replaceState(null, "", "/");
  const draw = (stats) => show(
    h("div", { class: "hero" + (stats && stats.top.length ? " compact" : "") },
      brand("hero-logo"),
      h("h1", {}, "Moviepicker"),
      h("p", { class: "muted" }, "Find a movie everyone wants to watch.")),
    statsSection(stats),
    h("div", { class: "bottom home-actions" },
      h("div", { class: "row-2" },
        h("button", { onclick: createScreen }, "New room"),
        h("button", { class: "alt", onclick: () => joinScreen("") }, "Join"))),
    h("p", { class: "attrib" }, "This product uses the TMDB API but is not endorsed or certified by TMDB. Movie data from ",
      h("a", { href: "https://www.themoviedb.org/", target: "_blank", rel: "noopener noreferrer" }, "themoviedb.org"), "."));
  draw(null);
  fadeIn();
  try {
    const stats = await api("GET", "/api/stats");
    if ($app.querySelector(".hero") && stats.top.length) { draw(stats); fitRanks(); }
  } catch {}
}

function fitRanks() {
  const rows = [...$app.querySelectorAll(".rank-row")];
  if (!rows.length) return;
  rows.forEach((r) => r.classList.remove("hidden"));
  const box = getComputedStyle($app).overflowY === "auto" ? $app : document.scrollingElement;
  for (let i = rows.length - 1; i > 0 && box.scrollHeight > box.clientHeight + 1; i--) rows[i].classList.add("hidden");
}
addEventListener("resize", fitRanks);

document.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && e.target.matches("main input")) $app.querySelector(".bottom button")?.click();
});

function createScreen() {
  fadeIn();
  show(
    h("h2", {}, "New room"),
    h("label", { for: "name" }, "Your name"), nameInput(),
    h("div", { class: "bottom" },
      h("button", { onclick: () => { const n = readName(); if (n) enter(() => api("POST", "/api/rooms", { name: n })); } }, "Create room"),
      h("button", { class: "ghost", onclick: homeScreen }, "Back")));
  document.getElementById("name").focus();
}

function joinScreen(code) {
  const codeIn = h("input", { id: "code", class: "code", maxlength: 12, autocomplete: "off", autocapitalize: "characters",
    autocorrect: "off", spellcheck: "false", inputmode: "text", value: code, placeholder: "K7XQ2P", "aria-label": "Room code" });
  fadeIn();
  show(
    h("h2", {}, "Join a room"),
    h("label", { for: "code" }, "Room code"), codeIn,
    h("label", { for: "name" }, "Your name"), nameInput(),
    h("div", { class: "bottom" },
      h("button", { onclick: () => {
        const c = codeIn.value.replace(/\s+/g, "").toUpperCase();
        if (!/^[A-Z2-9]{6}$/.test(c)) return toast("The room code has 6 letters and numbers", true);
        const n = readName();
        if (n) enter(() => api("POST", `/api/rooms/${c}/join`, { name: n }));
      } }, "Join"),
      h("button", { class: "ghost", onclick: homeScreen }, "Back")));
  (code ? document.getElementById("name") : codeIn).focus();
}

function qrImage(code) {
  try {
    const q = qrcode(0, "M");
    q.addData(location.origin + "/r/" + code);
    q.make();
    return h("img", { class: "qr", src: q.createDataURL(5, 0), alt: "QR code to join room " + code });
  } catch { return null; }
}

function playerChips(s) {
  return h("div", { class: "players" }, s.players.map((p) =>
    h("span", { class: "chip" + (p.host ? " host" : "") + (p.away ? " away" : "") },
      p.host ? h("span", { class: "crown", title: "Host", "aria-label": "Host" }, icon("star")) : null,
      h("span", {}, p.name),
      p.ready && s.phase === "genres" ? h("span", { class: "tick", "aria-label": "ready" }, icon("check")) : null)));
}

function lobbyScreen(s) {
  show(
    h("h2", {}, "Room"),
    h("div", { class: "roomcode", "aria-label": "Room code " + s.code.split("").join(" ") }, s.code),
    qrImage(s.code),
    h("p", { class: "muted" }, "Scan the code or type it on the other phones."),
    h("button", { class: "ghost share", onclick: async () => {
      const url = location.origin + "/r/" + s.code;
      try {
        if (navigator.share) await navigator.share({ title: "Moviepicker", text: "Join my movie room: " + s.code, url });
        else { await navigator.clipboard.writeText(url); toast("Invite link copied"); }
      } catch {}
    } }, "Share invite link"),
    playerChips(s),
    h("p", { class: "muted small" }, `${s.players.length} / ${s.max_players}`),
    h("div", { class: "bottom" },
      s.host ? h("button", { onclick: () => act("start") }, "Start") : h("p", { class: "muted" }, "Waiting for the host to start…"),
      leave()));
}

async function loadGenres() {
  if (!genreList) {
    try { genreList = await api("GET", "/api/genres"); } catch { genreList = null; toast("Couldn't load genres", true); }
  }
  return genreList || [];
}

function genreChips(onChange) {
  const box = h("div", { class: "chips" });
  const draw = () => box.replaceChildren(...(genreList || []).map((g) =>
    h("button", { class: "chip" + (picked.has(g.id) ? " on" : ""), "aria-pressed": String(picked.has(g.id)),
      onclick: () => { picked.has(g.id) ? picked.delete(g.id) : picked.add(g.id); draw(); onChange?.(); } }, g.name)));
  draw();
  loadGenres().then(draw);
  return box;
}

function genresScreen(s) {
  if (!picked) picked = new Set(s.me.genres);
  const here = s.players.filter((p) => !p.away);
  const ready = here.filter((p) => p.ready).length;
  show(
    h("h2", {}, "What are you in the mood for?"),
    h("p", { class: "muted" }, "Pick as many as you like. None = anything goes."),
    genreChips(),
    playerChips(s),
    h("div", { class: "bottom" },
      h("p", { class: "muted small" }, `${ready} of ${here.length} ready`),
      s.me.ready
        ? h("button", { disabled: true }, "Waiting for the others")
        : h("button", { onclick: async () => { await act("genres", { ids: [...picked] }); act("ready"); } }, "I'm ready"),
      s.host && here.length > 1 ? h("button", { class: "alt", onclick: () => act("force_start") }, "Start without waiting") : null));
}

function lateGenresScreen() {
  if (!picked) picked = new Set();
  show(
    h("h2", {}, "What are you in the mood for?"),
    h("p", { class: "muted" }, "Pick what you're in the mood for, then start swiping."),
    genreChips(),
    h("div", { class: "bottom" },
      h("button", { onclick: async () => { lateGenresSent = true; await act("genres", { ids: [...picked] }); } }, "Start swiping")));
}

function matchesBar(s) {
  const slots = [];
  for (let i = 0; i < 4; i++) {
    const m = s.shortlist[i];
    slots.push(m && m.poster ? h("img", { class: "slot-in", src: m.poster, alt: m.title, title: m.title }) : h("span", { class: "slot", "aria-hidden": "true" }));
  }
  return h("div", { class: "matches" },
    h("div", { class: "shortlist" }, `Matches ${Math.min(s.shortlist.length, 4)}/4`, slots),
    h("p", { class: "muted small" }, "At 4 matches you vote head-to-head."));
}

let lastCard = null, keyVote = null;
document.addEventListener("keydown", (e) => {
  if (!keyVote || !document.querySelector("main > .card") || document.querySelector(".backdrop")) return;
  if (e.key === "ArrowRight") keyVote(true);
  else if (e.key === "ArrowLeft") keyVote(false);
});
function swipeScreen(s) {
  if (!s.me.ready && !lateGenresSent && !s.me.genres.length) return lateGenresScreen();
  const notice = s.notice ? h("p", { class: "notice" }, s.notice) : null;
  const hostVote = s.host && s.shortlist.length
    ? h("button", { class: "ghost vote-now", onclick: () => {
        const n = s.shortlist.length;
        if (confirm(`Start the vote with ${n} match${n > 1 ? "es" : ""}?`)) act("start_tournament");
      } }, `Vote now (${s.shortlist.length})`) : null;
  const topbar = h("div", { class: "topbar" },
    h("span", { class: "code-chip", "aria-label": "Room code " + s.code.split("").join(" ") }, "Room ", h("b", {}, s.code)),
    hostVote);
  if (!s.cards.length) {
    return show(topbar, notice,
      h("div", { class: "wait-logo" }, brand(s.loading ? "loading" : "")),
      h("h2", {}, s.loading ? "Loading movies…" : "You're ahead!"),
      h("p", { class: "muted" }, s.loading ? "Picking movies for your genres." : "Waiting for the others to catch up."),
      matchesBar(s),
      h("div", { class: "bottom" }, leave()));
  }
  const m = s.cards[0];
  for (const next of s.cards.slice(1)) if (next.poster) new Image().src = next.poster;
  const fresh = lastCard !== m.index;
  lastCard = m.index;
  const card = h("article", { class: "card" + (fresh ? " in" : ""), "aria-label": titleYear(m) },
    framedPoster(m, "poster"),
    h("div", { class: "body" },
      h("div", { class: "title" }, titleYear(m)),
      h("div", { class: "meta" }, stars(m), runtime(m.runtime), provider(m)),
      h("p", { class: "overview" }, m.overview)));
  const vote = (yes) => {
    if (busy) return;
    busy = true;
    const dir = yes ? 1 : -1;
    card.classList.remove("in");
    card.style.transition = "transform .28s ease-in, opacity .28s ease-in";
    card.style.transform = `translateX(${dir * 130}%) rotate(${dir * 16}deg)`;
    card.style.opacity = "0";
    setTimeout(async () => { busy = false; await act("swipe", { index: m.index, yes }); }, 200);
  };
  dragGesture(card, vote, m);
  keyVote = vote;
  show(topbar, notice, card,
    h("div", { class: "votes" },
      h("button", { class: "no", "aria-label": "Skip", title: "Skip (← key)", onclick: () => vote(false) }, icon("x")),
      h("button", { class: "yes", "aria-label": "I'd watch it", title: "I'd watch it (→ key)", onclick: () => vote(true) }, icon("heart"))),
    matchesBar(s));
}

function dragGesture(card, vote, movie) {
  let x0 = null, dx = 0;
  card.addEventListener("pointerdown", (e) => {
    x0 = e.clientX; dx = 0; dragging = true;
    card.classList.remove("in");
    card.style.transition = "none";
    card.setPointerCapture(e.pointerId);
  });
  card.addEventListener("pointermove", (e) => {
    if (x0 === null) return;
    dx = e.clientX - x0;
    card.style.transform = `translateX(${dx}px) rotate(${dx / 18}deg)`;
    card.dataset.lean = dx > 40 ? "yes" : dx < -40 ? "no" : "";
  });
  const end = () => {
    if (x0 === null) return;
    x0 = null; dragging = false;
    card.dataset.lean = "";
    if (Math.abs(dx) > 90) return vote(dx > 0);
    if (Math.abs(dx) < 6) openSheet(movie);
    card.style.transition = "transform .2s ease-out";
    card.style.transform = "";
  };
  card.addEventListener("pointerup", end);
  card.addEventListener("pointercancel", end);
}

function tournamentScreen(s) {
  if (!s.pair) return show(h("h2", {}, "Counting votes…"));
  const pick = (m) => h("button", { class: "pick" + (s.my_vote === m.id ? " mine" : ""), "aria-pressed": String(s.my_vote === m.id),
      onclick: async () => {
        if (busy) return;
        busy = true;
        await act("vote", { movie_id: m.id, match: s.match });
        busy = false;
      } },
    framedPoster(m, "ph"),
    h("span", { class: "stack" }, h("strong", {}, titleYear(m)),
      h("span", { class: "meta" }, stars(m), provider(m))));
  show(
    h("h2", {}, s.round_name),
    h("p", { class: "muted" }, "Tap the one you'd rather watch."),
    h("div", { class: "pair" }, pick(s.pair[0]), h("div", { class: "vs" }, "VS"), pick(s.pair[1])),
    h("p", { class: "muted" }, `${s.voted}/${s.of} voted`),
    h("div", { class: "bracket" }, s.bracket.filter((b) => b.winner).map((b) =>
      h("div", { class: "row" }, b.round + ": ", h("span", { class: "w" }, b.winner), ` beat ${b.winner === b.a ? b.b : b.a}`))));
}

function confetti() {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const colors = ["#e5484d", "#ffd166", "#2fbf71", "#4cc9f0", "#b388ff"];
  const box = h("div", { class: "confetti", "aria-hidden": "true" });
  for (let i = 0; i < 60; i++) {
    const p = h("i");
    p.style.left = Math.random() * 100 + "%";
    p.style.background = colors[i % colors.length];
    p.style.animationDelay = Math.random() * 0.6 + "s";
    p.style.animationDuration = 1.8 + Math.random() * 1.4 + "s";
    p.style.setProperty("--drift", (Math.random() * 160 - 80) + "px");
    box.append(p);
  }
  document.body.append(box);
  setTimeout(() => box.remove(), 4000);
}

let celebrated = null;
function winnerScreen(s) {
  const m = s.winner;
  if (celebrated !== s.code) { celebrated = s.code; confetti(); }
  show(h("div", { class: "stack winner pop" },
      h("p", { class: "eyebrow" }, "Tonight's movie"),
      h("h1", {}, titleYear(m)),
      m.poster ? h("img", { src: m.poster, alt: "" }) : null,
      h("div", { class: "meta center" }, stars(m), runtime(m.runtime), provider(m)),
      watchLink(m)),
    h("div", { class: "bottom" },
      s.host ? h("button", { onclick: () => act("close") }, "Close room") : null,
      leave()));
}

function closedScreen() {
  history.replaceState(null, "", "/");
  fadeIn();
  show(
    h("div", { class: "wait-logo" }, brand("")),
    h("h2", {}, "This room is closed"),
    h("p", { class: "muted" }, "The host closed it, or nobody did anything for 10 minutes."),
    h("div", { class: "bottom" }, h("button", { onclick: homeScreen }, "Back to start")));
}

(function boot() {
  const m = location.pathname.match(/^\/r\/([A-Za-z2-9]{6})$/);
  const code = m ? m[1].toUpperCase() : null;
  if (session && (!code || code === session.code)) return poll();
  if (code) return joinScreen(code);
  homeScreen();
})();
