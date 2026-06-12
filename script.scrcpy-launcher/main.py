# -*- coding: utf-8 -*-
"""
scrcpy Launcher for Kodi - USB, WiFi and Samsung DeX

DeX support inspired by TuxDex (https://github.com/semarainc/TuxDex),
but with no miraclecast/root/network changes: the DeX desktop is just
another Android display, mirrored with scrcpy --display-id (auto-
detected). Virtual Desktop mode (--new-display) provides a desktop
without an active DeX session on One UI 7 / Android 15+.
"""

import os
import re
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


# ============================================================================
# SCRCPY LAUNCHERS (phone screen, Samsung DeX, virtual desktop)
# ============================================================================

def build_env():
    env = os.environ.copy()
    env['PATH'] = f"{BIN_PATH}:{env.get('PATH', '')}"
    env['LD_LIBRARY_PATH'] = BIN_PATH
    env['SCRCPY_SERVER_PATH'] = SCRCPY_SERVER
    env['ADB'] = ADB_PATH
    return env


def run_host_command(cmd, env, timeout=30):
    """Run a command, wrapping with flatpak-spawn when inside Flatpak Kodi."""
    if is_flatpak():
        fcmd = ['flatpak-spawn', '--host',
                f'--env=PATH={env["PATH"]}',
                f'--env=LD_LIBRARY_PATH={BIN_PATH}',
                f'--env=SCRCPY_SERVER_PATH={SCRCPY_SERVER}',
                f'--env=ADB={ADB_PATH}'] + cmd
        return subprocess.run(fcmd, capture_output=True, timeout=timeout)
    return subprocess.run(cmd, env=env, capture_output=True, timeout=timeout)


def adb_prepare(env, ip=None, port=None):
    """(Re)start the adb server and optionally connect over WiFi."""
    run_host_command([ADB_PATH, 'kill-server'], env, timeout=10)
    run_host_command([ADB_PATH, 'start-server'], env, timeout=15)
    time.sleep(1)
    if ip:
        result = run_host_command([ADB_PATH, 'connect', f'{ip}:{port}'], env, timeout=15)
        out = (result.stdout or b'').decode(errors='replace')
        log(f"adb connect: {out.strip()}")
        if 'connected' not in out:
            return False
        time.sleep(1)
    return True


def list_display_ids(env, serial=None):
    """Parse `scrcpy --list-displays` into a list of display ids."""
    cmd = [SCRCPY_PATH, '--list-displays']
    if serial:
        cmd = [SCRCPY_PATH, '-s', serial, '--list-displays']
    try:
        result = run_host_command(cmd, env, timeout=30)
        out = (result.stdout or b'').decode(errors='replace')
        out += (result.stderr or b'').decode(errors='replace')
        ids = sorted({int(i) for i in re.findall(r'--display-id=(\d+)', out)})
        log(f"Displays found: {ids}")
        return ids
    except Exception as e:
        log(f"list-displays failed: {e}")
        return []


def resolve_dex_display(env, serial=None):
    """
    Work out which display id is the DeX desktop.

    Display 0 is always the phone screen; DeX appears as an extra display
    (usually id 2). A fixed id can be set in settings; 0 means auto-detect.
    """
    configured = get_setting('display_id', 'int')
    if configured > 0:
        return configured

    ids = [i for i in list_display_ids(env, serial) if i != 0]
    if not ids:
        xbmcgui.Dialog().ok(
            ADDON_NAME,
            'No DeX display found.\n'
            'Start DeX on the phone first (e.g. wireless DeX or a DeX dock), '
            'or use Virtual Desktop mode instead.')
        return None
    if len(ids) == 1:
        return ids[0]
    sel = xbmcgui.Dialog().select('Select DeX display', [f'Display {i}' for i in ids])
    return ids[sel] if sel >= 0 else None


def get_virtual_display_arg():
    """--new-display arg for virtual desktop mode (scrcpy 3.x+)."""
    res = get_setting('virtual_resolution').strip()
    dpi = get_setting('virtual_dpi', 'int')
    if res and dpi > 0:
        return f'--new-display={res}/{dpi}'
    elif res:
        return f'--new-display={res}'
    return '--new-display'


def launch_libreelec(scrcpy_cmd, ip=None, port=None):
    """LibreELEC: stop Kodi, run scrcpy via systemd, restart Kodi after."""
    log(f"LibreELEC scrcpy cmd: {scrcpy_cmd}")

    connect_line = f'{ADB_PATH} connect {ip}:{port} >> $LOG 2>&1\nsleep 1\n' if ip else ''

    launcher = "/storage/.kodi/temp/scrcpy_launch.sh"
    script = f'''#!/bin/sh
LOG="/storage/.kodi/temp/scrcpy.log"
echo "=== SCRCPY START ===" > $LOG
date >> $LOG

systemctl stop kodi
sleep 1

{ADB_PATH} kill-server >> $LOG 2>&1
{ADB_PATH} start-server >> $LOG 2>&1
sleep 1

{connect_line}echo "Running: {scrcpy_cmd}" >> $LOG
{scrcpy_cmd} >> $LOG 2>&1
echo "Exit: $?" >> $LOG

systemctl start kodi
'''
    with open(launcher, 'w') as f:
        f.write(script)
    os.chmod(launcher, 0o755)

    xbmcgui.Dialog().notification(ADDON_NAME, "Starting scrcpy...", time=1000)
    os.system(f'systemd-run /bin/sh {launcher}')


