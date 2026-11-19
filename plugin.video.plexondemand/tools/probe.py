#!/usr/bin/env python3
"""Probe Plex's free on-demand (PLEXAVOD) provider and print response *shapes*.

Run from GitHub Actions (plex hosts are unreachable from the dev container).
Standard library only. Prints structure (keys, types, first list items, short
string values) so endpoints can be confirmed without dumping whole bodies.
Tokens are masked in the Actions log and replaced with *** in output.
"""
import json
import os
import sys
import uuid
import urllib.error
import urllib.parse
import urllib.request

CLIENT_ID = str(uuid.uuid4())
SECRETS = []
HEADERS = {
    "Accept": "application/json",
    "X-Plex-Client-Identifier": CLIENT_ID,
    "X-Plex-Product": "Plex Web",
    "X-Plex-Version": "4.145.1",
    "X-Plex-Platform": "Chrome",
    "X-Plex-Platform-Version": "131.0",
    "X-Plex-Device": "Linux",
    "X-Plex-Device-Name": "Kodi probe",
    "X-Plex-Language": "en",
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36",
    "Origin": "https://app.plex.tv",
    "Referer": "https://app.plex.tv/",
}


def mask(s):
    for t in SECRETS:
        if t:
            s = s.replace(t, "***")
    return s


def secret(v):
    if v:
        SECRETS.append(v)
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print("::add-mask::" + v)
    return v


def req(method, url, params=None, headers=None, data=None, raw=False):
    if params:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode(params, doseq=True)
    h = dict(HEADERS)
    h.update(headers or {})
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        h["Content-Type"] = "application/x-www-form-urlencoded"
    r = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=25) as resp:
            status, ctype, text = resp.status, resp.headers.get("Content-Type", ""), resp.read()
            final = resp.geturl()
    except urllib.error.HTTPError as e:
        status, ctype, text, final = e.code, e.headers.get("Content-Type", ""), e.read(), url
    except Exception as e:  # noqa
        return None, "", str(e).encode(), url
    return status, ctype, text, final


def shape(v, depth=0, maxdepth=6, key=""):
    pad = "  " * depth
    out = []
    if isinstance(v, dict):
        for k, x in list(v.items())[:60]:
            if isinstance(x, (dict, list)):
                n = f" [{len(x)}]" if isinstance(x, list) else ""
                out.append(f"{pad}{k}:{n}")
                if depth < maxdepth:
                    out += shape(x, depth + 1, maxdepth, k)
            else:
                s = repr(x)
                out.append(f"{pad}{k}: {s[:160]}")
    elif isinstance(v, list):
        for i, x in enumerate(v[:2]):
            out.append(f"{pad}- [{i}]")
            out += shape(x, depth + 1, maxdepth)
    return out


def show(label, method, url, depth=6, **kw):
    status, ctype, text, final = req(method, url, **kw)
    print(f"\n===== {label}\n{method} {mask(url)} -> {status} {ctype}")
    if final != url:
        print("final url:", mask(final))
    data = None
    if text and "json" in ctype:
        try:
            data = json.loads(text)
            _mask_tokens(data)
            for line in shape(data, maxdepth=depth)[:220]:
                print(mask(line))
        except Exception as e:
            print("json error", e)
    else:
        print(mask(text[:1500].decode("utf-8", "replace")))
    return status, data


def _mask_tokens(v):
    if isinstance(v, dict):
        for k, x in v.items():
            if k in ("authToken", "token", "accessToken") and isinstance(x, str):
                secret(x)
            else:
                _mask_tokens(x)
    elif isinstance(v, list):
        for x in v:
            _mask_tokens(x)


def raw(label, obj, limit=4000):
    print(f"\n----- raw {label}")
    print(mask(json.dumps(obj, indent=1)[:limit]))


def mc(d):
    return (d or {}).get("MediaContainer") or d or {}


