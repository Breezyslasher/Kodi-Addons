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


def round3():
    """Compact checks: hub list, catalogue listing, token needs, VMAP, DRM ratio."""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    _, hubs = req_json("GET", base + "/hubs", headers=T)
    print("===== hub list")
    for h in mc(hubs).get("Hub") or []:
        print(" ", h.get("hubIdentifier"), "|", h.get("title"), "|", h.get("key"), "|", h.get("style"),
              "| types", h.get("availabilityTypes"))
    for path, params in (("/library/sections/movies/all", {"type": 1}),
                         ("/library/sections/movies/all", {"type": 2}),
                         ("/library/sections/movies/genre", {"type": 2}),
                         ("/library/sections/movies/all", {"type": 1, "sort": "titleSort"})):
        params = dict(params, **{"X-Plex-Container-Size": 3, "X-Plex-Container-Start": 0})
        st, d = req_json("GET", base + path, headers=T, params=params)
        c = mc(d)
        print(f"\n===== {path} {params} -> {st} totalSize={c.get('totalSize')} size={c.get('size')}")
        print("  container keys:", sorted(k for k in c if not isinstance(c[k], list)))
        for m in (c.get("Metadata") or c.get("Directory") or [])[:3]:
            print("  ", m.get("type"), m.get("title"), m.get("ratingKey") or m.get("key"))
    # DRM ratio + item for playback tests
    drm = clear = 0
    sample = None
    for h in (mc(hubs).get("Hub") or [])[:25]:
        st, d = req_json("GET", base + h["key"], headers=T, params={"count": 20})
        for m in mc(d).get("Metadata") or []:
            for media in m.get("Media") or []:
                if media.get("protocol") != "hls":
                    continue
                if media.get("drm"):
                    drm += 1
                else:
                    clear += 1
                    if not sample and m.get("type") == "movie":
                        sample = m
    print(f"\n===== DRM ratio over hub items: clear={clear} drm={drm}")
    if sample:
        part = [mm for mm in sample["Media"] if mm.get("protocol") == "hls"][0]["Part"][0]["key"]
        url = base + part
        for label, h in (("with token", T), ("no token", {}), ("bare UA only", None)):
            hdrs = h if h is not None else {}
            if h is None:
                r = urllib.request.Request(url, headers={"User-Agent": "Kodi/21"})
                try:
                    with urllib.request.urlopen(r, timeout=20) as resp:
                        st, body = resp.status, resp.read()
                except urllib.error.HTTPError as e:
                    st, body = e.code, e.read()
            else:
                st, _, body, _ = req("GET", url, headers=hdrs)
            print(f"\n===== master {label} -> {st} len={len(body)}")
        st, _, body, fin = req("GET", url, headers={})
        text = body.decode("utf-8", "replace")
        variants = [l for l in text.splitlines() if l and not l.startswith("#")]
        if variants:
            v = urllib.parse.urljoin(fin, variants[0])
            r = urllib.request.Request(v, headers={"User-Agent": "Kodi/21"})
            with urllib.request.urlopen(r, timeout=20) as resp:
                vt = resp.read().decode()
            print("  variant bare ->", resp.status, "host", urllib.parse.urlparse(v).netloc,
                  "query?", bool(urllib.parse.urlparse(v).query))
            seg = [l for l in vt.splitlines() if l and not l.startswith("#")][0]
            sv = urllib.parse.urljoin(v, seg)
            r = urllib.request.Request(sv, headers={"User-Agent": "Kodi/21", "Range": "bytes=0-99"})
            try:
                with urllib.request.urlopen(r, timeout=20) as resp:
                    print("  segment bare ->", resp.status, resp.headers.get("Content-Type"))
            except urllib.error.HTTPError as e:
                print("  segment bare ->", e.code)
        sub = [l for l in text.splitlines() if "TYPE=SUBTITLES" in l]
        print("  subtitle lines:", len(sub))
        # VMAP
        ad = (sample.get("Ad") or [{}])[0].get("url")
        if ad:
            st, ct, body, _ = req("GET", ad, headers=dict(T, Accept="*/*"))
            print(f"\n===== VMAP -> {st} {ct} len={len(body)}")
            print(mask(body[:2500].decode("utf-8", "replace")))
        # timeline probe (report only)
        rk = sample["ratingKey"]
        st, ct, body, _ = req("POST", base + "/timeline", headers=T, params={
            "ratingKey": rk, "key": f"/library/metadata/{rk}", "state": "playing", "time": 1000,
            "duration": sample.get("duration", 0)})
        print(f"\n===== timeline POST -> {st} {mask(body[:300].decode('utf-8', 'replace'))}")
        st, ct, body, _ = req("PUT", base + "/timeline", headers=T, params={
            "ratingKey": rk, "key": f"/library/metadata/{rk}", "state": "playing", "time": 1000,
            "duration": sample.get("duration", 0)})
        print(f"===== timeline PUT -> {st} {mask(body[:300].decode('utf-8', 'replace'))}")
    # search: free section items
    st, sr = req_json("GET", "https://discover.provider.plex.tv/library/search", headers=T, params={
        "query": "star", "searchTypes": "movies,tv", "searchProviders": "discover,plexAVOD",
        "includeMetadata": 1, "limit": 5})
    for sec in mc(sr).get("SearchResults") or []:
        print(f"\n===== search section {sec.get('id')} {sec.get('title')}")
        for r in (sec.get("SearchResult") or [])[:5]:
            m = r.get("Metadata") or {}
            print("  ", m.get("type"), m.get("title"), m.get("ratingKey"), m.get("source"),
                  "Media" in m, m.get("key"))
    return 0


