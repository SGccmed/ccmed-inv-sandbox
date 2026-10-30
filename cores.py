"""Core repairs (CR): broken units we own and fix to sell.

A core is worked like a ticket: technicians post "ticket updates" (what they did, the day and time,
parts and other costs, and a status change). Every cost adds to the core. When it's ready, an Admin
converts it into a new stock (CC) item that carries the full cost.
"""
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from flask import abort, flash, g, redirect, render_template_string, request, url_for

from auth import ADMIN_ROLES, add_user_block, can, roles_of
from formatting import today_pacific

# Status: on hold or not (on-hold statuses don't age), and what to do next.
STAGES = {
    'Needs Repair': {'hold': False, 'guide': "Waiting for a technician. Post a ticket update and set the status to In Repair when you start."},
    'In Repair': {'hold': False, 'guide': "Being worked on. Post an update each time you work on it: what you did, parts used and their cost. "
                                          "When it's fixed and tested, set the status to Ready for Stock."},
    'Waiting on Parts': {'hold': True, 'guide': "Paused until parts arrive (on hold, so it doesn't age). Say which part and when it's expected "
                                                "in your update. Set it back to In Repair when they arrive."},
    'On Hold': {'hold': True, 'guide': "Paused (on hold). Say why in your update. Includes cores we can't fix right now."},
    'Ready for Stock': {'hold': False, 'guide': "Fixed and tested; the update that set this status is the QC sign-off. "
                                                "An Admin converts it to a CC stock item, carrying the full cost."},
    'Converted to Stock': {'hold': False, 'guide': "Done. It's now a stock item; this core can't be changed."},
}
WORK_STAGES = [s for s in STAGES if s != 'Converted to Stock']   # statuses a tech can set
OLD_STAGES = {'QC': 'In Repair'}                                 # before v1.07

CORE_COLUMNS = [('stage_started_at', 'TEXT')]
UPDATE_COLUMNS = [('work_date', 'TEXT'), ('work_time', 'TEXT'), ('actor', 'TEXT'), ('old_stage', 'TEXT'), ('new_stage', 'TEXT')]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_core_schema(c):
    """Add v1.07 columns to the core tables (created in app.py) and rename old statuses. Safe to run every start."""
    for table, columns in (('core_items', CORE_COLUMNS), ('core_transactions', UPDATE_COLUMNS)):
        have = {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
        for name, typ in columns:
            if name not in have:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {typ}')
    for old, new in OLD_STAGES.items():
        c.execute('UPDATE core_items SET stage=? WHERE stage=?', (new, old))
    c.execute('UPDATE core_items SET stage_started_at=created_at WHERE stage_started_at IS NULL')


def money(text, label):
    text = (text or '').strip().replace('$', '').replace(',', '')
    if not text:
        return 0.0
    try:
        n = round(float(text), 2)
    except ValueError:
        raise ValueError(f'{label} must be a number')
    if n < 0:
        raise ValueError(f'{label} cannot be negative')
    return n


def days_since(ts):
    try:
        then = datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
        if then.tzinfo is None:
            then = then.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - then).days)
    except ValueError:
        return 0


def core_view(r):
    d = dict(r)
    info = STAGES.get(d['stage'], {'hold': False, 'guide': ''})
    d['hold'] = info['hold']
    d['stage_days'] = days_since(d.get('stage_started_at') or d['created_at'])
    if d['stage'] == 'Converted to Stock':
        d['age'] = 'closed'
    elif info['hold']:
        d['age'] = 'hold'
    else:
        d['age'] = 'red' if d['stage_days'] > 7 else 'yellow' if d['stage_days'] > 3 else 'normal'
    return d


