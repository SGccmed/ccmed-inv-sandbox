"""Product history (v1.15): everything that happened to one stock item, newest first, on its page (/unit/<id>).

Where the lines come from:
    units                 how it was received (+ New Inventory, a core conversion, or the old spreadsheet import)
    core_items            the core it was converted from (converted_unit_id) and every time it was sent to repair
                          (source_unit_id), with each ticket update of those cores
    movements             shelf moves, with who moved it
    cost_adjustments      cost changes (only shown to roles that can see costs)
    repair_orders         the old "repair record" box (removed in v1.15; old entries still show)
    customer_repairs      tickets with the same serial AND the same product: most likely the same unit coming back
                          from a customer (serials alone can repeat across manufacturers, so those stay a warning)

Sending an item to repair: send_to_repair() opens a core ticket linked to the item (source_unit_id) and moves the
item to the Repair location. When that core is Ready for Stock, an Admin returns it to stock (cores.py): the same
item goes back on a shelf, with the repair costs added to its cost.
"""
import re
from datetime import datetime, timezone

from auth import can

NEW_COLUMNS = [('units', 'received_by', 'TEXT'), ('core_items', 'source_unit_id', 'INTEGER'),
               ('core_items', 'received_by', 'TEXT'), ('movements', 'actor', 'TEXT'),
               ('receiving_batches', 'created_by', 'TEXT')]
CLOSED_CORE_STAGES = ('Converted to Stock', 'Returned to Stock')


def ensure_history_schema(c):
    for table, name, typ in NEW_COLUMNS:
        if name not in {r[1] for r in c.execute(f'PRAGMA table_info({table})')}:
            c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {typ}')


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def squash(text):
    return re.sub(r'[^a-z0-9]', '', str(text or '').lower())


def plain(html, limit=140):
    """A short plain-text version of a rich-text note."""
    text = re.sub(r'<[^>]+>', ' ', str(html or ''))
    text = re.sub(r'\s+', ' ', text.replace('&nbsp;', ' ').replace('&amp;', '&')).strip()
    return text if len(text) <= limit else text[:limit - 1].rstrip() + '…'


def money(v):
    return '${:,.2f}'.format(v or 0)


# ---------- Records that belong to this item ----------

def same_product(c, unit, manufacturer, model):
    """Is (manufacturer, model) the same product as this stock item? Uses the Product Database when it can."""
    if unit['product_id']:
        from product_database import product_matcher
        if product_matcher(c)(manufacturer, model) == unit['product_id']:
            return True
    if not squash(model):
        return False
    makes_ok = not squash(manufacturer) or not squash(unit['manufacturer']) or squash(manufacturer) == squash(unit['manufacturer'])
    a, b = squash(model), squash(unit['product_raw'])
    return makes_ok and (a == b or (len(a) >= 4 and (a in b or b in a)))


def related(c, unit):
    """The cores and customer repairs tied to this item, and which serial matches are only a warning."""
    from clients import serial_matches
    uid = unit['id']
    origin = c.execute('SELECT * FROM core_items WHERE converted_unit_id=?', (uid,)).fetchone()
    repair_cores = c.execute('SELECT * FROM core_items WHERE source_unit_id=? ORDER BY created_at DESC, id DESC', (uid,)).fetchall()
    open_core = next((r for r in repair_cores if r['stage'] not in CLOSED_CORE_STAGES), None)
    own_cores = {f"/cores/{r['id']}" for r in ([origin] if origin else []) + list(repair_cores)}
    same_unit, others = [], []
    for m in serial_matches(c, unit['serial'], exclude=('unit', uid)):
        if m['url'] in own_cores:
            continue                                   # its own core: shown as a link, not a warning
        if m['url'].startswith('/repairs/'):
            r = c.execute('SELECT manufacturer, model FROM customer_repairs WHERE id=?', (int(m['url'].rsplit('/', 1)[1]),)).fetchone()
            if r and same_product(c, unit, r['manufacturer'], r['model']):
                same_unit.append(m)
                continue
        others.append(m)
    return {'origin': origin, 'repair_cores': repair_cores, 'open_core': open_core, 'same_unit': same_unit, 'others': others}


