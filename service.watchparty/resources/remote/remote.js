// The remote: runs a Watch Party room from any browser, with nothing to
// install. Search and start a title for everyone, play, pause, seek or stop
// the whole room; it plays nothing itself (a web page can't drive another
// site's video — the browser extension does that). It joins the room as a
// member so it can send commands, and shows up there under its name.
//
// The screens, link rules and styles are the extension's own (copied in by
// WatchParty's tools/vendor-remote.sh); this file is the remote's shell.
// JustWatch's API and HBO Max's pages don't answer other sites' pages, so
// those requests go through the relay (/remote/fetch, members only).

import { RelayClient, RelayError } from './relay.js';
import { serviceOf, roomItem } from './links.js';
import { JustWatch } from './justwatch.js';
import { el, label, toast } from './ui.js';
import { roomScreen, browseScreen, titleScreen } from './screens.js';

const $ = id => document.getElementById(id);
const BASE = location.origin;

let client = null;
let joined = false;
let joining = false;
let busy = false;
let lastRoom = null;
let myName = '';
/** What this remote started, by link: title, sub, service, image (for the Room screen). */
const playing = new Map();

// ── JustWatch and HBO Max through the relay ──────────────────────────────

const VIA_RELAY = /^https:\/\/(apis\.justwatch\.com\/graphql$|www\.hbomax\.com\/)/;
const realFetch = globalThis.fetch.bind(globalThis);
globalThis.fetch = async (input, init = {}) => {
  const url = typeof input === 'string' ? input : input.url;
  if (!VIA_RELAY.test(url)) return realFetch(input, init);
  if (!client || !client.memberId) throw new Error('join the room first');
  const res = await realFetch(BASE + '/remote/fetch', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ room: client.room, member_id: client.memberId, url,
      method: init.method || 'GET', body: init.body == null ? null : String(init.body) }),
  });
  const data = await res.json().catch(() => ({ ok: false, error: `relay sent HTTP ${res.status}` }));
  if (!data.ok) throw new Error(data.error || 'the relay could not fetch it');
  return new Response(data.body, { status: data.status, headers: { 'Content-Type': data.content_type || 'text/plain' } });
};

// ── settings (this browser only) ─────────────────────────────────────────

function saved(key) {
  try { return localStorage.getItem('wp.' + key) || ''; } catch (e) { return ''; }
}
function save(key, value) {
  try { localStorage.setItem('wp.' + key, value); } catch (e) { /* private window: not kept */ }
}

function defaultCountry() {
  const region = (navigator.language || 'en-US').split('-')[1];
  return region && /^[A-Za-z]{2}$/.test(region) ? region.toUpperCase() : 'US';
}

// ── what the screens use ─────────────────────────────────────────────────

const startCountry = saved('country') || defaultCountry();
const app = {
  get joined() { return joined; },
  get room() { return lastRoom; },
  playing,
  country: startCountry,
  jw: new JustWatch(startCountry),
  noSearchText: 'Join the room to search.',
  setCountry(c) {
    app.country = c;
    app.jw = new JustWatch(c);
    save('country', c);
  },
  /** Search goes through the relay, for members. */
  justWatchAccess: async () => joined,
  async start(link, label, shown, identity = null) {
    lastRoom = await client.command('open', 0, roomItem(link, label, serviceOf(link), identity));
    playing.set(link, shown);
    go('room');
    toast(`Sent to the room: ${shown.title}`);
    render(lastRoom);
  },
  go: s => go(s),
  openTitle: t => openTitle(t),
};

// ── the Room screen, with the room's controls ────────────────────────────

function fmt(sec) {
  sec = Math.max(0, Math.floor(sec));
  const h = Math.floor(sec / 3600), m = Math.floor(sec / 60) % 60, s = sec % 60;
  return (h ? `${h}:${String(m).padStart(2, '0')}` : `${m}`) + `:${String(s).padStart(2, '0')}`;
}

/** "1:02:03", "62:03", "90" (seconds) → seconds, or null. */
function parseTime(text) {
  const parts = text.trim().split(':');
  if (!parts.length || parts.length > 3 || parts.some(p => !/^\d+$/.test(p))) return null;
  return parts.reduce((acc, p) => acc * 60 + Number(p), 0);
}

/** Where the room is now (the relay's anchor, run forward while playing). */
function roomNow(room) {
  if (!room || !room.item) return 0;
  if (room.paused) return room.positionSec;
  return room.positionSec + Math.max(0, client.serverNowSec() - room.setAtSec) * (room.speed > 0 ? room.speed : 1);
}

