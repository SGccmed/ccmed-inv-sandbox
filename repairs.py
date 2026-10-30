"""Customer repair tickets (RP).

Each customer repair is a ticket, like an IT helpdesk: it has an ID (RP-...), an RMA
number, a serial number, and one team that owns it at each stage. This file holds the
stage rules, the database setup and the Customer Repairs pages.
"""
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from flask import flash, g, redirect, render_template_string, request, url_for

from auth import ADMIN_ROLES, add_user_block, can, roles_of
from clients import client_label, resolve_client, serial_matches
from formatting import clean_note

# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------
# For each stage: whose turn it is, whether it is "on hold" (waiting on someone outside
# the team, so the aging colours pause), and the guide text shown on the ticket.
STAGES = {
    'Received': {
        'team': 'Repair team', 'hold': False,
        'guide': "The unit is checked in and orange-taped. Repair team: pick it up and start the "
                 "evaluation. If we don't repair this model in-house, move it to At Partner instead.",
    },
    'In Evaluation': {
        'team': 'Repair team', 'hold': False,
        'guide': "Diagnose the unit and write the eval findings below: what's wrong, parts needed, rough "
                 "labor. Check this serial's past tickets: if the fault comes from our earlier work, tick "
                 "Warranty. If it can't be fixed, move it to Unrepairable.",
    },
    'Eval Complete': {
        'team': 'Sales', 'hold': False,
        'guide': "Sales: price the repair from the findings, enter the quoted price and the QuickBooks "
                 "estimate number, send the quote to the client, then move it to Awaiting Approval. "
                 "Warranty tickets skip straight to Approved at $0.",
    },
    'Awaiting Approval': {
        'team': 'Sales', 'hold': True,
        'guide': "Waiting on the client (on hold, so these days don't count against the team). When they "
                 "answer, move it to Approved or Declined.",
    },
    'Approved': {
        'team': 'Repair team', 'hold': False,
        'guide': "Repair order confirmed; this is the bench queue. Repair team: assign yourself and start "
                 "the repair. Outsourced repairs go to At Partner.",
    },
    'In Repair': {
        'team': 'Repair team', 'hold': False,
        'guide': "Fix and test the unit (testing is part of the repair). Stuck waiting for parts? Move it "
                 "to Waiting on Parts. Fixed and tested? Move it to Repair Complete.",
    },
    'Waiting on Parts': {
        'team': 'Repair team', 'hold': True,
        'guide': "Paused until parts arrive (on hold). Add a work-log note saying which part and when it's "
                 "expected. Move it back to In Repair when the parts are here.",
    },
    'Repair Complete': {
        'team': 'Sales', 'hold': False,
        'guide': "Sales: tell the client it's done, then release it to Shipping (Ready to Ship).",
    },
    'Ready to Ship': {
        'team': 'Shipping', 'hold': False,
        'guide': "Shipping: pack it and send it back to the client. Add the carrier and tracking number "
                 "as a work-log note, then move it to Shipped.",
    },
    'Shipped': {
        'team': 'Admin', 'hold': False,
        'guide': "Admin: turn the QuickBooks estimate into an invoice, enter the invoice number, then close "
                 "the ticket. Warranty and declined tickets have no invoice; just close them.",
    },
    'Closed': {
        'team': None, 'hold': False,
        'guide': "Done. The resolution shows how it ended. An Admin can reopen it if needed.",
    },
    'At Partner': {
        'team': 'Sales', 'hold': True,
        'guide': "Outsourced (on hold). Log the partner, their price and any partner RMA in the work log. "
                 "Partner's report in: move to Eval Complete. Repair done: move to Ready to Ship, or to "
                 "Shipped if the partner blind-ships to the client on our label.",
    },
    'Declined': {
        'team': 'Sales', 'hold': False,
        'guide': "The client said no. It goes back free (no eval fee, CC pays shipping): release it to "
                 "Shipping. The ticket will close as Declined.",
    },
    'Unrepairable': {
        'team': 'Sales', 'hold': False,
        'guide': "It can't be fixed. Sales: offer the client an exchange. If they accept, close the ticket "
                 "as Exchanged (the core and replacement are handled by hand for now). If not, release it "
                 "to Shipping to send it back.",
    },
}
STAGE_NAMES = list(STAGES)
OPEN_STAGES = [s for s in STAGE_NAMES if s != 'Closed']

