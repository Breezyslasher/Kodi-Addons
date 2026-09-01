"""Small helpers around the Kodi Python API: settings, logging, storage."""

import json
import os

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs


def addon():
    """A fresh Addon handle.

    Kodi 22 warns when a script leaves xbmcaddon.Addon instances behind
    ("has left several classes in memory that we couldn't clean up"), which is
    what a module-level instance does: it lives until interpreter teardown, by
    which point Kodi has already checked. Create one per call and let it go out
    of scope instead.
    """
    return xbmcaddon.Addon()


ADDON_ID = addon().getAddonInfo("id")
ADDON_NAME = addon().getAddonInfo("name")


def log(message, level=xbmc.LOGINFO):
    # Never raise from logging: this runs during teardown too, and on an
    # uninstall the addon id is already gone.
    try:
        xbmc.log("[%s] %s" % (ADDON_ID, message), level)
    except Exception:
        pass


def log_error(message):
    log(message, xbmc.LOGERROR)


def get_setting(setting_id, default=""):
    try:
        value = addon().getSetting(setting_id)
        return value if value else default
    except Exception:
        return default


def set_setting(setting_id, value):
    try:
        addon().setSetting(setting_id, str(value))
    except Exception:
        pass


def get_setting_bool(setting_id, default=False):
    value = get_setting(setting_id, "true" if default else "false")
    return str(value).lower() == "true"


def get_setting_int(setting_id, default=0):
    try:
        return int(get_setting(setting_id, str(default)))
    except (TypeError, ValueError):
        return default


def localize(string_id):
    return addon().getLocalizedString(string_id)


def notify(message, heading=None, icon=xbmcgui.NOTIFICATION_INFO, time_ms=5000):
    xbmcgui.Dialog().notification(heading or ADDON_NAME, message, icon, time_ms)


