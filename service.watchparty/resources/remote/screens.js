// The content screens, ported from the Android app (watchparty-tv
// Screens.kt, ServiceDialog.kt): Room, Browse (search; there's no Yamtrack
// library in the browser), a title's page with its season and episode
// columns, and the "Start for everyone on" dialog. Browse's search also takes a link.

import { el, label, art, posterCard, serviceCard, listButton, rating, date, toast } from './ui.js';
import { discoveryPlayLinks, sharedLinks, runtimeText } from './justwatch.js';
import { regularEpisode, isHboMax } from './hbomax.js';
import { serviceOf, normalize, SERVICE_NAMES } from './links.js';

const COUNTRIES = ['US', 'CA', 'GB', 'IE', 'AU', 'NZ', 'DE', 'AT', 'CH', 'FR', 'BE', 'NL', 'ES', 'IT', 'PT',
  'SE', 'NO', 'DK', 'FI', 'PL', 'BR', 'MX', 'AR', 'IN', 'JP', 'KR', 'ZA'];

const kind = objectType => (objectType === 'SHOW' ? 'TV' : 'Movie');
const seasonName = n => (n === 0 ? 'Specials' : `Season ${n}`);

/** JustWatch's names → the names people know (the Android app's serviceName). */
export function serviceName(o) {
  return ({
    'Disney Plus': 'Disney+', Max: 'HBO Max', 'Apple TV Plus': 'Apple TV+', 'Paramount Plus': 'Paramount+',
    'Amazon Prime Video': 'Prime Video', 'Amazon Prime Video with Ads': 'Prime Video',
  })[o.clearName] || o.clearName;
}

// ── Room (home) ──────────────────────────────────────────────────────────