def main():
    term = os.environ.get("PROBE_QUERY", "western")
    # ---- auth
    _, anon = show("anonymous user token", "POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret((anon or {}).get("authToken"))
    _, pin = show("PIN create", "POST", "https://plex.tv/api/v2/pins", params={"strong": "true"})
    _, pin4 = show("PIN create (4-char)", "POST", "https://plex.tv/api/v2/pins")
    if pin4 and pin4.get("id"):
        show("PIN poll", "GET", f"https://plex.tv/api/v2/pins/{pin4['id']}")
    if not tok:
        print("NO ANON TOKEN; stopping")
        return 1
    T = {"X-Plex-Token": tok}
    show("user (anon)", "GET", "https://plex.tv/api/v2/user", headers=T)
    show("geoip", "GET", "https://plex.tv/api/v2/geoip", headers=T)
    show("features", "GET", "https://plex.tv/api/v2/features", headers=T, )
    # ---- providers
    _, prov = show("media providers (plex.tv)", "GET", "https://plex.tv/media/providers", headers=T)
    show("clients providers", "GET", "https://clients.plex.tv/api/v2/user/providers", headers=T)
    show("resources", "GET", "https://clients.plex.tv/api/v2/resources",
         params={"includeHttps": 1, "includeRelay": 1}, headers=T)
    base = "https://vod.provider.plex.tv"
    _, root = show("VOD provider root", "GET", base + "/", headers=T)
    show("discover provider root", "GET", "https://discover.provider.plex.tv/", headers=T)
    # Collect feature keys from VOD provider root
    feats = []
    mp = mc(root).get("MediaProvider") or []
    mp = mp if isinstance(mp, list) else [mp]
    for p in mp:
        for f in p.get("Feature", []) or []:
            feats.append(f)
    print("\n===== VOD feature summary")
    for f in feats:
        print(f.get("type"), f.get("key"), [ (d.get("title"), d.get("key"), d.get("hubKey")) for d in (f.get("Directory") or [])][:20])
    for f in feats:
        if f.get("type") in ("content", "playqueue", "hero", "timeline"):
            raw("feature " + f.get("type"), f, 6000)
    # ---- hubs with items
    _, hubs = show("hubs (count=3)", "GET", base + "/hubs", headers=T,
                   params={"includeMeta": 1, "count": 3, "excludeFields": "summary"})
    hub_list = mc(hubs).get("Hub") or []
    print("\n===== hub list")
    for h in hub_list:
        print(h.get("hubIdentifier"), "|", h.get("title"), "|", h.get("key"), "| size", h.get("size"),
              "placeholder", h.get("placeholder"), "style", h.get("style"))
    first_item = first_show = None
    for h in hub_list[:4]:
        _, d = show(f"hub items {h.get('key')}", "GET", base + h["key"], headers=T, params={"count": 4})
        for m in mc(d).get("Metadata") or []:
            if m.get("type") == "movie" and not first_item:
                first_item = m
            if m.get("type") == "show" and not first_show:
                first_show = m
    for h in hub_list:
        if first_item and first_show:
            break
        st, ct, txt, _ = req("GET", base + h["key"], headers=T, params={"count": 10})
        try:
            for m in mc(json.loads(txt)).get("Metadata") or []:
                if m.get("type") == "movie" and not first_item:
                    first_item = m
                if m.get("type") == "show" and not first_show:
                    first_show = m
        except Exception:
            pass
    # ---- sections / genres
    for path in ("/library/sections", "/library/sections/movies", "/library/sections/movies/all",
                 "/library/sections/movies/categories", "/library/sections/movies/genre",
                 "/library/sections/tv", "/library/sections/shows/all", "/library/categories"):
        show(f"section {path}", "GET", base + path, headers=T,
             params={"X-Plex-Container-Size": 3, "X-Plex-Container-Start": 0})
    # ---- metadata + playback
    for label, m in (("movie", first_item), ("show", first_show)):
        if not m:
            print(f"\nno {label} found in hubs")
            continue
        raw(f"{label} hub item", m, 3000)
        rk = m.get("ratingKey")
        _, md = show(f"{label} metadata {rk}", "GET", base + f"/library/metadata/{rk}", headers=T)
        if label == "show":
            _, ch = show("show children (seasons)", "GET", base + f"/library/metadata/{rk}/children", headers=T)
            s = (mc(ch).get("Metadata") or [{}])[0]
            if s.get("ratingKey"):
                _, eps = show("season children (episodes)", "GET",
                              base + f"/library/metadata/{s['ratingKey']}/children", headers=T,
                              params={"X-Plex-Container-Size": 2})
                e = (mc(eps).get("Metadata") or [{}])[0]
                if e.get("ratingKey"):
                    rk = e["ratingKey"]
                    _, md = show("episode metadata", "GET", base + f"/library/metadata/{rk}", headers=T)
        item = (mc(md).get("Metadata") or [{}])[0]
        raw(f"{label} Media", item.get("Media"), 4000)
        # playQueue
        uri = f"provider://tv.plex.provider.vod/library/metadata/{rk}"
        _, pq = show(f"{label} playQueue POST", "POST", base + "/playQueues", headers=T,
                     params={"uri": uri, "type": "video", "continuous": 0, "includeChapters": 1})
        pqi = (mc(pq).get("Metadata") or [{}])[0]
        if pqi:
            raw(f"{label} playQueue item Media", pqi.get("Media"), 4000)
        medias = (pqi.get("Media") if pqi else None) or item.get("Media") or []
        for media in medias[:2]:
            for part in media.get("Part", [])[:1]:
                pk = part.get("key")
                if not pk:
                    continue
                url = pk if pk.startswith("http") else base + pk
                st, ct, txt, fin = req("GET", url, headers=T)
                print(f"\n===== stream part {label}\nGET {mask(url)} -> {st} {ct}\nfinal: {mask(fin)}")
                body = txt[:3000].decode("utf-8", "replace")
                print(mask(body))
                lines = [l for l in body.splitlines() if l and not l.startswith("#")]
                if lines and "#EXTM3U" in body:
                    v = urllib.parse.urljoin(fin, lines[0])
                    st, ct, txt, fin2 = req("GET", v, headers=T)
                    print(f"\n----- variant -> {st}\n" + mask(txt[:3000].decode("utf-8", "replace")))
    # ---- search variants
    for prov in ("discover,plexAVOD", "discover,PLEXAVOD,PLEXTVOD", "plexAVOD"):
        _, sr = show(f"search providers={prov}", "GET", "https://discover.provider.plex.tv/library/search",
                     headers=T, depth=3, params={"query": term, "searchTypes": "movies,tv",
                                                 "searchProviders": prov, "includeMetadata": 1, "limit": 3})
        for r in mc(sr).get("SearchResults") or []:
            print("  section:", r.get("id"), r.get("title"), r.get("size"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