def round4():
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    _, hubs = req_json("GET", base + "/hubs", headers=T)
    clear = drm = None
    for h in (mc(hubs).get("Hub") or [])[:25]:
        _, d = req_json("GET", base + h["key"], headers=T, params={"count": 20})
        for m in mc(d).get("Metadata") or []:
            ms = m.get("Media") or []
            if any(x.get("drm") for x in ms):
                drm = drm or m
            elif ms:
                clear = clear or m
        if clear and drm:
            break
    # genre listing + paging
    _, g = req_json("GET", base + "/library/sections/movies/genre", headers=T, params={"type": 1})
    gk = (mc(g).get("Directory") or [{}])[0].get("key")
    for start in (0, 3):
        st, d = req_json("GET", base + gk, headers=T,
                         params={"X-Plex-Container-Start": start, "X-Plex-Container-Size": 3})
        c = mc(d)
        print(f"===== genre {gk} start={start} -> {st} total={c.get('totalSize')} size={c.get('size')} offset={c.get('offset')}",
              [m.get("title") for m in c.get("Metadata") or []])
    st, d = req_json("GET", base + gk, headers=T, params={"sort": "titleSort", "X-Plex-Container-Size": 3})
    print("  sorted titleSort ->", st, [m.get("title") for m in mc(d).get("Metadata") or []])
    st, d = req_json("GET", base + "/hubs/sections/movies/trending", headers=T,
                     params={"X-Plex-Container-Start": 20, "X-Plex-Container-Size": 20})
    c = mc(d)
    print("===== hub paging start=20 ->", st, "total", c.get("totalSize"), "size", c.get("size"), "offset", c.get("offset"))
    for label, m in (("CLEAR", clear), ("DRM", drm)):
        if not m:
            print("no", label); continue
        print(f"\n######## {label}: {m.get('title')} {m.get('type')} {m.get('ratingKey')}")
        _, md = req_json("GET", base + f"/library/metadata/{m['ratingKey']}", headers=T)
        item = (mc(md).get("Metadata") or [m])[0]
        for media in item.get("Media") or []:
            print("  Media:", json.dumps({k: v for k, v in media.items() if k != "Part"})[:600])
            for part in media.get("Part") or []:
                print("   Part:", mask(json.dumps({k: v for k, v in part.items() if k != "Stream"})[:800]))
                url = base + part["key"]
                st, ct, body, fin = req("GET", url, headers=T)
                text = body.decode("utf-8", "replace")
                print(f"   GET part -> {st} {ct} len={len(text)}")
                for line in text.splitlines():
                    if "EXT-X-KEY" in line or "SESSION-KEY" in line or "license" in line.lower():
                        print("    ", mask(line[:600]))
                if "<MPD" in text:
                    import re as _re
                    for cp in _re.findall(r"<ContentProtection.*?(?:/>|</ContentProtection>)", text, _re.S)[:4]:
                        print("    CP:", mask(cp[:700]))
                    for k in _re.findall(r'[\w:]*(?:licen[cs]e|laurl|Laurl)[^>]{0,300}', text)[:4]:
                        print("    LIC:", mask(k[:400]))
                    bu = _re.findall(r"<BaseURL>(.*?)</BaseURL>", text)
                    print("    BaseURL:", bu[:1])
                    if bu:
                        im = _re.findall(r'initialization="([^"]+)"', text)
                        if im:
                            seg = urllib.parse.urljoin(bu[0], im[0])
                            r = urllib.request.Request(seg, headers={"User-Agent": "Kodi/21", "Range": "bytes=0-99"})
                            try:
                                with urllib.request.urlopen(r, timeout=20) as resp:
                                    print("    init seg bare ->", resp.status)
                            except urllib.error.HTTPError as e:
                                print("    init seg bare ->", e.code)
                elif "#EXTM3U" in text:
                    vs = [l for l in text.splitlines() if l and not l.startswith("#")]
                    if vs:
                        v = urllib.parse.urljoin(fin, vs[0])
                        r = urllib.request.Request(v, headers={"User-Agent": "Kodi/21"})
                        try:
                            with urllib.request.urlopen(r, timeout=20) as resp:
                                vt = resp.read().decode()
                            print("    variant bare -> 200", urllib.parse.urlparse(v).netloc)
                            for line in vt.splitlines():
                                if "EXT-X-KEY" in line or "EXT-X-MAP" in line:
                                    print("     ", mask(line[:400])); 
                            seg = [l for l in vt.splitlines() if l and not l.startswith("#")][0]
                            r = urllib.request.Request(urllib.parse.urljoin(v, seg),
                                                       headers={"User-Agent": "Kodi/21", "Range": "bytes=0-99"})
                            with urllib.request.urlopen(r, timeout=20) as resp:
                                print("    segment bare ->", resp.status)
                        except urllib.error.HTTPError as e:
                            print("    variant/segment bare ->", e.code)
        # playQueue for DRM: does it add license info?
        if label == "DRM":
            uri = f"provider://tv.plex.provider.vod/library/metadata/{m['ratingKey']}"
            _, pq = req_json("POST", base + "/playQueues", headers=T,
                             params={"uri": uri, "type": "video", "continuous": 0})
            pi = (mc(pq).get("Metadata") or [{}])[0]
            for media in pi.get("Media") or []:
                print("  PQ Media:", mask(json.dumps(media)[:1500]))
            for k in ("/library/parts/%s/license", "/drm/license/%s", "/library/metadata/%s/license"):
                pid = (item.get("Media") or [{}])[0].get("Part", [{}])[0].get("id")
                st, ct, body, _ = req("POST", base + (k % pid), headers=dict(T, **{"Content-Type": "application/octet-stream"}))
                print("  try", k % pid, "->", st, mask(body[:200].decode("utf-8", "replace")))
        ad = (item.get("Ad") or [{}])[0].get("url")
        if ad and label == "CLEAR":
            st, ct, body, _ = req("GET", ad, headers=dict(T, Accept="*/*"))
            print(f"\n===== VMAP -> {st} {ct} len={len(body)}")
            print(mask(body[:2000].decode("utf-8", "replace")))
            rk = m["ratingKey"]
            for meth in ("POST", "PUT", "GET"):
                st, ct, body, _ = req(meth, base + "/timeline", headers=T, params={
                    "ratingKey": rk, "key": f"/library/metadata/{rk}", "state": "stopped", "time": 60000,
                    "duration": item.get("duration", 0)})
                print(f"===== timeline {meth} -> {st} {mask(body[:200].decode('utf-8', 'replace'))}")
    st, sr = req_json("GET", "https://discover.provider.plex.tv/library/search", headers=T, params={
        "query": "star", "searchTypes": "movies,tv", "searchProviders": "discover,plexAVOD",
        "includeMetadata": 1, "limit": 5})
    for sec in mc(sr).get("SearchResults") or []:
        print(f"\n===== search section {sec.get('id')} {sec.get('title')}")
        for r in (sec.get("SearchResult") or [])[:4]:
            mm = r.get("Metadata") or {}
            print("  ", mm.get("type"), mm.get("title"), mm.get("ratingKey"), mm.get("source"), "Media" in mm)
    return 0


