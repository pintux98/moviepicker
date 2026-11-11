"use strict";

const $app = document.getElementById("app");
const $toast = document.getElementById("toast");
const POLL_MS = 1000;
let session = load("mp");
let state = null, lastKey = "", timer = null, seenMatch = null, genreList = null;
let picked = null, lateGenresSent = false, busy = false, dragging = false, animating = false, lastScreen = "";
let moodMode = null, typedWord = null;
const calm = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

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
  let ok = true;
  try {
    await api("POST", `/api/rooms/${session.code}/action`, { type, ...extra });
  } catch (e) {
    if (e.status === 404) { setSession(null); closedScreen(); return false; }
    toast(e.message, true);
    ok = false;
  }
  poll();
  return ok;
}

function onState() {
  const s = state;
  if (s.last_match) {
    if (seenMatch !== null && s.last_match.seq > seenMatch) {
      if (!s.solo) toast(`It's a match: ${s.last_match.title} (${s.last_match.yes} of ${s.last_match.of} said yes)`);
      const bar = $app.querySelector(".shortlist");
      if (bar) burst(...centerOf(bar), 50);
    }
    seenMatch = s.last_match.seq;
  } else if (seenMatch === null) seenMatch = 0;
  const key = JSON.stringify(s) + "|" + lateGenresSent;
  const screen = s.phase + "|" + (s.match ?? "");
  if (key === lastKey || dragging || animating || (screen === lastScreen && document.activeElement?.id === "word")) return;
  lastKey = key;
  if (screen !== lastScreen) { lastScreen = screen; fadeIn(); }
  ({ lobby: lobbyScreen, genres: genresScreen, swiping: swipeScreen, tournament: tournamentScreen, spin: spinScreen, winner: winnerScreen })[s.phase]?.(s);
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
    lastKey = ""; seenMatch = null; picked = null; lateGenresSent = false; moodMode = null; typedWord = null;
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

let bulbsLit = false;
function marquee(lines, small, tag = "h1", label = "Moviepicker") {
  const sign = h("div", { class: "marquee" + (small ? " small" : "") + (bulbsLit ? "" : " lit") },
    h(tag, { class: "wordmark", "aria-label": label }, lines.flatMap((l, i) => (i ? [h("br"), l] : [l]))));
  bulbsLit = true;
  return sign;
}

async function homeScreen() {
  history.replaceState(null, "", "/");
  const draw = (stats) => show(
    h("div", { class: "hero" + (stats && stats.top.length ? " compact" : "") },
      marquee(stats && stats.top.length ? ["MOVIEPICKER"] : ["MOVIE", "PICKER"], stats && stats.top.length),
      h("p", { class: "muted" }, "Find a movie everyone wants to watch.")),
    statsSection(stats),
    h("div", { class: "bottom home-actions" },
      h("div", { class: "row-2" },
        h("button", { onclick: createScreen }, "New room"),
        h("button", { class: "alt", onclick: () => joinScreen("") }, "Join")),
      h("button", { class: "ghost just-me", onclick: () => enter(() => api("POST", "/api/rooms", { name: load("mp-name") || "Me", solo: true })) },
        "Just me: pick a movie alone")),
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

async function shareInvite(code) {
  const url = location.origin + "/r/" + code;
  try {
    if (navigator.share) return await navigator.share({ title: "Moviepicker", text: "Join my movie room: " + code, url });
    await navigator.clipboard.writeText(url);
    toast("Invite link copied");
  } catch (e) {
    if (e.name === "AbortError") return;
    getSelection().selectAllChildren(document.getElementById("invite"));
    toast("Copy the link above");
  }
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
    h("h2", {}, "Your room"),
    h("div", { class: "ticket big", "aria-label": "Room code " + s.code.split("").join(" ") }, h("span", { class: "label" }, "ROOM"), s.code),
    qrImage(s.code),
    h("p", { class: "muted" }, "Scan the code or open this link:"),
    h("p", { class: "invite", id: "invite" }, location.host + "/r/" + s.code),
    h("button", { class: "alt", onclick: () => shareInvite(s.code) }, "Share invite link"),
    playerChips(s),
    h("p", { class: "muted small" }, `${s.players.length} / ${s.max_players}`),
    finaleToggle(s),
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

function finaleToggle(s) {
  const label = { vote: "Vote", spin: "Spin the wheel" };
  if (!s.host) return h("p", { class: "muted" }, "Final pick: " + label[s.finale].toLowerCase());
  return h("div", { class: "finale", role: "group", "aria-label": "Final pick" }, "Final pick:", Object.entries(label).map(([k, l]) =>
    h("button", { class: "chip" + (s.finale === k ? " on" : ""), "aria-pressed": String(s.finale === k),
      onclick: () => act("finale", { mode: k }) }, l)));
}

const MOODS = ["heist", "space", "zombie", "time travel", "superhero", "christmas", "road trip", "ghost"];
function moodPicker(s) {
  if (moodMode === null) moodMode = s.me.word ? "word" : "genres";
  if (typedWord === null) typedWord = s.me.word;
  const redraw = () => { lastKey = ""; onState(); };
  const tab = (k, l) => h("button", { class: "chip" + (moodMode === k ? " on" : ""), "aria-pressed": String(moodMode === k),
    onclick: () => { moodMode = k; redraw(); } }, l);
  const word = h("input", { id: "word", maxlength: 20, autocomplete: "off", enterkeyhint: "go", placeholder: "e.g. heist",
    "aria-label": "One word", value: typedWord, oninput: (e) => { typedWord = e.target.value; } });
  return [
    h("div", { class: "chips" }, tab("genres", "Genres"), tab("word", "One word")),
    moodMode === "genres" ? genreChips() : h("div", { class: "stack" }, word,
      h("div", { class: "chips" }, MOODS.map((w) => h("button", { class: "chip" + (typedWord.trim().toLowerCase() === w ? " on" : ""),
        onclick: () => { typedWord = w; redraw(); } }, w))))];
}

function sendMood() {
  const w = moodMode === "word" ? typedWord.trim() : "";
  return w ? act("mood", { word: w }) : act("genres", { ids: moodMode === "word" ? [] : [...picked] });
}

function genresScreen(s) {
  if (!picked) picked = new Set(s.me.genres);
  const here = s.players.filter((p) => !p.away);
  const ready = here.filter((p) => p.ready).length;
  const picker = moodPicker(s);
  show(
    h("h2", {}, "What are you in the mood for?"),
    h("p", { class: "muted" }, moodMode === "word" ? "Type a theme, a place, anything. Or tap one." : "Pick as many as you like."),
    picker,
    s.solo ? finaleToggle(s) : h("section", { class: "people" },
      h("h3", {}, "In this room"),
      playerChips(s)),
    h("div", { class: "bottom" },
      s.solo ? null : h("p", { class: "muted small" }, `${ready} of ${here.length} ready`),
      s.me.ready
        ? h("button", { disabled: true }, "Waiting for the others")
        : h("button", { onclick: async () => { if (await sendMood()) act("ready"); } }, s.solo ? "Start picking" : "I'm ready"),
      s.solo ? h("button", { class: "ghost", onclick: () => act("close") }, "Cancel")
        : s.host && here.length > 1 ? h("button", { class: "alt", onclick: () => act("force_start") }, "Start without waiting") : null));
}

function lateGenresScreen(s) {
  if (!picked) picked = new Set();
  show(
    h("h2", {}, "What are you in the mood for?"),
    h("p", { class: "muted" }, "Pick what you're in the mood for, then start picking."),
    moodPicker(s),
    h("div", { class: "bottom" },
      h("button", { onclick: async () => { lateGenresSent = true; if (!(await sendMood())) lateGenresSent = false; } }, "Start picking")));
}

function matchesBar(s) {
  const slots = [];
  for (let i = 0; i < 4; i++) {
    const m = s.shortlist[i];
    slots.push(m && m.poster ? h("img", { class: "slot-in", src: m.poster, alt: m.title, title: m.title }) : h("span", { class: "slot", "aria-hidden": "true" }));
  }
  return h("div", { class: "matches" },
    h("div", { class: "shortlist" }, `Matches ${Math.min(s.shortlist.length, 4)}/4`, slots),
    h("p", { class: "muted small" }, s.finale === "spin" ? "At 4 matches you spin the wheel." : "At 4 matches you vote head-to-head."));
}

let lastCard = null, keyVote = null;
document.addEventListener("keydown", (e) => {
  if (!keyVote || !document.querySelector("main > .card") || document.querySelector(".backdrop")) return;
  if (e.key === "ArrowRight") keyVote(true);
  else if (e.key === "ArrowLeft") keyVote(false);
});
function swipeScreen(s) {
  if (!s.me.ready && !lateGenresSent && !s.me.genres.length && !s.me.word) return lateGenresScreen(s);
  const notice = s.notice ? h("p", { class: "notice" }, s.notice) : null;
  const hostVote = s.host && s.shortlist.length
    ? h("button", { class: "ghost vote-now", onclick: () => {
        const n = s.shortlist.length, what = s.finale === "spin" ? "Spin the wheel" : "Start the vote";
        if (confirm(`${what} with ${n} match${n > 1 ? "es" : ""}?`)) act("start_tournament");
      } }, `${s.finale === "spin" ? "Spin" : "Vote"} now (${s.shortlist.length})`) : null;
  const topbar = h("div", { class: "topbar" },
    s.solo ? h("span", { class: "ticket code-chip" }, h("span", { class: "label" }, "JUST ME"))
      : h("span", { class: "ticket code-chip", "aria-label": "Room code " + s.code.split("").join(" ") }, h("span", { class: "label" }, "ROOM"), s.code),
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
      h("p", { class: "overview" }, m.overview)),
    h("b", { class: "stamp yes", "aria-hidden": "true" }, "YES"),
    h("b", { class: "stamp no", "aria-hidden": "true" }, "NOPE"));
  const vote = (yes) => {
    if (busy) return;
    busy = true;
    const dir = yes ? 1 : -1;
    card.dataset.lean = yes ? "yes" : "no";
    card.style.animation = "none";
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
  let x0 = null, dx = 0, far = 0;
  card.addEventListener("pointerdown", (e) => {
    x0 = e.clientX; dx = far = 0; dragging = true;
    card.style.animation = "none";
    card.style.transition = "none";
    card.setPointerCapture(e.pointerId);
  });
  card.addEventListener("pointermove", (e) => {
    if (x0 === null) return;
    dx = e.clientX - x0;
    far = Math.max(far, Math.abs(dx));
    card.style.transform = `translateX(${dx}px) rotate(${dx / 18}deg)`;
    card.dataset.lean = dx > 40 ? "yes" : dx < -40 ? "no" : "";
  });
  const end = () => {
    if (x0 === null) return;
    x0 = null; dragging = false;
    card.dataset.lean = "";
    if (Math.abs(dx) > 90) return vote(dx > 0);
    if (far < 6) openSheet(movie);
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
    h("p", { class: "muted" }, `${s.voted}/${s.of} voted`));
}

let fx = null, parts = [];
const centerOf = (el) => { const r = el.getBoundingClientRect(); return [r.left + r.width / 2, r.top + r.height / 2]; };
function burst(x, y, n) {
  if (calm()) return;
  if (!fx) { fx = h("canvas", { class: "fx", "aria-hidden": "true" }); document.body.append(fx); }
  const d = devicePixelRatio, colors = ["#ffc93c", "#fff6d8", "#ff7a6b", "#ff9f1c", "#ffe08a"];
  if (!parts.length) { fx.width = innerWidth * d; fx.height = innerHeight * d; requestAnimationFrame(fxTick); }
  for (let i = 0; i < n; i++) {
    const a = Math.random() * Math.PI * 2, v = 4 + Math.random() * 9, pop = Math.random() < .35;
    parts.push({ x: x * d, y: y * d, vx: Math.cos(a) * v * d, vy: (Math.sin(a) * v - 6) * d, r: (pop ? 6 + Math.random() * 4 : 3 + Math.random() * 3) * d,
      c: pop ? "#fff6d8" : colors[i % colors.length], pop, spin: Math.random() * 6, life: 1 });
  }
}
function fxTick() {
  const g = fx.getContext("2d"), d = devicePixelRatio;
  g.clearRect(0, 0, fx.width, fx.height);
  parts = parts.filter((p) => p.life > 0 && p.y < fx.height + 40);
  for (const p of parts) {
    p.vy += .35 * d; p.vx *= .985; p.x += p.vx; p.y += p.vy; p.life -= .008; p.spin += .2;
    g.globalAlpha = Math.max(0, p.life);
    g.fillStyle = p.c;
    if (p.pop) {
      for (let k = 0; k < 4; k++) { g.beginPath(); g.arc(p.x + Math.cos(k * 1.6) * p.r * .6, p.y + Math.sin(k * 1.6) * p.r * .6, p.r * .6, 0, 7); g.fill(); }
    } else {
      g.save(); g.translate(p.x, p.y); g.rotate(p.spin); g.fillRect(-p.r, -p.r / 2, p.r * 2, p.r); g.restore();
    }
  }
  g.globalAlpha = 1;
  if (parts.length) requestAnimationFrame(fxTick);
}

const SLICES = ["#ffc93c", "#5a1222", "#ff7a6b", "#3d0b16"];
function drawWheel(cv, list, angle) {
  const g = cv.getContext("2d"), c = 300, slice = Math.PI * 2 / list.length;
  cv.angle = angle;
  g.clearRect(0, 0, 600, 600);
  list.forEach((m, i) => {
    g.beginPath(); g.moveTo(c, c); g.arc(c, c, 284, angle + i * slice, angle + (i + 1) * slice); g.closePath();
    g.fillStyle = SLICES[i % 4]; g.fill();
    g.save(); g.translate(c, c); g.rotate(angle + (i + .5) * slice);
    g.textAlign = "right"; g.textBaseline = "middle";
    g.fillStyle = i % 2 ? "#fff1c2" : "#2a0710"; g.font = "700 26px Rubik, system-ui, sans-serif";
    g.fillText(m.title.length > 18 ? m.title.slice(0, 17) + "…" : m.title, 262, 0);
    g.restore();
  });
  g.beginPath(); g.arc(c, c, 290, 0, 7); g.lineWidth = 10; g.strokeStyle = "#ffc93c"; g.stroke();
  for (let i = 0; i < 24; i++) { g.beginPath(); g.arc(c + Math.cos(i * Math.PI / 12) * 290, c + Math.sin(i * Math.PI / 12) * 290, 6, 0, 7); g.fillStyle = "#fff6d8"; g.fill(); }
  g.beginPath(); g.arc(c, c, 36, 0, 7); g.fillStyle = "#1a050a"; g.fill(); g.lineWidth = 4; g.stroke();
}
function wheelBox(list) {
  const cv = h("canvas", { width: 600, height: 600, role: "img", "aria-label": "Wheel: " + list.map((m) => m.title).join(", ") });
  drawWheel(cv, list, 0);
  document.fonts?.ready.then(() => drawWheel(cv, list, cv.angle));
  return h("div", { class: "wheel" }, cv, h("span", { class: "pointer", "aria-hidden": "true" }));
}
function spinWheel(cv, list, idx, cover, done) {
  const slice = Math.PI * 2 / list.length, a0 = cv.angle, turn = Math.PI * 2;
  const land = -Math.PI / 2 - (idx + .5) * slice + (Math.random() - .5) * slice * .6;
  const total = turn * 6 + (((land - a0) % turn) + turn) % turn, t0 = performance.now(), dur = 4600;
  let covered = false;
  const step = (now) => {
    const k = Math.min(1, (now - t0) / dur);
    drawWheel(cv, list, a0 + total * (1 - Math.pow(1 - k, 4)));
    if (k > .7 && !covered) { covered = true; cover(); }
    if (k < 1) requestAnimationFrame(step); else setTimeout(done, 300);
  };
  requestAnimationFrame(step);
}

function spinScreen(s) {
  const host = s.players.find((p) => p.host);
  show(
    h("h2", {}, "Spin the wheel"),
    h("p", { class: "muted" }, "The wheel picks from your matches."),
    h("div", { class: "stage wide" }, wheelBox(s.shortlist.slice(0, 4))),
    h("div", { class: "bottom" },
      s.host ? h("button", { onclick: () => act("spin") }, "Spin")
        : h("p", { class: "muted" }, `Waiting for ${host ? host.name : "the host"} to spin…`),
      leave()));
}

let revealed = load("mp-seen");
function winnerScreen(s) {
  const m = s.winner, list = s.shortlist.slice(0, 4), key = s.code + ":" + s.spins;
  const fresh = revealed !== key && !calm();
  revealed = key;
  store("mp-seen", key);
  const wheel = fresh && s.finale === "spin" && s.spins > 0 && list.length > 1 ? wheelBox(list) : null;
  const flip = h("div", { class: "flip" + (fresh ? "" : " open") + (wheel ? " hide" : "") },
    h("div", { class: "flip-inner" },
      h("div", { class: "flip-face flip-front", "aria-hidden": "true" }, h("span", {}, "TONIGHT'S", h("br"), "MOVIE")),
      h("div", { class: "flip-face flip-back" }, m.poster ? h("img", { src: m.poster, alt: "" }) : h("span", {}, m.title))));
  $app.classList.toggle("hush", fresh);
  const again = s.host && s.finale === "spin" && list.length > 1
    ? h("button", { class: "alt late", onclick: () => act("spin") }, "Spin again") : null;
  show(h("div", { class: "stack winner" },
      marquee(["NOW", "SHOWING"], true, "p", "Now showing"),
      h("h1", { class: "late" }, titleYear(m)),
      h("div", { class: "stage" }, wheel, flip),
      h("div", { class: "meta center late" }, stars(m), runtime(m.runtime), provider(m)),
      h("div", { class: "late" }, watchLink(m))),
    h("div", { class: "bottom late" },
      s.solo ? [again, h("button", { onclick: () => act("close") }, "Back to start")]
        : [again, s.host ? h("button", { onclick: () => act("close") }, "Close room") : null, leave()]));
  if (!fresh) return;
  animating = true;
  const open = () => {
    flip.classList.remove("hide");
    flip.classList.add("open");
    setTimeout(() => { burst(...centerOf(flip), 160); $app.classList.remove("hush"); animating = false; poll(); }, 450);
  };
  if (!wheel) return setTimeout(open, 700);
  spinWheel(wheel.firstChild, list, list.findIndex((x) => x.id === m.id), () => { flip.classList.remove("hide"); wheel.classList.add("gone"); }, open);
}

function closedScreen() {
  const solo = state && state.solo;
  state = null;
  homeScreen();
  if (!solo) toast("The room was closed");
}

(function boot() {
  const m = location.pathname.match(/^\/r\/([A-Za-z2-9]{6})$/);
  const code = m ? m[1].toUpperCase() : null;
  if (session && (!code || code === session.code)) return poll();
  if (code) return joinScreen(code);
  homeScreen();
})();
