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


def round6():
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    st, ct, body, _ = req("GET", base + "/library/parts/62d04d8b700e44e586e47e68-dash.mpd", headers=T)
    print("== 451 title ->", st, mask(body[:300].decode("utf-8", "replace")))
    _, md = req_json("GET", base + "/library/metadata/5d9f356adfd12800208a1312", headers=T)
    it = (mc(md).get("Metadata") or [{}])[0]
    print("   451 title fields:", {k: it.get(k) for k in ("availabilityId", "expiresAt", "contentRating", "attribution")})
    lic = base + "/library/parts/62be188e5e5872e950deb0db-dash/license"
    challenge = b"\x08\x04"
    tries = [
        ("q drm=widevine", {"drm": "widevine"}, {}),
        ("q X-Plex-DRM=widevine", {"X-Plex-DRM": "widevine"}, {}),
        ("hdr X-Plex-DRM=widevine", {}, {"X-Plex-DRM": "widevine"}),
        ("q type=widevine", {"type": "widevine"}, {}),
        ("q drmType=widevine", {"drmType": "widevine"}, {}),
        ("q scheme=widevine", {"scheme": "widevine"}, {}),
        ("q drm=wv", {"drm": "wv"}, {}),
        ("q X-Plex-Drm=widevine", {"X-Plex-Drm": "widevine"}, {}),
        ("hdr X-Plex-Client-Platform-DRM", {}, {"X-Plex-DRM-Support": "widevine"}),
    ]
    for label, q, h in tries:
        url = lic + "?" + urllib.parse.urlencode(q) if q else lic
        r = urllib.request.Request(url, data=challenge, method="POST",
                                   headers=dict(HEADERS, **T, **h, **{"Content-Type": "application/octet-stream"}))
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                print(f"== {label} -> {resp.status} {resp.read()[:200]!r}")
        except urllib.error.HTTPError as e:
            print(f"== {label} -> {e.code} {mask(e.read()[:250].decode('utf-8', 'replace'))}")
    return 0


def logo():
    """Fetch Plex's own logo assets and print them base64 (for resources/)."""
    import base64
    urls = [
        "https://provider-static.plex.tv/icons/vod-560.svg",
        "https://upload.wikimedia.org/wikipedia/commons/7/7b/Plex_logo_2022.svg",
        "https://commons.wikimedia.org/wiki/Special:FilePath/Plex_logo_2022.svg",
        "https://app.plex.tv/desktop/favicon.ico",
        "https://www.plex.tv/wp-content/themes/plex/assets/img/plex-logo.svg",
    ]
    for u in urls:
        try:
            r = urllib.request.Request(u, headers={"User-Agent": "KodiAddonBuild/1.0"})
            with urllib.request.urlopen(r, timeout=20) as resp:
                body = resp.read()
                print(f"== {u} -> {resp.status} {resp.headers.get('Content-Type')} {len(body)} final={resp.geturl()}")
                if len(body) < 60000:
                    print("B64:" + base64.b64encode(body).decode())
        except Exception as e:
            print(f"== {u} -> {e}")
    return 0