# Where a ticket can go next from each stage (admins can also move it anywhere).
NEXT = {
    'Received': ['In Evaluation', 'At Partner'],
    'In Evaluation': ['Eval Complete', 'Unrepairable'],
    'Eval Complete': ['Awaiting Approval', 'Approved'],
    'Awaiting Approval': ['Approved', 'Declined'],
    'Approved': ['In Repair', 'At Partner'],
    'In Repair': ['Waiting on Parts', 'Repair Complete', 'Unrepairable'],
    'Waiting on Parts': ['In Repair'],
    'Repair Complete': ['Ready to Ship'],
    'Ready to Ship': ['Shipped'],
    'Shipped': ['Closed'],
    'Closed': [],
    'At Partner': ['Eval Complete', 'Ready to Ship', 'Shipped'],
    'Declined': ['Ready to Ship'],
    'Unrepairable': ['Closed', 'Ready to Ship'],
}
# Which role acts for each team. "Admin" means the Admin or SuperAdmin role.
TEAM_ROLE = {'Repair team': 'Technician', 'Sales': 'Sales', 'Shipping': 'Shipping'}
# Moves another team may also make (Sales coordinates outsourced repairs).
EXTRA_MOVES = {('Received', 'At Partner'): 'Sales', ('Approved', 'At Partner'): 'Sales'}

RESOLUTIONS = ['Repaired', 'Warranty', 'Declined', 'Exchanged', 'Returned unrepaired']
TEAMS = ['Repair team', 'Sales', 'Shipping', 'Admin']

# Stage names used before v1.05, mapped to the new ones.
OLD_STAGES = {'Open Repairs': 'Received', 'Evaled': 'Eval Complete', 'QC': 'In Repair', 'Completed': 'Closed'}


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
REPAIR_SCHEMA = """
CREATE TABLE IF NOT EXISTS customer_repairs (
    id INTEGER PRIMARY KEY,
    rma TEXT NOT NULL UNIQUE COLLATE NOCASE,
    customer TEXT NOT NULL,
    manufacturer TEXT, model TEXT, part_number TEXT, serial TEXT, location TEXT,
    received_date TEXT NOT NULL,
    technician TEXT,                -- free-text name from before v1.05; assigned_to replaces it
    notes TEXT,                     -- intake notes: what the client reported
    stage TEXT NOT NULL DEFAULT 'Received',
    stage_started_at TEXT NOT NULL,
    created_at TEXT NOT NULL, completed_at TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customer_repair_history (   -- stage changes before v1.05 (copied into repair_log)
    id INTEGER PRIMARY KEY,
    repair_id INTEGER NOT NULL REFERENCES customer_repairs(id),
    old_stage TEXT, new_stage TEXT NOT NULL, changed_at TEXT NOT NULL, note TEXT
);
CREATE TABLE IF NOT EXISTS repair_log (                 -- the ticket's work log
    id INTEGER PRIMARY KEY,
    repair_id INTEGER NOT NULL REFERENCES customer_repairs(id),
    at TEXT NOT NULL,
    actor TEXT,                     -- username, or blank for old entries
    kind TEXT NOT NULL,             -- created / stage / note / edit
    old_stage TEXT, new_stage TEXT,
    text TEXT,
    history_id INTEGER UNIQUE       -- set when copied from customer_repair_history
);
CREATE INDEX IF NOT EXISTS idx_repair_log_repair ON repair_log(repair_id);
"""

