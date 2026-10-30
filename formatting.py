"""How dates and long notes are shown on every page.

Dates are stored in UTC and shown in Pacific time:
    {{ value|day }}      -> Oct 30, 2026
    {{ value|daytime }}  -> Oct 30, 2026 · 2:15 PM
Long notes can use simple formatting (see rich()): numbered lists, bullets and **bold**.
"""
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from markupsafe import Markup, escape

PACIFIC = ZoneInfo('America/Los_Angeles')


def to_pacific(value):
    """Parse a stored date/time and return (datetime in Pacific time, has_time). None if it can't be read."""
    if value is None or value == '':
        return None, False
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        return datetime(value.year, value.month, value.day), False
    else:
        text = str(value).strip()
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):          # a plain date: no time zone to convert
            return datetime.fromisoformat(text), False
        try:
            dt = datetime.fromisoformat(text.replace('Z', '+00:00').replace(' ', 'T', 1))
        except ValueError:
            return None, False
    if dt.tzinfo is None:                                      # stored without a zone = UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(PACIFIC), True


def day(value, empty='—'):
    dt, _ = to_pacific(value)
    if dt is None:
        return value or empty
    return f"{dt.strftime('%b')} {dt.day}, {dt.year}"


def daytime(value, empty='—'):
    dt, has_time = to_pacific(value)
    if dt is None:
        return value or empty
    text = f"{dt.strftime('%b')} {dt.day}, {dt.year}"
    if has_time:
        hour = dt.hour % 12 or 12
        text += f" · {hour}:{dt.minute:02d} {'AM' if dt.hour < 12 else 'PM'}"
    return text


def clock(value):
    """'14:15' -> '2:15 PM' (a time of day typed into a form)."""
    try:
        h, m = (int(x) for x in str(value).split(':')[:2])
        return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"
    except ValueError:
        return value


def today_pacific():
    return datetime.now(PACIFIC).date()


def rich(text):
    """Show a note with simple formatting, safely (no HTML from the note is ever run).

    1. Lines starting "1." "2." ... become a numbered list
    - Lines starting "-" or "*" become bullets
    **words** become bold; blank lines separate paragraphs.
    """
    out, kind, para = [], None, []

    def close():
        nonlocal kind, para
        if para:
            out.append('<p>' + '<br>'.join(para) + '</p>'); para = []
        if kind:
            out.append(f'</{kind}>'); kind = None

    def inline(s):
        s = str(escape(s))
        return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', s)

    for raw in str(text or '').splitlines():
        line = raw.rstrip()
        m_num = re.match(r'\s*\d+[.)]\s+(.*)', line)
        m_bul = re.match(r'\s*[-*•]\s+(.*)', line)
        if m_num or m_bul:
            want = 'ol' if m_num else 'ul'
            if para:
                out.append('<p>' + '<br>'.join(para) + '</p>'); para = []
            if kind != want:
                if kind:
                    out.append(f'</{kind}>')
                out.append(f'<{want}>'); kind = want
            out.append('<li>' + inline((m_num or m_bul).group(1)) + '</li>')
        elif not line.strip():
            close()
        else:
            if kind:
                out.append(f'</{kind}>'); kind = None
            para.append(inline(line))
    close()
    return Markup(''.join(out))


def register_formatting(app):
    app.jinja_env.filters['day'] = day
    app.jinja_env.filters['daytime'] = daytime
    app.jinja_env.filters['rich'] = rich
    app.jinja_env.filters['clock'] = clock