def round7():
    """DRM subtitles, trailers, continue watching, viewOffset, playQueue up-next/markers."""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    _, hubs = req_json("GET", base + "/hubs", headers=T)
    clear = drm = show = None
    for h in (mc(hubs).get("Hub") or [])[:30]:
        _, d = req_json("GET", base + h["key"], headers=T, params={"count": 20})
        for m in mc(d).get("Metadata") or []:
            if m.get("type") == "show":
                show = show or m
            elif any(x.get("drm") for x in m.get("Media") or []):
                drm = drm or m
            elif m.get("Media"):
                clear = clear or m
        if clear and drm and show:
            break

    def keys(label, d):
        print(f"   {label} keys:", sorted(k for k in d if not isinstance(d.get(k), (dict, list))))
        print(f"   {label} list keys:", sorted(k for k in d if isinstance(d.get(k), (dict, list))))

    # ---- 1. DRM subtitles
    print("\n######## 1 DRM subtitles:", drm.get("title"))
    for params in ({}, {"includeStreams": 1}, {"checkFiles": 1, "includeStreams": 1}):
        _, md = req_json("GET", base + f"/library/metadata/{drm['ratingKey']}", headers=T, params=params or None)
        it = (mc(md).get("Metadata") or [{}])[0]
        streams = [st for me in it.get("Media") or [] for pa in me.get("Part") or [] for st in pa.get("Stream") or []]
        print(f"  metadata {params} -> streams={len(streams)} subs={[x for x in streams if x.get('streamType') == 3][:2]}")
    uri = f"provider://tv.plex.provider.vod/library/metadata/{drm['ratingKey']}"
    _, pq = req_json("POST", base + "/playQueues", headers=T, params={"uri": uri, "type": "video", "continuous": 0})
    it = next((x for x in mc(pq).get("Metadata") or [] if x.get("ratingKey") == drm["ratingKey"]), {})
    subs = [st for me in it.get("Media") or [] if me.get("protocol") == "dash"
            for pa in me.get("Part") or [] for st in pa.get("Stream") or [] if st.get("streamType") == 3]
    print("  playQueue dash subs:", json.dumps(subs)[:600])
    for sub in subs[:1]:
        for q in ({}, {"format": "vtt"}, {"format": "srt"}, {"encoding": "utf-8", "format": "vtt"}):
            st, ct, body, fin = req("GET", base + sub["key"], headers=T, params=q or None)
            print(f"   GET {sub['key']} {q} -> {st} {ct} final={mask(fin)[:160]}")
            print("     ", mask(body[:160].decode("utf-8", "replace")).replace("\n", " | "))
            r = urllib.request.Request(fin, headers={"User-Agent": "Kodi/21"})
            try:
                with urllib.request.urlopen(r, timeout=20) as resp:
                    print("      bare fetch of final ->", resp.status)
            except urllib.error.HTTPError as e:
                print("      bare fetch of final ->", e.code)

    # ---- 2. trailers
    print("\n######## 2 trailers:", clear.get("title"))
    _, md = req_json("GET", base + f"/library/metadata/{clear['ratingKey']}", headers=T)
    it = (mc(md).get("Metadata") or [{}])[0]
    keys("metadata", it)
    pek = it.get("primaryExtraKey")
    print("  primaryExtraKey:", pek)
    for path in ([pek] if pek else []) + [f"/library/metadata/{clear['ratingKey']}/extras"]:
        _, ex = req_json("GET", base + path, headers=T)
        for e in (mc(ex).get("Metadata") or [])[:3]:
            print("  extra:", e.get("type"), e.get("subtype"), e.get("extraType"), e.get("title"), e.get("duration"))
            for me in e.get("Media") or []:
                print("    media:", json.dumps({k: v for k, v in me.items() if k != "Part"})[:300])
                for pa in me.get("Part") or []:
                    print("    part:", json.dumps({k: v for k, v in pa.items() if k != "Stream"})[:300])
                    u = pa["key"] if pa["key"].startswith("http") else base + pa["key"]
                    st, ct, body, fin = req("GET", u, headers=T)
                    print("    GET part ->", st, ct, mask(fin)[:150])

    # ---- 3/4. timeline -> continue watching + viewOffset
    print("\n######## 3/4 continue watching / viewOffset:", clear.get("title"))
    rk = clear["ratingKey"]
    for state, t in (("playing", 120000), ("paused", 300000), ("stopped", 300000)):
        st, ct, body, _ = req("GET", base + "/timeline", headers=T, params={
            "ratingKey": rk, "key": f"/library/metadata/{rk}", "state": state, "time": t,
            "duration": clear.get("duration", 0)})
        print(f"  timeline {state} -> {st} {mask(body[:200].decode('utf-8', 'replace'))}")
    import time as _t
    _t.sleep(3)
    _, md = req_json("GET", base + f"/library/metadata/{rk}", headers=T)
    it = (mc(md).get("Metadata") or [{}])[0]
    print("  metadata viewOffset/viewCount/lastViewedAt:", it.get("viewOffset"), it.get("viewCount"), it.get("lastViewedAt"))
    for path in ("/hubs/continueWatching", "/hubs/sections/movies/continueWatching"):
        _, cw = req_json("GET", base + path, headers=T)
        c = mc(cw)
        items = c.get("Metadata") or [m for h in c.get("Hub") or [] for m in h.get("Metadata") or []]
        print(f"  {path} -> size={c.get('size')} items={[(m.get('title'), m.get('viewOffset')) for m in items[:5]]}")
    _, hl = req_json("GET", base + f"/hubs/sections/movies/recommended", headers=T, params={"count": 50})
    hit = [m for m in mc(hl).get("Metadata") or [] if m.get("ratingKey") == rk]
    print("  in hub listing viewOffset:", [m.get("viewOffset") for m in hit])
    # discover provider user state
    st, ct, body, _ = req("GET", f"https://discover.provider.plex.tv/library/metadata/{rk}/userState", headers=T)
    print("  discover userState ->", st, mask(body[:300].decode("utf-8", "replace")))
    # mark watched: scrobble candidates
    for path in ("/actions/scrobble", "/:/scrobble"):
        st, ct, body, _ = req("PUT", base + path, headers=T, params={"identifier": "tv.plex.provider.vod", "key": rk, "ratingKey": rk})
        print(f"  PUT {path} -> {st} {mask(body[:150].decode('utf-8', 'replace'))}")
    _, root = req_json("GET", base + "/", headers=T)
    for f in (mc(root).get("MediaProvider") or {}).get("Feature") or []:
        if f.get("type") == "actions":
            print("  actions:", [(a.get("id"), a.get("key")) for a in f.get("Action") or []])

    # ---- 5. playQueue up-next for an episode + markers
    print("\n######## 5 playQueue up-next:", show.get("title"))
    _, ch = req_json("GET", base + f"/library/metadata/{show['ratingKey']}/children", headers=T)
    season = (mc(ch).get("Metadata") or [{}])[0]
    _, eps = req_json("GET", base + f"/library/metadata/{season['ratingKey']}/children", headers=T)
    ep = (mc(eps).get("Metadata") or [{}])[0]
    print("  first episode:", ep.get("title"), ep.get("index"), "of", len(mc(eps).get("Metadata") or []))
    for cont in (0, 1):
        uri = f"provider://tv.plex.provider.vod/library/metadata/{ep['ratingKey']}"
        _, pq = req_json("POST", base + "/playQueues", headers=T, params={
            "uri": uri, "type": "video", "continuous": cont, "includeChapters": 1, "includeMarkers": 1})
        c = mc(pq)
        print(f"  continuous={cont}: id={c.get('playQueueID')} total={c.get('playQueueTotalCount')} "
              f"selOffset={c.get('playQueueSelectedItemOffset')} version={c.get('playQueueVersion')}")
        for m in (c.get("Metadata") or [])[:6]:
            print("    ", m.get("type"), m.get("parentIndex"), m.get("index"), m.get("title"), m.get("ratingKey"),
                  "pqItem", m.get("playQueueItemID"), "Chapter" in m, "Marker" in m)
        sel = next((m for m in c.get("Metadata") or [] if m.get("ratingKey") == ep["ratingKey"]), {})
        print("    markers:", json.dumps(sel.get("Marker"))[:500])
        print("    chapters:", json.dumps(sel.get("Chapter"))[:300])
        if cont and c.get("playQueueID"):
            st, d = req_json("GET", base + f"/playQueues/{c['playQueueID']}", headers=T, params={"window": 3})
            print("    GET playQueue ->", st, mc(d).get("size"))
    _, md = req_json("GET", base + f"/library/metadata/{ep['ratingKey']}", headers=T, params={"includeMarkers": 1, "includeChapters": 1})
    it = (mc(md).get("Metadata") or [{}])[0]
    print("  metadata markers:", json.dumps(it.get("Marker"))[:500], "chapters:", bool(it.get("Chapter")))
    _, md = req_json("GET", base + f"/library/metadata/{clear['ratingKey']}", headers=T, params={"includeMarkers": 1})
    it = (mc(md).get("Metadata") or [{}])[0]
    print("  movie markers:", json.dumps(it.get("Marker"))[:400])
    return 0