NEW_COLUMNS = [
    ('assigned_to', 'TEXT'),        # username of the assigned tech
    ('eval_findings', 'TEXT'),
    ('quoted_price', 'REAL'),
    ('estimate_number', 'TEXT'),
    ('invoice_number', 'TEXT'),
    ('warranty', 'INTEGER NOT NULL DEFAULT 0'),
    ('warranty_of_id', 'INTEGER'),  # the earlier ticket this warranty return relates to
    ('resolution', 'TEXT'),
    ('inventory_code', 'TEXT'),
    ('client_id', 'INTEGER'),
]


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def ensure_repair_schema(c):
    """Create the repair tables and bring older databases up to date. Safe to run every start."""
    c.executescript(REPAIR_SCHEMA)
    cols = {r[1] for r in c.execute('PRAGMA table_info(customer_repairs)')}
    for name, typ in NEW_COLUMNS:
        if name not in cols:
            c.execute(f'ALTER TABLE customer_repairs ADD COLUMN {name} {typ}')
    # Rename pre-v1.05 stages.
    for old, new in OLD_STAGES.items():
        c.execute('UPDATE customer_repairs SET stage=? WHERE stage=?', (new, old))
    c.execute("UPDATE customer_repairs SET resolution='Repaired' WHERE stage='Closed' AND resolution IS NULL")
    # Copy the old stage history into the work log (once per row).
    c.execute("""INSERT INTO repair_log(repair_id, at, actor, kind, old_stage, new_stage, text, history_id)
                 SELECT repair_id, changed_at, '', CASE WHEN old_stage IS NULL THEN 'created' ELSE 'stage' END,
                        old_stage, new_stage, note, id
                 FROM customer_repair_history
                 WHERE id NOT IN (SELECT history_id FROM repair_log WHERE history_id IS NOT NULL)""")


def current_username():
    try:
        return g.user['username'] if g.get('user') else ''
    except RuntimeError:        # outside a web request (demo data script)
        return ''


def log(c, rid, kind, text='', old_stage=None, new_stage=None, when=None):
    actor = current_username()
    c.execute('INSERT INTO repair_log(repair_id, at, actor, kind, old_stage, new_stage, text) VALUES (?,?,?,?,?,?,?)',
              (rid, when or now_iso(), actor, kind, old_stage, new_stage, text))


def next_rma(c):
    """Our own RMA numbers: RMA-10001, RMA-10002, ..."""
    last = c.execute("""SELECT MAX(CAST(SUBSTR(rma, 5) AS INTEGER)) FROM customer_repairs
                        WHERE rma GLOB 'RMA-[0-9][0-9][0-9][0-9][0-9]*'""").fetchone()[0]
    return f'RMA-{max(last or 10000, 10000) + 1}'


def tech_list(c):
    """Active staff who can be assigned a repair (Technicians first, then admins)."""
    rows = c.execute("SELECT username, full_name, roles FROM users WHERE active=1 ORDER BY username").fetchall()
    techs = [r for r in rows if 'Technician' in (r['roles'] or '').split(',')]
    others = [r for r in rows if r not in techs and set((r['roles'] or '').split(',')) & ADMIN_ROLES]
    return [{'username': r['username'], 'name': r['full_name'] or r['username']} for r in techs + others]


def sales_reps(c):
    """Everyone who can own a client: Sales staff plus any account owner already on a client."""
    names = {r['username'] for r in c.execute("SELECT username, roles FROM users WHERE active=1")
             if 'Sales' in (r['roles'] or '').split(',')}
    names |= {r[0] for r in c.execute("SELECT DISTINCT account_owner FROM clients WHERE COALESCE(account_owner,'')<>''")}
    return sorted(names, key=str.lower)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
def user_teams(user=None):
    roles = roles_of(user or g.user)
    teams = {team for team, role in TEAM_ROLE.items() if role in roles}
    if roles & ADMIN_ROLES:
        teams.add('Admin')
    return teams


def is_admin(user=None):
    return bool(roles_of(user or g.user) & ADMIN_ROLES)


def allowed_moves(ticket, user=None):
    """Stages this user may move the ticket to next."""
    stage = ticket['stage']
    roles = roles_of(user or g.user)
    if is_admin(user):
        return list(NEXT.get(stage, []))
    team = STAGES[stage]['team']
    out = []
    for target in NEXT.get(stage, []):
        ok = TEAM_ROLE.get(team) in roles or EXTRA_MOVES.get((stage, target)) in roles
        if stage == 'Eval Complete' and target == 'Approved' and not ticket['warranty']:
            ok = False      # only warranty tickets skip the quote
        if ok:
            out.append(target)
    return out


def days_since(ts):
    try:
        then = datetime.fromisoformat(str(ts).replace('Z', '+00:00')).date()
        return max(0, (datetime.now(timezone.utc).date() - then).days)
    except ValueError:
        return 0