export function roomScreen(app) {
  const image = el('img', { alt: '' });
  const nowLabel = el('div', { class: 'nowLabel' });
  const nowTitle = el('div', { class: 'nowTitle' });
  const nowSub = el('div', { class: 'nowSub' });
  const backdrop = el('div', { class: 'backdrop', hidden: true }, image, el('div', { class: 'overlay' }, nowLabel, nowTitle, nowSub));
  const idle = el('div', {},
    el('h1', {}, 'What are we watching?'),
    el('p', { class: 'lead' }, 'Pick a title and everyone in the room opens it: TVs and phones in their apps, browsers in a tab.'));
  const browseTile = el('button', { class: 'tile', onclick: () => app.go('browse') },
    el('b', {}, 'Browse'), el('span', {}, 'Search any movie or show, or paste a link'));
  const view = el('div', { class: 'screen' }, backdrop, idle, el('div', { class: 'tiles' }, browseTile));
  let shownFile = '-';

  return {
    view,
    defaultFocus: () => browseTile,
    tick(room) {
      const item = room && room.item;
      if (!item || !app.joined) {
        if (shownFile !== null) {
          shownFile = null;
          backdrop.hidden = true;
          idle.hidden = false;
        }
        return;
      }
      const mine = app.playing.get(item.file);
      const service = (mine && mine.service) || SERVICE_NAMES[item.service] ||
        (/^https?:\/\//.test(item.file) ? new URL(item.file).hostname : 'Kodi');
      nowLabel.textContent = `Now playing · ${service}`;
      nowTitle.textContent = (mine && mine.title) || item.label || item.file;
      nowSub.textContent = `${(mine && mine.sub) || 'Started in the room'} · ${room.paused ? 'Paused' : 'Playing'}`;
      if (shownFile !== item.file) {
        shownFile = item.file;
        backdrop.hidden = false;
        idle.hidden = true;
        if (mine && mine.image) image.src = mine.image; else image.removeAttribute('src');
        image.hidden = !(mine && mine.image);
      }
    },
  };
}

// ── Browse: search ───────────────────────────────────────────────────────

export function browseScreen(app) {
  let type = 'MOVIE';
  let generation = 0;
  const tabs = {};
  const tabRow = el('div', { class: 'tabs' });
  for (const [key, name] of [['MOVIE', 'Movies'], ['SHOW', 'TV shows']]) {
    tabs[key] = el('button', { class: 'tab', onclick: () => select(key) }, name);
    tabRow.append(tabs[key]);
  }
  const query = el('input', { class: 'field surface', id: 'query', placeholder: 'Search movies', autocomplete: 'off', enterkeyhint: 'search' });
  // (preventDefault: a pasted link's search moves focus to Start, and the same Enter would press it)
  query.addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); search(); } });
  const country = el('select', { class: 'field surface country', id: 'country', title: 'Where to watch: country' },
    COUNTRIES.map(c => el('option', { value: c }, c)));
  if (!COUNTRIES.includes(app.country)) country.prepend(el('option', { value: app.country }, app.country));
  country.value = app.country;
  country.addEventListener('change', () => {
    app.setCountry(country.value);
    if (query.value.trim()) search();
  });
  const empty = el('div', { class: 'empty' }, 'Type a title to search, or paste a link.');
  const grid = el('div', { class: 'grid' });
  /** A link typed into the search box: one result, to start it for everyone. */
  const linkResult = el('div', { class: 'linkResult', hidden: true });
  const view = el('div', { class: 'screen' }, tabRow, el('div', { class: 'searchRow' }, query, country), linkResult, empty, grid);
  select('MOVIE', false);

  function select(key, run = true) {
    type = key;
    for (const [k, t] of Object.entries(tabs)) t.classList.toggle('on', k === key);
    query.placeholder = key === 'SHOW' ? 'Search TV shows, or paste a link' : 'Search movies, or paste a link';
    if (run && query.value.trim()) search();
  }

  async function search() {
    const text = query.value.trim();
    linkResult.hidden = true;
    if (/^https?:\/\//.test(text)) {
      ++generation;
      grid.replaceChildren();
      showLink(text);
      return;
    }
    if (!text) {
      grid.replaceChildren();
      empty.textContent = 'Type a title to search, or paste a link.';
      empty.hidden = false;
      return;
    }
    const my = ++generation;
    if (!(await app.justWatchAccess())) {
      empty.textContent = app.noSearchText || 'Watch Party needs access to JustWatch to search.';
      empty.hidden = false;
      return;
    }
    grid.replaceChildren();
    empty.textContent = 'Searching…';
    empty.hidden = false;
    try {
      const titles = await app.jw.search(text, type);
      if (my !== generation) return;
      empty.textContent = titles.length ? '' : 'No matches.';
      empty.hidden = titles.length > 0;
      grid.replaceChildren(...titles.map(t =>
        posterCard(t.title, [kind(t.objectType), t.year || null].filter(Boolean).join(' · '), t.poster, () => app.openTitle(t))));
    } catch (e) {
      if (my === generation) empty.textContent = `Couldn't load: ${e.message}`;
    }
  }

  /** A pasted link: its service and Start for everyone (what Paste a link was). */
  function showLink(text) {
    const shared = normalize(text);
    if (!shared) {
      empty.textContent = "That isn't a web link.";
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    const service = serviceOf(shared);
    const name = SERVICE_NAMES[service] || new URL(shared).hostname;
    const title = service ? `${name} title` : name;
    const note = el('span', { class: 'warn' });
    const start = el('button', { class: 'primary', id: 'startLink' }, 'Start for everyone');
    start.addEventListener('click', async () => {
      if (!app.joined) {
        note.textContent = 'Join a room first.';
        return;
      }
      note.textContent = '';
      try {
        await app.start(shared, title, { title, sub: 'Shared link', service: name, image: null });
        query.value = '';
        linkResult.hidden = true;
      } catch (e) {
        note.textContent = `Couldn't share: ${e.message}`;
      }
    });
    linkResult.replaceChildren(
      label('Link'),
      el('div', { class: 'linkTitle' }, title),
      el('div', { class: 'linkSub' }, `${service ? `Opens on ${name}` : "Opens in a tab; can't be synced here"} · ${shared}`),
      el('div', { class: 'pasteActions' }, start, note),
      el('p', { class: 'linkHint' }, "Links copied while the title plays work best. Without a title's details, Kodi members can't play their own copy."));
    linkResult.hidden = false;
    start.focus();
  }

  return { view, defaultFocus: () => (query.value ? grid.querySelector('.poster') || query : query) };
}

// ── a title's page: season → episode → service ───────────────────────────

