#!/usr/bin/env python3
"""Does iFood.tv say where intros and credits are? (Skip Buttons.)

Usage:
    python3 probe_markers.py

Same Future Today records as Fawesome: intro_st / intro_et / endcredit_st /
endcredit_et in seconds, 0 = unset. Samples recipe clips from a few rows and
episodes from the "Movies & Shows" section, and reports how often each is
filled and what skip_markers() makes of them. Kept small (well under a
hundred requests) so it can run on every push without tripping a rate
limit. Read-only; prints only; never fails the job.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "lib"))
import api  # noqa: E402

FIELDS = ("intro_st", "intro_et", "endcredit_st", "endcredit_et")
a = api.FtApi("markers-probe-0000", log=lambda m: None)


def filled(v):
    return v not in (None, "", "None", "null", "0", 0, "0.0")


def report(label, items):
    print("\n== %s: %d sampled ==" % (label, len(items)))
    if not items:
        return
    for f in FIELDS:
        print("  %-13s filled in %3d / %d" % (f, sum(1 for it in items if filled(it.get(f))), len(items)))
    with_intro = sum(1 for it in items if "intro" in api.skip_markers(it))
    with_credits = sum(1 for it in items if "credits" in api.skip_markers(it))
    print("  skip_markers: intro on %d, credits on %d" % (with_intro, with_credits))
    shown = 0
    for it in items:
        if any(filled(it.get(f)) for f in FIELDS) and shown < 6:
            shown += 1
            print("   %-45s runtime=%-6s intro=%s-%s credits=%s-%s -> %s" % (
                str(it.get("title"))[:45], it.get("runtime"), it.get("intro_st"), it.get("intro_et"),
                it.get("endcredit_st"), it.get("endcredit_et"), api.skip_markers(it)))


def main():
    secs = a.sections()
    print("sections:", [s["name"] for s in secs])
    shows_sec = [s["name"] for s in secs if re.search(r"show|movie", s["name"], re.I)]
    recipe_sec = [s["name"] for s in secs if s["name"] not in shows_sec][:2]

    recipes = []
    for name in recipe_sec:
        for row in a.rows(parent=name)[:3]:
            if "shows.php" in row["url"]:
                continue
            items, _ = a.feed(row["url"])
            recipes += [i for i in items if api.kind(i) == "video"]
    report("recipe clips (%s)" % ", ".join(recipe_sec), recipes)

    eps, movies, shows = [], [], []
    for name in shows_sec:
        for row in a.rows(parent=name)[:6]:
            items, _ = ([], False) if "shows.php" in row["url"] else a.feed(row["url"])
            if "shows.php" in row["url"]:
                shows.append({"url": row["url"], "title": row["name"]})
            for it in items:
                if api.kind(it) == "folder" and "shows.php" in str(it.get("url")):
                    shows.append(it)
                elif api.kind(it) == "video":
                    (eps if api.is_episode(it) else movies).append(it)
    seen, series_done = set(), 0
    while shows and series_done < 5 and len(seen) < 20:
        sh = shows.pop(0)
        if sh["url"] in seen:
            continue
        seen.add(sh["url"])
        _, seasons, items = a.series(sh["url"])
        if not seasons:
            shows += [i for i in items if api.kind(i) == "folder" and "shows.php" in str(i.get("url"))]
            continue
        got, _ = a.feed(seasons[0].get("feed") or seasons[0].get("url"))
        vids = [g for g in got if api.kind(g) == "video"]
        print("  series %-34s seasons=%d first-season episodes=%d" % (
            str(sh.get("title"))[:34], len(seasons), len(vids)))
        if vids:
            series_done += 1
            eps += vids
    report("episodes (%s)" % ", ".join(shows_sec), eps)
    report("movies (%s)" % ", ".join(shows_sec), movies)

    ep = next((e for e in eps if api.is_episode(e)), None)
    if ep:
        d = a.detail(ep["node_id"])
        print("\n== next episode ==\n  %s S%sE%s show_id=%s -> next %s" % (
            str(d.get("title"))[:40], d.get("season"), d.get("episode"), d.get("show_id"),
            a.next_episode(d)))
    print("\nDONE")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001  research only
        print("probe error: %r" % (exc,))