def ticket_view(r, users=None):
    """A ticket row plus the things pages show: whose turn, aging colour, display names."""
    d = dict(r)
    info = STAGES.get(d['stage'], {'team': None, 'hold': False, 'guide': ''})
    d['team'] = info['team'] or '—'
    d['hold'] = info['hold']
    d['stage_days'] = days_since(d['stage_started_at']) if d['stage'] != 'Closed' else 0
    end = datetime.fromisoformat(d['completed_at']).date() if d['completed_at'] else datetime.now(timezone.utc).date()
    try:
        d['total_days'] = max(0, (end - date.fromisoformat(d['received_date'])).days)
    except ValueError:
        d['total_days'] = 0
    if d['stage'] == 'Closed':
        d['age'] = 'closed'
    elif info['hold']:
        d['age'] = 'hold'
    else:
        d['age'] = 'red' if d['stage_days'] > 7 else 'yellow' if d['stage_days'] > 3 else 'normal'
    users = users or {}
    d['assigned_name'] = users.get(d['assigned_to'], d['assigned_to']) if d['assigned_to'] else (d['technician'] or '')
    return d


def amount(text, label):
    text = (text or '').strip().replace('$', '').replace(',', '')
    if not text:
        return None
    try:
        n = round(float(text), 2)
    except ValueError:
        raise ValueError(f'{label} must be a number')
    if n < 0:
        raise ValueError(f'{label} cannot be negative')
    return n


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
# Set by register_repair_routes: the app's shared CC/CR/RP ID maker.
make_inventory_code = None