const SVG = 'http://www.w3.org/2000/svg';

/** The transport's icons, as the Android app draws them (Glyph in Ui.kt): play,
 *  pause, stop, and a circular arrow with a number for skipping back or forward. */
function glyph(kind, number) {
  const svg = document.createElementNS(SVG, 'svg');
  svg.setAttribute('viewBox', '0 0 100 100');
  svg.setAttribute('aria-hidden', 'true');
  const add = (tag, attrs) => {
    const e = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
    svg.append(e);
    return e;
  };
  if (kind === 'play') add('path', { d: 'M24 14 L88 50 L24 86 Z', fill: 'currentColor' });
  else if (kind === 'pause') {
    add('rect', { x: 22, y: 16, width: 20, height: 68, rx: 5, fill: 'currentColor' });
    add('rect', { x: 58, y: 16, width: 20, height: 68, rx: 5, fill: 'currentColor' });
  } else if (kind === 'stop') add('rect', { x: 19, y: 19, width: 62, height: 62, rx: 10, fill: 'currentColor' });
  else {
    // a clockwise arc ending at the top, its arrowhead pointing on; mirrored for back
    const g = add('g', kind === 'back' ? { transform: 'translate(100 0) scale(-1 1)' } : {});
    const r = 40, end = 285 * Math.PI / 180, start = (285 - 290) * Math.PI / 180;
    const pt = a => [50 + r * Math.cos(a), 50 + r * Math.sin(a)];
    const [sx, sy] = pt(start), [ex, ey] = pt(end);
    const arc = document.createElementNS(SVG, 'path');
    arc.setAttribute('d', `M${sx} ${sy} A${r} ${r} 0 1 1 ${ex} ${ey}`);
    arc.setAttribute('fill', 'none');
    arc.setAttribute('stroke', 'currentColor');
    arc.setAttribute('stroke-width', '7.5');
    arc.setAttribute('stroke-linecap', 'round');
    const dx = -Math.sin(end), dy = Math.cos(end), h = 20;
    const head = document.createElementNS(SVG, 'path');
    head.setAttribute('d', `M${ex + dx * h * 0.7} ${ey + dy * h * 0.7} ` +
      `L${ex - dx * h * 0.3 - dy * h * 0.55} ${ey - dy * h * 0.3 + dx * h * 0.55} ` +
      `L${ex - dx * h * 0.3 + dy * h * 0.55} ${ey - dy * h * 0.3 - dx * h * 0.55} Z`);
    head.setAttribute('fill', 'currentColor');
    g.append(arc, head);
    const t = add('text', { x: 50, y: 51, 'text-anchor': 'middle', 'dominant-baseline': 'central',
      'font-size': 30, 'font-weight': 700, fill: 'currentColor' });
    t.textContent = number;
  }
  return svg;
}

