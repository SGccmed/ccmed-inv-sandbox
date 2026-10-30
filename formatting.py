"""How dates and long notes are shown on every page.

Dates are stored in UTC and shown in Pacific time:
    {{ value|day }}      -> Oct 30, 2026
    {{ value|daytime }}  -> Oct 30, 2026 · 2:15 PM
Notes written in the rich text editor are stored as HTML and cleaned (only safe formatting tags kept) every time
they're shown. Older plain-text notes still show, with "1." lines as numbered lists and **bold**.
"""
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import nh3
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


# Formatting the editor can produce. Anything else (scripts, styles, images, event handlers) is removed.
SAFE_TAGS = {'div', 'p', 'br', 'strong', 'b', 'em', 'i', 'u', 'del', 's', 'ol', 'ul', 'li', 'h1', 'h2', 'h3',
             'blockquote', 'pre', 'code', 'a'}
HTML_HINT = re.compile(r'<(div|p|br|strong|b|em|i|u|del|s|ol|ul|li|h[1-3]|blockquote|pre|code|a)\b', re.I)


def is_html(text):
    return bool(HTML_HINT.search(str(text or '')))


def clean_html(html):
    return nh3.clean(str(html or ''), tags=SAFE_TAGS, attributes={'a': {'href'}}, url_schemes={'http', 'https', 'mailto'},
                     link_rel='noopener noreferrer')


def has_text(value):
    """True if a note has any visible text (an editor can send back empty tags)."""
    return bool(re.sub(r'<[^>]+>|&nbsp;|\s', '', str(value or '')))


def clean_note(value):
    """What to store from a note field: '' if empty, cleaned HTML from the editor, or plain text as typed."""
    value = str(value or '').strip()
    if not has_text(value):
        return ''
    return clean_html(value) if is_html(value) else value


def editor_value(text):
    """What to load into the rich text editor: cleaned HTML, or an older plain note converted to HTML."""
    if not str(text or '').strip():
        return ''
    return clean_html(text) if is_html(text) else str(rich(text))


def rich(text):
    """Show a note with formatting, safely. Editor notes are cleaned HTML; older plain notes are converted.

    1. Lines starting "1." "2." ... become a numbered list
    - Lines starting "-" or "*" become bullets
    **words** become bold; blank lines separate paragraphs.
    """
    if is_html(text):
        return Markup('<div class="rich">' + clean_html(text) + '</div>')
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
    app.jinja_env.filters['editor_value'] = editor_value