def round8():
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    disc = "https://discover.provider.plex.tv"
    # 1. DRM subtitle stream fetch
    sk = "/library/streams/62d04d8b700e44e586e47e68-eng"
    for q in ({}, {"format": "vtt"}, {"format": "srt"}):
        st, ct, body, fin = req("GET", base + sk, headers=T, params=q or None)
        print(f"== sub {q} -> {st} {ct} final={mask(fin)[:170]}")
        print("   ", mask(body[:220].decode("utf-8", "replace")).replace("\n", " | "))
        if st == 200 and fin != base + sk:
            r = urllib.request.Request(fin, headers={"User-Agent": "Kodi/21"})
            try:
                with urllib.request.urlopen(r, timeout=20) as resp:
                    print("    bare final ->", resp.status, resp.headers.get("Content-Type"))
            except urllib.error.HTTPError as e:
                print("    bare final ->", e.code)
    # 2. episode markers across a season
    _, eps = req_json("GET", base + "/library/metadata/61effc8a9911a6c1eee7f3df/children", headers=T)
    for e in (mc(eps).get("Metadata") or [])[:4]:
        _, md = req_json("GET", base + f"/library/metadata/{e['ratingKey']}", headers=T, params={"includeMarkers": 1})
        it = (mc(md).get("Metadata") or [{}])[0]
        print("== ep", e.get("index"), "markers:", json.dumps(it.get("Marker")))
    _, eps = req_json("GET", base + "/library/metadata/61effc8a9911a6c1eee7f3df/children", headers=T, params={"includeMarkers": 1})
    print("== children includeMarkers:", [bool(e.get("Marker")) for e in mc(eps).get("Metadata") or []][:6])
    # 3. scrobble / removeFromContinueWatching effect on userState
    rk = "5d9f3cb89dd5f4001e84c9e8"
    def us():
        _, d = req_json("GET", disc + f"/library/metadata/{rk}/userState", headers=T)
        return (mc(d).get("UserState") or [{}])[0]
    req("GET", base + "/timeline", headers=T, params={"ratingKey": rk, "key": f"/library/metadata/{rk}",
        "state": "stopped", "time": 300000, "duration": 5624703})
    print("== userState after progress:", us())
    st, ct, body, _ = req("PUT", base + "/actions/removeFromContinueWatching", headers=T, params={"ratingKey": rk})
    print("== removeFromContinueWatching ->", st, mask(body[:150].decode()))
    _, cw = req_json("GET", base + "/hubs/continueWatching", headers=T)
    print("   CW after remove:", [m.get("title") for h in mc(cw).get("Hub") or [] for m in h.get("Metadata") or []])
    st, ct, body, _ = req("PUT", base + "/actions/scrobble", headers=T, params={"ratingKey": rk})
    print("== scrobble(ratingKey) ->", st, "userState:", us())
    st, ct, body, _ = req("PUT", base + "/actions/unscrobble", headers=T, params={"ratingKey": rk})
    print("== unscrobble ->", st, mask(body[:120].decode()), "userState:", us())
    # batch userState?
    st, ct, body, _ = req("GET", disc + "/library/metadata/" + rk + ",5d9f3c2d9dd5f4001e84b59c/userState", headers=T)
    print("== batch userState ->", st, mask(body[:300].decode("utf-8", "replace")))
    return 0


def round9():
    """Marker survey: which marker types the free VOD catalogue carries, how often."""
    from collections import Counter
    from concurrent.futures import ThreadPoolExecutor
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    movies, shows = {}, {}
    _, hubs = req_json("GET", base + "/hubs", headers=T)
    for h in mc(hubs).get("Hub") or []:
        _, d = req_json("GET", base + h["key"], headers=T, params={"count": 40})
        for m in mc(d).get("Metadata") or []:
            (movies if m.get("type") == "movie" else shows if m.get("type") == "show" else {})[m["ratingKey"]] = m.get("title")
        if len(movies) >= 250 and len(shows) >= 60:
            break
    movies = dict(list(movies.items())[:250])
    shows = dict(list(shows.items())[:60])
    print(f"sample: {len(movies)} movies, {len(shows)} shows")

    def episodes(srk):
        out = []
        _, ch = req_json("GET", base + f"/library/metadata/{srk}/children", headers=T)
        for season in (mc(ch).get("Metadata") or [])[:2]:
            _, eps = req_json("GET", base + f"/library/metadata/{season['ratingKey']}/children", headers=T)
            for e in (mc(eps).get("Metadata") or [])[:4]:
                out.append((e["ratingKey"], "%s S%sE%s" % (shows[srk], e.get("parentIndex"), e.get("index"))))
        return out

    with ThreadPoolExecutor(8) as ex:
        eps = [e for lst in ex.map(episodes, shows) for e in lst]
    print(f"episodes sampled: {len(eps)}")

    def markers(item):
        rk, title = item
        _, md = req_json("GET", base + f"/library/metadata/{rk}", headers=T,
                         params={"includeMarkers": 1, "includeChapters": 1})
        it = (mc(md).get("Metadata") or [{}])[0]
        return title, it.get("Marker") or [], bool(it.get("Chapter")), it.get("duration") or 0

    for label, items in (("MOVIES", list(movies.items())), ("EPISODES", eps)):
        with ThreadPoolExecutor(8) as ex:
            res = list(ex.map(markers, items))
        types, any_marker, chapters, examples = Counter(), 0, 0, {}
        credit_pos = []
        for title, mk, ch, dur in res:
            if mk:
                any_marker += 1
            chapters += ch
            seen = set()
            for m in mk:
                t = m.get("type")
                seen.add(t)
                examples.setdefault(t, (title, m))
                if t == "credits" and dur:
                    credit_pos.append((dur - m.get("startTimeOffset", 0)) / 1000.0)
            for t in seen:
                types[t] += 1
        n = len(res)
        print(f"\n===== {label}: {n} titles, with any marker {any_marker} ({100 * any_marker // max(1, n)}%), "
              f"with chapters {chapters}")
        for t, c in types.most_common():
            print(f"  {t}: {c} titles ({100 * c // max(1, n)}%)  e.g. {examples[t][0]}: {json.dumps(examples[t][1])}")
        if credit_pos:
            credit_pos.sort()
            print(f"  credits start before end (s): min {credit_pos[0]:.0f}, median {credit_pos[len(credit_pos) // 2]:.0f},"
                  f" max {credit_pos[-1]:.0f}")
    return 0


