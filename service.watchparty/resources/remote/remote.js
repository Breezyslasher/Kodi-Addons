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
import { roomScreen, browseScreen, titleScreen, controlsPanel, roomNow } from './screens.js';

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

/** Where the room is now. */
const roomNowHere = room => roomNow(room, client ? client.serverNowSec() : 0);

async function send(cmd, positionSec = roomNowHere(lastRoom)) {
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
const controls = controlsPanel({
  get joined() { return joined; },
  get room() { return lastRoom; },
  serverNowSec: () => (client ? client.serverNowSec() : 0),
  send: (cmd, positionSec) => send(cmd, positionSec),
}, "Every TV, phone, browser and Kodi in the room follows. This page doesn't play anything itself.");
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
