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
from urllib.parse import urlencode

from flask import request

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


# ---------- Work time: Start / End / Total ----------

def minutes(hhmm):
    try:
        h, m = (int(x) for x in str(hhmm).split(':')[:2])
        return h * 60 + m if 0 <= h < 24 and 0 <= m < 60 else None
    except ValueError:
        return None


def work_hours(start, end):
    """(hours as a decimal, error). Both blank = no time logged. End must be after start."""
    start, end = (start or '').strip(), (end or '').strip()
    if not start and not end:
        return None, None
    a, b = minutes(start), minutes(end)
    if a is None or b is None:
        return None, 'Enter both a start and an end time, or leave both blank.'
    if b <= a:
        return None, 'The end time must be after the start time.'
    return round((b - a) / 60, 2), None


def hours(value):
    """1.5 -> '1.50 hrs'."""
    try:
        return f'{float(value):.2f} hrs'
    except (TypeError, ValueError):
        return ''


# ---------- Sortable table headers ----------

def sort_header(key, label, default=None):
    """A column header link: click to sort by this column, click again to reverse. Keeps the page's other filters."""
    current = request.args.get('sort') or default
    direction = request.args.get('dir', 'asc')
    new_dir = 'desc' if (current == key and direction == 'asc') else 'asc'
    args = {k: v for k, v in request.args.items() if k not in ('sort', 'dir', 'page')}
    args.update(sort=key, dir=new_dir)
    arrow = (' ▲' if direction == 'asc' else ' ▼') if current == key else ''
    return Markup(f'<a class="sortlink" href="?{escape(urlencode(args))}">{escape(label)}{arrow}</a>')


def order_by(columns, default, default_dir='asc'):
    """SQL ORDER BY for the requested column (only names in `columns` are ever used)."""
    key = request.args.get('sort') or default
    if key not in columns:
        key = default
    direction = 'DESC' if request.args.get('dir', default_dir) == 'desc' else 'ASC'
    return f'{columns[key]} {direction}'


def register_formatting(app):
    app.jinja_env.filters['day'] = day
    app.jinja_env.filters['daytime'] = daytime
    app.jinja_env.filters['rich'] = rich
    app.jinja_env.filters['clock'] = clock
    app.jinja_env.filters['editor_value'] = editor_value
    app.jinja_env.filters['hours'] = hours
    app.jinja_env.globals['sort_header'] = sort_header


def clean_link(text):
    """A web link typed on a ticket update (v1.17): http(s) only, or ValueError. Blank -> None."""
    text = (text or '').strip()
    if not text:
        return None
    if not re.match(r'^https?://[^\s<>"]+$', text, re.I) or len(text) > 500:
        raise ValueError('Links must start with https:// (for example a OneDrive share link).')
    return text