function controlsPanel() {
  const clock = el('div', { class: 'clock' }, '0:00');
  const state = el('span', { class: 'statePill' }, el('i', {}), el('span', {}, 'Playing'));
  const playButton = el('button', { class: 'round big', id: 'playPause', title: 'Pause',
    onclick: () => send(lastRoom && lastRoom.paused ? 'play' : 'pause') }, glyph('pause'));
  const seekButton = (kind, n) => el('button', { class: 'round', title: `${kind === 'back' ? 'Back' : 'Forward'} ${n} seconds`,
    'aria-label': `${kind === 'back' ? 'Back' : 'Forward'} ${n} seconds`,
    onclick: () => send('seek', Math.max(0, roomNow(lastRoom) + (kind === 'back' ? -n : n))) }, glyph(kind, String(n)));
  const jumpField = el('input', { class: 'field', id: 'jumpTo', placeholder: 'Go to 1:02:03', autocomplete: 'off', inputmode: 'numeric' });
  const jump = () => {
    const sec = parseTime(jumpField.value);
    if (sec == null) { toast('Type a time like 1:02:03 or 45:10'); return; }
    send('seek', sec);
    jumpField.value = '';
  };
  jumpField.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); jump(); } });
  // Stop asks twice (it ends the title for everyone): the first press arms it for 4 s
  const stopLabel = el('span', {}, 'Stop for everyone');
  let armedUntil = 0;
  const stop = el('button', { class: 'stopButton', id: 'stop', onclick: () => {
    if (Date.now() < armedUntil) {
      armedUntil = 0;
      stopLabel.textContent = 'Stop for everyone';
      send('stop', 0);
    } else {
      armedUntil = Date.now() + 4000;
      stopLabel.textContent = 'Press again to stop';
    }
  } }, glyph('stop'), stopLabel);
  let shownPaused = null;
  const view = el('div', { class: 'controls', hidden: true },
    el('div', { class: 'controlsHead' }, label('Everyone'), state),
    clock,
    el('div', { class: 'transport' }, seekButton('back', 30), seekButton('back', 10), playButton,
      seekButton('forward', 10), seekButton('forward', 30)),
    el('div', { class: 'controlsFoot' },
      el('div', { class: 'jump' }, jumpField, el('button', { class: 'primary', id: 'go', onclick: jump }, 'Go')),
      stop),
    el('p', { class: 'controlsNote' }, "Every TV, phone, browser and Kodi in the room follows. This page doesn't play anything itself."));
  return {
    view,
    tick(room) {
      if (armedUntil && Date.now() > armedUntil) {
        armedUntil = 0;
        stopLabel.textContent = 'Stop for everyone';
      }
      const item = room && room.item;
      view.hidden = !(joined && item);
      if (view.hidden) { shownPaused = null; return; }
      clock.textContent = fmt(roomNow(room));
      if (shownPaused !== room.paused) {
        shownPaused = room.paused;
        state.classList.toggle('paused', room.paused);
        state.lastChild.textContent = room.paused ? 'Paused' : 'Playing';
        // the button shows what it does: play when paused, pause when playing
        playButton.replaceChildren(glyph(room.paused ? 'play' : 'pause'));
        playButton.title = room.paused ? 'Play' : 'Pause';
        playButton.setAttribute('aria-label', playButton.title);
      }
    },
  };
}

async function send(cmd, positionSec = roomNow(lastRoom)) {
  if (!client) return;
  try {
    lastRoom = await client.command(cmd, positionSec);
    render(lastRoom);
  } catch (e) {
    toast(e.status === 403 ? 'Controls are locked by the host' : `Couldn't send it: ${e.message}`);
  }
}

// ── layout: the rail and the screens ─────────────────────────────────────

const room = roomScreen(app);
const controls = controlsPanel();
room.view.insertBefore(controls.view, room.view.children[1]);   // under Now playing, above Browse
const screens = { room, browse: browseScreen(app) };
let current = 'room';
const pages = [];   // title pages opened from Browse (Back returns through them)

const navs = {};
const field = (labelText, id, placeholder, caps = false) => el('label', { class: 'field-label' }, labelText,
  el('input', { class: 'field' + (caps ? ' caps' : ''), id, placeholder, autocomplete: 'off', spellcheck: 'false' }));
const form = el('div', {}, label('Join a room'), field('Room code', 'room', '', true), field('Your name', 'name', 'Remote'));
const roomCode = el('div', { class: 'roomCode' });
const members = el('div', { class: 'members' });
const info = el('div', { hidden: true }, label('In room'), roomCode, members);
const joinButton = el('button', { class: 'primary', id: 'join', onclick: toggleJoin }, 'Join room');
const statusLine = el('div', { class: 'status' }, 'Not in a room');
$('rail').append(
  el('div', { class: 'brand' }, 'Watch Party remote'),
  el('nav', { class: 'navs' }, [['room', 'Room'], ['browse', 'Browse']].map(([key, text]) => {
    navs[key] = el('button', { class: 'nav', onclick: () => go(key) }, text);
    return navs[key];
  })),
  el('div', { class: 'divider' }),
  el('div', { class: 'railBody' }, form, info, joinButton),
  el('div', { class: 'railSpacer' }),
  statusLine,
  el('div', { class: 'keep' }, `Relay: ${location.host}. The room's TVs, phones, browsers and Kodi play; this page runs it.`));
for (const id of ['room', 'name']) $(id).addEventListener('keydown', e => { if (e.key === 'Enter') toggleJoin(); });
$('room').value = new URLSearchParams(location.search).get('room') || saved('room');
$('name').value = saved('name');

function show(view, focus) {
  $('content').replaceChildren(view);
  for (const [key, b] of Object.entries(navs)) b.classList.toggle('on', key === current);
  window.scrollTo(0, 0);
  if (focus) focus.focus({ preventScroll: true });
}

