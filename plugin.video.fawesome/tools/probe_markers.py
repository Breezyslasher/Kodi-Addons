#!/usr/bin/env python3
"""Does Fawesome say where intros and credits are? (Skip Buttons step 1.)

Usage:
    python3 probe_markers.py

Title records carry intro_st / intro_et / endcredit_st / endcredit_et (and
shorts_*, ad_breaks). This samples episodes from several shows and a batch of
movies -- both from feed listings and from the detail call -- and reports how
often each field is filled, sample values next to the runtime (to work out the
units), and the web player's own code that reads them (skip intro / up next),
so the add-on uses them the way the website does. Read-only; prints only.
"""

import json
import os
import re
import sys
import urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))
import api  # noqa: E402

FIELDS = ("intro_st", "intro_et", "endcredit_st", "endcredit_et",
          "shorts_st", "shorts_et", "recap_st", "recap_et", "ad_breaks")
a = api.FawesomeApi("markers-probe-0000", log=lambda m: None)


def filled(v):
    return v not in (None, "", "None", "null", "0", 0, "0.0", [], {})


def marker_keys(it):
    return sorted(k for k in it if re.search(r"intro|credit|recap|skip|outro|cue|marker|shorts|next", k, re.I))


def collect():
    eps, movies = [], []
    shows = []
    for parent in ("Shows", "Movies"):
        for row in a.rows(parent=parent)[:8]:
            items, _ = a.feed(row["url"]) if "shows.php" not in row["url"] else ([], False)
            for it in items:
                k = api.kind(it)
                if k == "video" and not it.get("episode") or (k == "video" and str(it.get("episode")) in ("None", "0", "")):
                    movies.append(it)
                elif k == "folder" and "shows.php" in str(it.get("url")):
                    shows.append(it)
                elif k == "video":
                    eps.append(it)
    for term in ("love", "looney", "cops", "tarzan", "bbc", "crime", "western"):
        items, _ = a.search(term)
        shows += [it for it in items if api.kind(it) == "folder" and "shows.php" in str(it.get("url"))]
    # Shows rows hold collections (lists of shows) as well as shows; walk
    # through collections until real series (with seasons) turn up.
    print("[debug] shows queued from rows+search: %d; first: %s" % (
        len(shows), [(x.get("title"), str(x.get("url"))[:60]) for x in shows[:3]]))
    seen, queue, series_done = set(), list(shows), 0
    while queue and series_done < 8 and len(seen) < 80:
        sh = queue.pop(0)
        if sh["url"] in seen:
            continue
        seen.add(sh["url"])
        _, seasons, items = a.series(sh["url"])
        print("[debug] series %-30s seasons=%d items=%d" % (str(sh.get("title"))[:30], len(seasons), len(items)))
        if not seasons:
            queue += [it for it in items if api.kind(it) == "folder" and "shows.php" in str(it.get("url"))]
            eps += [it for it in items if api.kind(it) == "video" and str(it.get("episode")) not in ("None", "0", "")]
            continue
        real = False
        for s in seasons[:3]:
            got, _ = a.feed(s.get("feed") or s.get("url"))
            vids = [g for g in got if api.kind(g) == "video" and str(g.get("episode")) not in ("None", "0", "")]
            print("[debug]   season %-26s items=%d episodes=%d" % (str(s.get("title"))[:26], len(got), len(vids)))
            if vids:
                real = True
                eps += vids
            else:   # a collection's sub-list: its items are shows -> walk them
                queue += [g for g in got if api.kind(g) == "folder" and "shows.php" in str(g.get("url"))]
        if real:
            series_done += 1
    return eps, movies


def report(label, items):
    print("\n== %s: %d sampled ==" % (label, len(items)))
    if not items:
        return
    print("  marker-ish keys present:", marker_keys(items[0]))
    for f in FIELDS:
        n = sum(1 for it in items if filled(it.get(f)))
        print("  %-13s filled in %3d / %d" % (f, n, len(items)))
    shown = 0
    for it in items:
        if any(filled(it.get(f)) for f in FIELDS[:4]) and shown < 8:
            shown += 1
            print("   %-45s runtime=%-6s intro=%s-%s credits=%s-%s ad_breaks=%s" % (
                str(it.get("title"))[:45], it.get("runtime"), it.get("intro_st"), it.get("intro_et"),
                it.get("endcredit_st"), it.get("endcredit_et"), str(it.get("ad_breaks"))[:40]))


def episode_links(items):
    print("\n== episode -> show link fields ==")
    for it in items[:6]:
        print("  %-40s S%sE%s show_id=%s actionkey=%s bookmark_feed=%s series=%s" % (
            str(it.get("title"))[:40], it.get("season"), it.get("episode"), it.get("show_id"),
            it.get("actionkey"), str(it.get("bookmark_feed"))[:70], it.get("series_name")))


def detail_check(items, label):
    print("\n== detail call vs listing (%s) ==" % label)
    for it in items[:4]:
        d = a.detail(it["node_id"])
        print("  %s %s" % (it["node_id"], str(it.get("title"))[:40]))
        print("     list  :", {f: it.get(f) for f in FIELDS[:4]})
        print("     detail:", {f: d.get(f) for f in FIELDS[:4]},
              "| show link:", {k: str(d.get(k))[:80] for k in ("show_id", "series_id", "parent", "bookmark_feed",
                                                              "deeplink_url", "path_alias", "catId", "subCatId")
                               if filled(d.get(k))})


def player_code():
    print("\n== web player code using the markers ==")
    r = a.s.get(api.SITE, timeout=20)
    m = re.search(r'src="([^"]*iptv-main\.js[^"]*)"', r.text)
    bundle = a.s.get(urllib.parse.urljoin(api.SITE, m.group(1)), timeout=30).text
    for needle in ("checkIntroSkipVisibility:function", "endcredit_st)", "_triggeredBy=",
                   "_fetchNextEpisodeFromAPI:function", "_getShowId:function", "_searchSeasons:function",
                   "earlyOverlay"):
        hits = [i.start() for i in re.finditer(re.escape(needle), bundle)][:2]
        for i in hits:
            print("  [%s] %s" % (needle, " ".join(bundle[max(0, i - 120):i + 900].split())))
        if not hits:
            print("  [%s] not found" % needle)


def main():
    eps, movies = collect()
    movies = movies[:0] if eps else movies   # this pass is about episodes
    report("episodes (from season feeds)", eps)
    report("movies (from rows)", movies)
    episode_links(eps)
    detail_check(eps, "episodes")
    detail_check(movies, "movies")
    player_code()
    print("\nDONE")


if __name__ == "__main__":
    main()
