"""Home page: a big search bar, what's waiting on you, your favorite pages and the records you opened recently.

Favorites and recent pages are saved per user in the user_pages table. (On the free Render plan they reset when
the site sleeps, like all other data.)
"""
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from flask import g, jsonify, redirect, render_template_string, request

from auth import add_user_block, can
import repairs as R

SCHEMA = """
CREATE TABLE IF NOT EXISTS user_pages (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL,
    kind TEXT NOT NULL,        -- 'fav' or 'recent'
    url TEXT NOT NULL,
    label TEXT NOT NULL,
    at TEXT NOT NULL,
    UNIQUE(username, kind, url)
);
"""
RECENT_KEEP = 8
# Only individual records go into "Recent" (lists and searches don't).
RECORD_PAGES = [(re.compile(r'^/repairs/\d+$'), 'Repair ticket'), (re.compile(r'^/cores/\d+$'), 'Core'),
                (re.compile(r'^/unit/\d+$'), 'Stock item'), (re.compile(r'^/clients/\d+$'), 'Customer'),
                (re.compile(r'^/products/\d+$'), 'Product')]


def ensure_home_schema(c):
    c.executescript(SCHEMA)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def page_kind(url):
    path = url.split('?')[0]
    for pattern, kind in RECORD_PAGES:
        if pattern.match(path):
            return kind
    return 'Page'


def clean_url(url):
    url = str(url or '').strip()
    return url[:300] if url.startswith('/') and not url.startswith('//') else ''


def register_home_routes(app, conn, base_dir):
    template = add_user_block((Path(base_dir) / 'home.html').read_text(encoding='utf-8'))

    @app.route('/')
    def home():
        # Old inventory links (/?view=...) still work: they go to the inventory list.
        if any(k in request.args for k in ('view', 'q', 'page')):
            return redirect('/inventory?' + urlencode(list(request.args.items(multi=True))))
        me = g.user['username']
        c = conn()
        favs = c.execute("SELECT * FROM user_pages WHERE username=? AND kind='fav' ORDER BY label COLLATE NOCASE", (me,)).fetchall()
        recent = c.execute("SELECT * FROM user_pages WHERE username=? AND kind='recent' ORDER BY at DESC LIMIT ?", (me, RECENT_KEEP)).fetchall()
        tiles = work_tiles(c, me)
        c.close()
        return render_template_string(template, favs=favs, recent=recent, tiles=tiles, page_kind=page_kind)

    def work_tiles(c, me):
        """Counts of what's waiting, depending on the person's roles."""
        tiles = []
        open_rows = [R.ticket_view(r) for r in c.execute(
            "SELECT r.*, cl.account_owner FROM customer_repairs r LEFT JOIN clients cl ON cl.id=r.client_id WHERE r.stage<>'Closed'")]
        mine = [t for t in open_rows if t['assigned_to'] == me or t.get('account_owner') == me]
        tiles.append({'label': 'My open tickets', 'count': len(mine), 'hint': 'Assigned to you, or your customers',
                      'url': '/repairs?queue=mine', 'tone': ''})
        for team in sorted(R.user_teams()):
            waiting = [t for t in open_rows if t['team'] == team]
            red = [t for t in waiting if t['age'] == 'red']
            tiles.append({'label': f'Waiting on {team}', 'count': len(waiting), 'hint': 'Open tickets in your stages',
                          'url': '/repairs?' + urlencode({'queue': team}), 'tone': ''})
            if red:
                tiles.append({'label': f'{team}: over 7 days', 'count': len(red), 'hint': 'Oldest first',
                              'url': '/repairs?' + urlencode({'queue': team, 'sort': 'in_stage', 'dir': 'asc'}), 'tone': 'red'})
        if can('inventory_value'):
            # Admin only: what the stock on hand and out on loan/rental cost us (recorded costs, not a valuation).
            for label, statuses, url in (('Active stock value', ('Active',), '/inventory'),
                                         ('Loaned / rented out', ('On loan', 'Rental'), '/inventory?view=On%20loan')):
                marks = ','.join('?' * len(statuses))
                v = c.execute(f'SELECT COUNT(*), COALESCE(SUM(cost),0), COUNT(cost) FROM units WHERE status IN ({marks})', statuses).fetchone()
                hint = f"{v[0]} items" + (f" · {v[0] - v[2]} without a cost" if v[0] - v[2] else '')
                tiles.append({'label': label, 'count': f'${v[1]:,.0f}', 'hint': hint, 'url': url, 'tone': ''})
        if can('core_work'):
            n = c.execute("SELECT COUNT(*) FROM core_items WHERE stage IN ('Needs Repair','In Repair')").fetchone()[0]
            tiles.append({'label': 'Cores to work on', 'count': n, 'hint': 'Needs Repair or In Repair', 'url': '/cores', 'tone': ''})
        if can('core_convert'):
            n = c.execute("SELECT COUNT(*) FROM core_items WHERE stage='Ready for Stock'").fetchone()[0]
            tiles.append({'label': 'Cores ready to convert', 'count': n, 'hint': 'Ready for Stock',
                          'url': '/cores?stage=Ready%20for%20Stock', 'tone': 'green' if n else ''})
        return tiles

    @app.post('/api/page-visit')
    def page_visit():
        data = request.get_json(silent=True) or {}
        url, label = clean_url(data.get('url')), str(data.get('label') or '').strip()[:120]
        if not url or page_kind(url) == 'Page' or not label:
            return jsonify(ok=False)
        me = g.user['username']
        c = conn()
        c.execute("""INSERT INTO user_pages(username, kind, url, label, at) VALUES (?, 'recent', ?, ?, ?)
                     ON CONFLICT(username, kind, url) DO UPDATE SET label=excluded.label, at=excluded.at""", (me, url, label, now_iso()))
        c.execute("""DELETE FROM user_pages WHERE username=? AND kind='recent' AND id NOT IN
                     (SELECT id FROM user_pages WHERE username=? AND kind='recent' ORDER BY at DESC LIMIT 20)""", (me, me))
        c.commit(); c.close()
        return jsonify(ok=True)

    @app.route('/api/favorite', methods=['GET', 'POST'])
    def favorite():
        me = g.user['username']
        data = (request.get_json(silent=True) or {}) if request.method == 'POST' else request.args
        url = clean_url(data.get('url'))
        if not url:
            return jsonify(fav=False)
        c = conn()
        row = c.execute("SELECT id FROM user_pages WHERE username=? AND kind='fav' AND url=?", (me, url)).fetchone()
        if request.method == 'POST':
            if row:
                c.execute('DELETE FROM user_pages WHERE id=?', (row['id'],))
            else:
                label = str(data.get('label') or url).strip()[:120]
                c.execute("INSERT INTO user_pages(username, kind, url, label, at) VALUES (?, 'fav', ?, ?, ?)", (me, url, label, now_iso()))
            c.commit()
            row = None if row else True
        c.close()
        return jsonify(fav=bool(row))

    @app.post('/favorites/remove')
    def favorite_remove():
        c = conn()
        c.execute("DELETE FROM user_pages WHERE username=? AND kind='fav' AND id=?", (g.user['username'], request.form.get('id', '')))
        c.commit(); c.close()
        return redirect('/')