def round10():
    """Watchlist, image transcoder, sort/filter, related, people, search paging, languages."""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    disc = "https://discover.provider.plex.tv"
    rk = "5d9f3cb89dd5f4001e84c9e8"  # Rabid Dogs
    _, md = req_json("GET", base + f"/library/metadata/{rk}", headers=T)
    item = (mc(md).get("Metadata") or [{}])[0]

    def brief(label, url, params=None, method="GET"):
        st, ct, body, fin = req(method, url, headers=T, params=params)
        txt = body[:2000].decode("utf-8", "replace")
        info = ""
        try:
            c = mc(json.loads(txt if len(body) <= 2000 else body.decode("utf-8", "replace")))
            ms = c.get("Metadata") or c.get("Directory") or c.get("Hub") or []
            info = f"size={c.get('size')} total={c.get('totalSize')} offset={c.get('offset')} first={[ (m.get('type'), m.get('title'), m.get('key') or m.get('hubKey')) for m in ms[:4]]}"
        except Exception:
            info = mask(txt[:200]).replace("\n", " ")
        print(f"== {label}: {method} -> {st} {info}")
        return st, body

    # ---- watchlist
    print("\n######## watchlist")
    brief("add (vod action)", base + "/actions/addToWatchlist", {"ratingKey": rk}, "PUT")
    brief("add (discover action)", disc + "/actions/addToWatchlist", {"ratingKey": rk}, "PUT")
    for u in (disc + "/library/sections/watchlist/all", disc + "/library/sections/watchlist/all?includeMeta=1",
              base + "/library/sections/watchlist/all", "https://metadata.provider.plex.tv/library/sections/watchlist/all",
              disc + "/hubs/sections/watchlist", disc + "/library/sections/watchlist"):
        brief("list " + u.split("plex.tv")[1], u)
    brief("remove (vod action)", base + "/actions/removeFromWatchlist", {"ratingKey": rk}, "PUT")
    brief("list after remove", disc + "/library/sections/watchlist/all")
    _, d = req_json("GET", disc + f"/library/metadata/{rk}/userState", headers=T)
    print("   userState watchlisted?", (mc(d).get("UserState") or [{}])[0])

    # ---- image transcoder
    print("\n######## images")
    src = item.get("thumb")
    st, ct, body, _ = req("GET", src, headers={})
    print(f"== original {src.split('/')[2]} -> {st} {ct} {len(body)} bytes")
    for q in ({"url": src, "width": 300, "height": 450}, {"url": src, "width": 300, "height": 450, "minSize": 1, "upscale": 1},
              {"url": src, "width": 1920, "height": 1080, "format": "jpg", "quality": 80}):
        st, ct, body, fin = req("GET", "https://images.plex.tv/photo", headers={}, params=q)
        print(f"== photo {dict((k, v) for k, v in q.items() if k != 'url')} -> {st} {ct} {len(body)} bytes (no token)")

    # ---- sort / filter
    print("\n######## sort/filter")
    _, g = req_json("GET", base + "/library/sections/movies/genre", headers=T, params={"type": 1})
    gk = (mc(g).get("Directory") or [{}])[0].get("key")
    for sort in ("titleSort", "titleSort:desc", "year:desc", "originallyAvailableAt:desc", "addedAt:desc",
                 "rating:desc", "audienceRating:desc", "random"):
        _, d = req_json("GET", base + gk, headers=T, params={"sort": sort, "X-Plex-Container-Size": 3})
        print(f"== sort {sort}: {[(m.get('title'), m.get('year'), m.get('addedAt'), m.get('audienceRating')) for m in mc(d).get('Metadata') or []]}")
    for f in ({"year": 2019}, {"decade": 1990}, {"contentRating": "R"}, {"year>": 2020}):
        _, d = req_json("GET", base + gk, headers=T, params=dict(f, **{"X-Plex-Container-Size": 3}))
        c = mc(d)
        print(f"== filter {f}: total={c.get('totalSize')} {[(m.get('title'), m.get('year'), m.get('contentRating')) for m in c.get('Metadata') or []]}")
    for path in ("/library/sections/movies/year", "/library/sections/movies/decade", "/library/sections/movies/contentRating",
                 "/library/sections/movies/sorts", "/library/sections/movies/filters", "/library/sections/movies/studio"):
        brief(path, base + path, {"type": 1})

    # ---- related
    print("\n######## related")
    for u in (base + f"/library/metadata/{rk}/related", base + f"/hubs/metadata/{rk}/related",
              disc + f"/library/metadata/{rk}/related", disc + f"/hubs/metadata/{rk}/related",
              base + f"/hubs/metadata/{rk}", disc + f"/hubs/metadata/{rk}"):
        st, body = brief(u.replace(base, "{vod}").replace(disc, "{disc}"), u)
        if st == 200:
            try:
                c = mc(json.loads(body))
                for h in (c.get("Hub") or [])[:6]:
                    print("    hub:", h.get("title"), h.get("hubIdentifier"), h.get("key"), "size", h.get("size"),
                          [(m.get("title"), m.get("source") or "") for m in (h.get("Metadata") or [])[:2]])
            except Exception:
                pass

    # ---- people
    print("\n######## people")
    roles = item.get("Role") or []
    print("   Role[0]:", json.dumps(roles[:1])[:500])
    r0 = roles[0] if roles else {}
    for u in [disc + (r0.get("key") or "")] if r0.get("key") else []:
        brief("role key", u)
    for k in ("id", "tagKey", "slug"):
        if r0.get(k):
            for u in (disc + f"/library/people/{r0[k]}", disc + f"/library/people/{r0[k]}/media",
                      base + f"/library/people/{r0[k]}/media"):
                brief(f"people by {k}", u)
    if r0.get("tag"):
        brief("search by actor name", disc + "/library/search",
              {"query": r0["tag"], "searchTypes": "people", "searchProviders": "discover", "includeMetadata": 1, "limit": 3})

    # ---- search paging
    print("\n######## search paging")
    for q in ({"limit": 30}, {"limit": 100}, {"limit": 30, "offset": 30}):
        _, d = req_json("GET", disc + "/library/search", headers=T, params=dict(q, query="love", searchTypes="movies,tv",
                        searchProviders="discover,plexAVOD", includeMetadata=1))
        for sec in mc(d).get("SearchResults") or []:
            if sec.get("id") == "plex":
                res = sec.get("SearchResult") or []
                print(f"== search {q}: size={sec.get('size')} first={[(r.get('Metadata') or {}).get('title') for r in res[:3]]}")

    # ---- languages
    print("\n######## languages")
    _, d = req_json("GET", base + "/hubs/sections/movies/en-espanol-us", headers=T, params={"count": 5})
    for m in (mc(d).get("Metadata") or [])[:3]:
        _, md = req_json("GET", base + f"/library/metadata/{m['ratingKey']}", headers=T)
        it = (mc(md).get("Metadata") or [{}])[0]
        streams = [(s.get("streamType"), s.get("languageCode"), s.get("displayTitle"))
                   for me in it.get("Media") or [] if me.get("protocol") == "hls"
                   for pa in me.get("Part") or [] for s in pa.get("Stream") or []]
        print("== ", it.get("title"), "drm" if any(x.get("drm") for x in it.get("Media") or []) else "clear", streams)
        hls = [pa["key"] for me in it.get("Media") or [] if me.get("protocol") == "hls" and not me.get("drm")
               for pa in me.get("Part") or []]
        if hls:
            st, ct, body, _ = req("GET", base + hls[0], headers=T)
            print("    master renditions:", [l[:140] for l in body.decode("utf-8", "replace").splitlines() if "EXT-X-MEDIA" in l][:6])
    return 0


