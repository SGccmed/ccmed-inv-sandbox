"""Colour themes (v1.16): Light (the original look), Dark (blue / black) and OLED (black / dark blue).

Each person picks a theme on the Home page; it's saved on their account (users.theme), so it follows them
to any computer. The page templates were written with fixed light colours, so instead of rewriting every
template, the theme is applied to the finished page: every colour inside <style> blocks and style="..."
attributes is swapped for its themed colour on the way out (after_request). The swap depends on what the
colour is used for: a background, a border or text. So white stays white on a blue button but a white card
turns dark.

    PALETTES   the approved palettes (v1.16 palette proposal, Nov 13, 2026)
    BG / TEXT / BORDER   which original colour becomes which palette colour
    shade()    any colour not listed is darkened (backgrounds, borders) or lightened (text) by rule,
               keeping its hue, so new pages still look right in the dark themes

Adding a page: write it with the light colours as usual. Check it once in Dark and OLED (Home → Theme).
If something looks off, add its colour to the lists below rather than special-casing the page.
"""
import colorsys
import re
from functools import lru_cache

from flask import g, jsonify, request

THEMES = ('light', 'dark', 'oled')
THEME_NAMES = {'light': 'Light', 'dark': 'Dark', 'oled': 'OLED'}

PALETTES = {
    'light': dict(bg='#f3f6fa', surface='#ffffff', surface2='#f0f5f9', border='#dfe7ef', text='#1b3043', muted='#5f7486',
                  side='#112a42', sidetext='#dce9f4', sidemuted='#91a9be', hover='#203d56', active='#2b5f88', primary='#245d86',
                  link='#245d86', ok_bg='#e8f6eb', ok='#1d6b33', warn_bg='#fff4dc', warn='#7a5000', bad_bg='#fdecea', bad='#a8231b',
                  input='#ffffff', soft='#e7f0f6', warn_line='#f0d49b', bad_line='#f2b8b0'),
    'dark': dict(bg='#0e1622', surface='#162231', surface2='#1c2a3c', border='#2a3c52', text='#e3ebf4', muted='#9aacbf',
                 side='#0a121d', sidetext='#cbd9e8', sidemuted='#7f95ab', hover='#15253a', active='#2a5a87', primary='#2f6ea8',
                 link='#80b8ec', ok_bg='#12301f', ok='#7ad69d', warn_bg='#3a2d0e', warn='#f2c862', bad_bg='#3d1818', bad='#ff9a90',
                 input='#0f1a27', soft='#1f3550', warn_line='#5a4718', bad_line='#5a2522'),
    'oled': dict(bg='#000000', surface='#060a10', surface2='#0b121c', border='#16263a', text='#e6eef7', muted='#8a9cb0',
                 side='#000000', sidetext='#c4d3e3', sidemuted='#6f849a', hover='#0b1726', active='#123a63', primary='#1d5596',
                 link='#6fabe8', ok_bg='#05180d', ok='#62cc8f', warn_bg='#1d1605', warn='#e8bb4f', bad_bg='#200808', bad='#ff8078',
                 input='#000000', soft='#0d1d30', warn_line='#3a2d0a', bad_line='#3a1210'),
}