def _needs_room(message):
    """True when a message will not fit Kodi's ok box.

    That box is a fixed size -- roughly four lines of sixty characters in
    the stock skins -- and anything past it is cut off or has to be
    scrolled. Measured as wrapped lines rather than by raw length, so a
    message that is long only because it has paragraphs is judged on the
    room it actually needs.
    """
    lines = 0
    for paragraph in (message or "").split("\n"):
        lines += max(1, -(-len(paragraph) // 58))
    return lines > 4


def ok_dialog(message, heading=None):
    """Show an error and write it to the log.

    Every failure in this addon ends at a dialog, and a dialog alone leaves
    a screenshot as the only evidence of what went wrong. Log first, then
    show. A message too big for the ok box goes to the text viewer instead,
    which is full-screen and shows the whole thing at once.
    """
    if not (message or "").strip():
        message = ("%s, and the exception said nothing more. The log has "
                   "the detail." % (heading or "That did not work"))
    log("%s: %s" % (heading or ADDON_NAME, message))
    if _needs_room(message):
        try:
            xbmcgui.Dialog().textviewer(heading or ADDON_NAME, message)
            return
        except Exception:
            # Older Kodi without textviewer: better a clipped box than none.
            pass
    xbmcgui.Dialog().ok(heading or ADDON_NAME, message)


def yesno(message, heading=None):
    return xbmcgui.Dialog().yesno(heading or ADDON_NAME, message)


def input_text(heading, hidden=False, default=""):
    option = xbmcgui.ALPHANUM_HIDE_INPUT if hidden else 0
    result = xbmcgui.Dialog().input(heading, defaultt=default,
                                    type=xbmcgui.INPUT_ALPHANUM, option=option)
    return result or None


_PLATFORM = [""]


def platform():
    """Kodi's build, the platform it is on, and the ISA version beside it.

    One line, once per session. The decrypter InputStream Adaptive uses is
    chosen by platform, not by setting: on Android it is the MediaDrm-backed
    one, which is a different file from the desktop CDM host. Without this
    line a log cannot say which one ran, and a fix aimed at the wrong one is
    a round wasted.
    """
    if _PLATFORM[0]:
        return _PLATFORM[0]
    parts = []
    for label, info in (("kodi", "System.BuildVersion"),
                        ("on", "System.OSVersionInfo")):
        try:
            said = xbmc.getInfoLabel(info)
        except Exception:
            said = ""
        if said:
            parts.append("%s %s" % (label, said.replace("\n", " ").strip()))
    parts.append("inputstream.adaptive " + (isa_version() or "not installed"))
    # Named plainly rather than inferred from the OS string, which is
    # "Linux" on Android too.
    try:
        if xbmc.getCondVisibility("System.Platform.Android"):
            parts.append("ANDROID -- the MediaDrm decrypter, not the desktop "
                         "one")
    except Exception:
        pass
    _PLATFORM[0] = "; ".join(parts)
    return _PLATFORM[0]


def isa_version():
    """inputstream.adaptive's version string, or "" when it is not installed."""
    try:
        return xbmcaddon.Addon("inputstream.adaptive").getAddonInfo("version")
    except Exception:
        return ""


def isa_major():
    """The major version of inputstream.adaptive, or 0 when unknown.

    Decides which DRM configuration this addon writes: ISA 21 introduced the
    ``inputstream.adaptive.drm`` JSON property, and the older
    ``license_type``/``license_key`` pair is what every build before it reads.
    """
    version = isa_version()
    try:
        return int(version.split(".")[0])
    except (AttributeError, IndexError, ValueError):
        return 0


def profile_dir():
    """The addon's data directory, created if absent.

    Raises once the addon has been uninstalled -- xbmcaddon.Addon() cannot
    resolve an id Kodi has already dropped. Callers that run during teardown
    must treat that as normal, so read_json/write_json/delete_file each
    compute their path inside their own try block rather than ahead of it.
    """
    path = xbmcvfs.translatePath(addon().getAddonInfo("profile"))
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def _safe_name(filename):
    """A profile-dir filename with any path component stripped.

    read_json/write_json/delete_file join their argument onto the addon
    profile dir, so collapse it to a bare basename and reject a traversal
    name -- a value like "../../x" can never escape the profile dir.
    """
    name = os.path.basename(str(filename))
    if name in ("", ".", "..") or "/" in name or "\\" in name or "\x00" in name:
        raise ValueError("unsafe filename: %r" % filename)
    return name


def read_json(filename, default=None):
    try:
        path = os.path.join(profile_dir(), _safe_name(filename))
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as handle:
                return json.load(handle)
    except Exception as exc:
        log_error("Failed reading %s: %s" % (filename, exc))
    return default


def write_json(filename, data):
    try:
        path = os.path.join(profile_dir(), _safe_name(filename))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)
        return True
    except Exception as exc:
        log_error("Failed writing %s: %s" % (filename, exc))
        return False


def delete_file(filename):
    try:
        path = os.path.join(profile_dir(), _safe_name(filename))
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


# Certificate authorities, once, at import.
#
# Kodi's requests takes its CA bundle from script.module.certifi, and a box
# where that addon is mid-update or half-installed answers every HTTPS call
# with "Could not find a suitable TLS CA certificate bundle, invalid path:
# .../certifi/cacert.pem".
#
# So: if certifi's file is genuinely missing and the system has a bundle,
# point requests at the system one. Verification is never disabled and an
# existing REQUESTS_CA_BUNDLE is never overridden -- if someone set that
# deliberately, they meant it.
_SYSTEM_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",       # Debian, Ubuntu, Alpine
    "/etc/pki/tls/certs/ca-bundle.crt",         # Fedora, RHEL
    "/etc/ssl/ca-bundle.pem",                   # openSUSE
    "/etc/ssl/cert.pem",                        # BSD, macOS
)


def _repair_ca_bundle():
    if os.environ.get("REQUESTS_CA_BUNDLE"):
        return
    try:
        import certifi
        if os.path.exists(certifi.where()):
            return
        broken = certifi.where()
    except Exception as exc:
        broken = "certifi could not be imported (%s)" % exc
    for candidate in _SYSTEM_CA_BUNDLES:
        if os.path.exists(candidate):
            os.environ["REQUESTS_CA_BUNDLE"] = candidate
            log("CA bundle: %s is unusable, so HTTPS will verify against %s"
                % (broken, candidate))
            return
    log("CA bundle: %s is unusable and no system bundle was found -- HTTPS "
        "calls are likely to fail" % broken)


_repair_ca_bundle()