def round11():
    """Contents of the related hubs; whether their items are free VOD titles."""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    disc = "https://discover.provider.plex.tv"
    for rk in ("5d9f3cb89dd5f4001e84c9e8", "61effc8a9911a6c1eee7f3dd"):  # a movie, a show
        print(f"\n######## {rk}")
        for host, h in (("vod", base), ("disc", disc)):
            for tail in ("similarTitles", "more-from-credits", "studio"):
                st, d = req_json("GET", h + f"/library/metadata/{rk}/{tail}", headers=T, params={"count": 10})
                c = mc(d)
                items = c.get("Metadata") or []
                srcs = sorted(set((m.get("source") or "-").split("/")[-1] for m in items))
                media = sum(1 for m in items if m.get("Media"))
                print(f"== {host} {tail} -> {st} size={c.get('size')} total={c.get('totalSize')} sources={srcs} "
                      f"withMedia={media} first={[(m.get('type'), m.get('title')) for m in items[:4]]}")
        # are the disc similar titles playable from the VOD provider?
        _, d = req_json("GET", disc + f"/library/metadata/{rk}/similarTitles", headers=T, params={"count": 10})
        for m in (mc(d).get("Metadata") or [])[:4]:
            st, _ = req_json("GET", base + f"/library/metadata/{m['ratingKey']}", headers=T)
            print(f"   disc similar {m.get('title')!r} on VOD -> {st}")
    return 0


def round12():
    """/actions/rate and add/removeFromDislikes: parameters and where the state shows up."""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    disc = "https://discover.provider.plex.tv"
    rk = "5d9f3cb89dd5f4001e84c9e8"
    _, root = req_json("GET", base + "/", headers=T)
    for f in (mc(root).get("MediaProvider") or {}).get("Feature") or []:
        if f.get("type") == "actions":
            print("actions:", json.dumps(f.get("Action")))

    def state(label):
        _, d = req_json("GET", disc + f"/library/metadata/{rk}/userState", headers=T)
        us = (mc(d).get("UserState") or [{}])[0]
        _, m = req_json("GET", base + f"/library/metadata/{rk}", headers=T)
        it = (mc(m).get("Metadata") or [{}])[0]
        print(f"   [{label}] userState={us} meta.userRating={it.get('userRating')} meta.userState={it.get('userState')}")

    state("before")
    for params in ({"ratingKey": rk, "rating": 8}, {"key": rk, "rating": 6, "identifier": "tv.plex.provider.vod"},
                   {"ratingKey": rk, "rating": 10, "ratedAt": 1}, {"ratingKey": rk, "rating": -1}):
        for host, h in (("vod", base), ("disc", disc)):
            st, ct, body, _ = req("PUT", h + "/actions/rate", headers=T, params=params)
            print(f"== {host} PUT rate {params} -> {st} {mask(body[:150].decode('utf-8', 'replace'))}")
            state(f"after {host} rate {params.get('rating')}")
    for name in ("addToDislikes", "removeFromDislikes"):
        for host, h in (("vod", base), ("disc", disc)):
            st, ct, body, _ = req("PUT", h + f"/actions/{name}", headers=T, params={"ratingKey": rk})
            print(f"== {host} PUT {name} -> {st} {mask(body[:150].decode('utf-8', 'replace'))}")
            state(f"after {host} {name}")
    # where are dislikes listed / do disliked titles drop out of hubs?
    for u in (disc + "/library/sections/dislikes/all", disc + "/library/sections/watchlist/all?includeDislikes=1"):
        st, ct, body, _ = req("GET", u, headers=T)
        print(f"== GET {u.split('plex.tv')[1]} -> {st} {mask(body[:200].decode('utf-8', 'replace'))}")
    return 0


