"""Cost adjustments (Admin and SuperAdmin only).

Any recorded cost or price can be corrected with a required reason. The old value, new value, who changed it,
when and why are kept in cost_adjustments and shown (to roles that can see costs) on the record's page.

What can be adjusted:
    unit          a stock item's cost (units.cost)
    core          a core's purchase cost (core_items.original_cost)
    core_parts    parts cost on one core update (core_transactions.parts_cost)
    core_other    additional cost on one core update (core_transactions.other_cost)
    repair_quote  the quoted price on a customer repair ticket (customer_repairs.quoted_price)
"""
from datetime import datetime, timezone

from flask import abort, flash, g, redirect, request

from auth import can

SCHEMA = """
CREATE TABLE IF NOT EXISTS cost_adjustments (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,          -- see the list above
    record_id INTEGER NOT NULL,  -- the row that was changed
    page TEXT NOT NULL,          -- the page it belongs to, e.g. /cores/3
    label TEXT,                  -- what was changed, in words
    old_value REAL, new_value REAL,
    reason TEXT NOT NULL,
    actor TEXT NOT NULL,
    at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cost_adjustments_page ON cost_adjustments(page);
"""

# kind: (table, column, page url pattern, words)
TARGETS = {
    'unit': ('units', 'cost', '/unit/{id}', 'Stock cost'),
    'core': ('core_items', 'original_cost', '/cores/{id}', 'Core purchase cost'),
    'core_parts': ('core_transactions', 'parts_cost', '/cores/{parent}', 'Parts cost on an update'),
    'core_other': ('core_transactions', 'other_cost', '/cores/{parent}', 'Additional cost on an update'),
    'repair_quote': ('customer_repairs', 'quoted_price', '/repairs/{id}', 'Quoted price'),
}


def ensure_cost_schema(c):
    c.executescript(SCHEMA)


def adjustments_for(c, page):
    return c.execute('SELECT * FROM cost_adjustments WHERE page=? ORDER BY at DESC, id DESC', (page,)).fetchall()


def register_cost_routes(app, conn):
    @app.post('/adjust-cost')
    def adjust_cost():
        if not can('cost_adjust'):
            abort(403)
        f = request.form
        kind, rid = f.get('kind', ''), f.get('record_id', '')
        if f.get('pick'):                      # a drop-down of "kind:id" choices (core page)
            kind, _, rid = f['pick'].partition(':')
        back = f.get('back', '/')
        if not back.startswith('/'):
            back = '/'
        if kind not in TARGETS or not rid.isdigit():
            flash('Pick what to adjust.'); return redirect(back)
        reason = (f.get('reason') or '').strip()
        if not reason:
            flash('A reason is required to adjust a cost.'); return redirect(back)
        text = (f.get('amount') or '').strip().replace('$', '').replace(',', '')
        try:
            new = round(float(text), 2)
        except ValueError:
            flash('Enter the new amount as a number.'); return redirect(back)
        if new < 0:
            flash("Costs can't be negative."); return redirect(back)
        table, column, page, words = TARGETS[kind]
        c = conn()
        row = c.execute(f'SELECT * FROM {table} WHERE id=?', (int(rid),)).fetchone()
        if row is None:
            c.close(); abort(404)
        if kind.startswith('core'):
            core_id = row['core_id'] if kind in ('core_parts', 'core_other') else row['id']
            stage = c.execute('SELECT stage FROM core_items WHERE id=?', (core_id,)).fetchone()[0]
            if stage in ('Converted to Stock', 'Returned to Stock'):
                c.close(); flash('This core is already stock; adjust the stock item\'s cost instead.'); return redirect(back)
        old = row[column]
        url = page.format(id=row['id'], parent=row['core_id'] if 'core_id' in row.keys() else '')
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        c.execute(f'UPDATE {table} SET {column}=? WHERE id=?', (new, int(rid)))
        c.execute("""INSERT INTO cost_adjustments(kind, record_id, page, label, old_value, new_value, reason, actor, at)
                     VALUES (?,?,?,?,?,?,?,?,?)""", (kind, int(rid), url, words, old, new, reason, g.user['username'], now))
        # Leave a trace in the record's own log, without amounts (technicians read these logs).
        if kind == 'repair_quote':
            c.execute("""INSERT INTO repair_log(repair_id, at, actor, kind, text) VALUES (?,?,?,?,?)""",
                      (row['id'], now, g.user['username'], 'edit', f'Quoted price adjusted. Reason: {reason}'))
        c.commit(); c.close()
        flash(f'{words} adjusted.')
        return redirect(back)