def register_repair_routes(app, conn, base_dir, inventory_code_maker):
    global make_inventory_code
    make_inventory_code = inventory_code_maker
    template = add_user_block((Path(base_dir) / 'repairs.html').read_text(encoding='utf-8'))

    def render(**ctx):
        return render_template_string(template, stages=STAGES, stage_names=STAGE_NAMES, open_stages=OPEN_STAGES,
                                      teams=TEAMS, resolutions=RESOLUTIONS, **ctx)

    def user_names(c):
        return {r['username']: (r['full_name'] or r['username']) for r in c.execute('SELECT username, full_name FROM users')}

    @app.context_processor
    def repair_helpers():
        # Used by the New Inventory form's technician drop-down.
        def tech_options():
            c = conn()
            try:
                return tech_list(c)
            finally:
                c.close()
        return {'tech_options': tech_options}

    # ----- Queue (list) -----
    @app.route('/repairs')
    def repairs():
        c = conn()
        me = g.user['username']
        teams = user_teams()
        # Default queue depends on the person's role.
        if 'Admin' in teams:
            default = 'open'
        elif 'Sales' in teams:
            default = 'mine'
        elif 'Repair team' in teams:
            default = 'Repair team'
        elif 'Shipping' in teams:
            default = 'Shipping'
        else:
            default = 'open'
        queue = request.args.get('queue', default)
        stage = request.args.get('stage', '')
        rep = request.args.get('rep', '')
        tech = request.args.get('tech', '')
        q = request.args.get('q', '').strip()

        where, args = [], []
        if stage in STAGES:
            where.append('r.stage=?'); args.append(stage)
        elif queue == 'closed':
            where.append("r.stage='Closed'")
        else:
            where.append("r.stage<>'Closed'")
            if queue in TEAMS:
                team_stages = [s for s, i in STAGES.items() if i['team'] == queue]
                where.append('r.stage IN (%s)' % ','.join('?' * len(team_stages))); args += team_stages
            elif queue == 'mine':
                where.append('(r.assigned_to=? OR cl.account_owner=?)'); args += [me, me]
        if rep:
            where.append('cl.account_owner=?'); args.append(rep)
        if tech:
            where.append('r.assigned_to=?'); args.append(tech)
        if q:
            like = '%' + q + '%'
            where.append('(r.rma LIKE ? OR r.inventory_code LIKE ? OR r.customer LIKE ? OR r.serial LIKE ? '
                         'OR r.model LIKE ? OR r.part_number LIKE ? OR r.location LIKE ?)')
            args += [like] * 7
        sql = ('SELECT r.*, cl.account_owner FROM customer_repairs r LEFT JOIN clients cl ON cl.id=r.client_id'
               + (' WHERE ' + ' AND '.join(where) if where else '')
               + ' ORDER BY r.stage_started_at ASC, r.id DESC')
        names = user_names(c)
        rows = [ticket_view(r, names) for r in c.execute(sql, args)]
        counts = {r['stage']: r['n'] for r in c.execute('SELECT stage, COUNT(*) n FROM customer_repairs GROUP BY stage')}
        team_counts = {t: sum(counts.get(s, 0) for s, i in STAGES.items() if i['team'] == t) for t in TEAMS}
        mine = c.execute("""SELECT COUNT(*) FROM customer_repairs r LEFT JOIN clients cl ON cl.id=r.client_id
                            WHERE r.stage<>'Closed' AND (r.assigned_to=? OR cl.account_owner=?)""", (me, me)).fetchone()[0]
        reps, techs = sales_reps(c), tech_list(c)
        c.close()
        return render(mode='list', rows=rows, counts=counts, team_counts=team_counts, mine_count=mine,
                      queue=queue if stage not in STAGES else '', stage=stage, rep=rep, tech=tech, q=q,
                      reps=reps, techs=techs, open_count=sum(v for k, v in counts.items() if k != 'Closed'))

    # ----- New ticket -----
    @app.route('/repairs/new', methods=['GET', 'POST'])
    def repair_new():
        c = conn()
        if request.method == 'GET':
            techs = tech_list(c); suggestion = next_rma(c); c.close()
            return render(mode='new', today=date.today().isoformat(), preset_client=request.args.get('client', ''),
                          techs=techs, rma_suggestion=suggestion)
        f = request.form
        if not can('rma_create'):
            c.close(); flash("Your role can't create repair tickets."); return redirect(url_for('repairs'))
        client = resolve_client(c, f.get('customer', ''))
        if client is None:
            c.close(); flash('Pick the client from the list, or add them under Clients first.')
            return redirect(url_for('repair_new'))
        received = f.get('received_date', '').strip()
        try:
            date.fromisoformat(received)
        except ValueError:
            c.close(); flash('Enter a valid received date.'); return redirect(url_for('repair_new'))
        try:
            rid = create_ticket(c, client=client, rma=f.get('rma', ''), received=received,
                                fields={k: f.get(k, '').strip() for k in ('manufacturer', 'model', 'part_number', 'serial', 'location', 'notes')},
                                assigned_to=f.get('assigned_to', '').strip(), note='Ticket created')
            c.commit()
        except sqlite3.IntegrityError:
            c.rollback(); c.close(); flash('That RMA number is already used. Each repair needs its own RMA.')
            return redirect(url_for('repair_new'))
        prior = serial_matches(c, f.get('serial', ''), exclude=('repair', rid))
        code = c.execute('SELECT inventory_code, rma FROM customer_repairs WHERE id=?', (rid,)).fetchone()
        c.close()
        flash(f"Created {code['inventory_code']} ({code['rma']}) in Received")
        for m in prior:
            flash('⚠ Serial number alert: this S/N is also on ' + m['code'] + ' — ' + m['manufacturer'] + ' ' + m['product'] + ' [' + m['status'] + ']')
        return redirect(url_for('repair_detail', rid=rid))

    # ----- Ticket page -----
    @app.route('/repairs/<int:rid>')
    def repair_detail(rid):
        c = conn()
        r = c.execute('SELECT r.*, cl.account_owner FROM customer_repairs r LEFT JOIN clients cl ON cl.id=r.client_id WHERE r.id=?', (rid,)).fetchone()
        if r is None:
            c.close(); return 'Repair ticket not found', 404
        names = user_names(c)
        t = ticket_view(r, names)
        work_log = c.execute('SELECT * FROM repair_log WHERE repair_id=? ORDER BY at DESC, id DESC', (rid,)).fetchall()
        client = c.execute('SELECT * FROM clients WHERE id=?', (r['client_id'],)).fetchone() if r['client_id'] else None
        same_serial = serial_matches(c, r['serial'], exclude=('repair', rid))
        # Earlier tickets for the same serial: candidates for "warranty return of".
        earlier = [m for m in same_serial if m['url'].startswith('/repairs/')]
        warranty_of = c.execute('SELECT id, inventory_code, rma, stage FROM customer_repairs WHERE id=?', (r['warranty_of_id'],)).fetchone() if r['warranty_of_id'] else None
        returns = c.execute('SELECT id, inventory_code, rma, stage FROM customer_repairs WHERE warranty_of_id=?', (rid,)).fetchall()
        techs = tech_list(c)
        c.close()
        return render(mode='detail', r=t, guide=STAGES[t['stage']]['guide'], moves=allowed_moves(r),
                      all_moves=[s for s in STAGE_NAMES if s != t['stage']] if is_admin() else [],
                      work_log=work_log, client=client, client_text=client_label(client) if client else r['customer'],
                      same_serial=same_serial, earlier=earlier, warranty_of=warranty_of, returns=returns,
                      techs=techs, names=names)

    # ----- Move to another stage -----
    @app.post('/repairs/<int:rid>/move')
    def repair_move(rid):
        c = conn()
        r = c.execute('SELECT * FROM customer_repairs WHERE id=?', (rid,)).fetchone()
        if r is None:
            c.close(); return 'Repair ticket not found', 404
        target = request.form.get('stage', '')
        note = request.form.get('note', '').strip()
        if target not in STAGES or target == r['stage']:
            c.close(); flash('Pick a stage to move to.'); return redirect(url_for('repair_detail', rid=rid))
        if target not in allowed_moves(r) and not is_admin():
            c.close(); flash(f"Your role can't move this ticket from {r['stage']} to {target}.")
            return redirect(url_for('repair_detail', rid=rid))
        move_ticket(c, r, target, note, request.form.get('resolution', ''))
        c.commit(); c.close()
        flash(f'Moved to {target}')
        return redirect(url_for('repair_detail', rid=rid))

    # ----- Save ticket details -----
    @app.post('/repairs/<int:rid>/update')
    def repair_update(rid):
        c = conn()
        r = c.execute('SELECT * FROM customer_repairs WHERE id=?', (rid,)).fetchone()
        if r is None:
            c.close(); return 'Repair ticket not found', 404
        if not can('rma_edit'):
            c.close(); flash("Your role can't edit repair tickets."); return redirect(url_for('repair_detail', rid=rid))
        f = request.form
        changes = {}

        def text(name):
            return f.get(name, '').strip()

        # Equipment details: anyone who can edit tickets.
        for name in ('manufacturer', 'model', 'part_number', 'serial', 'location'):
            if name in f:
                changes[name] = text(name)
        # Client and RMA: the roles that create tickets.
        if can('rma_create'):
            if 'customer' in f:
                client = resolve_client(c, f.get('customer', ''))
                if client is None:
                    c.close(); flash('Pick the client from the list.'); return redirect(url_for('repair_detail', rid=rid))
                changes['customer'] = client['name']; changes['client_id'] = client['id']
            if 'rma' in f and text('rma'):
                changes['rma'] = text('rma')
        if can('repair_assign') and 'assigned_to' in f:
            changes['assigned_to'] = text('assigned_to') or None
        if can('repair_eval') and 'eval_findings' in f:
            changes['eval_findings'] = clean_note(f.get('eval_findings'))
        if can('repair_eval') or can('repair_price'):
            if 'warranty_form' in f:
                changes['warranty'] = 1 if f.get('warranty') else 0
                link = f.get('warranty_of_id', '')
                changes['warranty_of_id'] = int(link) if link.isdigit() and changes['warranty'] else None
        try:
            if can('repair_price'):
                if 'quoted_price' in f:
                    changes['quoted_price'] = amount(f.get('quoted_price'), 'Quoted price')
                if 'estimate_number' in f:
                    changes['estimate_number'] = text('estimate_number')
            if can('repair_invoice'):
                if 'invoice_number' in f:
                    changes['invoice_number'] = text('invoice_number')
                if 'resolution' in f and r['stage'] == 'Closed' and f.get('resolution') in RESOLUTIONS:
                    changes['resolution'] = f.get('resolution')
        except ValueError as e:
            c.close(); flash(str(e)); return redirect(url_for('repair_detail', rid=rid))

        changed = {k: v for k, v in changes.items() if (r[k] if r[k] != '' else None) != (v if v != '' else None)}
        if changed:
            sets = ', '.join(f'{k}=?' for k in changed)
            try:
                c.execute(f'UPDATE customer_repairs SET {sets}, updated_at=? WHERE id=?', [*changed.values(), now_iso(), rid])
            except sqlite3.IntegrityError:
                c.close(); flash('That RMA number is already used on another ticket.'); return redirect(url_for('repair_detail', rid=rid))
            log(c, rid, 'edit', describe_changes(r, changed))
            c.commit(); flash('Ticket saved')
        else:
            flash('No changes to save')
        c.close()
        return redirect(url_for('repair_detail', rid=rid))

    # ----- Add a work-log note -----
    @app.post('/repairs/<int:rid>/note')
    def repair_note(rid):
        note = request.form.get('note', '').strip()
        note = clean_note(note)
        if not note:
            flash('Type a note first.'); return redirect(url_for('repair_detail', rid=rid))
        if not can('rma_edit'):
            flash("Your role can't add notes."); return redirect(url_for('repair_detail', rid=rid))
        c = conn()
        if c.execute('SELECT 1 FROM customer_repairs WHERE id=?', (rid,)).fetchone() is None:
            c.close(); return 'Repair ticket not found', 404
        log(c, rid, 'note', note)
        c.execute('UPDATE customer_repairs SET updated_at=? WHERE id=?', (now_iso(), rid))
        c.commit(); c.close()
        flash('Note added')
        return redirect(url_for('repair_detail', rid=rid))


