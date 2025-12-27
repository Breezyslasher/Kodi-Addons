# -*- coding: utf-8 -*-
"""
scrcpy Launcher Service - Auto-detect USB devices
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

IS_LIBREELEC = os.path.exists('/storage/.kodi')

# Track state
last_device = None
scrcpy_running = False


def log(msg):
    xbmc.log(f"[{ADDON_NAME}] {msg}", xbmc.LOGINFO)


def get_setting(sid, stype='string'):
    # Refresh addon to get latest settings
    addon = xbmcaddon.Addon(id='script.scrcpy-launcher')
    val = addon.getSetting(sid)
    if stype == 'bool':
        return val.lower() == 'true'
    elif stype == 'int':
        try:
            return int(val) if val else 0
        except:
            return 0
    return val


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
    
    extra = get_setting('extra_args')
    if extra:
        args.append(extra)
    
    return ' '.join(args)


def get_usb_device():
    """Check for connected USB device"""
    try:
        result = subprocess.run(
            [ADB_PATH, 'devices'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if result.returncode == 0:
            lines = result.stdout.strip().split('\n')
            for line in lines[1:]:
                if '\tdevice' in line:
                    device_id = line.split('\t')[0]
                    # Only return USB devices (no IP:port format)
                    if ':' not in device_id:
                        return device_id
    except Exception as e:
        log(f"Error checking devices: {e}")
    
    return None


def launch_scrcpy():
    """Launch scrcpy for USB device"""
    global scrcpy_running
    
    log("=== Auto-launching scrcpy ===")
    
    os.chmod(ADB_PATH, 0o755)
    os.chmod(SCRCPY_PATH, 0o755)
    
    render = get_render()
    extra = get_extra_args()
    
    scrcpy_cmd = SCRCPY_PATH
    if render:
        scrcpy_cmd += f" --render-driver {render}"
    scrcpy_cmd += " -d"
    if extra:
        scrcpy_cmd += f" {extra}"
    
    log(f"scrcpy cmd: {scrcpy_cmd}")
    
    if IS_LIBREELEC:
        launcher = "/storage/.kodi/temp/scrcpy_launch.sh"
        
        script = f'''#!/bin/sh
LOG="/storage/.kodi/temp/scrcpy.log"
echo "=== AUTO START ===" > $LOG
date >> $LOG

echo "Stopping Kodi..." >> $LOG
systemctl stop kodi
sleep 3

echo "ADB kill-server..." >> $LOG
{ADB_PATH} kill-server >> $LOG 2>&1
sleep 1

echo "ADB start-server..." >> $LOG
{ADB_PATH} start-server >> $LOG 2>&1
sleep 2

echo "Running scrcpy: {scrcpy_cmd}" >> $LOG
{scrcpy_cmd} >> $LOG 2>&1
echo "scrcpy exit: $?" >> $LOG

echo "Starting Kodi..." >> $LOG
systemctl start kodi
echo "=== END ===" >> $LOG
'''
        
        with open(launcher, 'w') as f:
            f.write(script)
        os.chmod(launcher, 0o755)
        
        xbmcgui.Dialog().notification(ADDON_NAME, "Phone connected! Starting scrcpy...", time=2000)
        time.sleep(1)
        
        scrcpy_running = True
        os.system(f'systemd-run /bin/sh {launcher}')
    else:
        # Desktop - just notify, don't auto-launch (would block Kodi)
        xbmcgui.Dialog().notification(ADDON_NAME, "Phone connected! Use addon to stream.", time=3000)


class ScrcpyMonitor(xbmc.Monitor):
    def __init__(self):
        super().__init__()
        self.check_interval = 3  # seconds
        log("Service started")
    
    def run(self):
        global last_device, scrcpy_running
        
        # Make ADB executable
        if os.path.exists(ADB_PATH):
            os.chmod(ADB_PATH, 0o755)
        
        # Start ADB server once
        try:
            subprocess.run([ADB_PATH, 'start-server'], capture_output=True, timeout=10)
            log("ADB server started")
        except Exception as e:
            log(f"Failed to start ADB server: {e}")
        
        # Wait a bit before starting monitoring
        time.sleep(5)
        
        while not self.abortRequested():
            # Check if auto-start is enabled
            if get_setting('auto_start', 'bool'):
                device = get_usb_device()
                
                if device and device != last_device and not scrcpy_running:
                    log(f"New device detected: {device}")
                    last_device = device
                    launch_scrcpy()
                elif not device:
                    last_device = None
                    scrcpy_running = False
            
            # Wait before next check
            if self.waitForAbort(self.check_interval):
                break
        
        log("Service stopped")


if __name__ == '__main__':
    monitor = ScrcpyMonitor()
    monitor.run()