def round13():
    """Store (rentals): what an un-rented title returns, so the addon can tell
    'not rented' from a real failure. Anonymous token, no purchase."""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    st, d = req_json("GET", base + "/hubs/sections/store", headers=T, params={"count": 3})
    print("== store hubs ->", st, [(h.get("title"), h.get("key")) for h in (mc(d).get("Hub") or [])[:4]])
    st, d = req_json("GET", base + "/hubs/sections/store/myRentalsAndPurchases", headers=T)
    print("== myRentalsAndPurchases (anon) ->", st, "total", mc(d).get("totalSize"))
    st, d = req_json("GET", base + "/hubs/sections/store/newRentals", headers=T, params={"count": 3})
    items = mc(d).get("Metadata") or []
    print("== newRentals ->", st, [(m.get("title"), m.get("rentalPrice"), m.get("userState")) for m in items])
    m = items[0]
    _, md = req_json("GET", base + f"/library/metadata/{m['ratingKey']}", headers=T)
    it = (mc(md).get("Metadata") or [{}])[0]
    print("   metadata keys:", sorted(k for k in it if not isinstance(it[k], (dict, list))))
    print("   publicPagesURL:", it.get("publicPagesURL"), "| availabilityId:", it.get("availabilityId"))
    part = [x for x in it.get("Media") or [] if x.get("protocol") == "dash"][0]["Part"][0]
    st, ct, body, _ = req("GET", base + part["key"], headers=T)
    print(f"== manifest (not rented) -> {st} {ct} {mask(body[:250].decode('utf-8', 'replace'))}")
    r = urllib.request.Request(base + part["license"], data=b"\x08\x04", method="POST",
                               headers=dict(HEADERS, **T, **{"X-Plex-DRM": "widevine", "Content-Type": "application/octet-stream"}))
    try:
        with urllib.request.urlopen(r, timeout=20) as resp:
            print("== licence cert request (not rented) ->", resp.status, resp.read()[:20])
    except urllib.error.HTTPError as e:
        print("== licence cert request (not rented) ->", e.code, mask(e.read()[:250].decode("utf-8", "replace")))
    uri = f"provider://tv.plex.provider.vod/library/metadata/{m['ratingKey']}"
    st, ct, body, _ = req("POST", base + "/playQueues", headers=T, params={"uri": uri, "type": "video", "continuous": 0})
    print(f"== playQueue (not rented) -> {st} {mask(body[:250].decode('utf-8', 'replace'))}")
    st, d = req_json("GET", "https://discover.provider.plex.tv" + f"/library/metadata/{m['ratingKey']}/userState", headers=T)
    print("== userState:", (mc(d).get("UserState") or [{}])[0])
    return 0


def round14():
    """Do genre listings / search mix in store (rental) titles, and does any
    parameter restrict them to free ones?"""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"

    def priced(items):
        return [m for m in items if m.get("rentalPrice") or m.get("purchasePrice") or m.get("price")]

    for mtype in (1, 2):
        _, g = req_json("GET", base + "/library/sections/movies/genre", headers=T, params={"type": mtype})
        key = (mc(g).get("Directory") or [{}])[0].get("key")
        print(f"\n######## type={mtype} {key}")
        for extra in ({}, {"availability": "free"}, {"availabilityTypes": "free"}, {"availabilityType": "free"},
                      {"availability": "avod"}, {"includeStore": 0}, {"store": 0}, {"free": 1},
                      {"contentDirectoryID": "movies"}, {"tvod": 0}):
            params = dict(extra, **{"X-Plex-Container-Size": 100})
            st, d = req_json("GET", base + key, headers=T, params=params)
            c = mc(d)
            items = c.get("Metadata") or []
            p = priced(items)
            print(f"== {extra}: {st} total={c.get('totalSize')} page={len(items)} priced={len(p)} "
                  f"e.g. {[(m.get('title'), m.get('rentalPrice')) for m in p[:2]]}")
        # what distinguishes a priced item? keys present only on priced ones
        st, d = req_json("GET", base + key, headers=T, params={"X-Plex-Container-Size": 100})
        items = mc(d).get("Metadata") or []
        p = priced(items); f = [m for m in items if m not in p]
        if p and f:
            only_p = set.intersection(*[set(m) for m in p]) - set.union(*[set(m) for m in f])
            print("   keys only on priced items:", sorted(only_p))
            print("   priced media drm:", [x.get("drm") for m in p[:3] for x in (m.get("Media") or [])][:6])
            print("   priced availabilityTypes-ish:", [(m.get("availabilityId"), m.get("userState")) for m in p[:2]])
    # hubs and search
    _, hubs = req_json("GET", base + "/hubs", headers=T)
    n_priced = 0
    for h in (mc(hubs).get("Hub") or [])[:20]:
        _, d = req_json("GET", base + h["key"], headers=T, params={"count": 50})
        n_priced += len(priced(mc(d).get("Metadata") or []))
    print("\n== priced items across first 20 home hubs:", n_priced)
    _, sr = req_json("GET", "https://discover.provider.plex.tv/library/search", headers=T, params={
        "query": "love", "searchTypes": "movies,tv", "searchProviders": "discover,plexAVOD", "includeMetadata": 1, "limit": 100})
    for sec in mc(sr).get("SearchResults") or []:
        if sec.get("id") == "plex":
            ms = [r.get("Metadata") or {} for r in sec.get("SearchResult") or []]
            print("== search 'plex' section:", len(ms), "priced:", len(priced(ms)),
                  [(m.get("title"), m.get("rentalPrice")) for m in priced(ms)[:3]])
    return 0


