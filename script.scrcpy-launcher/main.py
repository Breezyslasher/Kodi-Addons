# -*- coding: utf-8 -*-
"""
scrcpy Launcher for Kodi - USB and WiFi
"""

import os
import subprocess
import platform
import time
import xbmcaddon
import xbmcgui
import xbmc

ADDON = xbmcaddon.Addon(id='script.scrcpy-launcher')
ADDON_PATH = ADDON.getAddonInfo('path')
ADDON_NAME = ADDON.getAddonInfo('name')

ARCH = platform.machine()

if ARCH == 'aarch64':
    BIN_PATH = os.path.join(ADDON_PATH, 'bin', 'lib_arm64')
else:
    BIN_PATH = os.path.join(ADDON_PATH, 'bin', 'lib_x64')

ADB_PATH = os.path.join(BIN_PATH, 'adb')
SCRCPY_PATH = os.path.join(BIN_PATH, 'scrcpy')
SCRCPY_SERVER = os.path.join(BIN_PATH, 'scrcpy-server')

IS_LIBREELEC = os.path.exists('/storage/.kodi')


def log(msg):
    xbmc.log(f"[{ADDON_NAME}] {msg}", xbmc.LOGINFO)


def get_setting(sid, stype='string'):
    val = ADDON.getSetting(sid)
    if stype == 'bool':
        return val.lower() == 'true'
    elif stype == 'int':
        try:
            return int(val) if val else 0
        except:
            return 0
    return val


def is_flatpak():
    return '.var/app/' in ADDON_PATH


def get_render():
    r = get_setting('render_driver', 'int')
    return {0: '', 1: 'opengles2', 2: 'opengl', 3: 'software'}.get(r, 'opengles2')


def get_extra_args():
    args = []
    
    fps = get_setting('fps', 'int')
    if fps > 0:
        args.append(f'--max-fps {fps}')
    
    size = get_setting('size', 'int')
    if size > 0:
        args.append(f'--max-size {size}')
    
    bitrate = get_setting('bitrate')
    if bitrate:
        args.append(f'--video-bit-rate {bitrate}')
    
    vc = get_setting('video_codec', 'int')
    codecs = {0: 'h264', 1: 'h265', 2: 'av1'}
    if vc in codecs:
        args.append(f'--video-codec {codecs[vc]}')
    
    crop = get_setting('crop')
    if crop:
        args.append(f'--crop {crop}')
    
    if get_setting('fullscreen', 'bool'):
        args.append('--fullscreen')
    
    if get_setting('audio_enabled', 'bool'):
        ac = get_setting('audio_codec', 'int')
        acodecs = {0: 'opus', 1: 'aac', 2: 'flac', 3: 'raw'}
        args.append(f'--audio-codec {acodecs.get(ac, "opus")}')
    else:
        args.append('--no-audio')
    
    if get_setting('stay_awake', 'bool'):
        args.append('--stay-awake')
    
    if get_setting('turn_screen_off', 'bool'):
        args.append('--turn-screen-off')
    
    # Quit shortcut key
    shortcut = get_setting('shortcut_mod', 'int')
    shortcut_map = {0: '', 1: 'lalt', 2: 'lsuper', 3: 'ralt', 4: 'rsuper'}
    if shortcut in shortcut_map and shortcut_map[shortcut]:
        args.append(f'--shortcut-mod {shortcut_map[shortcut]}')
    
    extra = get_setting('extra_args')
    if extra:
        args.append(extra)
    
    return ' '.join(args)


def stream_usb_libreelec():
    """Stream USB device on LibreELEC"""
    log("=== LibreELEC USB Stream ===")
    
    os.chmod(ADB_PATH, 0o755)
    os.chmod(SCRCPY_PATH, 0o755)
    
    render = get_render()
    extra = get_extra_args()
    
    # Build scrcpy command
    scrcpy_cmd = SCRCPY_PATH
    if render:
        scrcpy_cmd += f" --render-driver {render}"
    scrcpy_cmd += " -d"
    if extra:
        scrcpy_cmd += f" {extra}"
    
    log(f"scrcpy cmd: {scrcpy_cmd}")
    
    launcher = "/storage/.kodi/temp/scrcpy_launch.sh"
    
    script = f'''#!/bin/sh
LOG="/storage/.kodi/temp/scrcpy.log"
echo "=== USB START ===" > $LOG
date >> $LOG

systemctl stop kodi
sleep 1

{ADB_PATH} kill-server >> $LOG 2>&1
{ADB_PATH} start-server >> $LOG 2>&1
sleep 1

echo "Running: {scrcpy_cmd}" >> $LOG
{scrcpy_cmd} >> $LOG 2>&1
echo "Exit: $?" >> $LOG

systemctl start kodi
'''
    
    with open(launcher, 'w') as f:
        f.write(script)
    os.chmod(launcher, 0o755)
    
    xbmcgui.Dialog().notification(ADDON_NAME, "Starting...", time=1000)
    
    os.system(f'systemd-run /bin/sh {launcher}')