def register_core_routes(app, conn, base_dir, inventory_code_maker, norm):
    template = add_user_block((Path(base_dir) / 'cores.html').read_text(encoding='utf-8'))

    def render(**ctx):
        return render_template_string(template, stages=STAGES, work_stages=WORK_STAGES, **ctx)

    def staff(c):
        rows = c.execute('SELECT username, full_name, roles FROM users WHERE active=1 ORDER BY username').fetchall()
        return [{'username': r['username'], 'name': r['full_name'] or r['username']} for r in rows
                if {'Technician', 'Admin', 'SuperAdmin'} & set((r['roles'] or '').split(','))]

    def me_name():
        return g.user['full_name'] or g.user['username']

    @app.route('/cores')
    def cores():
        stage = request.args.get('stage', '')
        c = conn()
        where, args = '', []
        if stage in STAGES:
            where, args = ' WHERE c.stage=?', [stage]
        elif stage != 'all':
            where = " WHERE c.stage<>'Converted to Stock'"
        rows = c.execute(f"""
            SELECT c.*,
                   COALESCE((SELECT SUM(labor_cost+parts_cost+other_cost) FROM core_transactions WHERE core_id=c.id),0) repair_cost,
                   (SELECT MAX(performed_at) FROM core_transactions WHERE core_id=c.id) last_update
            FROM core_items c{where}
            ORDER BY CASE WHEN c.stage='Converted to Stock' THEN 1 ELSE 0 END, c.stage_started_at ASC""", args).fetchall()
        counts = {r['stage']: r['n'] for r in c.execute('SELECT stage, COUNT(*) n FROM core_items GROUP BY stage')}
        c.close()
        return render(detail=None, rows=[core_view(r) for r in rows], counts=counts, stage=stage)

    @app.route('/cores/<int:cid>')
    def core_detail(cid):
        c = conn()
        core = c.execute('SELECT * FROM core_items WHERE id=?', (cid,)).fetchone()
        if core is None:
            c.close(); abort(404)
        updates = c.execute('SELECT * FROM core_transactions WHERE core_id=? ORDER BY performed_at DESC, id DESC', (cid,)).fetchall()
        parts = sum(u['parts_cost'] for u in updates)
        other = sum(u['other_cost'] + u['labor_cost'] for u in updates)
        techs = staff(c)
        c.close()
        return render(detail=core_view(core), guide=STAGES.get(core['stage'], {}).get('guide', ''), updates=updates,
                      parts=parts, other=other, total=core['original_cost'] + parts + other, techs=techs,
                      today=today_pacific().isoformat(), me=me_name(), is_admin=bool(roles_of(g.user) & ADMIN_ROLES))

    @app.post('/cores/<int:cid>/update')
    def core_update(cid):
        if not can('core_work'):
            abort(403)
        f = request.form
        c = conn()
        core = c.execute('SELECT * FROM core_items WHERE id=?', (cid,)).fetchone()
        if core is None:
            c.close(); abort(404)
        back = redirect(url_for('core_detail', cid=cid))
        if core['stage'] == 'Converted to Stock':
            c.close(); flash('This core is already stock and can\'t be changed.'); return back
        try:
            parts = money(f.get('parts'), 'Parts cost')
            other = money(f.get('other'), 'Additional cost')
            work_date = f.get('work_date', '').strip() or today_pacific().isoformat()
            date.fromisoformat(work_date)
        except ValueError as e:
            c.close(); flash(str(e) if 'cost' in str(e) else 'Enter a valid day worked on.'); return back
        body = f.get('body', '').strip()
        status = f.get('status', core['stage'])
        if status not in WORK_STAGES:
            status = core['stage']
        changed = status != core['stage']
        if not (body or parts or other or changed):
            c.close(); flash('Write what you did, add a cost, or change the status before submitting.'); return back
        # Who did the work: the signed-in tech, or (Admins only) someone picked from the list.
        actor, name = g.user['username'], me_name()
        pick = f.get('technician', '').strip()
        if pick and pick != actor and roles_of(g.user) & ADMIN_ROLES:
            row = c.execute('SELECT username, full_name FROM users WHERE username=?', (pick,)).fetchone()
            if row:
                actor, name = row['username'], row['full_name'] or row['username']
        now = now_iso()
        c.execute("""INSERT INTO core_transactions(core_id, performed_at, technician, description, labor_method, labor_hours,
                     labor_rate, labor_cost, parts_cost, other_cost, work_date, work_time, actor, old_stage, new_stage)
                     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                  (cid, now, name, body, 'none', 0, 0, 0, parts, other, work_date, f.get('work_time', '').strip(), actor,
                   core['stage'] if changed else None, status if changed else None))
        if changed:
            c.execute('UPDATE core_items SET stage=?, stage_started_at=? WHERE id=?', (status, now, cid))
            if status == 'Ready for Stock':      # this update is the QC sign-off
                c.execute('UPDATE core_items SET qc_result=?, qc_technician=?, qc_date=?, qc_notes=? WHERE id=?',
                          ('Passed', name, now, body[:500], cid))
            elif core['stage'] == 'Ready for Stock':
                c.execute('UPDATE core_items SET qc_result=NULL WHERE id=?', (cid,))
        c.commit(); c.close()
        flash('Update posted' + (f' · status now {status}' if changed else ''))
        return back

    @app.post('/cores/<int:cid>/convert')
    def core_convert(cid):
        if not can('core_convert'):
            abort(403)
        c = conn()
        core = c.execute('SELECT * FROM core_items WHERE id=?', (cid,)).fetchone()
        if core is None:
            c.close(); abort(404)
        if core['stage'] != 'Ready for Stock':
            c.close(); flash('Set the status to Ready for Stock before converting.'); return redirect(url_for('core_detail', cid=cid))
        try:
            c.execute('BEGIN IMMEDIATE')
            repair_cost = c.execute('SELECT COALESCE(SUM(labor_cost+parts_cost+other_cost),0) FROM core_transactions WHERE core_id=?', (cid,)).fetchone()[0]
            code = inventory_code_maker(c, 'CC')
            total = round(core['original_cost'] + repair_cost, 2)
            cur = c.execute("""INSERT INTO units(manufacturer, product_raw, product_key, serial, cost, notes, location_raw, physical_location,
                               status, condition, review, inventory_code, supplier, received_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (core['manufacturer'], core['product_raw'], norm(core['product_raw']), core['serial'], total,
                             'Converted from ' + core['inventory_code'] + '; ' + (core['notes'] or ''), core['location'], core['location'],
                             'Active', 'Refurbished', '', code, core['supplier'], date.today().isoformat()))
            now = now_iso()
            c.execute('UPDATE core_items SET stage=?, stage_started_at=?, converted_unit_id=? WHERE id=?',
                      ('Converted to Stock', now, cur.lastrowid, cid))
            c.execute("""INSERT INTO core_transactions(core_id, performed_at, technician, description, labor_method, labor_hours, labor_rate,
                         labor_cost, parts_cost, other_cost, work_date, actor, old_stage, new_stage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (cid, now, me_name(), f'Converted to stock as {code}.', 'none', 0, 0, 0, 0, 0, today_pacific().isoformat(),
                       g.user['username'], 'Ready for Stock', 'Converted to Stock'))
            c.commit()
        except sqlite3.Error as e:
            c.rollback(); c.close(); flash('Not converted: ' + str(e)); return redirect(url_for('core_detail', cid=cid))
        c.close()
        flash(f'Converted to {code} with total cost ${total:,.2f}')
        return redirect(url_for('core_detail', cid=cid))
