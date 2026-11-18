// Which streaming site a link is, and the link to share with the room.
//
// The room stores ordinary https links, the same ones Watch Party's Android
// app shares (its MediaLink.kt rules are ported here), so a browser, a TV and
// a phone can start the same title. Kodi members skip https links.

const UUID = '([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})';
const Q = '(?:[/?#].*)?$';   // the rest of the URL: ignored (tracking etc.)

/** Site → service key, by host name (a host or any subdomain of it). */
const SERVICES = [
  ['netflix.com', 'netflix'],
  ['disneyplus.com', 'disneyplus'],
  ['hbomax.com', 'hbomax'],
  ['max.com', 'hbomax'],
  ['primevideo.com', 'primevideo'],
  ['watch.amazon.com', 'primevideo'],
  ['hulu.com', 'hulu'],
  ['peacocktv.com', 'peacock'],
  ['paramountplus.com', 'paramountplus'],
  ['tv.apple.com', 'appletv'],
  ['youtube.com', 'youtube'],
  ['crunchyroll.com', 'crunchyroll'],
  ['tubitv.com', 'tubi'],
  ['pluto.tv', 'pluto'],
  ['starz.com', 'starz'],
  ['plex.tv', 'plex'],
  ['discoveryplus.com', 'discoveryplus'],
];

/** An Android app for each service, sent with what a browser shares: Watch
 *  Party's Android app opens an item only when it names a package, and when
 *  that one isn't installed it uses the app that opens the link (or one
 *  named like the service) instead — so these only need to be plausible.
 *  The ones the Android app knows, or that were seen on a device. */
const ANDROID = {
  netflix: 'com.netflix.ninja',
  disneyplus: 'com.disney.disneyplus',
  hbomax: 'com.wbd.stream',
  primevideo: 'com.amazon.amazonvideo.livingroom',
  hulu: 'com.hulu.livingroomplus',
  peacock: 'com.peacocktv.peacockandroid',
  paramountplus: 'com.cbs.ott',
  appletv: 'com.apple.atve.androidtv.appletv',
  youtube: 'com.google.android.youtube.tv',
  crunchyroll: 'com.crunchyroll.crunchyroid',
  tubi: 'com.tubitv',
  pluto: 'tv.pluto.android',
  starz: 'com.bydeluxe.d3.android.program.starz',
  plex: 'com.plexapp.android',
  discoveryplus: 'com.discovery.discoveryplus.mobile',
};

/** Names for the room page and shared labels. */
export const SERVICE_NAMES = {
  netflix: 'Netflix', disneyplus: 'Disney+', hbomax: 'HBO Max', primevideo: 'Prime Video',
  hulu: 'Hulu', peacock: 'Peacock', paramountplus: 'Paramount+', appletv: 'Apple TV',
  youtube: 'YouTube', crunchyroll: 'Crunchyroll', tubi: 'Tubi', pluto: 'Pluto TV',
  starz: 'Starz', plex: 'Plex', discoveryplus: 'Discovery+',
};

function host(url) {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch (e) {
    return '';
  }
}