def same_unit_stock(c, serial, manufacturer, model):
    """For a customer repair ticket: our stock items that are most likely this same unit (same serial and product)."""
    from clients import serial_key
    key = serial_key(serial)
    if not key:
        return []
    rows = c.execute("""SELECT * FROM units WHERE REPLACE(REPLACE(UPPER(TRIM(COALESCE(serial,''))),' ',''),char(9),'')=?""", (key,)).fetchall()
    return [u for u in rows if same_product(c, u, manufacturer, model)]


# ---------- The timeline ----------

def user_names(c):
    return {r['username']: (r['full_name'] or r['username']) for r in c.execute('SELECT username, full_name FROM users')}


def timeline(c, unit, rel):
    """Product history lines, newest first. Each: when, title, detail, url, cost (True = only for roles that see costs)."""
    names = user_names(c)
    who = lambda u: names.get(u, u) if u else ''
    show_cost = can('view_cost')
    out = []

    def add(when, title, detail='', url='', cost=False, rank=1):
        # rank breaks ties between lines saved in the same second (e.g. a return to stock writes a cost change,
        # a move and a ticket line at once): higher rank = shown above.
        if cost and not show_cost:
            return
        out.append({'when': when or '', 'title': title, 'detail': detail, 'url': url, 'rank': rank})

    # How it came in
    origin = rel['origin']
    if origin:
        add(origin['created_at'], f"Received as core {origin['inventory_code']}" + (f" by {who(origin['received_by'])}" if origin['received_by'] else ''),
            ' · '.join(x for x in [f"shelf {origin['location']}" if origin['location'] else '',
                                   f"purchase cost {money(origin['original_cost'])}" if show_cost else ''] if x),
            f"/cores/{origin['id']}", rank=0)
    elif unit['source_sheet']:
        add(unit['received_date'] or '', 'Imported from the old spreadsheet', f"row {unit['source_row']} · original location {unit['location_raw'] or '—'}", rank=0)
    else:
        batch = c.execute('SELECT * FROM receiving_batches WHERE id=?', (unit['entry_batch_id'],)).fetchone() if unit['entry_batch_id'] else None
        by = unit['received_by'] or (batch['created_by'] if batch else None)
        parts = [f"shelf {unit['location_raw']}" if unit['location_raw'] else '']
        if unit['review']:
            parts.append('Needs review: ' + unit['review'])
        add(batch['created_at'] if batch else unit['received_date'], f"Received on + New Inventory" + (f" by {who(by)}" if by else ''),
            ' · '.join(p for p in parts if p), rank=0)

    # Core tickets: the one it came from, and each time it was sent to repair
    for core in ([origin] if origin else []) + list(rel['repair_cores']):
        code, url = core['inventory_code'], f"/cores/{core['id']}"
        if core['source_unit_id'] == unit['id']:
            add(core['created_at'], f"Sent to repair as {code}" + (f" by {who(core['received_by'])}" if core['received_by'] else ''),
                plain(core['notes']), url)
        for t in c.execute('SELECT * FROM core_transactions WHERE core_id=? ORDER BY performed_at, id', (core['id'],)):
            if t['new_stage'] == 'Converted to Stock':
                add(t['performed_at'], f"Converted to stock as {unit['inventory_code'] or 'this item'}" + (f" by {who(t['actor'])}" if t['actor'] else ''),
                    f"total cost {money(unit_cost_at_conversion(c, core))}" if show_cost else '', url)
                continue
            if t['new_stage'] == 'Returned to Stock':
                add(t['performed_at'], f"Back from repair ({code})" + (f", returned by {who(t['actor'])}" if t['actor'] else ''), plain(t['description']), url, rank=4)
                continue
            bits = []
            if t['new_stage']:
                bits.append(f"{t['old_stage']} → {t['new_stage']}")
            if t['hours']:
                bits.append(f"{t['hours']:.2f} hrs")
            if show_cost and (t['parts_cost'] or t['other_cost'] or t['labor_cost']):
                bits.append('cost ' + money((t['parts_cost'] or 0) + (t['other_cost'] or 0) + (t['labor_cost'] or 0)))
            note = plain(t['description'], 100)
            if note:
                bits.append(note)
            add(t['performed_at'], f"{code}: {'QC sign-off' if t['new_stage'] == 'Ready for Stock' else 'ticket update'}"
                + (f" by {t['technician']}" if t['technician'] else ''), ' · '.join(bits), url)

    # Shelf moves
    for m in c.execute('SELECT * FROM movements WHERE unit_id=? ORDER BY id', (unit['id'],)):
        note, actor = m['note'] or '', m['actor']
        tag = re.search(r'\s*\[([^\]]+)\]\s*$', note)
        if tag:                                         # before v1.15 the mover was kept at the end of the note
            actor = actor or tag.group(1)
            note = note[:tag.start()]
        add(m['created_at'], f"Moved {m['old_location'] or '—'} → {m['new_location']}" + (f" by {who(actor)}" if actor else ''), note.strip(), rank=2)

    # Cost changes (repair costs added on return show here too)
    for a in c.execute('SELECT * FROM cost_adjustments WHERE page=? ORDER BY at', (f"/unit/{unit['id']}",)):
        add(a['at'], f"Cost {money(a['old_value'])} → {money(a['new_value'])} by {who(a['actor'])}", a['reason'], cost=True, rank=3)

    # The old "repair record" box (before v1.15)
    for r in c.execute('SELECT * FROM repair_orders WHERE unit_id=? ORDER BY id', (unit['id'],)):
        add(r['created_at'], f"Old repair record: {r['repair_type']}", ' · '.join(x for x in [r['status'], plain(r['notes'])] if x))

    # Customer repairs that are most likely this same unit
    for m in rel['same_unit']:
        rid = int(m['url'].rsplit('/', 1)[1])
        r = c.execute('SELECT * FROM customer_repairs WHERE id=?', (rid,)).fetchone()
        add(r['created_at'], f"Came in as customer repair {r['inventory_code'] or r['rma']} ({r['rma']}) for {r['customer']}",
            f"same serial and product · now {r['stage']}", m['url'])
        if r['completed_at']:
            add(r['completed_at'], f"Customer repair {r['inventory_code'] or r['rma']} closed", r['stage'], m['url'])

    out.sort(key=lambda e: (str(e['when']).replace(' ', 'T')[:19], e['rank']), reverse=True)
    return out