def stream_wifi_libreelec():
    """Stream WiFi device on LibreELEC"""
    ip = get_setting('ip_address')
    port = get_setting('port', 'int') or 5555
    
    if not ip:
        xbmcgui.Dialog().ok("Error", "Set IP address in Settings")
        return
    
    log(f"=== LibreELEC WiFi Stream to {ip}:{port} ===")
    
    os.chmod(ADB_PATH, 0o755)
    os.chmod(SCRCPY_PATH, 0o755)
    
    render = get_render()
    extra = get_extra_args()
    
    scrcpy_cmd = SCRCPY_PATH
    if render:
        scrcpy_cmd += f" --render-driver {render}"
    scrcpy_cmd += f" -s {ip}:{port}"
    if extra:
        scrcpy_cmd += f" {extra}"
    
    log(f"scrcpy cmd: {scrcpy_cmd}")
    
    launcher = "/storage/.kodi/temp/scrcpy_launch.sh"
    
    script = f'''#!/bin/sh
LOG="/storage/.kodi/temp/scrcpy.log"
echo "=== WIFI START ===" > $LOG
date >> $LOG

systemctl stop kodi
sleep 1

{ADB_PATH} kill-server >> $LOG 2>&1
{ADB_PATH} start-server >> $LOG 2>&1
sleep 1

{ADB_PATH} connect {ip}:{port} >> $LOG 2>&1
sleep 1

echo "Running: {scrcpy_cmd}" >> $LOG
{scrcpy_cmd} >> $LOG 2>&1
echo "Exit: $?" >> $LOG

systemctl start kodi
'''
    
    with open(launcher, 'w') as f:
        f.write(script)
    os.chmod(launcher, 0o755)
    
    xbmcgui.Dialog().notification(ADDON_NAME, f"Connecting to {ip}...", time=1000)
    
    os.system(f'systemd-run /bin/sh {launcher}')


def stream_usb_desktop():
    """Stream USB device on desktop Linux"""
    log("=== Desktop USB Stream ===")
    
    os.chmod(ADB_PATH, 0o755)
    os.chmod(SCRCPY_PATH, 0o755)
    
    env = os.environ.copy()
    env['PATH'] = f"{BIN_PATH}:{env.get('PATH', '')}"
    env['LD_LIBRARY_PATH'] = BIN_PATH
    env['SCRCPY_SERVER_PATH'] = SCRCPY_SERVER
    env['ADB'] = ADB_PATH
    
    os.system(f'{ADB_PATH} kill-server')
    subprocess.run([ADB_PATH, 'start-server'], env=env, capture_output=True, timeout=10)
    time.sleep(1)
    
    cmd = [SCRCPY_PATH]
    
    render = get_render()
    if render:
        cmd.extend(['--render-driver', render])
    
    cmd.append('-d')
    
    extra = get_extra_args()
    if extra:
        cmd.extend(extra.split())
    
    log(f"Running: {' '.join(cmd)}")
    
    try:
        if is_flatpak():
            fcmd = ["flatpak-spawn", "--host"]
            fcmd.append(f'--env=PATH={env["PATH"]}')
            fcmd.append(f'--env=LD_LIBRARY_PATH={BIN_PATH}')
            fcmd.append(f'--env=SCRCPY_SERVER_PATH={SCRCPY_SERVER}')
            fcmd.append(f'--env=ADB={ADB_PATH}')
            fcmd.extend(cmd)
            subprocess.run(fcmd, timeout=3600)
        else:
            subprocess.run(cmd, env=env, timeout=3600)
    except:
        pass


def stream_wifi_desktop():
    """Stream WiFi device on desktop Linux"""
    ip = get_setting('ip_address')
    port = get_setting('port', 'int') or 5555
    
    if not ip:
        xbmcgui.Dialog().ok("Error", "Set IP address in Settings")
        return
    
    log(f"=== Desktop WiFi Stream to {ip}:{port} ===")
    
    os.chmod(ADB_PATH, 0o755)
    os.chmod(SCRCPY_PATH, 0o755)
    
    env = os.environ.copy()
    env['PATH'] = f"{BIN_PATH}:{env.get('PATH', '')}"
    env['LD_LIBRARY_PATH'] = BIN_PATH
    env['SCRCPY_SERVER_PATH'] = SCRCPY_SERVER
    env['ADB'] = ADB_PATH
    
    os.system(f'{ADB_PATH} kill-server')
    subprocess.run([ADB_PATH, 'start-server'], env=env, capture_output=True, timeout=10)
    time.sleep(1)
    
    subprocess.run([ADB_PATH, 'connect', f'{ip}:{port}'], env=env, capture_output=True, timeout=10)
    time.sleep(1)
    
    cmd = [SCRCPY_PATH]
    
    render = get_render()
    if render:
        cmd.extend(['--render-driver', render])
    
    cmd.extend(['-s', f'{ip}:{port}'])
    
    extra = get_extra_args()
    if extra:
        cmd.extend(extra.split())
    
    log(f"Running: {' '.join(cmd)}")
    
    try:
        if is_flatpak():
            fcmd = ["flatpak-spawn", "--host"]
            fcmd.append(f'--env=PATH={env["PATH"]}')
            fcmd.append(f'--env=LD_LIBRARY_PATH={BIN_PATH}')
            fcmd.append(f'--env=SCRCPY_SERVER_PATH={SCRCPY_SERVER}')
            fcmd.append(f'--env=ADB={ADB_PATH}')
            fcmd.extend(cmd)
            subprocess.run(fcmd, timeout=3600)
        else:
            subprocess.run(cmd, env=env, timeout=3600)
    except:
        pass


def main():
    log(f"Start - Arch:{ARCH} LibreELEC:{IS_LIBREELEC}")
    
    if not os.path.exists(SCRCPY_PATH):
        xbmcgui.Dialog().ok("Error", f"scrcpy not found:\n{SCRCPY_PATH}")
        return
    
    menu = ['Stream USB Device', 'Stream WiFi Device', 'Settings']
    sel = xbmcgui.Dialog().contextmenu(menu)
    
    if sel == 0:
        if IS_LIBREELEC:
            stream_usb_libreelec()
        else:
            stream_usb_desktop()
    elif sel == 1:
        if IS_LIBREELEC:
            stream_wifi_libreelec()
        else:
            stream_wifi_desktop()
    elif sel == 2:
        ADDON.openSettings()


if __name__ == '__main__':
    main()