# ---------------------------------------------------------------------------
# Shared actions (also used by New Inventory and the demo data)
# ---------------------------------------------------------------------------
LABELS = {'manufacturer': 'manufacturer', 'model': 'model', 'part_number': 'part number', 'serial': 'serial',
          'location': 'location', 'customer': 'client', 'rma': 'RMA', 'assigned_to': 'assigned tech',
          'eval_findings': 'eval findings', 'quoted_price': 'quoted price', 'estimate_number': 'estimate number',
          'invoice_number': 'invoice number', 'warranty': 'warranty', 'warranty_of_id': 'warranty link',
          'resolution': 'resolution'}
QUIET = {'eval_findings', 'quoted_price', 'client_id', 'warranty_of_id'}   # logged without values


def describe_changes(old, changed):
    parts = []
    for k, v in changed.items():
        if k == 'client_id':
            continue
        if k in QUIET:
            parts.append(f'{LABELS[k]} updated')
        elif k == 'warranty':
            parts.append('marked as warranty' if v else 'warranty removed')
        else:
            parts.append(f"{LABELS.get(k, k)}: {old[k] or '—'} → {v or '—'}")
    return 'Updated ' + '; '.join(parts)


def create_ticket(c, client, rma, received, fields, assigned_to='', note='Ticket created', code=None, when=None):
    """Insert a new ticket in Received. Returns its id. Raises sqlite3.IntegrityError on a duplicate RMA."""
    when = when or now_iso()
    rma = (rma or '').strip() or next_rma(c)
    code = code or make_inventory_code(c, 'RP')
    cur = c.execute("""INSERT INTO customer_repairs(rma, customer, client_id, manufacturer, model, part_number, serial, location,
                       received_date, notes, assigned_to, stage, stage_started_at, created_at, updated_at, inventory_code)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (rma, client['name'], client['id'], fields.get('manufacturer', ''), fields.get('model', ''),
                     fields.get('part_number', ''), fields.get('serial', ''), fields.get('location', ''), received,
                     clean_note(fields.get('notes')), assigned_to or None, 'Received', when, when, when, code))
    rid = cur.lastrowid
    log(c, rid, 'created', note, new_stage='Received', when=when)
    return rid


def move_ticket(c, r, target, note='', resolution='', when=None):
    """Move a ticket to a new stage, set its resolution when it ends, and log it."""
    when = when or now_iso()
    sets = {'stage': target, 'stage_started_at': when, 'updated_at': when}
    if target == 'Declined':
        sets['resolution'] = 'Declined'
    elif r['stage'] == 'Unrepairable' and target == 'Ready to Ship':
        sets['resolution'] = 'Returned unrepaired'
    if target == 'Closed':
        sets['completed_at'] = when
        if resolution in RESOLUTIONS:
            sets['resolution'] = resolution
        elif r['stage'] == 'Unrepairable':
            sets['resolution'] = 'Exchanged'
        elif not r['resolution']:
            sets['resolution'] = 'Warranty' if r['warranty'] else 'Repaired'
    elif r['stage'] == 'Closed':      # reopened
        sets['completed_at'] = None
        sets['resolution'] = None
    c.execute('UPDATE customer_repairs SET ' + ', '.join(f'{k}=?' for k in sets) + ' WHERE id=?', [*sets.values(), r['id']])
    log(c, r['id'], 'stage', note, old_stage=r['stage'], new_stage=target, when=when)