function go(key) {
  for (const p of pages) p.close();
  pages.length = 0;
  current = key;
  history.replaceState({ depth: 0 }, '');
  const s = screens[key];
  show(s.view, document.activeElement && document.activeElement.closest('.rail') ? null : s.defaultFocus());
}

function openTitle(t) {
  const page = titleScreen(app, t);
  pages.push(page);
  current = 'browse';
  history.pushState({ depth: pages.length }, '');
  show(page.view, page.defaultFocus());
}

window.addEventListener('popstate', () => {
  const page = pages.pop();
  if (page) page.close();
  const top = pages[pages.length - 1];
  show(top ? top.view : screens[current].view, null);
});
window.addEventListener('pagehide', () => {
  // leave the room when the page closes (the relay also drops silent members)
  if (joined && client && client.memberId) {
    navigator.sendBeacon(BASE + '/leave', new Blob([JSON.stringify({ member_id: client.memberId, room: client.room })],
      { type: 'application/json' }));
  }
});
go('room');

// ── joining ──────────────────────────────────────────────────────────────

function toggleJoin() {
  if (joined) leave(); else join();
}

async function join() {
  if (joining) return;
  const code = $('room').value.trim().toUpperCase();
  myName = $('name').value.trim() || 'Remote';
  if (!code) {
    setStatus('Enter the room code.', true);
    return;
  }
  save('room', code);
  save('name', $('name').value.trim());
  joining = true;
  joinButton.disabled = true;
  setStatus('Joining…');
  client = new RelayClient(BASE, code, realFetch);
  try {
    lastRoom = await client.join(myName);
  } catch (e) {
    client = null;
    setStatus(`Couldn't join: ${e.message}`, true);
    return;
  } finally {
    joining = false;
    joinButton.disabled = false;
  }
  joined = true;
  roomCode.textContent = code;
  toast(`Joined ${code}`);
  render(lastRoom);
}

async function leave() {
  joined = false;
  const c = client;
  client = null;
  lastRoom = null;
  render(null);
  setStatus('Not in a room');
  if (c) {
    await c.leave();
    toast(`Left ${c.room}`);
  }
}

// ── the loop: stay in the room, show it ──────────────────────────────────

async function step() {
  if (!joined || busy) return;
  busy = true;
  try {
    // a member that's never on the title: it doesn't hold the room for buffering or count for drift
    const room = await client.poll({ positionSec: 0, paused: true, file: '', caching: false, onItem: false, corrections: 0 });
    if (!joined) return;
    lastRoom = room;
    render(room);
  } catch (e) {
    if (e instanceof RelayError && e.status === 410 && client) {
      try {
        lastRoom = await client.join(myName);   // the relay restarted, or we were silent too long
      } catch (e2) {
        setStatus(`Rejoining failed: ${e2.message}`, true);
      }
    } else if (joined) {
      setStatus(e.message || String(e), true);
    }
  } finally {
    busy = false;
  }
}
setInterval(step, 1000);
// the clock runs between polls
setInterval(() => { if (joined && lastRoom) controls.tick(lastRoom); }, 250);

let shownMembers = '';

function render(room) {
  form.hidden = joined;
  info.hidden = !joined;
  joinButton.textContent = joined ? 'Leave room' : 'Join room';
  const list = joined && room ? room.members : [];
  const key = JSON.stringify(list.map(m => [m.name, m.caching, m.onItem, m.paused, m.driftSec == null ? null : m.driftSec.toFixed(1)]));
  if (key !== shownMembers) {
    shownMembers = key;
    members.replaceChildren(...list.map(m => el('div', { class: 'member' },
      el('div', { class: 'who' }, el('div', { class: 'name' }, m.name || 'Someone'),
        el('div', { class: 'what' }, m.caching ? 'Buffering' : !m.onItem ? 'Not on the title' : m.paused ? 'Paused' : 'Watching')),
      el('div', { class: 'drift mono' }, m.driftSec == null ? '—' : `${m.driftSec >= 0 ? '+' : ''}${m.driftSec.toFixed(1)}s`))));
  }
  room && joined ? screens.room.tick(room) : screens.room.tick(null);
  controls.tick(room);
  if (joined && room) setStatus(`In room ${client.room} · ${room.members.length} here`);
}

function setStatus(text, warn = false) {
  statusLine.textContent = text;
  statusLine.classList.toggle('warn', warn);
}

// for the end-to-end test
window.__remote = { get room() { return lastRoom; } };
