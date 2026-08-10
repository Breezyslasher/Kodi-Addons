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
import json
import socket

import xbmc

VERSION = 1
TIMEOUT = 30

# The route Kodi hands us in sys.argv[0] for each of the two calls
CHANNELS_ROUTE = '/iptv/channels'
EPG_ROUTE = '/iptv/epg'


def send(port, data):
    """Hand a JSON document back to IPTV Manager over its callback socket."""
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    connection.settimeout(TIMEOUT)
    try:
        connection.connect(('127.0.0.1', int(port)))
        connection.sendall(json.dumps(data).encode('utf-8'))
    finally:
        connection.close()


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
                        'name': channel.get('title'),
                        'logo': firstImage(channel.get('images'), 'thumbnail', 'poster', 'landscape'),
                        'stream': playPath % channelId,
                        'group': groups.get(channelId)})
    return {'version': VERSION, 'streams': streams}


def epg(api, channelIds):
    """The JSON-EPG document for the given channels.

    Tubi timestamps its programmes in ISO-8601 UTC already, which is what
    IPTV Manager wants, so they go across untouched.
    """
    guide = {}
    for row in api.liveProgramming(channelIds):
        programs = []
        for program in row.get('programs') or []:
            entry = {'start': program.get('start_time'),
                     'stop': program.get('end_time'),
                     'title': program.get('title')}
            if program.get('description'):
                entry['description'] = program['description']
            if program.get('episode_title'):
                entry['subtitle'] = program['episode_title']
            episode = episodeOf(program)
            if episode is not None:
                entry['episode'] = episode
            image = firstImage(program.get('images'), 'thumbnail', 'landscape', 'poster')
            if image is not None:
                entry['image'] = image
            tags = program.get('tags') or []
            if tags:
                entry['genre'] = tags[0]
            programs.append(entry)
        guide[str(row.get('content_id'))] = programs
    return {'version': VERSION, 'epg': guide}


def handle(route, port, api, playPath, log=None):
    """Answer one IPTV Manager call. Returns True when it was ours."""
    if route.endswith(CHANNELS_ROUTE):
        data = channels(api, playPath)
    elif route.endswith(EPG_ROUTE):
        lineUp, _ = api.liveChannels()
        data = epg(api, [str(channel.get('id')) for channel in lineUp])
    else:
        return False

    if port is None:
        # Nothing to reply to - the call did not come from IPTV Manager
        xbmc.log(msg='plugin.video.tubitv : iptv call without a port', level=xbmc.LOGWARNING)
        return True
    send(port, data)
    if log is not None:
        log(''.join(['iptv manager served ', route]))
    return True
