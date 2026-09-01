# -*- coding: utf-8 -*-
# IPTV Manager integration
#
# https://github.com/add-ons/service.iptv.manager/wiki/Integration
#
# IPTV Manager finds this addon through the iptv.* settings, then calls
# plugin://plugin.video.tubitv/iptv/channels?port=N and .../iptv/epg?port=N.
# Each call is expected to open a TCP connection back to 127.0.0.1 on that
# port and write a JSON document - JSON-STREAMS for the channel line-up,
# JSON-EPG for the guide.
#
import html
import json
import re
import socket
import time

import xbmc

VERSION = 1
TIMEOUT = 30

# The route Kodi hands us in sys.argv[0] for each of the two calls
CHANNELS_ROUTE = '/iptv/channels'
EPG_ROUTE = '/iptv/epg'


def send(port, produce):
    """Hand a JSON document back to IPTV Manager over its callback socket.

    IPTV Manager allows ten seconds for the addon to connect and then waits
    as long as the connection stays open, so the socket is opened *before*
    the document is built. Building the guide takes a request per batch of
    channels, which is far more than ten seconds' work - doing it first is
    what makes IPTV Manager give up and leave the guide empty.
    """
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection.settimeout(TIMEOUT)
    try:
        connection.connect(('127.0.0.1', int(port)))
        connection.settimeout(None)
        connection.sendall(json.dumps(produce()).encode('utf-8'))
    finally:
        connection.close()


TAG = re.compile(r'<[^<>]{0,120}>')
# XML 1.0 forbids most control characters outright
CONTROL = re.compile(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]')
ANGLE = re.compile(r'[<>]')


def plainText(value):
    """Turn Tubi's guide text into something safe to hand on.

    Tubi writes its titles and descriptions as HTML: entity-escaped, and
    occasionally with markup in them. What consumes this JSON turns it into
    XMLTV, and HTML entities are not XML entities - `&eacute;` is undefined
    in XML and takes the whole guide down with it, and a stray tag breaks
    the document just as thoroughly.

    So markup goes, entities are resolved to the characters they stand for,
    and anything left that could still be read as a tag is dropped. A bare
    angle bracket in prose is a fair price for a guide that parses.
    """
    if not value:
        return value
    text = str(value)
    # Entities can hide markup and markup can hide entities, so do both twice
    for _ in range(2):
        text = TAG.sub('', text)
        text = html.unescape(text)
    text = CONTROL.sub('', text)
    return ANGLE.sub('', text).strip()


def firstImage(images, *keys):
    for key in keys:
        values = (images or {}).get(key)
        if values:
            return values[0]
    return None


def episodeOf(program):
    """An S00E00 label, when the programme carries both numbers."""
    season = program.get('season_number')
    episode = program.get('episode_number')
    if not season or not episode:
        return None
    return 'S%02dE%02d' % (int(season), int(episode))


def channels(api, playPath):
    """The JSON-STREAMS document for Tubi's linear channels."""
    lineUp, groups = api.liveChannels()
    streams = []
    for channel in lineUp:
        channelId = str(channel.get('id'))
        streams.append({'id': channelId,
                        'name': plainText(channel.get('title')),
                        'logo': firstImage(channel.get('images'), 'thumbnail', 'poster', 'landscape'),
                        'stream': playPath % channelId,
                        'group': groups.get(channelId)})
    return {'version': VERSION, 'streams': streams}


def epg(api, channelIds):
    """The JSON-EPG document for the given channels.

    Tubi timestamps its programmes in ISO-8601 UTC already, which is what
    IPTV Manager wants, so they go across untouched. A batch that fails is
    skipped rather than abandoning the whole guide.
    """
    guide = {}
    for row in api.liveProgramming(channelIds, tolerant=True):
        programs = []
        for program in row.get('programs') or []:
            entry = {'start': program.get('start_time'),
                     'stop': program.get('end_time'),
                     'title': plainText(program.get('title'))}
            if program.get('description'):
                entry['description'] = plainText(program['description'])
            if program.get('episode_title'):
                entry['subtitle'] = plainText(program['episode_title'])
            episode = episodeOf(program)
            if episode is not None:
                entry['episode'] = episode
            image = firstImage(program.get('images'), 'thumbnail', 'landscape', 'poster')
            if image is not None:
                entry['image'] = image
            tags = program.get('tags') or []
            if tags:
                entry['genre'] = plainText(tags[0])
            programs.append(entry)
        guide[str(row.get('content_id'))] = programs
    return {'version': VERSION, 'epg': guide}


def guideFor(api):
    lineUp, _ = api.liveChannels()
    return epg(api, [str(channel.get('id')) for channel in lineUp])


def summarise(route, data, seconds):
    if 'streams' in data:
        what = '%d channels' % len(data['streams'])
    else:
        guide = data.get('epg') or {}
        what = '%d channels, %d programmes' % (len(guide),
                                               sum(len(v) for v in guide.values()))
    return 'iptv served %s : %s in %.1fs' % (route, what, seconds)


def handle(route, port, api, playPath, log=None):
    """Answer one IPTV Manager call. Returns True when it was ours.

    The work is deferred into a callable so the socket can be opened first -
    see send().
    """
    if route.endswith(CHANNELS_ROUTE):
        build = lambda: channels(api, playPath)
    elif route.endswith(EPG_ROUTE):
        build = lambda: guideFor(api)
    else:
        return False

    if port is None:
        # Nothing to reply to - the call did not come from IPTV Manager
        xbmc.log(msg='plugin.video.tubitv : iptv call without a port', level=xbmc.LOGWARNING)
        return True

    # Report what was handed over, so a guide that arrives empty can be told
    # apart from one that was never asked for.
    outcome = {}

    def produce():
        started = time.time()
        data = build()
        outcome['message'] = summarise(route, data, time.time() - started)
        return data

    try:
        send(port, produce)
    finally:
        message = outcome.get('message', ''.join(['iptv failed to serve ', route]))
        xbmc.log(msg=''.join(['plugin.video.tubitv : ', message]), level=xbmc.LOGINFO)
        if log is not None:
            log(message)
    return True