# Original (light) colours -> palette role, by what they're used for.
BG = {
    'bg': ['#f3f6fa', '#f4f6f9', '#f6f9fb', '#f7f9fc', '#eef2f6', '#eef0f3', '#f3f0ea'],
    'surface': ['#ffffff', '#fff', 'white'],
    'surface2': ['#f0f5f9', '#edf3f8', '#e9eff5', '#eaf2f9', '#eef5fb', '#e5edf3', '#f1f4f7', '#f6f9fb'],
    'soft': ['#e7f0f6', '#e8f1f8', '#e3eef7'],
    'side': ['#112a42', '#0b1f33'],
    'hover': ['#203d56'],
    'active': ['#2b5f88'],
    'primary': ['#245d86', '#21628b'],
    'ok_bg': ['#e8f6eb', '#dcf3e6', '#e3f4e6'],
    'warn_bg': ['#fff4dc', '#fff6e5', '#fff6d6', '#fff0bc'],
    'bad_bg': ['#fdecea', '#ffe0df', '#f1e4e4', '#fffafa'],
}
TEXT = {
    'text': ['#182b3c', '#1b3043', '#112a42', '#1b2f42'],
    'muted': ['#667c8d', '#667a8e', '#697b8d', '#5d6f80', '#55697b', '#5f7486', '#6b7785', '#7c8d9e', '#8696a5', '#7a8a99'],
    'link': ['#245d86', '#21628b'],
    'ok': ['#236947', '#1d6b33'],
    'warn': ['#8a5a00', '#7a5600', '#795500', '#7a5000'],
    'bad': ['#b3261e', '#8e1f15', '#a42c28', '#c0392b', '#8a2c2c', '#a8231b'],
}
BORDER = {
    'border': ['#dfe7ef', '#e5edf3', '#eaf0f5', '#cdd9e4', '#cbd8e2', '#c9d5e0', '#dfe7ee', '#e1e7ee', '#e9eff5', '#eef2f6',
               '#cfdeea', '#bcd3e6', '#e4edf5'],
    'warn_line': ['#f0d49b', '#f0ddb8'],
    'bad_line': ['#f2b8b0'],
}
KEEP = {'#f5c451', '#d64541', '#3a2a00'}       # sidebar count badges and "Viewing as" stay the same in every theme


def _norm(color):
    c = color.lower()
    if c == 'white':
        return '#ffffff'
    if re.fullmatch(r'#[0-9a-f]{3}', c):
        return '#' + ''.join(ch * 2 for ch in c[1:])
    return c


def _table(groups, theme):
    pal = PALETTES[theme]
    return {_norm(c): pal[role] for role, colors in groups.items() for c in colors}


