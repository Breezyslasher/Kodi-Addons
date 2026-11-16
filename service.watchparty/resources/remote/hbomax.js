// HBO Max lists some shows twice: the show, and an "<Title> (with ASL)"
// edition with sign-language interpretation, a separate series whose episode
// links JustWatch can give (The Last of Us, checked 2026-11-15). A port of the
// Android app's HboMax.kt: swap such a link for the regular edition's episode,
// found on HBO Max's public pages (no account):
//
//   www.hbomax.com/accessibility/asl   every ASL title, with its seriesId
//   www.hbomax.com/show/<asl id>/s<N>  "You May Also Like" lists the regular series
//   www.hbomax.com/show/<id>/s<N>      season N's episodes and their video ids
//
// Shows without an ASL edition cost one fetch; anything unexpected keeps the link.

const SITE = 'https://www.hbomax.com';
const UUID = '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}';
const WATCH = new RegExp(`(?:hbo)?max\\.com/video/watch/(${UUID})`);
// one JSON object's fields, with at most one level of nested objects — so a match can't run into the next item
const FIELDS = '(?:[^{}]|\\{[^{}]*\\})*?';

const cache = new Map();

export function isHboMax(link) {
  return WATCH.test(link);
}

const escape = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
/** The title as a pattern: literal, except either apostrophe matches (HBO Max writes ’). */
const titlePattern = title => title.trim().split(/['’]/).map(escape).join("['’]");

export function aslSeriesId(page, title) {
  const m = new RegExp(`"__typename":"Series","seriesId":"(${UUID})"${FIELDS}"title":\\{[^{}]*"short":"${titlePattern(title)} \\(with ASL\\)"`, 'i').exec(page);
  return m ? m[1] : null;
}

export function regularSeriesId(page, title, aslId) {
  const re = new RegExp(`"__typename":"Series","seriesId":"(${UUID})"${FIELDS}"title":\\{[^{}]*"short":"${titlePattern(title)}"`, 'gi');
  for (const m of page.matchAll(re)) if (m[1] !== aslId) return m[1];
  return null;
}

export function episodeId(page, seriesId, season, episode) {
  const m = new RegExp(`"episodeUrl":"/show/${seriesId}/s${season}/e${episode}-[^"/]*/(${UUID})"`).exec(page);
  return m ? m[1] : null;
}

async function cachedGet(url) {
  if (cache.has(url)) return cache.get(url);
  const res = await fetch(url, { signal: AbortSignal.timeout(20000) });
  if (res.status !== 200) throw new Error(`HBO Max page ${res.status}`);
  const text = await res.text();
  if (cache.size > 6) cache.clear();   // pages are ~1 MB: keep a few
  cache.set(url, text);
  return text;
}

/** The regular edition's link for season / episode of title, or link unchanged. */
export async function regularEpisode(link, title, season, episode, fetchText = cachedGet) {
  if (!isHboMax(link) || !title.trim()) return link;
  try {
    const asl = aslSeriesId(await fetchText(`${SITE}/accessibility/asl`), title);
    if (!asl) return link;
    const regular = regularSeriesId(await fetchText(`${SITE}/show/${asl}/s${season}`), title, asl);
    if (!regular) return link;
    const id = episodeId(await fetchText(`${SITE}/show/${regular}/s${season}`), regular, season, episode);
    return id ? `https://play.hbomax.com/video/watch/${id}` : link;
  } catch (e) {
    return link;
  }
}