/** The service a link belongs to ("netflix", …), or null. */
export function serviceOf(url) {
  const h = host(url);
  if (!h) return null;
  for (const [site, key] of SERVICES) {
    if (h === site || h.endsWith('.' + site)) return key;
  }
  // Prime Video on amazon.com: /gp/video/…
  if ((h === 'amazon.com' || h.endsWith('.amazon.com')) && /\/gp\/video\//.test(url)) return 'primevideo';
  return null;
}

export function androidPackage(service) {
  return ANDROID[service] || null;
}

/** Peacock's deeplink that starts pvid (as the Android app shares it). */
export function peacockPlay(pvid, type) {
  return 'https://www.peacocktv.com/deeplink?deeplinkData=' +
    encodeURIComponent(JSON.stringify({ pvid, type, action: 'PLAY' }));
}

const TRACKING = /^(utm_|trackId|tctx|ref|ref_|src|source|irclickid|clickid)/i;

/**
 * The link to share for a title's page or player URL: the forms the Android
 * app uses where it has a rule (they start playback on TVs and phones), else
 * the URL without tracking parameters ([strip]). null if it isn't a web link.
 */
export function normalize(raw, { strip = true } = {}) {
  const text = (raw || '').trim();
  if (!/^https?:\/\//.test(text)) return null;
  let m;
  // affiliate redirects (*.pxf.io, *.sjv.io) wrap the real link in u=
  if ((m = text.match(/^https?:\/\/(?:[a-z0-9-]+\.)*(?:pxf|sjv)\.io\/[^?#]*\?([^#]*)/))) {
    const u = new URLSearchParams(m[1]).get('u');
    if (u && /^https?:\/\//.test(u)) return normalize(u, { strip });
  }
  if ((m = text.match(new RegExp(`^https?://(?:www\\.)?disneyplus\\.com/(?:[a-z]{2}-[a-z]{2}/)?(?:play/|browse/entity-)${UUID}${Q}`)))) {
    return `https://www.disneyplus.com/play/${m[1].toLowerCase()}`;
  }
  if ((m = text.match(new RegExp(`^https?://(?:www\\.)?starz\\.com/([a-z]{2})/([a-z]{2})/(?:play|movies)/(\\d+)${Q}`)))) {
    return `https://www.starz.com/${m[1]}/${m[2]}/play/${m[3]}`;
  }
  if ((m = text.match(new RegExp(`^https?://(?:www\\.)?netflix\\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?(?:title|watch)/(\\d+)${Q}`)))) {
    return `https://www.netflix.com/watch/${m[1]}`;
  }
  if ((m = text.match(new RegExp(`^https?://(?:www\\.|play\\.)?(?:hbo)?max\\.com/video/watch/(${UUID}(?:/${UUID})?)${Q}`)))) {
    return `https://play.hbomax.com/video/watch/${m[1].toLowerCase()}`;
  }
  if ((m = text.match(new RegExp(`^https?://(?:www\\.|play\\.)?discoveryplus\\.com/video/watch/(${UUID}(?:/${UUID})?)${Q}`)))) {
    return `https://play.discoveryplus.com/video/watch/${m[1].toLowerCase()}`;
  }
  if ((m = text.match(new RegExp(`^https?://tv\\.apple\\.com/([a-z]{2})/movie/([^/?#]+)/(umc\\.[A-Za-z0-9.]+)${Q}`)))) {
    return `https://tv.apple.com/${m[1]}/movie/${m[2]}/${m[3]}?action=play`;
  }
  if ((m = text.match(/^https?:\/\/tv\.apple\.com\/([a-z]{2})\/episode\/([^/?#]+)\/(umc\.[A-Za-z0-9.]+)(?:\?([^#]*))?(?:#.*)?$/))) {
    const keep = (m[4] || '').split('&').filter(kv => kv.startsWith('showId=') || kv.startsWith('playableId='));
    return `https://tv.apple.com/${m[1]}/episode/${m[2]}/${m[3]}?${[...keep, 'action=play'].join('&')}`;
  }
  if ((m = text.match(/^https?:\/\/(?:www\.)?peacocktv\.com\/watch\/asset\/(movies|tv)\/([^?#]*)\/([0-9a-f-]{36})(?:[/?#].*)?$/))) {
    const episode = m[1] === 'tv' && m[2].includes('/episodes/');
    if (m[1] === 'movies' || episode) return peacockPlay(m[3], episode ? 'EPISODE' : 'PROGRAMME');
  }
  if ((m = text.match(/^https?:\/\/(?:www\.)?crunchyroll\.com\/(?:[a-z]{2}(?:-[a-z]{2})?\/)?watch\/([A-Z0-9]+)(\/[^?#]*)?(?:[?#].*)?$/))) {
    return `https://www.crunchyroll.com/watch/${m[1]}${m[2] || ''}`;
  }
  // anything else: as given (strip: false — JustWatch's links, exactly as the
  // Android app shares them), or without tracking parameters (a tab's URL;
  // YouTube's v=, Amazon's asin= etc. are kept)
  if (!strip) return text;
  try {
    const url = new URL(text);
    for (const key of [...url.searchParams.keys()]) if (TRACKING.test(key)) url.searchParams.delete(key);
    url.hash = '';
    return url.toString();
  } catch (e) {
    return text;
  }
}

/**
 * The form of a room link to open in a browser. Starz's Android TV links
 * (starz.com/movies/<id>, starz.com/series/<id>/episodes/<id> — what TVs
 * share) become the website's /us/en/play/<id>, the form the Android app
 * gives phones (its MediaLink.alternatives). A Paramount+ movie
 * (paramountplus.com/movies/<name>/<id>, /movies/video/<id>/ — JustWatch's
 * link, which the TV app plays) is a page on the website, which plays the
 * movie over it at /movies/video/<id>/#open (the address while playing;
 * opening it starts the movie, 2026-11-17). Others open as they are.
 */
export function browserLink(link) {
  const p = link.trim().match(/^https?:\/\/(?:www\.)?paramountplus\.com\/(?:[a-z]{2}\/)?movies\/(?:video\/|[^/?#]+\/)([A-Za-z0-9_-]+)\/?(?:[?#].*)?$/);
  if (p) return `https://www.paramountplus.com/movies/video/${p[1]}/#open`;
  const m = link.trim().match(/^https?:\/\/(?:www\.)?starz\.com\/(movies|series)\/(\d+)(\/episodes\/(\d+))?(?:[/?#].*)?$/);
  if (m) {
    if (m[1] === 'movies') return `https://www.starz.com/us/en/play/${m[2]}`;
    return m[4] ? `https://www.starz.com/us/en/play/${m[4]}` : `https://www.starz.com/us/en/series/${m[2]}`;
  }
  return link;
}

/** A tab title without the site's suffix: "Thunderbolts* | Disney+" → "Thunderbolts*". */
export function cleanTitle(title, service) {
  let t = (title || '').trim();
  const name = SERVICE_NAMES[service];
  t = t.replace(/^(Watch|Watching)\s+/i, '');
  for (const sep of [' | ', ' - ', ' – ', ' — ', ' · ']) {
    const at = t.lastIndexOf(sep);
    if (at > 0) {
      const tail = t.slice(at + sep.length).toLowerCase();
      if ((name && tail.includes(name.toLowerCase().replace('+', ''))) ||
          /netflix|disney|max|prime|hulu|peacock|paramount|apple|youtube|crunchyroll|tubi|pluto|starz|plex|discovery/.test(tail)) {
        t = t.slice(0, at).trim();
      }
    }
  }
  return t || (name ? `${name} title` : 'A title');
}

/**
 * The relay item for a shared title (the Android app's appItem shape). With
 * the title's identity — in the Kodi addon's own fields: type, title, year,
 * ids (tmdb, imdb) for a movie; show, season, episode for an episode — Kodi
 * members play their library copy of it.
 */
export function roomItem(link, label, service, identity = null) {
  const item = { file: link, label, title: label };
  const ids = {};
  const pkg = androidPackage(service);
  if (pkg) ids.android = pkg;
  if (usable(identity)) {
    item.type = identity.type;
    if (identity.type === 'movie') {
      item.title = identity.title;
      if (identity.year) item.year = identity.year;
    } else {
      Object.assign(item, { show: identity.show, season: identity.season, episode: identity.episode });
      if (identity.year) item.year = identity.year;   // the show's premiere year
    }
    Object.assign(ids, identity.ids || {});
  }
  if (Object.keys(ids).length) item.ids = ids;
  return item;
}

/** Enough to look a title up: a movie's title, or an episode's show, season and episode. */
export function usable(identity) {
  if (!identity) return false;
  if (identity.type === 'movie') return !!identity.title;
  if (identity.type === 'episode') return !!identity.show && Number.isInteger(identity.season) && Number.isInteger(identity.episode);
  return false;
}

/** The title's identity from a relay item (Kodi's items, and Android's and browsers' from Browse). */
export function parseIdentity(it) {
  if (!it || (it.type !== 'movie' && it.type !== 'episode')) return null;
  const ids = {};
  for (const [k, v] of Object.entries(it.ids || {})) if (k !== 'android' && v != null && v !== '') ids[k] = String(v);
  const num = v => (v == null || v === '' || Number.isNaN(Number(v)) ? null : Number(v));
  const text = v => (typeof v === 'string' && v.trim() ? v : null);
  const identity = { type: it.type, title: text(it.title), year: num(it.year), show: text(it.show),
    season: num(it.season), episode: num(it.episode), ids };
  return usable(identity) ? identity : null;
}

/**
 * A Kodi streaming addon's plugin:// path → the web link of the same title
 * (the Android app's AddonLinks.kt; the Kodi addon's addon_links.py does the
 * other way). Formats from each addon's source (2026-11-15): Netflix
 * (play/movie/<id>/, play/show/…/episode/<id>/), YouTube (play/?video_id=),
 * SlyGuy Disney+ (?_=play&deeplink_id=) and Hulu (?_=play&id=EAB::<id>::…),
 * Amazon (?mode=PlayVideo&asin=), Tubi (?mode=GV&url=), Crunchyroll
 * (/video/[<series>/]<episode>/<stream>), SkyOTT for Peacock (?action=play&slug=
 * <web link's path>, probed 2026-11-16), Paramount+ (?_=play&video_id=:
 * /movies/video/<id>/ for a movie, else /shows/video/<id>/, which sends a
 * movie's id on to the movie) and Apple TV (?action=play&item_id=<umc>&
 * item_type=: tv.apple.com/movie|episode/<umc>, which redirects to the full
 * link) — both by id alone (tools/probe/nameless_links.py, 2026-11-16).
 * HBO Max and Discovery+ (edit ids, which no public page gives): null.
 * [type]: the Kodi item's media type ("movie", "episode"), if it gave one.
 */
export function pluginLink(plugin, type) {
  if (!plugin || !plugin.startsWith('plugin://')) return null;
  let url;
  try {
    url = new URL(plugin.replace(/^plugin:/, 'http:'));
  } catch (e) {
    return null;
  }
  const addon = url.hostname;
  const path = url.pathname.split('/').filter(Boolean);
  const arg = k => url.searchParams.get(k);
  switch (addon) {
    case 'plugin.video.netflix': {
      if (!['play', 'play_strm'].includes(path[0])) return null;
      const id = path[1] === 'movie' ? path[2] : (path[1] === 'show' && path[5] === 'episode' ? path[6] : null);
      return id && /^\d+$/.test(id) ? `https://www.netflix.com/watch/${id}` : null;
    }
    case 'plugin.video.youtube': {
      const id = arg('video_id');
      return path[0] === 'play' && id && /^[A-Za-z0-9_-]{6,}$/.test(id) ? `https://www.youtube.com/watch?v=${id}` : null;
    }
    case 'slyguy.disney.plus': {
      const id = arg('_') === 'play' && arg('deeplink_id');
      return id ? `https://www.disneyplus.com/play/${id.replace(/^entity-/, '')}` : null;
    }
    case 'slyguy.hulu': {
      const m = arg('_') === 'play' && (arg('id') || '').match(/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}/);
      return m ? `https://www.hulu.com/watch/${m[0].toLowerCase()}` : null;
    }
    case 'plugin.video.amazon-test': {
      const asin = arg('mode') === 'PlayVideo' && arg('asin');
      return asin ? `https://watch.amazon.com/watch?${asin.startsWith('amzn1.dv.gti') ? 'gti' : 'asin'}=${asin}` : null;
    }
    case 'plugin.video.starz': {
      // Breezyslasher's addon (Kodi-Addons branch starz): Starz's own content id
      const id = arg('action') === 'play' ? arg('content_id') : null;
      return id && /^\d+$/.test(id) ? `https://www.starz.com/us/en/play/${id}` : null;
    }
    case 'plugin.video.tubitv': {
      const parts = arg('mode') === 'GV' ? (arg('url') || '').split('|') : [];
      if (!parts.length || !/^\d+$/.test(parts[0])) return null;
      return parts.length >= 3 ? `https://tubitv.com/tv-shows/${parts[0]}` : `https://tubitv.com/movies/${parts[0]}`;
    }
    case 'plugin.video.skyott': {
      // Peacock (Paco8's SkyOTT): the slug is the web link's path (tools/probe/peacock_slug.py)
      const slug = arg('action') === 'play' && arg('slug');
      return slug && /^\/(movies|tv)\/[^?#\s]+$/.test(slug) ? `https://www.peacocktv.com/watch/asset${slug}` : null;
    }
    case 'slyguy.paramount.plus': {
      const id = arg('_') === 'play' && arg('video_id');
      if (!id || !/^[A-Za-z0-9_-]+$/.test(id)) return null;
      return `https://www.paramountplus.com/${type === 'movie' ? 'movies' : 'shows'}/video/${id}/`;
    }
    case 'plugin.video.appletv': {
      const id = arg('action') === 'play' && arg('item_id');
      const kind = (arg('item_type') || '').toLowerCase();
      if (!id || !/^umc\.[A-Za-z0-9.]+$/.test(id) || !['movie', 'episode'].includes(kind)) return null;
      return `https://tv.apple.com/${kind}/${id}`;
    }
    case 'plugin.video.crunchyroll': {
      if (path[0] !== 'video') return null;
      const episode = path.length === 4 ? path[2] : (path.length === 3 ? path[1] : null);
      return episode && /^[A-Z0-9]+$/.test(episode) ? `https://www.crunchyroll.com/watch/${episode}` : null;
    }
    default:
      return null;
  }
}