def round15():
    """Genre icons are SVG on provider-static.plex.tv (Kodi gets 403 and can't
    draw SVG). Does images.plex.tv/photo rasterise them to PNG/JPEG?"""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    _, g = req_json("GET", base + "/library/sections/movies/genre", headers=T, params={"type": 1})
    icons = [d.get("icon") for d in (mc(g).get("Directory") or []) if d.get("icon")][:3]
    print("icons:", icons)
    for icon in icons[:2]:
        for label, hdr in (("bare", {"User-Agent": "Kodi/21"}), ("browser", {"User-Agent": HEADERS["User-Agent"],
                           "Referer": "https://app.plex.tv/", "Origin": "https://app.plex.tv"})):
            r = urllib.request.Request(icon, headers=hdr)
            try:
                with urllib.request.urlopen(r, timeout=20) as resp:
                    print(f"== direct {label} -> {resp.status} {resp.headers.get('Content-Type')}")
            except urllib.error.HTTPError as e:
                print(f"== direct {label} -> {e.code}")
        for q in ({"url": icon, "width": 256, "height": 256},
                  {"url": icon, "width": 256, "height": 256, "format": "png"},
                  {"url": icon, "width": 256, "height": 256, "minSize": 1, "upscale": 1, "format": "png"}):
            u = "https://images.plex.tv/photo?" + urllib.parse.urlencode(q)
            r = urllib.request.Request(u, headers={"User-Agent": "Kodi/21"})
            try:
                with urllib.request.urlopen(r, timeout=20) as resp:
                    body = resp.read()
                    print(f"== transcoder {dict((k, v) for k, v in q.items() if k != 'url')} -> {resp.status} "
                          f"{resp.headers.get('Content-Type')} {len(body)} bytes magic={body[:8]!r}")
            except urllib.error.HTTPError as e:
                print(f"== transcoder {dict((k, v) for k, v in q.items() if k != 'url')} -> {e.code} {e.read()[:120]!r}")
    return 0


def round16():
    """Where does Plex keep 'Brands' (studios / networks / channels)?"""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    disc = "https://discover.provider.plex.tv"
    _, root = req_json("GET", base + "/", headers=T)
    for f in (mc(root).get("MediaProvider") or {}).get("Feature") or []:
        if f.get("type") == "content":
            print("== content feature:", mask(json.dumps(f))[:3000])

    def brief(label, url, params=None):
        st, ct, body, _ = req("GET", url, headers=T, params=params)
        try:
            c = mc(json.loads(body))
            rows = c.get("Directory") or c.get("Hub") or c.get("Metadata") or []
            print(f"== {label} -> {st} size={c.get('size')} total={c.get('totalSize')} "
                  f"{[(r.get('type'), r.get('title'), r.get('key') or r.get('hubKey')) for r in rows[:6]]}")
        except Exception:
            print(f"== {label} -> {st} {mask(body[:160].decode('utf-8', 'replace'))}")

    for path in ("/library/sections", "/library/sections/movies", "/library/sections/movies/brand",
                 "/library/sections/movies/brands", "/library/sections/movies/network",
                 "/library/sections/movies/studio", "/library/sections/movies/attribution",
                 "/library/sections/movies/collection", "/library/sections/movies/channel",
                 "/library/sections/movies/firstCharacter", "/hubs/sections/brands", "/hubs/brands",
                 "/library/sections/brands", "/library/sections/brands/all", "/brands", "/channels",
                 "/hubs/sections/movies/brands", "/library/sections/movies/categories"):
        for params in ({"type": 1}, None):
            brief(path + (" ?type=1" if params else ""), base + path, params)
    for path in ("/hubs/sections/home", "/hubs/sections/brands", "/library/sections/brands/all"):
        brief("disc " + path, disc + path)
    # attribution values seen on items: does filtering by it work?
    _, hubs = req_json("GET", base + "/hubs/sections/movies/trending", headers=T, params={"count": 50})
    attrs = sorted(set(m.get("attribution") for m in mc(hubs).get("Metadata") or [] if m.get("attribution")))
    print("== attribution values:", attrs[:20])
    _, g = req_json("GET", base + "/library/sections/movies/genre", headers=T, params={"type": 1})
    gk = (mc(g).get("Directory") or [{}])[0].get("key")
    if attrs:
        st, d = req_json("GET", base + gk, headers=T, params={"attribution": attrs[0], "X-Plex-Container-Size": 3})
        print(f"== genre filtered attribution={attrs[0]} -> {st} total={mc(d).get('totalSize')}")
        brief("attribution path", base + "/library/sections/movies/all", {"attribution": attrs[0], "type": 1})
    return 0


def round17():
    """Category artwork: title, key and a 16x9 colour grid of each background,
    so the art can be rebuilt with the name drawn in (Kodi can't overlay text)."""
    import io
    import subprocess
    subprocess.call([sys.executable, "-m", "pip", "install", "--quiet", "pillow"])
    from PIL import Image
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    _, c = req_json("GET", "https://vod.provider.plex.tv/library/sections/categories/all", headers=T)
    rows = mc(c).get("Directory") or []
    print("== categories:", len(rows))
    for i, d in enumerate(rows):
        thumb = d.get("thumb") or ""
        st, ct, body, _ = req("GET", thumb)
        try:
            im = Image.open(io.BytesIO(body)).convert("RGB")
            size = im.size
            grid = im.resize((16, 9), Image.BOX)
            hexes = "".join("%02x%02x%02x" % grid.getpixel((x, y)) for y in range(9) for x in range(16))
        except Exception as exc:
            size, hexes = None, "ERR %s" % exc
        print(f"== CAT {i} title={d.get('title')!r} key={d.get('key')!r} thumb={mask(thumb)} -> {st} {ct} {len(body)}B size={size}")
        print(f"GRID {i} {hexes}")
    return 0