export function titleScreen(app, t) {
  const show = t.objectType === 'SHOW';
  let gen = 0;
  let dialog = null;
  let movieOffers = null;   // loaded with the page, so Watch opens them at once
  let movieError = null;
  let seasonEpisodes = [];
  let firstSeason = null;

  const poster = art(t.poster);
  const facts = el('div', { class: 'facts', hidden: true });
  const genres = el('div', { class: 'tags', hidden: true });
  const about = el('div');
  const watch = show ? null : el('button', { class: 'primary', onclick: () => openMovieServices() }, 'Watch');
  const hint = el('div', { class: 'hint', hidden: true });
  const seasonList = el('div', { class: 'list' });
  const episodeList = el('div', { class: 'list' });
  const more = el('div', { class: 'section', hidden: true });

  const view = el('div', { class: 'screen' },
    el('div', { class: 'hero' }, poster,
      el('div', { class: 'heroText' },
        el('div', { class: 'kind' }, [kind(t.objectType), t.year || null].filter(Boolean).join(' · ')),
        el('h1', {}, t.title), facts, genres, about, watch)),
    hint,
    show ? el('div', { class: 'cols' },
      el('div', { class: 'col seasons' }, label('Season'), seasonList),
      el('div', { class: 'col episodes' }, label('Episode'), episodeList)) : null,
    more);

  function showHint(text, warn = false) {
    hint.textContent = text;
    hint.classList.toggle('warn', warn);
    hint.hidden = false;
  }

  // the page: description, genres, rating, similar titles (optional: the picker works without it)
  app.jw.page(t.id).then(p => {
    if (!p) return;
    if (p.poster && !poster.querySelector('img')) poster.replaceWith(art(p.poster));
    const parts = show
      ? [p.date && `First aired ${date(p.date)}`, p.runtime && runtimeText(p.runtime), p.certification]
      : [date(p.date), p.runtime && runtimeText(p.runtime), p.certification];
    facts.replaceChildren(p.score != null ? rating(p.score, p.scoreSource) : null,
      el('span', {}, parts.filter(Boolean).join(' · ')));
    facts.hidden = false;
    genres.replaceChildren(...p.genres.slice(0, 5).map(g => el('span', { class: 'tag' }, g)));
    genres.hidden = !p.genres.length;
    if (p.synopsis) {
      const text = el('button', { class: 'about', title: 'Show all' }, p.synopsis);
      text.addEventListener('click', () => text.classList.toggle('open'));
      about.replaceChildren(text);
    }
    if (p.similar.length) {
      more.replaceChildren(label('More like this'), el('div', { class: 'rail-row' },
        p.similar.slice(0, 20).map(s => posterCard(s.title, kind(s.objectType), s.poster, () => app.openTitle(s)))));
      more.hidden = false;
    }
  }).catch(() => {});

  if (show) loadSeasons(); else loadMovie();

  async function loadMovie() {
    try {
      movieOffers = await app.jw.offers(t.id);
    } catch (e) {
      movieError = `Couldn't look it up: ${e.message}`;
      movieOffers = [];
    }
    refreshMovieDialog();
  }

  function openMovieServices() {
    if (dialog) dialog.close();
    dialog = serviceDialog(t.title, 'Movie');
    refreshMovieDialog();
  }

  function refreshMovieDialog() {
    if (!dialog || !dialog.open || show) return;
    if (movieOffers == null) dialog.hint('Finding where to watch…');
    else if (movieError) dialog.hint(movieError, true);
    else fill(dialog, t.title, 'Movie', movieOffers);
  }

  async function loadSeasons() {
    try {
      const seasons = await app.jw.seasons(t.id);
      if (!seasons.length) {
        showHint(`No seasons found for "${t.title}".`);
        return;
      }
      const buttons = seasons.map(s => {
        const b = listButton(seasonName(s.number), null, () => chooseSeason(s, b));
        return b;
      });
      seasonList.replaceChildren(...buttons);
      firstSeason = buttons[0];
      // season 1 first (Specials are season 0)
      const first = seasons.findIndex(s => s.number > 0);
      chooseSeason(seasons[Math.max(0, first)], buttons[Math.max(0, first)], false);
    } catch (e) {
      showHint(`Couldn't look it up: ${e.message}`, true);
    }
  }

  async function chooseSeason(s, button, focus = true) {
    const my = ++gen;
    for (const b of seasonList.children) b.classList.toggle('on', b === button);
    episodeList.replaceChildren();
    showHint(`Loading ${seasonName(s.number).toLowerCase()}…`);
    try {
      const episodes = await app.jw.episodes(s.id);
      if (my !== gen) return;
      seasonEpisodes = episodes;
      if (!episodes.length) {
        showHint(`No episodes for ${seasonName(s.number).toLowerCase()}.`);
        return;
      }
      hint.hidden = true;
      const buttons = episodes.map(e => {
        const b = listButton(`Episode ${e.number}`, e.title, () => chooseEpisode(s, e, b));
        return b;
      });
      episodeList.replaceChildren(...buttons);
      if (focus) buttons[0].focus();
    } catch (e) {
      if (my === gen) showHint(`Couldn't look it up: ${e.message}`, true);
    }
  }

  function chooseEpisode(s, e, button) {
    for (const b of episodeList.children) b.classList.toggle('on', b === button);
    const sub = `S${s.number} · E${e.number}` + (e.title ? ` · ${e.title}` : '');
    if (dialog) dialog.close();
    dialog = serviceDialog(t.title, sub, button);
    // a link the next (or previous) episode shares is the show's page, not this episode (Pluto TV for some shows)
    const other = seasonEpisodes.find(o => o.number === e.number + 1) || seasonEpisodes.find(o => o.number === e.number - 1);
    const showLevel = other ? sharedLinks(e.offers, other.offers) : new Set();
    fill(dialog, t.title, sub, e.offers, showLevel, { season: s.number, episode: e.number });
  }

  // the episode's show and numbers on HBO Max / Discovery+ (wbdEpisode), for Kodi
  let wbd = null;

  function fill(d, title, sub, offers, showLevel = new Set(), episode = null) {
    // a Discovery+ movie page becomes its play link when HBO Max has the title
    offers = discoveryPlayLinks(offers);
    wbd = (offers.find(o => o.wbd) || {}).wbd || null;
    if (!offers.length) {
      d.setServices([]);
      d.hint(`No streaming service has this in ${app.country}.`);
      return;
    }
    if (app.joined) d.hideHint();
    else d.hint('Join a room first (in the rail), then pick a service here.', true);
    d.setServices(offers.map(o => {
      const note = showLevel.has(o.link) ? "Opens the show's page: pick the episode."
        : o.link.includes('discoveryplus.com/movie/') ? "Opens the movie's page: press Play."
        : serviceOf(o.link) ? 'Opens on TVs and phones, and in a tab here.'
        : "Opens on TVs and phones. This site can't be synced in a browser.";
      return serviceCard(serviceName(o), note, o.icon, () => start(d, title, sub, o, episode));
    }));
    // one service: nothing to choose, start it
    const sole = new Set(offers.map(o => o.link)).size === 1 ? offers[0] : null;
    if (sole && !showLevel.has(sole.link) && app.joined) {
      d.hint(`Starting on ${serviceName(sole)}…`);
      start(d, title, sub, sole, episode);
    }
  }

  let starting = false;
  async function start(d, title, sub, o, episode) {
    if (!app.joined) {
      d.hint('Join a room first (in the rail), then pick a service here.', true);
      return;
    }
    if (starting) return;
    starting = true;
    let link = o.link;
    if (episode && isHboMax(link)) {
      // JustWatch's HBO Max link can be the "(with ASL)" edition: use the regular one
      d.hint('Checking HBO Max…');
      link = await regularEpisode(link, t.title, episode.season, episode.episode);
    }
    const label = show ? `${title} · ${sub}` : title;
    try {
      // the title's identity, so Kodi members play their library copy
      const identity = show
        // the show's ids and premiere year tell same-named shows apart (One Piece, 1999 and 2023)
        ? { type: 'episode', show: t.title, season: episode && episode.season, episode: episode && episode.episode,
          year: t.year || null, ids: Object.fromEntries(
            [['show_tmdb', t.tmdbId], ['show_imdb', t.imdbId], ['wbd', wbd]].filter(([, v]) => v)) }
        : { type: 'movie', title: t.title, year: t.year || null, ids: Object.fromEntries(
          [['tmdb', t.tmdbId], ['imdb', t.imdbId]].filter(([, v]) => v)) };
      await app.start(link, label, { title, sub, service: serviceName(o), image: t.poster }, identity);
      d.close();
    } catch (e) {
      d.hint(`Couldn't start it: ${e.message}`, true);
    }
    starting = false;
  }

  return { view, defaultFocus: () => watch || firstSeason, close: () => dialog && dialog.close() };
}