def unit_cost_at_conversion(c, core):
    row = c.execute('SELECT COALESCE(SUM(labor_cost+parts_cost+other_cost),0) FROM core_transactions WHERE core_id=?', (core['id'],)).fetchone()
    return (core['original_cost'] or 0) + row[0]


def now_line(unit, rel):
    """The "Now" line at the top of the history."""
    where = unit['physical_location'] or unit['location_raw'] or 'no location'
    text = f"{unit['status']} · {where}"
    if rel['open_core']:
        text += f" · in repair as {rel['open_core']['inventory_code']} ({rel['open_core']['stage']})"
    return text


# ---------- Send to repair ----------

def send_to_repair(c, unit, reason, username, inventory_code_maker):
    """Open a core ticket for one of our stock items and move it to the Repair location. Returns (core id, code)."""
    code = inventory_code_maker(c, 'CR')
    now = now_iso()
    cur = c.execute("""INSERT INTO core_items(inventory_code, manufacturer, product_raw, serial, location, supplier, original_cost,
                       notes, created_at, stage, stage_started_at, source_unit_id, received_by)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (code, unit['manufacturer'], unit['product_raw'], unit['serial'], 'Repair', None, 0,
                     f"Sent to repair from stock item {unit['inventory_code'] or '#' + str(unit['id'])}. {reason}".strip(),
                     now, 'Needs Repair', now, unit['id'], username))
    c.execute('INSERT INTO movements(unit_id, old_location, new_location, note, actor) VALUES (?,?,?,?,?)',
              (unit['id'], unit['physical_location'], 'Repair', f'Sent to repair as {code}', username))
    c.execute("UPDATE units SET physical_location='Repair', status=? WHERE id=?",
              ('Active' if unit['status'] in ('On loan', 'Rental') else unit['status'], unit['id']))
    return cur.lastrowid, code
