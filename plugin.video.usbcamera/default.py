"""
USB Camera Viewer for Kodi
View USB cameras, capture cards, and V4L2 video devices
"""
import sys
import os
import subprocess
import re
import urllib.parse
import xbmc
import xbmcgui
import xbmcplugin
import xbmcaddon
import xbmcvfs

ADDON = xbmcaddon.Addon()
ADDON_ID = ADDON.getAddonInfo('id')
ADDON_NAME = ADDON.getAddonInfo('name')
ADDON_PATH = xbmcvfs.translatePath(ADDON.getAddonInfo('path'))
ADDON_PROFILE = xbmcvfs.translatePath(ADDON.getAddonInfo('profile'))
HANDLE = int(sys.argv[1])
BASE_URL = sys.argv[0]


def log(msg, level=xbmc.LOGINFO):
    """Log message to Kodi log"""
    xbmc.log(f'{ADDON_ID}: {msg}', level)


# Settings lookup tables for select-type settings
# 'auto' means let ffplay detect the best settings
RESOLUTION_OPTIONS = ['auto', '1920x1080', '1280x720', '960x544', '864x488', '800x600', '640x480', '480x272', '320x240']
FRAMERATE_OPTIONS = ['auto', '60', '30', '25', '24', '20', '15']
PIXEL_FORMAT_OPTIONS = ['auto', 'mjpeg', 'yuyv422', 'nv12', 'h264']
VIDEO_STANDARD_OPTIONS = ['auto', 'ntsc', 'pal', 'secam']


def get_setting(key):
    """Get addon setting as string"""
    return ADDON.getSetting(key)


def get_setting_bool(key):
    """Get boolean addon setting"""
    value = ADDON.getSetting(key)
    return value.lower() == 'true'


def get_setting_int(key):
    """Get integer addon setting"""
    value = ADDON.getSetting(key)
    try:
        return int(value)
    except (ValueError, TypeError):
        return 0


def get_resolution():
    """Get resolution setting value. Returns 'auto' or a resolution string like '1280x720'"""
    try:
        index = int(ADDON.getSetting('resolution'))
        if 0 <= index < len(RESOLUTION_OPTIONS):
            return RESOLUTION_OPTIONS[index]
    except (ValueError, TypeError):
        pass
    return 'auto'  # Default to auto for best compatibility


def get_framerate():
    """Get framerate setting value. Returns 'auto' or a framerate string like '30'"""
    try:
        index = int(ADDON.getSetting('framerate'))
        if 0 <= index < len(FRAMERATE_OPTIONS):
            return FRAMERATE_OPTIONS[index]
    except (ValueError, TypeError):
        pass
    return 'auto'  # Default to auto for best compatibility


def get_pixel_format():
    """Get pixel format setting value"""
    try:
        index = int(ADDON.getSetting('pixel_format'))
        if 0 <= index < len(PIXEL_FORMAT_OPTIONS):
            return PIXEL_FORMAT_OPTIONS[index]
    except (ValueError, TypeError):
        pass
    return 'auto'


def build_url(query):
    """Build plugin URL with query parameters"""
    return f'{BASE_URL}?{urllib.parse.urlencode(query)}'


def get_video_devices():
    """
    Detect all V4L2 video devices on the system.
    Returns list of dicts with device info.
    """
    devices = []

    # Find all /dev/video* devices
    dev_path = '/dev'
    try:
        for entry in os.listdir(dev_path):
            if entry.startswith('video'):
                device_path = os.path.join(dev_path, entry)
                device_info = get_device_info(device_path)
                if device_info:
                    devices.append(device_info)
    except Exception as e:
        log(f'Error scanning /dev: {e}', xbmc.LOGERROR)

    # Sort by device number
    devices.sort(key=lambda x: x.get('device_num', 999))

    return devices