/** "Start for everyone on": the services in a dialog centered over the page. */
function serviceDialog(title, sub, returnFocus = document.activeElement) {
  const hint = el('div', { class: 'hint', hidden: true });
  const list = el('div', { class: 'services' });
  const box = el('div', { class: 'dialog', role: 'dialog', 'aria-modal': 'true', 'aria-label': `Start for everyone on: ${title}` },
    label('Start for everyone on'), el('h2', {}, title), sub ? el('div', { class: 'sub' }, sub) : null, hint, list);
  const scrim = el('div', { class: 'scrim' }, box);
  scrim.addEventListener('click', e => { if (e.target === scrim) d.close(); });
  const onKey = e => { if (e.key === 'Escape') d.close(); };
  document.addEventListener('keydown', onKey);
  document.body.append(scrim);
  const d = {
    open: true,
    hint(text, warn = false) {
      hint.textContent = text;
      hint.classList.toggle('warn', warn);
      hint.hidden = false;
    },
    hideHint() { hint.hidden = true; },
    setServices(cards) {
      list.replaceChildren(...cards);
      if (cards[0]) cards[0].focus();
    },
    close() {
      if (!d.open) return;
      d.open = false;
      document.removeEventListener('keydown', onKey);
      scrim.remove();
      if (returnFocus && returnFocus.isConnected) returnFocus.focus();
    },
  };
  return d;
}

export { toast };
