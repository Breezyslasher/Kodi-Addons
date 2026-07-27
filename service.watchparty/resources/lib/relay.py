"""
Watch Party relay server (host side).

A small threaded HTTP server that keeps the shared playback state for one
party ("room") in memory. Every member — including the hosting Kodi, which
connects to itself over localhost — talks to it with short JSON POSTs:

    POST /join     {room, name}                    -> {ok, member_id, state, server_time}
    POST /leave    {room, member_id}               -> {ok}
    POST /command  {room, member_id, cmd, ...}     -> {ok, state, server_time}
    POST /poll     {room, member_id, position,
                    paused, file}                  -> {ok, state, server_time}
    GET  /ping                                     -> {ok, app, server_time}

The playback state anchors a position to a server timestamp (set_at), so
any member can compute the expected "now" position as
position + (server_now - set_at) * speed while playing. All requests carry
the room code, which doubles as the access token.

Pure standard library — no Kodi imports — so it is unit-testable outside
Kodi.
"""
import json
import os
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

START_TIME = time.time()
ICON_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'icon.png')


MEMBER_TIMEOUT = 15.0    # seconds without a poll before a member is pruned
MAX_BODY = 64 * 1024
MAX_ART = 1024 * 1024    # uploaded cover art, per room

# Bumped whenever the protocol gains features. 1 = original release,
# 2 = buffer hold, control lock, library-id item fields,
# 3 = music identity (artist/album) and shared playlists.
PROTOCOL_VERSION = 3


def mask_code(code):
    """Obscure a room code — codes are the access tokens."""
    if len(code) <= 2:
        return '·' * len(code)
    return code[0] + '·' * (len(code) - 2) + code[-1]


def rooms_stats(rooms, show_codes=False):
    """Dashboard payload for [(code, RoomState), ...].

    Additive: 'item' stays the flat label older clients read, and the
    detail lives alongside it under 'now'.
    """
    now = time.time()
    out = []
    for code, room in sorted(rooms, key=lambda pair: pair[0]):
        snap = room.snapshot()
        item = snap.get('item') or {}
        position = float(snap.get('position') or 0.0)
        if item and not snap.get('paused'):
            position += max(0.0, now - float(snap.get('set_at') or now)) \
                * float(snap.get('speed') or 1.0)
        detail = None
        if item:
            detail = {k: item.get(k) for k in
                      ('title', 'year', 'type', 'show', 'season',
                       'episode', 'ids', 'art', 'duration', 'artist',
                       'album')
                      if item.get(k) not in (None, '', {})}
            detail['label'] = item.get('label') or ''
            detail['queue'] = len(item.get('playlist') or []) or None
            detail['queue_pos'] = item.get('playlist_pos')
            detail['repeat'] = item.get('repeat') or 'off'
            detail['shuffled'] = bool(item.get('shuffled'))
            # art the relay holds itself — works for viewers who could
            # never reach the origin, and never carries its credentials
            art_id = snap.get('art_id')
            detail['art_url'] = ('/art/' + art_id) if art_id else None
        out.append({
            'room': code if show_codes else mask_code(code),
            'members': [
                {'name': m.get('name'), 'position': m.get('position'),
                 'paused': m.get('paused'), 'drift': m.get('drift'),
                 'caching': m.get('caching'), 'on_item': m.get('on_item'),
                 'age': round(float(m.get('age') or 0.0), 1),
                 'is_host': m.get('is_host'), 'is_owner': m.get('is_owner'),
                 'file': m.get('file')}
                for m in snap.get('members') or []
            ],
            'item': item.get('label') or item.get('plugin')
                    or item.get('file') or None,
            'now': detail,
            'duration': item.get('duration'),
            'paused': bool(snap.get('paused')),
            'position': position,
            'set_at': snap.get('set_at'),
            'seq': snap.get('seq'),
            'locked_by_name': snap.get('locked_by_name'),
            'buffer_hold_name': snap.get('buffer_hold_name'),
            'buffer_hold_since': snap.get('buffer_hold_since') or 0.0,
            'commands_per_min': snap.get('commands_per_min') or 0,
            'corrections': snap.get('corrections') or 0,
        })
    return out


