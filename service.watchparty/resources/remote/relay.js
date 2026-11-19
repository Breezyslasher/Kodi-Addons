// The Watch Party relay protocol (v3), as the Kodi addon's client.py and the
// Android app's RelayClient.kt speak it: JSON POSTs to /join, /poll,
// /command and /leave, each with the room code; every reply carries
// server_time, used for an EMA-smoothed estimate of the relay's clock.

import { serviceOf, parseIdentity, pluginLink, addonOf } from './links.js';

export class RelayError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.status = status;
  }
}

/** "relay.example.com", "192.168.1.5:8765" or a full URL → base URL. */
export function normalizeBase(raw) {
  let url = (raw || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\//.test(url)) url = 'http://' + url;
  return url;
}

/** A web address anyone can open: not a device's own (127.0.0.1, localhost)
 *  or one on a home network. A Kodi addon playing through its local proxy
 *  (SlyGuy's http://127.0.0.1:8095/…) shared that as the file; opened here
 *  it was a dead tab. */
export function isPublicWebLink(url) {
  let u;
  try { u = new URL(String(url || '')); } catch (e) { return false; }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
  const h = u.hostname.replace(/^\[|\]$/g, '').toLowerCase();
  if (h === 'localhost' || h.endsWith('.local') || h.endsWith('.lan') || h === '::1' || !h.includes('.') && !h.includes(':')) return false;
  const v4 = h.match(/^(\d+)\.(\d+)\.(\d+)\.(\d+)$/);
  if (v4) {
    const [a, b2] = [Number(v4[1]), Number(v4[2])];
    if (a === 127 || a === 10 || a === 0 || (a === 169 && b2 === 254) || (a === 192 && b2 === 168) || (a === 172 && b2 >= 16 && b2 <= 31) || (a === 100 && b2 >= 64 && b2 <= 127)) return false;
  }
  if (/^(fc|fd|fe80)/.test(h)) return false;
  return true;
}

/** The relay's member list: name, paused, caching, on_item, drift (null off the item). */
export function parseMembers(array) {
  if (!Array.isArray(array)) return [];
  return array.filter(m => m && typeof m === 'object').map(m => ({
    name: String(m.name || ''),
    paused: !!m.paused,
    caching: !!m.caching,
    onItem: !!m.on_item,
    driftSec: typeof m.drift === 'number' ? m.drift : null,
  }));
}

export class RelayClient {
  constructor(baseUrl, room, fetchImpl = (...a) => fetch(...a)) {
    this.base = normalizeBase(baseUrl);
    this.room = room;
    this.fetch = fetchImpl;
    this.memberId = null;
    this.clockOffsetSec = 0;
    this.offsetSamples = 0;
    // the file last told to the relay: polls send it only when it changes
    // (the relay keeps the last one). null = not yet told (after joining)
    this.sentFile = null;
  }

  /** The relay's clock, in seconds. */
  serverNowSec() {
    return Date.now() / 1000 + this.clockOffsetSec;
  }

  async join(name) {
    const data = await this.post('/join', { name });
    this.memberId = data.member_id;
    this.sentFile = null;
    return this.parseState(data.state);
  }

  async leave() {
    if (!this.memberId) return;
    try {
      await this.post('/leave', { member_id: this.memberId });
    } catch (e) {
      // best-effort; the relay prunes silent members anyway
    }
    this.memberId = null;
  }

  async poll({ positionSec, paused, file, caching, onItem, corrections }) {
    const body = {
      member_id: this.memberId, position: Math.round((Number(positionSec) || 0) * 1000) / 1000,
      paused, caching, on_item: onItem, corrections,
    };
    if (file !== this.sentFile) body.file = file;
    const data = await this.post('/poll', body);
    this.sentFile = file;
    return this.parseState(data.state);
  }

  async command(cmd, positionSec, item = null) {
    const body = { member_id: this.memberId, cmd, position: positionSec };
    if (item) body.item = item;
    return this.parseState((await this.post('/command', body)).state);
  }

  async post(path, body) {
    const t0 = Date.now();
    let res;
    try {
      res = await this.fetch(this.base + path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ...body, room: this.room }),
        signal: AbortSignal.timeout(5000),
      });
    } catch (e) {
      throw new RelayError(`relay unreachable (${e.message || e})`);
    }
    const t1 = Date.now();
    let data;
    try {
      data = await res.json();
    } catch (e) {
      throw new RelayError(`relay sent HTTP ${res.status}, not JSON`, res.status);
    }
    this.updateOffset(Number(data.server_time) || 0, t0, t1);
    if (!data.ok) throw new RelayError(data.error || `relay error (HTTP ${res.status})`, res.status);
    return data;
  }

  updateOffset(serverTime, t0, t1) {
    if (serverTime <= 0) return;
    // assume the relay stamped the reply halfway through the round trip
    const sample = serverTime - (t0 + t1) / 2000;
    this.clockOffsetSec = this.offsetSamples === 0 ? sample : 0.8 * this.clockOffsetSec + 0.2 * sample;
    this.offsetSamples++;
  }

  parseState(s) {
    s = s || {};
    const it = s.item;
    let item = null;
    if (it && typeof it === 'object') {
      const file = String(it.file || '');
      // a Kodi addon's title: its path's web link (its file is Kodi's own
      // stream, e.g. on 127.0.0.1, never opened here)
      const link = it.plugin ? pluginLink(String(it.plugin), it.type ? String(it.type) : '') : (isPublicWebLink(file) ? file : null);
      item = { file, link, label: String(it.label || ''), service: link ? serviceOf(link) : null, identity: parseIdentity(it),
        addon: it.plugin ? addonOf(String(it.plugin)) : null };
    }
    return {
      seq: Number(s.seq) || 0,
      item,
      positionSec: Number(s.position) || 0,
      paused: !!s.paused,
      speed: typeof s.speed === 'number' ? s.speed : 1,
      setAtSec: Number(s.set_at) || 0,
      setByMe: !!this.memberId && s.set_by === this.memberId,
      members: parseMembers(s.members),
    };
  }
}
