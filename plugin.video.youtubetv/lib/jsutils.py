"""The handful of yt-dlp utilities jsinterp.py needs.

Copied from yt_dlp/utils/_utils.py so the vendored interpreter has no
dependency on the rest of yt-dlp. Public domain, as above.
"""

import calendar
import collections.abc
import contextlib
import datetime as dt
import email.utils
import functools
import json
import re

class NO_DEFAULT:
    pass

def remove_quotes(s):
    if s is None or len(s) < 2:
        return s
    for quote in ('"', "'"):
        if s[0] == quote and s[-1] == quote:
            return s[1:-1]
    return s

def truncate_string(s, left, right=0):
    assert left > 3 and right >= 0
    if s is None or len(s) <= left + right:
        return s
    return f'{s[:left - 3]}...{s[-right:] if right else ""}'

class function_with_repr:
    def __init__(self, func, repr_=None):
        functools.update_wrapper(self, func)
        self.func, self.__repr = func, repr_

    def __call__(self, *args, **kwargs):
        return self.func(*args, **kwargs)

    @classmethod
    def set_repr(cls, repr_):
        return functools.partial(cls, repr_=repr_)

    def __repr__(self):
        if self.__repr:
            return self.__repr
        return f'{self.func.__module__}.{self.func.__qualname__}'

def js_to_json(code, vars={}, *, strict=False):
    # vars is a dict of var, val pairs to substitute
    STRING_QUOTES = '\'"`'
    STRING_RE = '|'.join(rf'{q}(?:\\.|[^\\{q}])*{q}' for q in STRING_QUOTES)
    COMMENT_RE = r'/\*(?:(?!\*/).)*?\*/|//[^\n]*\n'
    SKIP_RE = fr'\s*(?:{COMMENT_RE})?\s*'
    INTEGER_TABLE = (
        (fr'(?s)^(0[xX][0-9a-fA-F]+){SKIP_RE}:?$', 16),
        (fr'(?s)^(0+[0-7]+){SKIP_RE}:?$', 8),
    )

    def process_escape(match):
        JSON_PASSTHROUGH_ESCAPES = R'"\bfnrtu'
        escape = match.group(1) or match.group(2)

        return (Rf'\{escape}' if escape in JSON_PASSTHROUGH_ESCAPES
                else R'\u00' if escape == 'x'
                else '' if escape == '\n'
                else escape)

    def template_substitute(match):
        evaluated = js_to_json(match.group(1), vars, strict=strict)
        if evaluated[0] == '"':
            with contextlib.suppress(json.JSONDecodeError):
                return json.loads(evaluated)
        return evaluated

    def fix_kv(m):
        v = m.group(0)
        if v in ('true', 'false', 'null'):
            return v
        elif v in ('undefined', 'void 0'):
            return 'null'
        elif v.startswith(('/*', '//', '!')) or v == ',':
            return ''

        if v[0] in STRING_QUOTES:
            v = re.sub(r'(?s)\${([^}]+)}', template_substitute, v[1:-1]) if v[0] == '`' else v[1:-1]
            escaped = re.sub(r'(?s)(")|\\(.)', process_escape, v)
            return f'"{escaped}"'

        for regex, base in INTEGER_TABLE:
            im = re.match(regex, v)
            if im:
                i = int(im.group(1), base)
                return f'"{i}":' if v.endswith(':') else str(i)

        if v in vars:
            try:
                if not strict:
                    json.loads(vars[v])
            except json.JSONDecodeError:
                return json.dumps(vars[v])
            else:
                return vars[v]

        if not strict:
            return f'"{v}"'

        raise ValueError(f'Unknown value: {v}')

    def create_map(mobj):
        return json.dumps(dict(json.loads(js_to_json(mobj.group(1) or '[]', vars=vars))))

    code = re.sub(r'(?:new\s+)?Array\((.*?)\)', r'[\g<1>]', code)
    code = re.sub(r'new Map\((\[.*?\])?\)', create_map, code)
    if not strict:
        code = re.sub(rf'new Date\(({STRING_RE})\)', r'\g<1>', code)
        code = re.sub(r'new \w+\((.*?)\)', lambda m: json.dumps(m.group(0)), code)
        code = re.sub(r'parseInt\([^\d]+(\d+)[^\d]+\)', r'\1', code)
        code = re.sub(r'\(function\([^)]*\)\s*\{[^}]*\}\s*\)\s*\(\s*(["\'][^)]*["\'])\s*\)', r'\1', code)

    return re.sub(rf'''(?sx)
        {STRING_RE}|
        {COMMENT_RE}|,(?={SKIP_RE}[\]}}])|
        void\s0|(?:(?<![0-9])[eE]|[a-df-zA-DF-Z_$])[.a-zA-Z_$0-9]*|
        \b(?:0[xX][0-9a-fA-F]+|(?<!\.)0+[0-7]+)(?:{SKIP_RE}:)?|
        [0-9]+(?={SKIP_RE}:)|
        !+
        ''', fix_kv, code)