def get_device_info(device_path):
    """
    Get detailed information about a V4L2 device.
    Uses v4l2-ctl if available, falls back to basic detection.
    """
    device_info = {
        'path': device_path,
        'name': os.path.basename(device_path),
        'device_num': int(re.search(r'\d+', os.path.basename(device_path)).group()) if re.search(r'\d+', os.path.basename(device_path)) else 0,
        'driver': 'unknown',
        'card': f'Video Device {os.path.basename(device_path)}',
        'capabilities': [],
        'formats': [],
        'inputs': []
    }

    # Check if device is accessible
    if not os.path.exists(device_path):
        return None

    # Try to get device info using v4l2-ctl
    try:
        result = subprocess.run(
            ['v4l2-ctl', '--device', device_path, '--all'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            output = result.stdout

            # Parse driver name
            driver_match = re.search(r'Driver name\s*:\s*(.+)', output)
            if driver_match:
                device_info['driver'] = driver_match.group(1).strip()

            # Parse card name
            card_match = re.search(r'Card type\s*:\s*(.+)', output)
            if card_match:
                device_info['card'] = card_match.group(1).strip()

            # Parse capabilities
            if 'Video Capture' in output:
                device_info['capabilities'].append('capture')
            if 'Video Output' in output:
                device_info['capabilities'].append('output')

            # Check for video inputs (for capture cards)
            input_matches = re.findall(r'Input\s+:\s+(\d+)\s+\(([^)]+)\)', output)
            for inp_num, inp_name in input_matches:
                device_info['inputs'].append({'num': int(inp_num), 'name': inp_name})

    except FileNotFoundError:
        log('v4l2-ctl not found, using basic detection', xbmc.LOGWARNING)
    except subprocess.TimeoutExpired:
        log(f'Timeout getting info for {device_path}', xbmc.LOGWARNING)
    except Exception as e:
        log(f'Error getting device info: {e}', xbmc.LOGWARNING)

    # Only return devices that appear to be capture devices
    # Filter out metadata/output-only devices
    try:
        # Try to check if device can capture
        result = subprocess.run(
            ['v4l2-ctl', '--device', device_path, '--list-formats'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0 and ('YUYV' in result.stdout or 'MJPG' in result.stdout or
                                        'H264' in result.stdout or 'NV12' in result.stdout or
                                        'RGB' in result.stdout or 'YUV' in result.stdout):
            # Parse formats
            format_matches = re.findall(r"'(\w+)'", result.stdout)
            device_info['formats'] = list(set(format_matches))
            return device_info
        elif 'capture' in device_info['capabilities']:
            return device_info
    except:
        pass

    # Fallback: try to open the device to check if it's valid
    try:
        import fcntl
        import struct

        VIDIOC_QUERYCAP = 0x80685600

        with open(device_path, 'rb') as f:
            buf = bytearray(104)
            try:
                fcntl.ioctl(f, VIDIOC_QUERYCAP, buf)
                # Device responded, likely valid
                return device_info
            except:
                return None
    except:
        # If we can't verify, still return it and let playback fail gracefully
        return device_info


def get_device_resolutions(device_path):
    """Get supported resolutions for a device"""
    resolutions = []

    try:
        result = subprocess.run(
            ['v4l2-ctl', '--device', device_path, '--list-framesizes', 'MJPG'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            # Parse discrete resolutions
            matches = re.findall(r'(\d+)x(\d+)', result.stdout)
            for w, h in matches:
                resolutions.append(f'{w}x{h}')

        # Also try YUYV format
        result = subprocess.run(
            ['v4l2-ctl', '--device', device_path, '--list-framesizes', 'YUYV'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode == 0:
            matches = re.findall(r'(\d+)x(\d+)', result.stdout)
            for w, h in matches:
                res = f'{w}x{h}'
                if res not in resolutions:
                    resolutions.append(res)

    except Exception as e:
        log(f'Error getting resolutions: {e}', xbmc.LOGWARNING)

    # Fallback common resolutions
    if not resolutions:
        resolutions = ['1920x1080', '1280x720', '640x480', '320x240']

    return resolutions


def get_friendly_device_name(device_info):
    """Get a user-friendly name for the device"""
    card = device_info.get('card', 'Unknown Device')

    # Detect device type based on common patterns
    card_lower = card.lower()

    if any(x in card_lower for x in ['hdmi', 'capture', 'grabber', 'elgato', 'avermedia', 'magewell']):
        device_type = 'HDMI Capture'
    elif any(x in card_lower for x in ['composite', 's-video', 'easycap', 'usbtv']):
        device_type = 'Composite Capture'
    elif any(x in card_lower for x in ['webcam', 'camera', 'cam', 'logitech', 'microsoft']):
        device_type = 'USB Camera'
    elif 'uvc' in card_lower:
        device_type = 'USB Video Device'
    else:
        device_type = 'Video Device'

    return f'{card} ({device_type})'


def play_device(device_path, input_num=None):
    """
    Play video from a V4L2 device using ffplay.
    ffplay has native V4L2 support and is the most reliable method.
    """
    log(f'Playing device: {device_path}, input: {input_num}')

    # Get settings
    resolution = get_resolution()
    framerate = get_framerate()
    low_latency = get_setting_bool('low_latency')
    pixel_format = get_pixel_format()

    log(f'Settings - Resolution: {resolution}, Framerate: {framerate}, Low Latency: {low_latency}, Pixel Format: {pixel_format}')

    # Parse resolution (only needed if not auto)
    width, height = None, None
    if resolution != 'auto':
        try:
            width, height = resolution.split('x')
        except:
            width, height = '1280', '720'

    # Set input if specified (for capture cards)
    if input_num is not None:
        try:
            subprocess.run(
                ['v4l2-ctl', '--device', device_path, '--set-input', str(input_num)],
                timeout=5
            )
            log(f'Set input to {input_num}')
        except Exception as e:
            log(f'Error setting input: {e}', xbmc.LOGWARNING)

    # Build ffplay command - it has native V4L2 support
    cmd = [
        'ffplay',
        '-f', 'v4l2',
    ]

    # Add pixel format if not auto
    if pixel_format != 'auto':
        cmd.extend(['-input_format', pixel_format])

    # Add resolution only if not auto (let device decide)
    if resolution != 'auto':
        cmd.extend(['-video_size', f'{width}x{height}'])

    # Add framerate only if not auto (let device decide)
    if framerate != 'auto':
        cmd.extend(['-framerate', framerate])

    # Add input device and display options
    cmd.extend([
        '-i', device_path,
        '-fs',  # Fullscreen
        '-noborder',
        '-loglevel', 'error',
        '-window_title', 'USB Camera - Press Q or ESC to exit'
    ])

    # Low latency options
    if low_latency:
        cmd.extend([
            '-fflags', 'nobuffer',
            '-flags', 'low_delay',
            '-framedrop',
            '-sync', 'video'
        ])

    log(f'Running: {" ".join(cmd)}')

    # Run ffplay - it will take over the screen
    try:
        # Show brief notification
        xbmcgui.Dialog().notification(
            ADDON_NAME,
            'Starting camera... Press Q or ESC to exit',
            xbmcgui.NOTIFICATION_INFO,
            2000
        )

        # Small delay to let notification show
        xbmc.sleep(500)

        # Start ffplay
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE
        )

        # Wait for ffplay to exit
        process.wait()

        # Check for errors
        if process.returncode != 0:
            stderr = process.stderr.read().decode('utf-8', errors='ignore')
            if stderr:
                log(f'ffplay error: {stderr}', xbmc.LOGERROR)
                # Show user-friendly error message
                if 'No such file or directory' in stderr:
                    xbmcgui.Dialog().notification(
                        ADDON_NAME,
                        'Device not found. Is it still connected?',
                        xbmcgui.NOTIFICATION_ERROR,
                        5000
                    )
                elif 'Permission denied' in stderr:
                    xbmcgui.Dialog().notification(
                        ADDON_NAME,
                        'Permission denied. Try running: sudo chmod 666 ' + device_path,
                        xbmcgui.NOTIFICATION_ERROR,
                        5000
                    )
                elif 'Invalid argument' in stderr or 'not supported' in stderr.lower():
                    xbmcgui.Dialog().notification(
                        ADDON_NAME,
                        'Try different resolution or pixel format in settings',
                        xbmcgui.NOTIFICATION_WARNING,
                        5000
                    )
                else:
                    xbmcgui.Dialog().notification(
                        ADDON_NAME,
                        'Playback error. Check Kodi log for details.',
                        xbmcgui.NOTIFICATION_ERROR,
                        5000
                    )

    except FileNotFoundError:
        log('ffplay not found', xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            ADDON_NAME,
            'ffplay not found. Please install FFmpeg.',
            xbmcgui.NOTIFICATION_ERROR,
            5000
        )
    except Exception as e:
        log(f'Error launching ffplay: {e}', xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            ADDON_NAME,
            f'Error: {str(e)}',
            xbmcgui.NOTIFICATION_ERROR,
            5000
        )


def play_device_external(device_path, input_num=None):
    """
    Play device using external player (ffplay) in a window.
    Used as fallback or when requested.
    """
    log(f'Playing device externally: {device_path}')

    resolution = get_resolution()
    framerate = get_framerate()

    # Set input if specified
    if input_num is not None:
        try:
            subprocess.run(
                ['v4l2-ctl', '--device', device_path, '--set-input', str(input_num)],
                timeout=5
            )
        except:
            pass

    # Build ffplay command
    cmd = [
        'ffplay',
        '-f', 'v4l2',
    ]

    # Add resolution only if not auto
    if resolution != 'auto':
        try:
            width, height = resolution.split('x')
            cmd.extend(['-video_size', f'{width}x{height}'])
        except:
            pass

    # Add framerate only if not auto
    if framerate != 'auto':
        cmd.extend(['-framerate', framerate])

    cmd.extend([
        '-i', device_path,
        '-fs',  # Fullscreen
        '-noborder',
        '-loglevel', 'error'
    ])

    low_latency = get_setting_bool('low_latency')
    if low_latency:
        cmd.extend(['-fflags', 'nobuffer', '-flags', 'low_delay', '-framedrop'])

    log(f'Running: {" ".join(cmd)}')

    # Run ffplay - it will take over the screen
    try:
        subprocess.Popen(cmd)
        xbmcgui.Dialog().notification(
            ADDON_NAME,
            'Press Q or ESC to exit camera view',
            xbmcgui.NOTIFICATION_INFO,
            3000
        )
    except FileNotFoundError:
        xbmcgui.Dialog().notification(
            ADDON_NAME,
            'ffplay not found. Please install FFmpeg.',
            xbmcgui.NOTIFICATION_ERROR,
            5000
        )
    except Exception as e:
        log(f'Error launching ffplay: {e}', xbmc.LOGERROR)
        xbmcgui.Dialog().notification(
            ADDON_NAME,
            f'Error: {str(e)}',
            xbmcgui.NOTIFICATION_ERROR,
            5000
        )


def show_device_menu(device_info):
    """Show menu for a specific device with options"""
    device_path = device_info['path']

    options = ['Play Video']

    # Add input selection if device has multiple inputs
    if device_info.get('inputs'):
        for inp in device_info['inputs']:
            options.append(f"Play Input: {inp['name']}")

    options.extend([
        'Play with External Player (ffplay)',
        'Device Information',
        'Configure Resolution'
    ])

    dialog = xbmcgui.Dialog()
    selection = dialog.select(device_info['card'], options)

    if selection == 0:
        # Play video
        play_device(device_path)
    elif selection > 0 and selection <= len(device_info.get('inputs', [])):
        # Play specific input
        input_num = device_info['inputs'][selection - 1]['num']
        play_device(device_path, input_num)
    elif selection == len(device_info.get('inputs', [])) + 1:
        # External player
        play_device_external(device_path)
    elif selection == len(device_info.get('inputs', [])) + 2:
        # Device info
        show_device_info(device_info)
    elif selection == len(device_info.get('inputs', [])) + 3:
        # Configure resolution
        configure_resolution(device_path)


def show_device_info(device_info):
    """Show detailed device information"""
    info_text = f"""Device: {device_info['card']}
Path: {device_info['path']}
Driver: {device_info['driver']}
Capabilities: {', '.join(device_info.get('capabilities', ['Unknown']))}
Formats: {', '.join(device_info.get('formats', ['Unknown']))}
Inputs: {len(device_info.get('inputs', []))}"""

    for inp in device_info.get('inputs', []):
        info_text += f"\n  - Input {inp['num']}: {inp['name']}"

    xbmcgui.Dialog().textviewer('Device Information', info_text)


def configure_resolution(device_path):
    """Allow user to select resolution for a device"""
    resolutions = get_device_resolutions(device_path)

    # Add common resolutions if not already present (including PS Vita)
    common = ['auto', '1920x1080', '1280x720', '960x544', '864x488', '640x480']
    for res in common:
        if res not in resolutions:
            resolutions.append(res)

    # Sort by resolution (width), but keep 'auto' at the top
    auto_present = 'auto' in resolutions
    if auto_present:
        resolutions.remove('auto')
    resolutions.sort(key=lambda x: int(x.split('x')[0]) if 'x' in x else 0, reverse=True)
    if auto_present:
        resolutions.insert(0, 'auto')

    current = get_resolution()

    dialog = xbmcgui.Dialog()
    selection = dialog.select(
        'Select Resolution',
        resolutions,
        preselect=resolutions.index(current) if current in resolutions else 0
    )

    if selection >= 0:
        # Find the index in our RESOLUTION_OPTIONS to store
        selected_res = resolutions[selection]
        if selected_res in RESOLUTION_OPTIONS:
            ADDON.setSetting('resolution', str(RESOLUTION_OPTIONS.index(selected_res)))
        else:
            # If it's a custom resolution not in options, set to auto as fallback
            ADDON.setSetting('resolution', '0')
        xbmcgui.Dialog().notification(
            ADDON_NAME,
            f'Resolution set to {selected_res}',
            xbmcgui.NOTIFICATION_INFO,
            2000
        )


def list_devices():
    """List all detected video devices in Kodi menu"""
    devices = get_video_devices()

    if not devices:
        # No devices found - show helpful message
        li = xbmcgui.ListItem(label='[No video devices detected]')
        li.setInfo('video', {'plot': 'No USB cameras or capture cards were detected. Make sure your device is connected and recognized by the system.'})
        xbmcplugin.addDirectoryItem(HANDLE, '', li, False)

        # Add refresh option
        li = xbmcgui.ListItem(label='[Refresh Device List]')
        url = build_url({'action': 'refresh'})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, False)

        # Add help
        li = xbmcgui.ListItem(label='[Help & Troubleshooting]')
        url = build_url({'action': 'help'})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, False)
    else:
        for device in devices:
            friendly_name = get_friendly_device_name(device)

            li = xbmcgui.ListItem(label=friendly_name)
            li.setInfo('video', {
                'title': friendly_name,
                'plot': f"Path: {device['path']}\nDriver: {device['driver']}\nFormats: {', '.join(device.get('formats', ['Unknown']))}"
            })
            li.setProperty('IsPlayable', 'true')

            # Set icon based on device type
            icon_path = os.path.join(ADDON_PATH, 'resources', 'icon.png')
            li.setArt({'icon': icon_path, 'thumb': icon_path})

            # Build URL for this device
            url = build_url({
                'action': 'play',
                'device': device['path'],
                'name': device['card']
            })

            # Add context menu
            context_menu = [
                ('Play with External Player', f'RunPlugin({build_url({"action": "play_external", "device": device["path"]})})'),
                ('Device Information', f'RunPlugin({build_url({"action": "info", "device": device["path"]})})'),
                ('Configure Resolution', f'RunPlugin({build_url({"action": "configure", "device": device["path"]})})')
            ]

            # Add input selection if multiple inputs
            if device.get('inputs'):
                for inp in device['inputs']:
                    context_menu.append((
                        f"Play Input: {inp['name']}",
                        f'RunPlugin({build_url({"action": "play", "device": device["path"], "input": inp["num"]})})'
                    ))

            li.addContextMenuItems(context_menu)

            xbmcplugin.addDirectoryItem(HANDLE, url, li, False)

        # Add refresh option at the end
        li = xbmcgui.ListItem(label='[Refresh Device List]')
        url = build_url({'action': 'refresh'})
        xbmcplugin.addDirectoryItem(HANDLE, url, li, False)

    # Add settings shortcut
    li = xbmcgui.ListItem(label='[Settings]')
    url = build_url({'action': 'settings'})
    xbmcplugin.addDirectoryItem(HANDLE, url, li, False)

    xbmcplugin.endOfDirectory(HANDLE)


def show_help():
    """Show help and troubleshooting information"""
    help_text = """USB Camera Viewer - Help & Troubleshooting

SUPPORTED DEVICES:
- USB webcams and cameras
- HDMI capture cards (Elgato, AVerMedia, generic USB capture)
- Composite/S-Video capture cards
- Game capture devices (via HDMI/composite capture card)
- Any V4L2 compatible video device

PS VITA / GAME CONSOLES:
To view your PS Vita or game console, you need an HDMI or composite capture card:
1. Connect your console's video output to the capture card
2. Connect the capture card to a USB port on your device
3. The capture card should appear in the device list

TROUBLESHOOTING:

1. No devices detected:
   - Ensure device is properly connected via USB
   - Try a different USB port
   - Check if device is recognized: 'ls -la /dev/video*'
   - LibreELEC may need a reboot after connecting new devices

2. Video won't play:
   - Try lowering the resolution in settings
   - Try "Play with External Player" option
   - Install inputstream.ffmpegdirect addon for better compatibility

3. Low framerate or stuttering:
   - Enable "Low Latency Mode" in settings
   - Lower the resolution
   - Try a different pixel format in settings

4. No audio from capture card:
   - This addon focuses on video only
   - For audio, use a separate audio capture/passthrough

5. Capture card shows wrong input:
   - Use the context menu to select specific inputs
   - Some capture cards have multiple inputs (HDMI, composite, etc.)

For LibreELEC users:
- All V4L2 drivers are built into LibreELEC
- Most USB capture cards work out of the box
- If a device doesn't work, it may not have V4L2 driver support"""

    xbmcgui.Dialog().textviewer('Help & Troubleshooting', help_text)


def router(paramstring):
    """Route plugin calls to appropriate functions"""
    params = dict(urllib.parse.parse_qsl(paramstring))
    action = params.get('action')

    if action is None:
        # Main menu - list devices
        list_devices()
    elif action == 'play':
        device = params.get('device')
        input_num = params.get('input')
        if device:
            play_device(device, int(input_num) if input_num else None)
    elif action == 'play_external':
        device = params.get('device')
        input_num = params.get('input')
        if device:
            play_device_external(device, int(input_num) if input_num else None)
    elif action == 'info':
        device = params.get('device')
        if device:
            devices = get_video_devices()
            for d in devices:
                if d['path'] == device:
                    show_device_info(d)
                    break
    elif action == 'configure':
        device = params.get('device')
        if device:
            configure_resolution(device)
    elif action == 'refresh':
        xbmc.executebuiltin('Container.Refresh')
    elif action == 'settings':
        ADDON.openSettings()
    elif action == 'help':
        show_help()


if __name__ == '__main__':
    router(sys.argv[2][1:])
