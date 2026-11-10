"use strict";

const $app = document.getElementById("app");
const $toast = document.getElementById("toast");
const POLL_MS = 1000;
let session = load("mp");
let state = null, lastKey = "", timer = null, seenMatch = null, genreList = null;
let picked = null, lateGenresSent = false, busy = false, dragging = false;

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
    if (seenMatch !== null && s.last_match.seq > seenMatch) toast(`💘 Match! ${s.last_match.title} · ${s.last_match.yes} of ${s.last_match.of} said yes`);
    seenMatch = s.last_match.seq;
  } else if (seenMatch === null) seenMatch = 0;
  const key = JSON.stringify(s) + "|" + lateGenresSent;
  if (key === lastKey || dragging) return;
  lastKey = key;
  ({ lobby: lobbyScreen, genres: genresScreen, swiping: swipeScreen, tournament: tournamentScreen, winner: winnerScreen })[s.phase]?.(s);
}

const runtime = (m) => (m ? `${Math.floor(m / 60)}h ${String(m % 60).padStart(2, "0")}m` : null);
const titleYear = (m) => (m.year ? `${m.title} (${m.year})` : m.title);
const poster = (m, cls) => (m.poster ? h("img", { src: m.poster, alt: "", class: cls, loading: "eager" }) : h("div", { class: cls }));
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

function homeScreen() {
  history.replaceState(null, "", "/");
  show(
    h("div", { class: "logo", "aria-hidden": "true" }, "🍿"),
    h("h1", {}, "Moviepicker"),
    h("p", { class: "muted" }, "Find a movie everyone wants to watch."),
    h("div", { class: "bottom" },
      h("button", { onclick: createScreen }, "New room"),
      h("button", { class: "alt", onclick: () => joinScreen("") }, "Join a room"),
      h("p", { class: "attrib" }, "This product uses the TMDB API but is not endorsed or certified by TMDB. Movie data from ",
        h("a", { href: "https://www.themoviedb.org/", target: "_blank", rel: "noopener noreferrer" }, "themoviedb.org"), ".")));
}

function createScreen() {
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
      p.host ? h("span", { class: "crown", title: "Host", "aria-label": "Host" }, "⭐") : null,
      p.name, p.ready && s.phase === "genres" ? h("span", { class: "tick", "aria-label": "ready" }, "✓") : null)));
}

function lobbyScreen(s) {
  show(
    h("h2", {}, "Room"),
    h("div", { class: "roomcode", "aria-label": "Room code " + s.code.split("").join(" ") }, s.code),
    qrImage(s.code),
    h("p", { class: "muted" }, "Scan the code or type it on the other phones."),
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
      s.me.ready
        ? h("button", { disabled: true }, `Ready ✓ · ${ready}/${here.length}`)
        : h("button", { onclick: async () => { await act("genres", { ids: [...picked] }); act("ready"); } }, `Ready · ${ready}/${here.length}`),
      s.host ? h("button", { class: "alt", onclick: () => act("force_start") }, "Start swiping now") : null));
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

function shortlistBar(s) {
  const slots = [];
  for (let i = 0; i < 4; i++) {
    const m = s.shortlist[i];
    slots.push(m && m.poster ? h("img", { src: m.poster, alt: m.title, title: m.title }) : h("span", { class: "slot", "aria-hidden": "true" }));
  }
  return h("div", { class: "shortlist" }, `Shortlist ${s.shortlist.length}`, slots);
}

function swipeScreen(s) {
  if (!s.me.ready && !lateGenresSent && !s.me.genres.length) return lateGenresScreen();
  const notice = s.notice ? h("p", { class: "notice" }, s.notice) : null;
  const hostVote = s.host && s.shortlist.length
    ? h("button", { class: "alt", onclick: () => act("start_tournament") }, `Vote now with ${s.shortlist.length}`) : null;
  if (!s.cards.length) {
    return show(notice,
      h("div", { class: "logo", "aria-hidden": "true" }, "⏳"),
      h("h2", {}, "You're ahead!"),
      h("p", { class: "muted" }, "Waiting for the others to catch up. New cards show up in a second."),
      shortlistBar(s),
      h("div", { class: "bottom" }, hostVote, leave()));
  }
  const m = s.cards[0];
  for (const next of s.cards.slice(1)) if (next.poster) new Image().src = next.poster;
  const card = h("article", { class: "card", "aria-label": titleYear(m) },
    poster(m, "poster"),
    h("div", { class: "body" },
      h("div", { class: "title" }, titleYear(m)),
      h("div", { class: "meta" }, `⭐ ${m.rating.toFixed(1)}`, runtime(m.runtime), provider(m)),
      h("p", { class: "overview" }, m.overview)));
  const vote = (yes) => {
    if (busy) return;
    busy = true;
    card.classList.add(yes ? "yes" : "no");
    setTimeout(async () => { busy = false; await act("swipe", { index: m.index, yes }); }, 180);
  };
  dragGesture(card, vote);
  show(notice, card,
    h("div", { class: "votes" },
      h("button", { class: "no", "aria-label": "No", onclick: () => vote(false) }, "👎"),
      h("button", { class: "yes", "aria-label": "Yes", onclick: () => vote(true) }, "👍")),
    shortlistBar(s), hostVote);
}

function dragGesture(card, vote) {
  let x0 = null, dx = 0;
  card.addEventListener("pointerdown", (e) => { x0 = e.clientX; dx = 0; dragging = true; card.setPointerCapture(e.pointerId); });
  card.addEventListener("pointermove", (e) => {
    if (x0 === null) return;
    dx = e.clientX - x0;
    card.style.transform = `translateX(${dx}px) rotate(${dx / 20}deg)`;
  });
  const end = () => {
    if (x0 === null) return;
    x0 = null; dragging = false;
    if (Math.abs(dx) > 90) vote(dx > 0);
    else card.style.transform = "";
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
    poster(m, "ph"),
    h("span", { class: "stack" }, h("strong", {}, titleYear(m)),
      h("span", { class: "meta" }, `⭐ ${m.rating.toFixed(1)}`, provider(m))));
  show(
    h("h2", {}, s.round_name),
    h("p", { class: "muted" }, "Tap the one you'd rather watch."),
    h("div", { class: "pair" }, pick(s.pair[0]), h("div", { class: "vs" }, "VS"), pick(s.pair[1])),
    h("p", { class: "muted" }, `${s.voted}/${s.of} voted`),
    h("div", { class: "bracket" }, s.bracket.filter((b) => b.winner).map((b) =>
      h("div", { class: "row" }, b.round + ": ", h("span", { class: "w" }, b.winner), ` beat ${b.winner === b.a ? b.b : b.a}`))));
}

function winnerScreen(s) {
  const m = s.winner;
  show(h("div", { class: "stack winner" },
      h("div", { class: "trophy", "aria-hidden": "true" }, "🏆"),
      h("p", { class: "muted" }, "Tonight:"),
      h("h1", {}, titleYear(m)),
      m.poster ? h("img", { src: m.poster, alt: "" }) : null,
      m.provider ? provider(m) : h("p", { class: "muted" }, "Find it on your favorite service.")),
    h("div", { class: "bottom" },
      s.host ? h("button", { onclick: () => act("close") }, "Close room") : null,
      leave()));
}

function closedScreen() {
  history.replaceState(null, "", "/");
  show(
    h("div", { class: "logo", "aria-hidden": "true" }, "🍿"),
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