def launch_desktop(cmd, env):
    log(f"Desktop scrcpy cmd: {' '.join(cmd)}")
    try:
        if is_flatpak():
            fcmd = ['flatpak-spawn', '--host',
                    f'--env=PATH={env["PATH"]}',
                    f'--env=LD_LIBRARY_PATH={BIN_PATH}',
                    f'--env=SCRCPY_SERVER_PATH={SCRCPY_SERVER}',
                    f'--env=ADB={ADB_PATH}'] + cmd
            subprocess.run(fcmd, timeout=14400)
        else:
            subprocess.run(cmd, env=env, timeout=14400)
    except Exception as e:
        log(f"scrcpy exited: {e}")


def ask_connection():
    """Return True for WiFi, False for USB.

    Only prompts when an IP is configured; otherwise defaults to USB so
    the common single-cable case is one click.
    """
    if not get_setting('ip_address'):
        return False
    return xbmcgui.Dialog().yesno(ADDON_NAME, 'Connect over which transport?',
                                  nolabel='USB', yeslabel='WiFi')


def launch_scrcpy(mode, use_wifi):
    """
    mode: 'phone'   - mirror the default phone screen
          'dex'     - mirror the existing DeX desktop display
          'virtual' - create a scrcpy virtual desktop
    """
    os.chmod(ADB_PATH, 0o755)
    os.chmod(SCRCPY_PATH, 0o755)
    env = build_env()

    serial = None
    ip = port = None
    if use_wifi:
        ip = get_setting('ip_address')
        port = get_setting('port', 'int') or 5555
        if not ip:
            xbmcgui.Dialog().ok(ADDON_NAME, "Set the device IP address in Settings first.")
            return
        serial = f'{ip}:{port}'

    if not adb_prepare(env, ip, port):
        target = serial or 'USB device'
        xbmcgui.Dialog().ok(ADDON_NAME, f"Could not connect to {target}.\n"
                            "Check wireless debugging / `adb tcpip 5555`.")
        return

    if mode == 'dex':
        display_id = resolve_dex_display(env, serial)
        if display_id is None:
            return
        mode_args = [f'--display-id={display_id}']
    elif mode == 'virtual':
        mode_args = [get_virtual_display_arg()]
    else:  # 'phone'
        mode_args = []

    cmd = [SCRCPY_PATH]
    render = get_render()
    if render:
        cmd.append(f'--render-driver={render}')
    if serial:
        cmd.extend(['-s', serial])
    else:
        cmd.append('-d')
    cmd.extend(mode_args)
    if get_setting('forward_all_clicks', 'bool'):
        # scrcpy 2.0+ replaced --forward-all-clicks with --mouse-bind;
        # "++++" forwards right/middle/4th/5th clicks to the device.
        cmd.append('--mouse-bind=++++')
    extra = get_extra_args()
    if extra:
        cmd.extend(extra.split())

    if IS_LIBREELEC:
        launch_libreelec(' '.join(cmd), ip, port)
    else:
        launch_desktop(cmd, env)


def detect_displays_dialog():
    os.chmod(ADB_PATH, 0o755)
    os.chmod(SCRCPY_PATH, 0o755)
    env = build_env()

    use_wifi = xbmcgui.Dialog().yesno(ADDON_NAME, 'Detect over which connection?',
                                      nolabel='USB', yeslabel='WiFi')
    serial = None
    if use_wifi:
        ip = get_setting('ip_address')
        port = get_setting('port', 'int') or 5555
        if not ip:
            xbmcgui.Dialog().ok(ADDON_NAME, "Set the device IP address in Settings first.")
            return
        serial = f'{ip}:{port}'
        if not adb_prepare(env, ip, port):
            xbmcgui.Dialog().ok(ADDON_NAME, f"Could not connect to {serial}.")
            return
    else:
        adb_prepare(env)

    ids = list_display_ids(env, serial)
    if not ids:
        xbmcgui.Dialog().ok(ADDON_NAME, 'No displays reported.\nIs the device connected and authorized?')
        return

    lines = []
    for i in ids:
        label = 'phone screen' if i == 0 else 'DeX / external'
        lines.append(f'Display {i} ({label})')
    xbmcgui.Dialog().ok('Displays found', '\n'.join(lines))


def main():
    log(f"Start - Arch:{ARCH} LibreELEC:{IS_LIBREELEC}")

    if not os.path.exists(SCRCPY_PATH):
        xbmcgui.Dialog().ok("Error", f"scrcpy not found:\n{SCRCPY_PATH}")
        return

    menu = ['Stream Device',
            'Samsung DeX',
            'Virtual Desktop',
            'Detect Displays',
            'Settings']
    sel = xbmcgui.Dialog().contextmenu(menu)

    if sel == 0:
        launch_scrcpy('phone', use_wifi=ask_connection())
    elif sel == 1:
        launch_scrcpy('dex', use_wifi=ask_connection())
    elif sel == 2:
        launch_scrcpy('virtual', use_wifi=ask_connection())
    elif sel == 3:
        detect_displays_dialog()
    elif sel == 4:
        ADDON.openSettings()


if __name__ == '__main__':
    main()