def round18():
    """Live TV: which provider, its features, channels, a channel's stream and guide."""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    _, provs = req_json("GET", "https://plex.tv/media/providers", headers=T)
    plist = provs if isinstance(provs, list) else (mc(provs).get("MediaProvider") or [])
    for p in plist:
        print(f"== provider {p.get('identifier')} title={p.get('title')!r} base={p.get('baseURL')} types={p.get('types')} proto={p.get('protocols')}")
    live = [p for p in plist if "epg" in (p.get("identifier") or "") or "live" in (p.get("title") or "").lower()
            or "tv" in str(p.get("types") or "") and "vod" not in (p.get("identifier") or "")]
    for p in live:
        base = (p.get("baseURL") or "").rstrip("/")
        print(f"\n#### LIVE PROVIDER {p.get('identifier')} {base}")
        st, root = req_json("GET", base + "/", headers=T)
        print("== root", st)
        for f in (mc(root).get("MediaProvider") or {}).get("Feature") or []:
            print("== feature:", mask(json.dumps(f))[:1500])
        for path in ("/hubs", "/lineups", "/channels", "/library/sections", "/grid"):
            st, ct, body, _ = req("GET", base + path, headers=T)
            print(f"== {path} -> {st} {ct} {mask(body[:600].decode('utf-8', 'replace'))}")
        # channels via the content feature's keys
        for f in (mc(root).get("MediaProvider") or {}).get("Feature") or []:
            for d in f.get("Directory") or []:
                k = d.get("key") or ""
                if not k or k.startswith("http"):
                    continue
                st, ct, body, _ = req("GET", base + k, headers=T, params={"X-Plex-Container-Size": 3})
                print(f"== dir {d.get('title')!r} {k} -> {st} {mask(body[:900].decode('utf-8', 'replace'))}")
    return 0


def round19():
    """Live TV: channel list, a channel's stream, the guide grid."""
    import time as _t
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://epg.provider.plex.tv"
    st, ch = show("all channels", "GET", base + "/lineups/plex/channels", depth=5, headers=T)
    chans = mc(ch).get("Channel") or []
    print("== channel count:", len(chans), "size/total:", mc(ch).get("size"), mc(ch).get("totalSize"))
    st2, fe = req_json("GET", base + "/lineups/plex/channels/featured", headers=T)
    print("== featured count:", len(mc(fe).get("Channel") or []))
    st3, ge = req_json("GET", base + "/lineups/plex/channels", headers=T, params={"genre": "news"})
    print("== news count:", len(mc(ge).get("Channel") or []))
    print("== sample:", [(c.get("title"), c.get("callSign"), c.get("vcn"), c.get("gridKey"), c.get("id")) for c in chans[:5]])
    print("== keys on a channel:", sorted(chans[0].keys()) if chans else None)
    if not chans:
        return 0
    c = chans[0]
    # stream: try whatever the channel's Media/Part says
    media = c.get("Media") or []
    print("== media:", mask(json.dumps(media)[:1500]))
    part = ((media or [{}])[0].get("Part") or [{}])[0]
    for k in [part.get("key"), c.get("key")]:
        if not k:
            continue
        u = k if k.startswith("http") else base + k
        st, ct, body, final = req("GET", u, headers=T)
        print(f"== stream try {mask(u)} -> {st} {ct} final={mask(final)}\n{mask(body[:700].decode('utf-8', 'replace'))}")
        if st == 200 and b"#EXTM3U" in body[:20]:
            lines = [l for l in body.decode().splitlines() if l and not l.startswith("#")]
            if lines:
                sub = lines[0] if lines[0].startswith("http") else final.rsplit("/", 1)[0] + "/" + lines[0]
                st, ct, b2, _ = req("GET", sub)
                print(f"== variant without token {mask(sub)[:200]} -> {st} {mask(b2[:300].decode('utf-8', 'replace'))}")
    # guide grid
    gk = c.get("gridKey") or c.get("id")
    day = _t.strftime("%Y-%m-%d", _t.gmtime())
    show("grid one channel", "GET", base + "/grid", depth=6, headers=T, params={"channelGridKey": gk, "date": day})
    keys = ",".join(x.get("gridKey") or x.get("id") for x in chans[:3])
    st, g = req_json("GET", base + "/grid", headers=T, params={"channelGridKey": keys, "date": day})
    md = mc(g).get("Metadata") or []
    print("== grid 3 channels ->", st, "items", len(md), "size", mc(g).get("size"))
    st, g = req_json("GET", base + "/grid", headers=T, params={"channelGridKey": gk})
    print("== grid no date ->", st, "items", len(mc(g).get("Metadata") or []))
    show("hubs whatsOnNow", "GET", base + "/hubs/sections/home/whatsOnNow", depth=4, headers=T,
         params={"X-Plex-Container-Size": 2})
    return 0


def round20():
    """Free movies that carry a credits marker (for a manual test)."""
    _, anon = req_json("POST", "https://plex.tv/api/v2/users/anonymous")
    tok = secret(anon.get("authToken"))
    T = {"X-Plex-Token": tok}
    base = "https://vod.provider.plex.tv"
    _, hubs = req_json("GET", base + "/hubs", headers=T)
    seen, found = set(), []
    for h in (mc(hubs).get("Hub") or [])[:25]:
        _, row = req_json("GET", base + h["key"], headers=T, params={"X-Plex-Container-Size": 30})
        for m in mc(row).get("Metadata") or []:
            if m.get("type") != "movie" or m["ratingKey"] in seen:
                continue
            seen.add(m["ratingKey"])
            _, md = req_json("GET", base + "/library/metadata/" + m["ratingKey"], headers=T, params={"includeMarkers": 1})
            meta = (mc(md).get("Metadata") or [{}])[0]
            cr = [mk for mk in meta.get("Marker") or [] if mk.get("type") == "credits"]
            if cr:
                start = max(int(c.get("startTimeOffset") or 0) for c in cr) // 1000
                found.append((meta.get("title"), meta.get("year"), meta.get("duration", 0) // 60000, start // 60, start % 60))
                print("== CREDITS %s (%s) runtime %d min, credits at %d:%02d" % found[-1])
            if len(found) >= 10 or len(seen) >= 150:
                break
        if len(found) >= 10 or len(seen) >= 150:
            break
    print("== checked", len(seen), "movies, found", len(found))
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
    mode = os.environ.get("PROBE_MODE", "round20")
    sys.exit({"round3": round3, "round4": round4, "round5": round5, "round6": round6, "logo": logo, "full": main, "round7": round7, "round8": round8, "round9": round9, "round10": round10, "round11": round11, "round12": round12, "round13": round13, "round14": round14, "round15": round15, "round16": round16, "round17": round17, "round18": round18, "round19": round19, "round20": round20}.get(mode, main)())