TIMEZONE_NAMES = {
    "UT": 0, "UTC": 0, "GMT": 0, "Z": 0,
    "AST": -4, "ADT": -3, "EST": -5, "EDT": -4, "CST": -6, "CDT": -5,
    "MST": -7, "MDT": -6, "PST": -8, "PDT": -7,
}

_TZ_RE = re.compile(
    r"""^(?P<rest>.*?)\s*
        (?:(?P<sign>[+-])(?P<hours>[0-9]{2}):?(?P<minutes>[0-9]{2})
         |(?P<name>Z|[A-Z]{2,5}))\s*$""", re.VERBOSE)


def extract_timezone(date_str, default=None):
    """(offset, date_str without the zone).

    A reduced version of the helper this module was vendored from, which
    was left behind: unified_timestamp called it and nothing defined it, so
    every date the JS interpreter parsed raised NameError instead. It
    handles what the player's own dates carry -- a numeric offset, a Z, or
    one of the North American abbreviations -- and falls back to `default`
    rather than inventing an offset for a zone it does not know.
    """
    match = _TZ_RE.match(date_str or "")
    if not match:
        return (default or dt.timedelta()), date_str
    if match.group("sign"):
        offset = dt.timedelta(hours=int(match.group("hours")),
                              minutes=int(match.group("minutes")))
        if match.group("sign") == "-":
            offset = -offset
        return offset, match.group("rest")
    name = match.group("name")
    if name in TIMEZONE_NAMES:
        return dt.timedelta(hours=TIMEZONE_NAMES[name]), match.group("rest")
    return (default or dt.timedelta()), date_str


_DATE_FORMATS = (
    "%d %B %Y", "%d %b %Y", "%B %d %Y", "%B %dst %Y", "%B %dnd %Y",
    "%B %drd %Y", "%B %dth %Y", "%b %d %Y", "%b %dst %Y", "%b %dnd %Y",
    "%b %drd %Y", "%b %dth %Y", "%b %dst %Y %I:%M", "%Y %m %d",
    "%Y-%m-%d", "%Y.%m.%d.", "%Y/%m/%d", "%Y/%m/%d %H:%M",
    "%Y/%m/%d %H:%M:%S", "%Y%m%d%H%M", "%Y%m%d%H%M%S", "%Y%m%d",
    "%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f",
    "%d.%m.%Y %H:%M", "%d.%m.%Y %H.%M", "%H:%M %d-%b-%Y",
)
_DAY_FIRST = ("%d-%m-%Y", "%d.%m.%Y", "%d/%m/%Y", "%d/%m/%y",
              "%d/%m/%Y %H:%M:%S", "%d-%m-%Y %H:%M")
_MONTH_FIRST = ("%m-%d-%Y", "%m.%d.%Y", "%m/%d/%Y", "%m/%d/%y",
                "%m/%d/%Y %H:%M:%S")


def date_formats(day_first=True):
    """strptime formats to try, ambiguous ones ordered by the day_first hint."""
    return _DATE_FORMATS + (_DAY_FIRST if day_first else _MONTH_FIRST)


def unified_timestamp(date_str, day_first=True, tz_offset=0):
    if not isinstance(date_str, str):
        return None

    date_str = re.sub(r'\s+', ' ', re.sub(
        r'(?i)[,|]|(mon|tues?|wed(nes)?|thu(rs)?|fri|sat(ur)?|sun)(day)?', '', date_str))

    pm_delta = 12 if re.search(r'(?i)PM', date_str) else 0
    timezone, date_str = extract_timezone(
        date_str, default=dt.timedelta(hours=tz_offset) if tz_offset else None)

    # Remove AM/PM + timezone
    date_str = re.sub(r'(?i)\s*(?:AM|PM)(?:\s+[A-Z]+)?', '', date_str)

    # Remove unrecognized timezones from ISO 8601 alike timestamps
    m = re.search(r'\d{1,2}:\d{1,2}(?:\.\d+)?(?P<tz>\s*[A-Z]+)$', date_str)
    if m:
        date_str = date_str[:-len(m.group('tz'))]

    # Python only supports microseconds, so remove nanoseconds
    m = re.search(r'^([0-9]{4,}-[0-9]{1,2}-[0-9]{1,2}T[0-9]{1,2}:[0-9]{1,2}:[0-9]{1,2}\.[0-9]{6})[0-9]+$', date_str)
    if m:
        date_str = m.group(1)

    for expression in date_formats(day_first):
        with contextlib.suppress(ValueError):
            dt_ = dt.datetime.strptime(date_str, expression) - timezone + dt.timedelta(hours=pm_delta)
            return calendar.timegm(dt_.timetuple())

    timetuple = email.utils.parsedate_tz(date_str)
    if timetuple:
        return calendar.timegm(timetuple) + pm_delta * 3600 - int(timezone.total_seconds())

def write_string(s, out=None, encoding=None):
    """jsinterp's debug sink.

    yt-dlp writes interpreter tracing to stderr; a Kodi addon has no stderr
    worth writing to, and this only fires when JSInterpreter is constructed
    with strict debugging. Kept as a no-op so the vendored file needs no edit.
    """