DASH_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Watch Party relay</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
 *{box-sizing:border-box}
 body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
      background:#0b0c0e;color:#e8e8e8;margin:0;padding:24px 16px}
 .page{max-width:1080px;margin:0 auto;background:#14161a;
       border:1px solid #23262c;border-radius:16px;overflow:hidden}
 .mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}
 /* top bar */
 .top{display:flex;justify-content:space-between;align-items:center;
      flex-wrap:wrap;gap:14px;padding:18px 24px;background:#101216;
      border-bottom:1px solid #23262c}
 .brand{display:flex;align-items:center;gap:10px}
 .mark{width:26px;height:26px;border-radius:8px;background:#1d2026;
       border:1px solid #23262c;display:flex;align-items:center;
       justify-content:center;font-size:13px;color:#7ee08a}
 .mark img{width:26px;height:26px;border-radius:8px;display:block}
 .brand h1{font:600 15px/1.1 system-ui;margin:0}
 .pill{display:inline-flex;align-items:center;gap:6px;padding:4px 9px;
       border-radius:99px;font:600 11px ui-monospace,Menlo,monospace;
       letter-spacing:.1em}
 .pill.ok{background:rgba(126,224,138,.12);
          border:1px solid rgba(126,224,138,.3);color:#7ee08a}
 .pill.warn{background:rgba(255,184,107,.12);
            border:1px solid rgba(255,184,107,.3);color:#ffb86b}
 .pill.bad{background:rgba(224,90,122,.12);
           border:1px solid rgba(224,90,122,.35);color:#e05a7a}
 .dot{width:6px;height:6px;border-radius:99px;background:currentColor;
      animation:pulse 2s ease-in-out infinite}
 @keyframes pulse{0%,100%{opacity:1}50%{opacity:.35}}
 .stats{display:flex;gap:26px;align-items:flex-start;flex-wrap:wrap}
 .stat span{display:block;font-size:10px;letter-spacing:.14em;
            color:#6e7580}
 .stat b{display:block;font:500 13px ui-monospace,Menlo,monospace;
         margin-top:3px}
 .upd{font-size:11px;color:#6e7580;text-align:right;line-height:1.5}
 /* body */
 .body{display:grid;grid-template-columns:1fr 300px}
 .main{padding:24px;display:flex;flex-direction:column;gap:20px;min-width:0}
 .rail{border-left:1px solid #23262c;background:#101216;padding:22px 20px;
       display:flex;flex-direction:column;gap:24px;min-width:0}
 .room{display:flex;flex-direction:column;gap:20px}
 /* now playing */
 .np{display:flex;gap:20px}
 .art{width:132px;height:196px;border-radius:10px;background:#1d2026;
      border:1px dashed #33373f;flex:none;object-fit:cover;
      display:flex;align-items:center;justify-content:center;
      color:#3a3f47;font-size:26px}
 .npr{display:flex;flex-direction:column;gap:10px;flex:1;min-width:0}
 .codeline{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
 .code{font:700 13px ui-monospace,Menlo,monospace;letter-spacing:.22em}
 .cap{font:500 11px system-ui;color:#6e7580}
 .title{font:600 24px/1.2 system-ui;color:#fff;margin:0;
        text-wrap:pretty;overflow-wrap:anywhere}
 .meta{font:400 13px system-ui;color:#8a919c;overflow-wrap:anywhere}
 .tl{margin-top:auto}
 .tlnum{display:flex;justify-content:space-between;
        font:400 12px ui-monospace,Menlo,monospace}
 .tlnum b{font-weight:400;color:#e8e8e8}
 .tlnum i{font-style:normal;color:#8a919c}
 .track{position:relative;height:34px;margin-top:6px}
 .rail6{position:absolute;top:13px;left:0;right:0;height:6px;
        border-radius:99px;background:#23262c}
 .fill{position:absolute;top:13px;left:0;height:6px;border-radius:99px;
       background:#7ee08a;transition:width .25s linear}
 .anchor{position:absolute;top:6px;width:2px;height:20px;background:#fff;
         border-radius:2px;transition:left .25s linear}
 .mk{position:absolute;top:12px;width:8px;height:8px;border-radius:99px;
     border:2px solid #14161a;background:#9ecbff;margin-left:-4px;
     transition:left .25s linear}
 .mk.off{background:#ffb86b}
 .mkd{position:absolute;top:26px;font:500 10px ui-monospace,Menlo,monospace;
      color:#ffb86b;transform:translateX(-50%)}
 .legend{display:flex;gap:14px;flex-wrap:wrap;font:400 11px system-ui;
         color:#6e7580;margin-top:2px}
 .legend i{display:inline-block;width:7px;height:7px;margin-right:5px;
           background:#fff;font-style:normal}
 .legend i.b{background:#9ecbff;border-radius:99px}
 .legend i.a{background:#ffb86b;border-radius:99px}
 /* members */
 .mhead{display:flex;justify-content:space-between;align-items:baseline;
        gap:10px;border-bottom:1px solid #23262c;padding-bottom:8px}
 .mhead h2{margin:0;font:600 12px system-ui;letter-spacing:.12em;
           color:#8a919c}
 .mhead span{font:400 11px system-ui;color:#6e7580}
 .m{display:flex;gap:14px;align-items:center;padding:12px 4px;
    border-bottom:1px solid #1b1e23}
 .m.buf{background:rgba(255,184,107,.05)}
 .m.stale{opacity:.55}
 .av{width:30px;height:30px;border-radius:99px;flex:none;display:flex;
     align-items:center;justify-content:center;font:600 12px system-ui;
     background:#2a2d33;color:#8a919c}
 .av.sync{background:#2a3f2e;color:#7ee08a}
 .av.norm{background:#25303d;color:#9ecbff}
 .av.buf{background:#3d3325;color:#ffb86b}
 .mi{flex:1;min-width:0}
 .mn{display:flex;align-items:center;gap:8px;flex-wrap:wrap;
     font:500 14px system-ui}
 .badge{font:600 9px ui-monospace,Menlo,monospace;letter-spacing:.1em;
        border-radius:4px;padding:3px 5px}
 .b-host{color:#9ecbff;border:1px solid rgba(158,203,255,.4)}
 .b-buf{color:#ffb86b;border:1px solid rgba(255,184,107,.4)}
 .b-old{color:#8a919c;border:1px solid rgba(138,145,156,.4)}
 .ms{font:400 11px system-ui;color:#6e7580;overflow-wrap:anywhere;
     margin-top:2px}
 .mp{font:500 13px ui-monospace,Menlo,monospace;color:#e8e8e8}
 .md{width:62px;text-align:right;font:500 12px ui-monospace,Menlo,monospace}
 .d-ok{color:#7ee08a}.d-sm{color:#8a919c}.d-big{color:#ffb86b}
 /* rail */
 .rh{font:600 11px system-ui;letter-spacing:.14em;color:#6e7580;
     margin-bottom:10px}
 .bars{display:flex;align-items:flex-end;gap:3px;height:46px}
 .bars div{flex:1;border-radius:2px;background:#2a3f2e;min-height:2px}
 .bars div.peak{background:#3d3325}
 .bars div.last{background:#7ee08a}
 .rcap{font:400 10px system-ui;color:#6e7580;margin-top:6px}
 .kv{display:flex;justify-content:space-between;gap:10px;
     font:400 12px system-ui;color:#8a919c;padding:3px 0}
 .kv b{font:400 12px ui-monospace,Menlo,monospace;color:#e8e8e8}
 .rcard{border:1px solid #23262c;border-radius:10px;padding:12px;
        background:#14161a;margin-bottom:10px}
 .rcard .t{font:400 12px/1.3 system-ui;color:#9ecbff;word-break:break-all;
           margin:6px 0 4px}
 .rcard .s{font:400 11px system-ui;color:#6e7580}
 .rempty{border:1px dashed #23262c;border-radius:10px;padding:12px;
         font:italic 400 12px system-ui;color:#6e7580}
 .note{font:400 10px/1.5 system-ui;color:#4f555e}
 /* states */
 .banner{margin:0 0 4px;padding:12px 14px;border-radius:10px;
         border:1px solid #23262c;background:#101216}
 .banner.bad{border-color:rgba(224,90,122,.35)}
 .banner.warn{border-color:rgba(255,184,107,.35)}
 .banner h3{margin:0 0 4px;font:600 13px system-ui}
 .banner p{margin:0;font:400 11px/1.4 system-ui;color:#8a919c}
 .sweep{height:3px;background:#23262c;border-radius:2px;margin-top:8px;
        overflow:hidden}
 .sweep i{display:block;width:40%;height:100%;background:#e05a7a;
          animation:sweep 1.6s linear infinite}
 @keyframes sweep{from{transform:translateX(-100%)}
                  to{transform:translateX(220%)}}
 .empty{text-align:center;padding:18px 0}
 .empty .o{width:34px;height:34px;border-radius:99px;
           border:1px dashed #3a3f47;margin:0 auto 10px}
 .empty h3{margin:0 0 4px;font:500 13px system-ui}
 .empty p{margin:0;font:400 11px/1.4 system-ui;color:#6e7580}
 .dim{opacity:.5}
 @media (max-width:900px){
   .body{grid-template-columns:1fr}
   .rail{border-left:0;border-top:1px solid #23262c}
 }
 @media (max-width:720px){
   body{padding:0}
   .page{border:0;border-radius:0}
   .main{padding:16px}
   .stats{display:grid;grid-template-columns:1fr 1fr;gap:12px 20px;
          width:100%}
   .upd{text-align:left}
   .art{width:96px;height:142px}
   .title{font-size:20px}
 }
 @media (prefers-reduced-motion:reduce){
   .dot,.sweep i{animation:none}
   .fill,.anchor,.mk{transition:none}
 }
</style></head><body>
<div class="page">
 <div class="top">
  <div class="brand">
   <span class="mark" id="mark">&#9654;</span>
   <h1>Watch Party relay</h1>
   <span class="pill ok" id="state"><span class="dot"></span><span
     id="statetxt">ONLINE</span></span>
  </div>
  <div class="stats">
   <div class="stat"><span>UPTIME</span><b id="uptime">&mdash;</b></div>
   <div class="stat"><span>PROTOCOL</span><b id="proto">&mdash;</b></div>
   <div class="stat"><span>ROOMS</span><b id="nrooms">0</b></div>
   <div class="stat"><span>POLL RTT</span><b id="rtt">&mdash;</b></div>
   <div class="upd">updated<br><span class="mono" id="updated">&mdash;</span></div>
  </div>
 </div>
 <div class="body">
  <div class="main" id="main"></div>
  <div class="rail">
   <div>
    <div class="rh">RELAY HEALTH</div>
    <div class="bars" id="bars"></div>
    <div class="rcap" id="rcap">poll round-trip, last 90s</div>
    <div style="margin-top:14px">
     <div class="kv">Commands / min <b id="cpm">0</b></div>
     <div class="kv">Corrective seeks <b id="corr">0</b></div>
     <div class="kv">Members <b id="nmem">0</b></div>
     <div class="kv">State seq <b id="seq">0</b></div>
    </div>
   </div>
   <div>
    <div class="rh">OTHER ROOMS</div>
    <div id="others"></div>
   </div>
   <div class="note">Room codes stay masked &mdash; they are the access
    token for joining. Set WATCHPARTY_SHOW_CODES=1 only on a private
    deployment.</div>
  </div>
 </div>
</div>
<script>
var snap=null,lastGoodAt=0,unreachable=false,clockSkew=0,rtt=[],first=true;
var prevPos={};
function esc(t){var d=document.createElement('div');
 d.textContent=t==null?'':String(t);return d.innerHTML}
function fmt(s){s=Math.max(0,Math.floor(s||0));
 var h=Math.floor(s/3600),m=Math.floor(s%3600/60),x=s%60;
 return (h?h+':':'')+String(m).padStart(h?2:1,'0')+':'+
        String(x).padStart(2,'0')}
function dur(s){s=Math.max(0,Math.floor(s||0));
 var d=Math.floor(s/86400),h=Math.floor(s%86400/3600),
     m=Math.floor(s%3600/60),x=s%60;
 return (d?d+'d ':'')+String(h).padStart(2,'0')+':'+
        String(m).padStart(2,'0')+':'+String(x).padStart(2,'0')}
function clock(t){var d=new Date(t*1000);
 return d.toTimeString().slice(0,8)}
function serverNow(){return Date.now()/1000+clockSkew}
function livePos(r){var p=r.position||0;
 if(r.now&&!r.paused){p+=Math.max(0,serverNow()-(r.set_at||serverNow()))}
 return p}
function initials(n){n=(n||'?').trim();
 var parts=n.split(/[\s._-]+/).filter(Boolean);
 return ((parts[0]||'?')[0]+(parts.length>1?parts[parts.length-1][0]:''))
        .toUpperCase()}
function driftClass(d){if(d==null)return 'd-sm';
 var a=Math.abs(d);return a<0.5?'d-ok':(a<3?'d-sm':'d-big')}
function driftText(d){if(d==null)return '&mdash;';
 return (d>=0?'+':'\u2212')+Math.abs(d).toFixed(1)+'s'}
function memberRow(m,threshold){
 var stale=(m.age||0)>8,cls='m'+(m.caching?' buf':'')+(stale?' stale':'');
 var big=m.drift!=null&&Math.abs(m.drift)>=threshold;
 var av='av '+(m.caching?'buf':(stale?'':(big?'norm':'sync')));
 var badges='';
 if(m.is_host)badges+='<span class="badge b-host">HOST \u00b7 LOCKED</span>';
 else if(m.is_owner)badges+='<span class="badge b-host">OPENED IT</span>';
 if(m.caching)badges+='<span class="badge b-buf">BUFFERING</span>';
 if(stale)badges+='<span class="badge b-old">NO POLL '+
   Math.round(m.age)+'s</span>';
 var sub=m.caching?'buffering &mdash; the party waits':
   (m.on_item?esc(m.file||''):'not on the party item');
 return '<div class="'+cls+'"><div class="'+av+'">'+esc(initials(m.name))+
  '</div><div class="mi"><div class="mn">'+esc(m.name)+badges+
  '</div><div class="ms">'+sub+'</div></div><div class="mp">'+
  (stale?'&mdash;':fmt(m.position))+'</div><div class="md '+
  driftClass(m.drift)+'">'+(stale?'&mdash;':driftText(m.drift))+
  '</div></div>'}
function roomHtml(r,threshold){
 var n=r.now||{},h='';
 if(r.buffer_hold_name){
  var since=r.buffer_hold_since?Math.round(serverNow()-r.buffer_hold_since):0;
  h+='<div class="banner warn"><h3>Buffer hold &mdash; held for '+
    esc(r.buffer_hold_name)+'</h3><p>Everyone paused at '+fmt(r.position)+
    ' \u00b7 '+since+'s so far. Resumes automatically when caching '+
    'clears.</p></div>'}
 // art can be unreachable from wherever the dashboard is open (a LAN
 // media server, or http art on an https page) — fall back to the
 // placeholder instead of leaving a broken image
 var glyph=(n.type=='song'?'&#9834;':'&#9654;');
 // prefer art the relay holds: reachable from anywhere, unlike an
 // origin URL that may point at someone else's LAN
 var src=n.art_url||n.art;
 var art=src?'<img class="art" src="'+esc(src)+'" alt="" '+
   'onerror="this.outerHTML=\\'<div class=&quot;art&quot;>'+glyph+
   '</div>\\'">':'<div class="art">'+glyph+'</div>';
 var pill=r.paused?'<span class="pill warn">&#10073;&#10073; PAUSED</span>':
   '<span class="pill ok">&#9654; PLAYING</span>';
 var title=n.title||r.item||'Nothing playing';
 var bits=[];
 if(n.type)bits.push(esc(n.type));
 if(n.artist)bits.push(esc([].concat(n.artist).join(', ')));
 if(n.album)bits.push(esc(n.album));
 if(n.show)bits.push(esc(n.show)+' S'+n.season+'E'+n.episode);
 if(n.year)bits.push(n.year);
 if(n.queue)bits.push('queue '+((n.queue_pos||0)+1)+'/'+n.queue);
 if(n.repeat&&n.repeat!='off')bits.push('repeat '+esc(n.repeat));
 if(n.shuffled)bits.push('shuffled');
 if(r.locked_by_name)bits.push('controls locked to '+esc(r.locked_by_name));
 var pos=livePos(r),total=r.duration||n.duration||0;
 var pct=total?Math.min(100,pos/total*100):0;
 var marks='';
 (r.members||[]).forEach(function(m){
   if(!m.on_item||m.drift==null||!total)return;
   var mp=Math.max(0,Math.min(100,(m.position||0)/total*100));
   var big=Math.abs(m.drift)>=threshold;
   marks+='<div class="mk'+(big?' off':'')+'" style="left:'+mp+'%" '+
     'title="'+esc(m.name)+'"></div>';
   if(big)marks+='<div class="mkd" style="left:'+mp+'%">'+
     driftText(m.drift)+'</div>'});
 h+='<div class="np">'+art+'<div class="npr"><div class="codeline">'+
  '<span class="code">'+esc(r.room)+'</span><span class="cap">room code'+
  '</span>'+pill+'</div><h2 class="title">'+esc(title)+'</h2>'+
  '<div class="meta">'+(bits.length?bits.join(' \u00b7 '):'&mdash;')+
  '</div><div class="tl"><div class="tlnum"><b>'+fmt(pos)+'</b><i>'+
  (total?fmt(total):'live')+'</i></div><div class="track">'+
  '<div class="rail6"></div><div class="fill" style="width:'+pct+'%">'+
  '</div>'+(total?'<div class="anchor" style="left:'+pct+'%"></div>':'')+
  marks+'</div><div class="legend"><span><i></i>server anchor</span>'+
  '<span><i class="b"></i>in sync</span><span><i class="a"></i>past '+
  threshold+'s drift</span></div></div></div></div>';
 var ms=r.members||[];
 h+='<div><div class="mhead"><h2>MEMBERS \u00b7 '+ms.length+'</h2>'+
  '<span>pruned after 15s without a poll</span></div>'+
  (ms.length?ms.map(function(m){return memberRow(m,threshold)}).join(''):
   '<div class="rempty" style="margin-top:10px">nobody connected</div>')+
  '</div>';
 return '<div class="room">'+h+'</div>'}
function render(){
 var main=document.getElementById('main');
 document.getElementById('proto').textContent=snap&&snap.protocol?
   'v'+snap.protocol:'\u2014';
 document.getElementById('uptime').textContent=snap?dur(snap.uptime):'\u2014';
 var rooms=(snap&&snap.rooms)||[];
 document.getElementById('nrooms').textContent=rooms.length;
 document.getElementById('updated').textContent=snap?
   clock(snap.server_time):'\u2014';
 var st=document.getElementById('state'),tx=document.getElementById('statetxt');
 var hold=rooms.some(function(r){return r.buffer_hold_name});
 st.className='pill '+(unreachable?'bad':(hold?'warn':'ok'));
 tx.textContent=unreachable?'UNREACHABLE':(hold?'BUFFER HOLD':'ONLINE');
 main.className='main'+(unreachable?' dim':'');
 var h='';
 if(unreachable){
  var ago=Math.round(Date.now()/1000-lastGoodAt);
  h+='<div class="banner bad"><h3>Relay unreachable</h3><p>'+
    (lastGoodAt?'Last good snapshot '+clock(lastGoodAt)+' ('+ago+
     's ago). Retrying every 2s &mdash; data below is frozen.':
     'Retrying every 2s.')+'</p><div class="sweep"><i></i></div></div>'}
 if(!rooms.length&&!unreachable){
  h+='<div class="empty"><div class="o"></div><h3>No active rooms</h3>'+
    '<p>Start a party in Kodi &mdash; it appears here within 2s.</p></div>'}
 var threshold=3;
 if(rooms.length)h+=roomHtml(rooms[0],threshold);
 main.innerHTML=h;
 var totals=rooms.reduce(function(a,r){
   a.c+=r.commands_per_min||0;a.s+=r.corrections||0;
   a.m+=(r.members||[]).length;a.q=Math.max(a.q,r.seq||0);return a},
   {c:0,s:0,m:0,q:0});
 document.getElementById('cpm').textContent=totals.c;
 document.getElementById('corr').textContent=totals.s;
 document.getElementById('nmem').textContent=totals.m;
 document.getElementById('seq').textContent=totals.q;
 var others=rooms.slice(1);
 document.getElementById('others').innerHTML=others.length?
  others.map(function(r){
   var p=r.paused?'<span class="pill warn">PAUSED</span>':
     '<span class="pill ok">PLAYING</span>';
   return '<div class="rcard"><span class="code">'+esc(r.room)+'</span> '+p+
    '<div class="t">'+esc((r.now&&r.now.title)||r.item||'idle')+'</div>'+
    '<div class="s">'+((r.members||[]).length)+' member'+
    ((r.members||[]).length==1?'':'s')+' \u00b7 '+
    fmt(livePos(r))+'</div></div>'}).join(''):
  '<div class="rempty">No other active rooms</div>';
 first=false}
function drawBars(){
 var el=document.getElementById('bars');
 if(!rtt.length){el.innerHTML='';return}
 var peak=Math.max.apply(null,rtt),h='';
 rtt.forEach(function(v,i){
   var cls=i==rtt.length-1?'last':(v>=peak&&peak>0?'peak':'');
   h+='<div class="'+cls+'" style="height:'+
     Math.max(2,Math.round(v/Math.max(peak,1)*46))+'px"></div>'});
 el.innerHTML=h;
 document.getElementById('rcap').textContent=
  'poll round-trip, last 90s \u00b7 peak '+Math.round(peak)+' ms';
 document.getElementById('rtt').textContent=
  Math.round(rtt[rtt.length-1])+' ms'}
async function tick(){
 var t0=performance.now();
 try{
  var r=await fetch('/status.json',{cache:'no-store'});
  var d=await r.json();
  var ms=performance.now()-t0;
  rtt.push(ms);if(rtt.length>45)rtt.shift();
  clockSkew=(d.server_time||0)-Date.now()/1000;
  snap=d;lastGoodAt=Date.now()/1000;unreachable=false;
  drawBars()}
 catch(e){unreachable=true}
 render()}
document.getElementById('mark').innerHTML=
 '<img src="/icon.png" alt="" onerror="this.remove()">';
tick();setInterval(tick,2000);setInterval(render,250);
</script></body></html>
"""


class RoomState:
    """Thread-safe state for a single party."""

    def __init__(self, room_code):
        self.lock = threading.Lock()
        self.room_code = room_code
        self.members = {}   # member_id -> dict
        self.seq = 0
        self.item = None    # {'file', 'label', 'plugin', + library ids} or None
        self.position = 0.0
        self.paused = False
        self.speed = 1.0
        self.set_at = time.time()
        self.set_by = None
        self.locked_by = None    # member allowed to control, or None = anyone
        self.buffer_hold = None  # member we auto-paused for, or None
        self.buffer_hold_since = 0.0
        self.command_times = []  # recent command timestamps, for /status
        # cover art bytes uploaded by the announcer, so the dashboard can
        # show art the viewer could never fetch itself (a LAN-only media
        # server) without ever handling that server's credentials
        self.art = None          # (art_id, content_type, bytes)

    # -- members -----------------------------------------------------------

    def join(self, name):
        member_id = uuid.uuid4().hex[:12]
        with self.lock:
            self.members[member_id] = {
                'name': name or 'Kodi',
                'joined': time.time(),
                'last_seen': time.time(),
                'position': 0.0,
                'paused': True,
                'file': '',
                'caching': False,
                'on_item': False,
                'corrections': 0,
            }
        return member_id

    def leave(self, member_id):
        with self.lock:
            self.members.pop(member_id, None)

    def touch(self, member_id, position=None, paused=None, file=None,
              caching=None, on_item=None, corrections=None):
        with self.lock:
            m = self.members.get(member_id)
            if m is None:
                return False
            m['last_seen'] = time.time()
            if position is not None:
                m['position'] = float(position)
            if paused is not None:
                m['paused'] = bool(paused)
            if file is not None:
                m['file'] = str(file)
            if caching is not None:
                m['caching'] = bool(caching)
            if on_item is not None:
                m['on_item'] = bool(on_item)
            if corrections is not None:
                try:
                    m['corrections'] = int(corrections)
                except (TypeError, ValueError):
                    pass
            return True

    def _prune(self):
        # caller holds lock
        cutoff = time.time() - MEMBER_TIMEOUT
        stale = [mid for mid, m in self.members.items()
                 if m['last_seen'] < cutoff]
        for mid in stale:
            del self.members[mid]
        if self.locked_by and self.locked_by not in self.members:
            self.locked_by = None
        if self.buffer_hold and self.buffer_hold not in self.members:
            self.buffer_hold = None

    def buffer_check(self):
        """Pause the party while a member playing the item is buffering,
        and resume once nobody is. A manual command in between cancels
        the hold, so a deliberate pause is never overridden."""
        with self.lock:
            if not self.item:
                self.buffer_hold = None
                return
            now = time.time()
            caching = [mid for mid, m in self.members.items()
                       if m.get('caching') and m.get('on_item')]
            if caching and not self.paused:
                self.position += max(0.0, now - self.set_at) \
                    * (self.speed or 1.0)
                self.paused = True
                self.set_at = now
                self.set_by = caching[0]
                self.buffer_hold = caching[0]
                self.buffer_hold_since = now
                self.seq += 1
            elif self.buffer_hold and self.paused and not caching:
                self.paused = False
                self.set_at = now
                self.set_by = self.buffer_hold
                self.buffer_hold = None
                self.seq += 1
            elif self.buffer_hold and not self.paused:
                self.buffer_hold = None

    # -- playback ----------------------------------------------------------

    # keys an 'open' command may attach beyond file/label/plugin —
    # library identity so guests can match their own copy, plus the
    # queue the item is playing from
    ITEM_EXTRA_KEYS = ('type', 'ids', 'title', 'year', 'show', 'season',
                       'episode', 'artist', 'album', 'playlist',
                       'playlist_pos', 'repeat', 'shuffled', 'duration',
                       'art')
    MAX_PLAYLIST = 100

    def _clean_playlist(self, value):
        """Sanitized queue entries (file/label + identity), or []."""
        if not isinstance(value, list):
            return []
        clean = []
        for e in value[:self.MAX_PLAYLIST]:
            if not isinstance(e, dict):
                continue
            entry = {'file': str(e.get('file') or ''),
                     'label': str(e.get('label') or '')}
            for field in ('type', 'ids', 'title', 'year', 'show',
                          'season', 'episode', 'artist', 'album'):
                v = e.get(field)
                if v not in (None, '', {}):
                    entry[field] = v
            clean.append(entry)
        return clean if len(clean) >= 2 else []

    def command(self, member_id, cmd, payload):
        """Apply a control command and bump the sequence number.
        Returns True, False (bad command), or 'locked'."""
        now = time.time()
        with self.lock:
            if member_id not in self.members:
                return False
            if self.locked_by and member_id != self.locked_by:
                return 'locked'
            if cmd == 'modes':
                # repeat/shuffle (and a re-shuffled queue) change without
                # touching the playback anchor — position must not jump
                if not self.item:
                    return False
                changes = payload.get('item') or {}
                if 'repeat' in changes:
                    self.item['repeat'] = str(changes.get('repeat')
                                              or 'off')
                if 'shuffled' in changes:
                    if changes['shuffled']:
                        self.item['shuffled'] = True
                    else:
                        self.item.pop('shuffled', None)
                playlist = self._clean_playlist(changes.get('playlist'))
                if playlist:
                    self.item['playlist'] = playlist
                    pos = changes.get('playlist_pos')
                    if isinstance(pos, int) and 0 <= pos < len(playlist):
                        self.item['playlist_pos'] = pos
                self.set_by = member_id
                self.seq += 1
                return True
            position = float(payload.get('position') or 0.0)
            if cmd == 'open':
                item = payload.get('item') or {}
                if not item.get('file'):
                    return False
                stored = {'file': str(item['file']),
                          'label': str(item.get('label') or ''),
                          'plugin': str(item.get('plugin') or '')}
                for key in self.ITEM_EXTRA_KEYS:
                    value = item.get(key)
                    if value in (None, '', {}):
                        continue
                    if key == 'playlist':
                        value = self._clean_playlist(value)
                        if not value:
                            continue
                    stored[key] = value
                self.item = stored
                self.position = position
                self.paused = False
                self.speed = 1.0
                # opener may claim sole control of the party
                self.locked_by = member_id if payload.get('lock') else None
            elif cmd == 'play':
                self.position = position
                self.paused = False
            elif cmd == 'pause':
                self.position = position
                self.paused = True
            elif cmd == 'seek':
                self.position = position
            elif cmd == 'stop':
                self.item = None
                self.position = 0.0
                self.paused = False
                self.locked_by = None
            else:
                return False
            self.buffer_hold = None  # a deliberate command wins over a hold
            self.set_at = now
            self.set_by = member_id
            self.seq += 1
            self.command_times.append(now)
            self.command_times = [t for t in self.command_times
                                  if now - t <= 60.0]
            return True

    # -- persistence (used by the standalone relay) ------------------------

    def state_dict(self):
        """Serializable playback state. Members are deliberately not
        included — they re-poll and auto-rejoin within seconds."""
        with self.lock:
            return {'code': self.room_code,
                    'item': dict(self.item) if self.item else None,
                    'position': self.position,
                    'paused': self.paused,
                    'speed': self.speed,
                    'set_at': self.set_at,
                    'seq': self.seq}

    @classmethod
    def from_state(cls, data):
        room = cls(str(data.get('code') or ''))
        item = data.get('item')
        room.item = dict(item) if item else None
        room.position = float(data.get('position') or 0.0)
        room.paused = bool(data.get('paused'))
        room.speed = float(data.get('speed') or 1.0)
        room.set_at = float(data.get('set_at') or time.time())
        room.seq = int(data.get('seq') or 0)
        # locks and holds reference member ids that no longer exist
        room.locked_by = None
        room.buffer_hold = None
        return room

    def set_art(self, member_id, content_type, data):
        """Store cover art for the current item. The id is opaque —
        deriving it from the room code would leak the code on a page
        that deliberately masks it."""
        with self.lock:
            if member_id not in self.members:
                return None
            art_id = uuid.uuid4().hex[:16]
            self.art = (art_id, content_type, data)
            return art_id

    def get_art(self, art_id):
        art = self.art
        if art and art[0] == art_id:
            return art[1], art[2]
        return None, None

    def snapshot(self):
        now = time.time()
        with self.lock:
            self._prune()
            expected = self.position
            if self.item and not self.paused:
                expected += max(0.0, now - self.set_at) * (self.speed or 1.0)
            names = {mid: m['name'] for mid, m in self.members.items()}
            return {
                'seq': self.seq,
                'item': dict(self.item) if self.item else None,
                'position': self.position,
                'paused': self.paused,
                'speed': self.speed,
                'set_at': self.set_at,
                'set_by': self.set_by,
                'locked_by': self.locked_by,
                # names, never ids — the dashboard is world-readable
                'locked_by_name': names.get(self.locked_by),
                'buffer_hold_name': names.get(self.buffer_hold),
                'buffer_hold_since': (self.buffer_hold_since
                                      if self.buffer_hold else 0.0),
                'art_id': self.art[0] if self.art else None,
                'commands_per_min': len(self.command_times),
                'corrections': sum(m.get('corrections') or 0
                                   for m in self.members.values()),
                'members': [
                    {
                        'name': m['name'],
                        'position': m['position'],
                        'paused': m['paused'],
                        'file': m['file'],
                        'caching': m.get('caching', False),
                        'on_item': m.get('on_item', False),
                        'age': max(0.0, now - m['last_seen']),
                        'corrections': m.get('corrections') or 0,
                        # drift only means something on the party's item
                        'drift': (round(float(m['position']) - expected, 2)
                                  if m.get('on_item') and self.item
                                  else None),
                        'is_owner': mid == self.set_by,
                        'is_host': bool(self.locked_by) and
                        mid == self.locked_by,
                    }
                    for mid, m in self.members.items()
                ],
            }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'WatchParty/1.0'

    # the RelayServer sets this on the handler class
    room = None
    show_codes = False  # full room codes on /status (they're the tokens)

    def log_message(self, fmt, *args):  # silence per-request stderr noise
        pass

    def iter_rooms(self):
        """[(code, RoomState), ...] for the dashboard. The embedded relay
        has one room; the standalone registry overrides this."""
        room = self.room
        return [(room.room_code, room)] if room is not None else []

    def lookup_room(self, code):
        """Map a room code to a RoomState, or an error string to reject.

        The embedded (in-Kodi) relay hosts exactly one room; the
        standalone relay overrides this with a multi-room registry.
        """
        room = self.room
        if room is not None and code == room.room_code:
            return room
        return 'wrong room code'

    def _send(self, code, obj):
        body = json.dumps(obj).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_body(self):
        length = int(self.headers.get('Content-Length') or 0)
        if length <= 0 or length > MAX_BODY:
            return None
        try:
            return json.loads(self.rfile.read(length).decode('utf-8'))
        except Exception:
            return None

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/ping':
            self._send(200, {'ok': True, 'app': 'watchparty',
                             'protocol': PROTOCOL_VERSION,
                             'server_time': time.time()})
        elif path in ('/', '/status'):
            # the dashboard is the landing page: typing the relay's
            # address into a browser should show the party, not JSON
            body = DASH_HTML.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif path == '/status.json':
            self._send(200, {'ok': True,
                             'rooms': rooms_stats(self.iter_rooms(),
                                                  self.show_codes),
                             'protocol': PROTOCOL_VERSION,
                             'uptime': time.time() - START_TIME,
                             'server_time': time.time()})
        elif path.startswith('/art/'):
            art_id = path[len('/art/'):]
            for _code, room in self.iter_rooms():
                content_type, data = room.get_art(art_id)
                if data:
                    self.send_response(200)
                    self.send_header('Content-Type', content_type)
                    self.send_header('Content-Length', str(len(data)))
                    # ids are per upload, so this can cache hard
                    self.send_header('Cache-Control', 'max-age=3600')
                    self.end_headers()
                    self.wfile.write(data)
                    return
            self._send(404, {'ok': False, 'error': 'no art'})
        elif path == '/icon.png':
            # present next to the addon; absent in the slim relay image,
            # where the page falls back to a drawn mark
            try:
                with open(ICON_PATH, 'rb') as f:
                    body = f.read()
            except OSError:
                self._send(404, {'ok': False, 'error': 'no icon'})
                return
            self.send_response(200)
            self.send_header('Content-Type', 'image/png')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'max-age=86400')
            self.end_headers()
            self.wfile.write(body)
        else:
            self._send(404, {'ok': False, 'error': 'not found'})

    def _handle_art_upload(self):
        """Raw image bytes from the announcer — kept out of the JSON
        path because artwork is far larger than a control message."""
        try:
            length = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            length = 0
        if length <= 0 or length > MAX_ART:
            self._send(413, {'ok': False, 'error': 'bad art size'})
            return
        room = self.lookup_room(str(self.headers.get('X-Room') or ''))
        if not isinstance(room, RoomState):
            # drain so the connection can be reused
            self.rfile.read(length)
            self._send(403, {'ok': False, 'error': room or 'wrong room'})
            return
        content_type = self.headers.get('Content-Type') or ''
        if not content_type.startswith('image/'):
            self.rfile.read(length)
            self._send(400, {'ok': False, 'error': 'not an image'})
            return
        data = self.rfile.read(length)
        art_id = room.set_art(str(self.headers.get('X-Member') or ''),
                              content_type, data)
        if not art_id:
            self._send(410, {'ok': False, 'error': 'not a member'})
            return
        self._send(200, {'ok': True, 'art': '/art/' + art_id,
                         'server_time': time.time()})

    def do_POST(self):
        if self.path.split('?', 1)[0] == '/art':
            self._handle_art_upload()
            return
        data = self._read_body()
        if data is None:
            self._send(400, {'ok': False, 'error': 'bad request'})
            return
        room = self.lookup_room(str(data.get('room') or ''))
        if not isinstance(room, RoomState):
            self._send(403, {'ok': False,
                             'error': room or 'wrong room code'})
            return

        now = time.time()
        if self.path == '/join':
            member_id = room.join(str(data.get('name') or ''))
            self._send(200, {'ok': True, 'member_id': member_id,
                             'protocol': PROTOCOL_VERSION,
                             'state': room.snapshot(), 'server_time': now})
            return

        member_id = str(data.get('member_id') or '')
        if self.path == '/leave':
            room.leave(member_id)
            self._send(200, {'ok': True, 'server_time': now})
        elif self.path == '/command':
            result = room.command(member_id, str(data.get('cmd') or ''), data)
            if result == 'locked':
                self._send(403, {'ok': False,
                                 'error': 'controls are locked by the host',
                                 'server_time': now})
                return
            if not result:
                self._send(400, {'ok': False, 'error': 'bad command',
                                 'server_time': now})
                return
            self._send(200, {'ok': True, 'state': room.snapshot(),
                             'server_time': now})
        elif self.path == '/poll':
            if not room.touch(member_id,
                              position=data.get('position'),
                              paused=data.get('paused'),
                              file=data.get('file'),
                              caching=data.get('caching'),
                              on_item=data.get('on_item'),
                              corrections=data.get('corrections')):
                self._send(410, {'ok': False, 'error': 'not a member',
                                 'server_time': now})
                return
            room.buffer_check()
            self._send(200, {'ok': True, 'state': room.snapshot(),
                             'server_time': now})
        else:
            self._send(404, {'ok': False, 'error': 'not found'})


class RelayServer:
    """Owns the ThreadingHTTPServer and its serve_forever thread."""

    def __init__(self, port, room_code):
        self.room = RoomState(room_code)
        handler = type('BoundHandler', (_Handler,), {'room': self.room})
        self._httpd = ThreadingHTTPServer(('0.0.0.0', port), handler)
        self._httpd.daemon_threads = True
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        name='watchparty-relay', daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        try:
            self._httpd.shutdown()
            self._httpd.server_close()
        except Exception:
            pass
        self._thread.join(timeout=5)