def shade(color, kind, theme):
    """A colour with no entry in the lists: darken backgrounds/borders, lighten text, keep hue."""
    c = _norm(color)
    r, gr, b = (int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, s = colorsys.rgb_to_hls(r, gr, b)
    if kind == 'text':
        if l > 0.5:
            return color                                   # already light (e.g. white on a button)
        l = 0.88 - l * 0.4
    elif kind == 'border':
        if l < 0.5:
            return color
        l = (0.12 if theme == 'oled' else 0.18) + (1 - l) * 0.6
        s *= 0.6
    else:
        if l < 0.5:
            return color                                   # already dark (buttons, the sidebar)
        l = (0.02 if theme == 'oled' else 0.11) + (1 - l) * (0.45 if theme == 'oled' else 0.5)
        s *= 0.6
    r, gr, b = colorsys.hls_to_rgb(h, max(0, min(1, l)), max(0, min(1, s)))
    return '#%02x%02x%02x' % (round(r * 255), round(gr * 255), round(b * 255))


def kind_of(prop):
    p = prop.strip().lower()
    if p.startswith('--'):
        if any(w in p for w in ('line', 'border')):
            return 'border'
        if any(w in p for w in ('ink', 'text', 'muted')):
            return 'text'
        return 'bg'
    if p in ('color', 'fill', 'stroke', 'caret-color', 'text-decoration-color'):
        return 'text'
    if p.startswith('border') or p.startswith('outline') or p in ('box-shadow', 'column-rule'):
        return 'border'
    if p.startswith('background'):
        return 'bg'
    return None


HEX = re.compile(r'#[0-9a-fA-F]{8}\b|#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b|\bwhite\b')
DECL = re.compile(r'([a-zA-Z-]+)(\s*:\s*)([^;{}"]+)')


@lru_cache(maxsize=4096)
def theme_css(css, theme):
    """Swap the colours in a piece of CSS (a style block or a style attribute) for the theme's."""
    if theme == 'light':
        # Light keeps the original look; only the grey, warning and error text are darkened a little to read better.
        tables = {'text': _table({k: TEXT[k] for k in ('muted', 'warn', 'bad')}, 'light'), 'bg': {}, 'border': {}}
    else:
        tables = {'bg': _table(BG, theme), 'text': _table(TEXT, theme), 'border': _table(BORDER, theme)}

    def swap_value(kind, value):
        def one(m):
            raw = m.group(0)
            alpha = ''
            if len(raw) == 9:                              # #rrggbbaa: keep the transparency
                raw, alpha = raw[:7], raw[7:]
            key = _norm(raw)
            if key in KEEP:
                return m.group(0)
            new = tables[kind].get(key)
            if new is None:
                if theme == 'light':
                    return m.group(0)
                new = shade(key, kind, theme)
            return new + alpha
        return HEX.sub(one, value)

    def decl(m):
        kind = kind_of(m.group(1))
        if not kind:
            return m.group(0)
        return m.group(1) + m.group(2) + swap_value(kind, m.group(3))
    return DECL.sub(decl, css)


STYLE_BLOCK = re.compile(r'(<style[^>]*>)(.*?)(</style>)', re.S | re.I)
STYLE_ATTR = re.compile(r'''(\sstyle=)(["'])(.*?)\2''', re.S)

DARK_EXTRA = '''<meta name="color-scheme" content="dark"><style>
:root{color-scheme:dark}
trix-toolbar .trix-button-group{border-color:%(border)s!important}
trix-toolbar .trix-button{background:%(surface2)s!important;color:%(text)s!important;border-color:%(border)s!important}
trix-toolbar .trix-button.trix-active{background:%(active)s!important}
trix-toolbar .trix-button--icon::before{filter:invert(1)}
input::placeholder,textarea::placeholder{color:%(muted)s;opacity:.8}
select,input,textarea{background-color:%(input)s;color:%(text)s}
</style>'''


def apply_theme(html, theme):
    if theme not in THEMES:
        return html
    html = STYLE_BLOCK.sub(lambda m: m.group(1) + theme_css(m.group(2), theme) + m.group(3), html)
    html = STYLE_ATTR.sub(lambda m: m.group(1) + m.group(2) + theme_css(m.group(3), theme) + m.group(2), html)
    html = re.sub(r'<html\b', f'<html data-theme="{theme}"', html, count=1)
    if theme != 'light':
        html = re.sub(r'<head>', '<head>' + DARK_EXTRA % PALETTES[theme], html, count=1)
    return html


def ensure_theme_schema(c):
    if 'theme' not in {r[1] for r in c.execute('PRAGMA table_info(users)')}:
        c.execute("ALTER TABLE users ADD COLUMN theme TEXT NOT NULL DEFAULT 'light'")


def current_theme():
    u = g.get('user')
    try:
        t = u['theme'] if u is not None else 'light'
    except (IndexError, KeyError):
        t = 'light'
    return t if t in THEMES else 'light'


def register_theme(app, conn):
    @app.context_processor
    def theme_ctx():
        return {'current_theme': current_theme(), 'theme_names': THEME_NAMES}

    @app.post('/api/theme')
    def set_theme():
        if not g.get('user'):
            return jsonify(ok=False), 401
        data = request.get_json(silent=True) or request.form
        t = (data.get('theme') or '').strip().lower()
        if t not in THEMES:
            return jsonify(ok=False, error='Pick light, dark or oled'), 400
        c = conn()
        try:
            c.execute('UPDATE users SET theme=? WHERE id=?', (t, g.user['id']))
            c.commit()
        finally:
            c.close()
        return jsonify(ok=True, theme=t)

    @app.after_request
    def themed(resp):
        if resp.mimetype != 'text/html' or resp.direct_passthrough or resp.status_code >= 300 and resp.status_code < 400:
            return resp
        theme = current_theme()
        html = resp.get_data(as_text=True)
        resp.set_data(apply_theme(html, theme))
        return resp