def round5():
    """What unlocks a DRM manifest (451)? Print error bodies across variants."""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    _, hubs = req_json("GET", base + "/hubs", headers=T)
    drm = None
    for h in (mc(hubs).get("Hub") or [])[:25]:
        _, d = req_json("GET", base + h["key"], headers=T, params={"count": 20})
        for m in mc(d).get("Metadata") or []:
            if any(x.get("drm") for x in m.get("Media") or []):
                drm = m
                break
        if drm:
            break
    print("DRM title:", drm.get("title"), drm.get("ratingKey"))
    pid = [x for x in drm["Media"] if x.get("protocol") == "dash"][0]["Part"][0]["id"]
    mpd = base + f"/library/parts/{pid}-dash.mpd"
    variants = [
        ("plain", {}, {}),
        ("hdr X-Plex-DRM=widevine", {"X-Plex-DRM": "widevine"}, {}),
        ("q X-Plex-DRM=widevine", {}, {"X-Plex-DRM": "widevine"}),
        ("q drm=widevine", {}, {"drm": "widevine"}),
        ("hdr Platform=Android", {"X-Plex-Platform": "Android", "X-Plex-Product": "Plex for Android (TV)",
                                  "X-Plex-Device": "SHIELD Android TV"}, {}),
        ("hdr Platform=Android + DRM", {"X-Plex-Platform": "Android", "X-Plex-Product": "Plex for Android (TV)",
                                        "X-Plex-Device": "SHIELD Android TV", "X-Plex-DRM": "widevine"}, {}),
        ("hdr Chrome UA + Client-Profile", {"X-Plex-Client-Profile-Extra": "add-drm-support(scheme=widevine)"}, {}),
        ("hdr X-Plex-Features", {"X-Plex-Features": "external-media,indirect-media,hub-style-list,drm"}, {}),
        ("hdr X-Plex-Session-Identifier", {"X-Plex-Session-Identifier": str(uuid.uuid4())}, {}),
    ]
    for label, h, q in variants:
        st, ct, body, _ = req("GET", mpd, headers=dict(T, **h), params=q or None)
        text = body.decode("utf-8", "replace")
        print(f"\n== {label} -> {st} {ct}")
        print("  ", mask(text[:400]))
        if st == 200:
            import re as _re
            for cp in _re.findall(r"<ContentProtection.*?(?:/>|</ContentProtection>)", text, _re.S)[:3]:
                print("   CP:", mask(cp[:500]))
            break
    # playQueue for the DRM title, full dump of the relevant item
    uri = f"provider://tv.plex.provider.vod/library/metadata/{drm['ratingKey']}"
    st, ct, body, _ = req("POST", base + "/playQueues", headers=T,
                          params={"uri": uri, "type": "video", "continuous": 0})
    print("\n== playQueue ->", st, mask(body[:300].decode("utf-8", "replace")))
    try:
        pq = json.loads(body)
        for it in mc(pq).get("Metadata") or []:
            if it.get("ratingKey") == drm["ratingKey"]:
                print("   PQ item Media:", mask(json.dumps(it.get("Media"))[:1500]))
    except Exception:
        pass
    # licence endpoint with a junk challenge
    st, ct, body, _ = req("POST", base + f"/library/parts/{pid}-dash/license", headers=dict(T, **{"Content-Type": "application/octet-stream"}))
    print("\n== license (empty body) ->", st, mask(body[:300].decode("utf-8", "replace")))
    r = urllib.request.Request(base + f"/library/parts/{pid}-dash/license", data=b"\x08\x04", method="POST",
                               headers=dict(HEADERS, **T, **{"Content-Type": "application/octet-stream"}))
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            print("== license (junk) ->", resp.status, resp.read()[:200])
    except urllib.error.HTTPError as e:
        print("== license (junk) ->", e.code, mask(e.read()[:300].decode("utf-8", "replace")))
    # Is DRM a region thing? check user features for drm-ish names
    _, u = req_json("GET", "https://plex.tv/api/v2/user", headers=T)
    feats = (u.get("subscription") or {}).get("features") or []
    print("\n== user features containing drm/avod/vod:", [f for f in feats if any(k in str(f).lower() for k in ("drm", "vod", "widevine", "fairplay"))])
    return 0


def req_json(method, url, **kw):
    st, ct, body, _ = req(method, url, **kw)
    try:
        d = json.loads(body)
        _mask_tokens(d)
        return st, d
    except Exception:
        return st, {}


if __name__ == "__main__":
    mode = os.environ.get("PROBE_MODE", "round5")
    sys.exit({"round3": round3, "round4": round4, "round5": round5}.get(mode, main)())
