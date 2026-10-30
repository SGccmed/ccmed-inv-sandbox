"""Client accounts (Salesforce / QuickBooks style): accounts, contacts, activity log and full history.

Every client gets a permanent ID (CL-00001). Repairs link to a client by that ID, so renaming a client never breaks its history.
"""
import os
import re
import sqlite3
from datetime import datetime, timezone
from flask import request, redirect, flash, render_template_string, abort, g, jsonify
from auth import add_user_block, actor_name

ACCOUNT_TYPES = ['Hospital', 'Imaging Center', 'Clinic / Physician Office', 'Cardiology', "OB/GYN / Women's Health",
                 'Veterinary', 'Mobile Ultrasound', 'Dealer / Broker', 'Biomed / ISO', 'Other']
ACCOUNT_STATUSES = ['Active', 'Prospect', 'On Hold', 'Inactive']
PAYMENT_TERMS = ['Net 30', 'Net 15', 'Net 45', 'Due on receipt', 'Prepaid', 'Credit card on file', 'COD']
ACTIVITY_KINDS = ['Note', 'Call', 'Email', 'Meeting', 'Quote', 'Follow-up']
CLIENT_FIELDS = ['name', 'account_type', 'status', 'phone', 'email', 'website', 'billing_address', 'shipping_address',
                 'payment_terms', 'tax_exempt', 'account_owner', 'quickbooks_id', 'salesforce_id', 'notes']

SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
 id INTEGER PRIMARY KEY, client_code TEXT UNIQUE, name TEXT NOT NULL,
 account_type TEXT DEFAULT 'Other', status TEXT NOT NULL DEFAULT 'Active',
 phone TEXT, email TEXT, website TEXT, billing_address TEXT, shipping_address TEXT,
 payment_terms TEXT, tax_exempt INTEGER NOT NULL DEFAULT 0, account_owner TEXT,
 quickbooks_id TEXT, salesforce_id TEXT, notes TEXT,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_contacts (
 id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id), name TEXT NOT NULL,
 title TEXT, phone TEXT, email TEXT, is_primary INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS client_activity (
 id INTEGER PRIMARY KEY, client_id INTEGER NOT NULL REFERENCES clients(id), kind TEXT NOT NULL,
 subject TEXT, details TEXT, actor TEXT, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_client_contacts ON client_contacts(client_id);
CREATE INDEX IF NOT EXISTS idx_client_activity ON client_activity(client_id, created_at);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def clean(x):
    return str(x or '').strip()


def add_column(c, table, name, typ):
    if name not in {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}:
        c.execute(f'ALTER TABLE {table} ADD COLUMN {name} {typ}')


def create_client(c, name, actor='system', **fields):
    """Insert a client, give it the next CL-##### ID, and log it. Returns the new id."""
    now = now_iso()
    cols = ['name', 'created_at', 'updated_at'] + [k for k in fields if k in CLIENT_FIELDS and k != 'name']
    vals = [clean(name), now, now] + [fields[k] for k in cols[3:]]
    cid = c.execute(f'INSERT INTO clients({",".join(cols)}) VALUES ({",".join("?" * len(cols))})', vals).lastrowid
    c.execute('UPDATE clients SET client_code=? WHERE id=?', (f'CL-{cid:05d}', cid))
    log(c, cid, 'System', 'Client created', f'Client record created for {clean(name)}', actor, now)
    return cid


def log(c, client_id, kind, subject, details='', actor=None, when=None):
    c.execute('INSERT INTO client_activity(client_id,kind,subject,details,actor,created_at) VALUES (?,?,?,?,?,?)',
              (client_id, kind, subject, details, actor or actor_name(), when or now_iso()))


def ensure_client_schema(c):
    """Create client tables and link columns. Safe to run on every start and after a restore."""
    c.executescript(SCHEMA)
    add_column(c, 'customer_repairs', 'client_id', 'INTEGER')
    # One Salesforce account per client (blank allowed). Skipped if an older database already has duplicates.
    try:
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_clients_salesforce ON clients(salesforce_id) "
                  "WHERE salesforce_id IS NOT NULL AND salesforce_id<>''")
    except sqlite3.IntegrityError:
        pass
    # The blue/orange tape feature was removed; drop its columns from databases that have them.
    for table, col in (('units', 'tape'), ('units', 'owner_client_id'), ('core_items', 'tape')):
        if col in {r['name'] for r in c.execute(f'PRAGMA table_info({table})')}:
            try:
                c.execute(f'ALTER TABLE {table} DROP COLUMN {col}')
            except sqlite3.OperationalError:
                pass   # very old SQLite: column stays, unused
    # Older repairs only had a typed customer name: create one client per distinct name and link them.
    for r in c.execute("SELECT DISTINCT TRIM(customer) name FROM customer_repairs WHERE client_id IS NULL AND TRIM(COALESCE(customer,''))<>''").fetchall():
        existing = c.execute('SELECT id FROM clients WHERE name=? COLLATE NOCASE', (r['name'],)).fetchone()
        cid = existing['id'] if existing else create_client(c, r['name'], 'system', notes='Auto-created from existing repair records')
        c.execute('UPDATE customer_repairs SET client_id=? WHERE client_id IS NULL AND TRIM(customer)=? COLLATE NOCASE', (cid, r['name']))


# ---------- Salesforce account IDs ----------
# Salesforce stays the sales system; each client keeps its CL- number and also stores its Salesforce Account ID.

def salesforce_id_problem(sfid):
    """None if blank or a valid-looking Salesforce ID (15 or 18 letters/digits), otherwise what's wrong."""
    sfid = clean(sfid)
    if not sfid:
        return None
    if not re.fullmatch(r'[A-Za-z0-9]{15}([A-Za-z0-9]{3})?', sfid):
        return 'A Salesforce ID is 15 or 18 letters and digits (Account IDs start with 001)'
    return None


def salesforce_url(sfid):
    """Link to the account in Salesforce, if CCM_SALESFORCE_URL (e.g. https://yourcompany.lightning.force.com) is set."""
    base = os.environ.get('CCM_SALESFORCE_URL', '').rstrip('/')
    return f'{base}/lightning/r/Account/{sfid}/view' if base and sfid else ''


def salesforce_check(c, sfid, client_id=None):
    """Error message if the ID is malformed or already on another client."""
    problem = salesforce_id_problem(sfid)
    if problem or not clean(sfid):
        return problem
    other = c.execute('SELECT client_code, name FROM clients WHERE salesforce_id=? AND id<>?', (clean(sfid), client_id or 0)).fetchone()
    if other:
        return f"That Salesforce ID is already on {other['client_code']} · {other['name']}"
    return None


def client_label(row):
    return f"{row['client_code']} · {row['name']}"


def resolve_client(c, text):
    """Turn what was typed/picked in a client box into a client row (by CL- ID first, then exact name)."""
    text = clean(text)
    if not text:
        return None
    m = re.match(r'^(CL-\d+)', text, re.I)
    if m:
        return c.execute('SELECT * FROM clients WHERE client_code=? COLLATE NOCASE', (m.group(1),)).fetchone()
    rows = c.execute('SELECT * FROM clients WHERE name=? COLLATE NOCASE', (text,)).fetchall()
    return rows[0] if len(rows) == 1 else None


# ---------- Serial number look-up (shared with New Inventory) ----------

def serial_key(s):
    return re.sub(r'\s+', '', clean(s)).upper()


def serial_matches(c, serial, exclude=None):
    """Every record anywhere in the system with this serial. exclude=('unit'|'core'|'repair', id)."""
    key = serial_key(serial)
    if not key:
        return []
    out = []
    q = "REPLACE(REPLACE(UPPER(TRIM(COALESCE({0},''))),' ',''),char(9),'')=?"
    for r in c.execute('SELECT id,inventory_code,manufacturer,product_raw,status,physical_location,location_raw FROM units WHERE ' + q.format('serial'), (key,)):
        if exclude != ('unit', r['id']):
            out.append({'kind': 'Stock (CC)', 'code': r['inventory_code'] or f"Legacy #{r['id']}", 'manufacturer': r['manufacturer'] or '',
                        'product': r['product_raw'], 'status': r['status'], 'where': r['physical_location'] or r['location_raw'] or '',
                        'url': f"/unit/{r['id']}"})
    for r in c.execute('SELECT id,inventory_code,manufacturer,product_raw,stage,location FROM core_items WHERE ' + q.format('serial'), (key,)):
        if exclude != ('core', r['id']):
            out.append({'kind': 'Core (CR)', 'code': r['inventory_code'], 'manufacturer': r['manufacturer'] or '', 'product': r['product_raw'],
                        'status': r['stage'], 'where': r['location'] or '', 'url': f"/cores/{r['id']}"})
    for r in c.execute('SELECT id,inventory_code,rma,customer,manufacturer,model,stage,location FROM customer_repairs WHERE ' + q.format('serial'), (key,)):
        if exclude != ('repair', r['id']):
            out.append({'kind': 'Customer repair (RP)', 'code': r['inventory_code'] or r['rma'], 'manufacturer': r['manufacturer'] or '',
                        'product': r['model'] or '', 'status': f"{r['stage']} · {r['rma']} · {r['customer']}", 'where': r['location'] or '',
                        'url': f"/repairs/{r['id']}"})
    for m in out:   # don't show "Philips Philips eL18-4" when the product name already includes the maker
        if m['manufacturer'] and m['product'].upper().startswith(m['manufacturer'].upper()):
            m['manufacturer'] = ''
    return out


# ---------- Page layout ----------

STYLE = '''<style>*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#1b3043;font:14px/1.5 system-ui,Segoe UI,Arial,sans-serif}
aside{width:220px;background:#112a42;min-height:100vh;padding:22px 15px;position:fixed;display:flex;flex-direction:column}aside a{display:block;color:#dce9f4;padding:11px;text-decoration:none;border-radius:7px}aside a:hover,aside a.active{background:#2b5f88}aside h2{color:white;margin:0 0 12px 11px}
main{margin-left:220px;padding:28px 32px;max-width:1500px}h1{margin:0 0 4px;font-size:26px}h2{font-size:17px;margin:0 0 12px}
section,.panel{background:white;border:1px solid #dfe7ef;border-radius:12px;padding:20px;margin:16px 0}
input,select,textarea{padding:9px;border:1px solid #cbd8e2;border-radius:7px;font:inherit;width:100%}
button,.btn{background:#245d86;color:white;border:0;border-radius:7px;padding:9px 14px;cursor:pointer;text-decoration:none;display:inline-block;font:inherit;white-space:nowrap}
.secondary{background:#e7f0f6;color:#245d86}table{width:100%;border-collapse:collapse}td,th{padding:9px 10px;border-bottom:1px solid #e5edf3;text-align:left;vertical-align:top}th{background:#f0f5f9;font-size:12px;color:#55697b}td:first-child,.nw{white-space:nowrap}
a{color:#245d86}label{display:block;margin:0 0 4px;font-weight:600;font-size:12px}.muted{color:#667c8d}.msg{padding:11px 13px;background:#e8f6eb;border-radius:8px;margin:10px 0}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.grid .wide{grid-column:span 3}.toolbar{display:flex;gap:10px;flex-wrap:wrap;align-items:end}.toolbar>div{min-width:170px}
.cards{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:12px;margin:16px 0}.card{background:white;border:1px solid #dfe7ef;border-radius:12px;padding:14px}.card span{display:block;font-size:12px;color:#667c8d}.card strong{font-size:22px}
.pill{display:inline-block;padding:2px 9px;border-radius:99px;background:#e7f0f6;font-size:12px}.pill.Active{background:#e3f4e6;color:#1d6b33}.pill.Prospect{background:#fff4dc;color:#8a5a00}.pill.Inactive,.pill.On.Hold{background:#f1e4e4;color:#8a2c2c}
.tabs{display:flex;gap:4px;border-bottom:2px solid #dfe7ef;margin-top:18px;flex-wrap:wrap}.tabs a{padding:9px 15px;text-decoration:none;color:#55697b;border-radius:8px 8px 0 0;font-weight:600}.tabs a.on{background:white;color:#245d86;border:1px solid #dfe7ef;border-bottom:2px solid white;margin-bottom:-2px}
.tab{display:none}.tab.on{display:block}dl{display:grid;grid-template-columns:170px 1fr;gap:6px 14px;margin:0}dt{color:#667c8d}dd{margin:0;white-space:pre-line}
.tl{border-left:3px solid #dfe7ef;margin-left:8px;padding-left:18px}.tl .ev{position:relative;padding:0 0 14px}.tl .ev:before{content:"";position:absolute;left:-26px;top:5px;width:11px;height:11px;border-radius:50%;background:#245d86}.tl .ev.Repair:before{background:#c47a12}.tl .ev.Inventory:before{background:#2f8a4a}.tl .ev.System:before{background:#9aa9b6}
@media(max-width:900px){aside{position:static;width:auto;min-height:0}main{margin:0;padding:15px}.grid,.cards{grid-template-columns:1fr 1fr}.grid .wide{grid-column:span 2}}
@media(max-width:560px){.grid,.cards{grid-template-columns:1fr}.grid .wide{grid-column:span 1}dl{grid-template-columns:1fr}}</style>'''
LAYOUT = add_user_block('''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>CC Medical | Clients</title>''' + STYLE + '''</head><body>
<aside><h2>CC Medical</h2><a href="/">Inventory Dashboard</a>{% if can('receive') %}<a href="/inventory/new">+ New Inventory</a>{% endif %}<a class="active" href="/clients">Clients</a><a href="/products">Product Database</a><a href="/cores">Core Repairs (CR)</a><a href="/repairs">Customer Repairs (RP)</a></aside>
<main>{% with msgs=get_flashed_messages() %}{% for m in msgs %}<div class="msg">{{m}}</div>{% endfor %}{% endwith %}{{body|safe}}</main></body></html>''')


def page(body, **ctx):
    return render_template_string(LAYOUT, body=render_template_string(body, **ctx))


LIST_PAGE = '''<div class="toolbar" style="justify-content:space-between"><div><h1>Clients</h1><div class="muted">Accounts with contacts, activity and full repair / equipment history.</div></div><div style="display:flex;gap:8px">{% if can('client_import') %}<a class="btn secondary" href="/clients/salesforce">Link to Salesforce</a>{% endif %}{% if can('client_edit') %}<a class="btn" href="/clients/new">+ New Client</a>{% endif %}</div></div>
<div class="cards"><div class="card"><span>Total clients</span><strong>{{stats.total}}</strong></div><div class="card"><span>Active</span><strong>{{stats.active}}</strong></div><div class="card"><span>Prospects</span><strong>{{stats.prospect}}</strong></div><div class="card"><span>Clients with open repairs</span><strong>{{stats.open_clients}}</strong></div><div class="card"><span>Open RMAs</span><strong>{{stats.open_rmas}}</strong></div></div>
<section><form class="toolbar"><div style="flex:1;min-width:240px"><label>Search</label><input name="q" value="{{q}}" placeholder="Name, CL- number, Salesforce ID, phone, email, contact…"></div>
<div><label>Type</label><select name="type"><option value="">All types</option>{% for t in types %}<option {% if t==type %}selected{% endif %}>{{t}}</option>{% endfor %}</select></div>
<div><label>Status</label><select name="status"><option value="">All statuses</option>{% for s in statuses %}<option {% if s==status %}selected{% endif %}>{{s}}</option>{% endfor %}</select></div>
<div><label>Account owner</label><select name="owner"><option value="">Anyone</option>{% for o in owners %}<option {% if o==owner %}selected{% endif %}>{{o}}</option>{% endfor %}</select></div>
<button>Filter</button><a class="btn secondary" href="/clients">Clear</a></form></section>
<section><div style="overflow:auto"><table><tr><th>Client ID</th><th>Name</th><th>Type</th><th>Status</th><th>Primary contact</th><th>Phone</th><th>Account owner</th><th>Salesforce</th><th>Open RMAs</th><th>Total RMAs</th><th>Last activity</th></tr>
{% for r in rows %}<tr><td>{{r.client_code}}</td><td><a href="/clients/{{r.id}}"><strong>{{r.name}}</strong></a></td><td>{{r.account_type or ''}}</td><td><span class="pill {{r.status}}">{{r.status}}</span></td><td>{{r.contact or '—'}}</td><td class="nw">{{r.phone or ''}}</td><td>{{r.account_owner or ''}}</td><td>{{'Linked' if r.salesforce_id else '—'}}</td><td>{{r.open_rmas}}</td><td>{{r.total_rmas}}</td><td class="nw">{{r.last_activity|day if r.last_activity else ''}}</td></tr>
{% else %}<tr><td colspan="11">No clients match.</td></tr>{% endfor %}</table></div><p class="muted">{{rows|length}} shown</p></section>'''

FORM_PAGE = '''<p><a href="{{'/clients/'~c.id if c else '/clients'}}">← {{'Back to '~c.name if c else 'Clients'}}</a></p><h1>{{'Edit '~c.name if c else 'New Client'}}</h1>
<form method="post"><section><h2>Account</h2><div class="grid">
<div class="wide"><label>Client / account name *</label><input name="name" required maxlength="160" value="{{c.name if c else ''}}"></div>
<div><label>Account type</label><select name="account_type">{% for t in types %}<option {% if c and c.account_type==t %}selected{% endif %}>{{t}}</option>{% endfor %}</select></div>
<div><label>Status</label><select name="status">{% for s in statuses %}<option {% if c and c.status==s %}selected{% endif %}>{{s}}</option>{% endfor %}</select></div>
<div><label>Account owner (sales rep)</label><input name="account_owner" value="{{c.account_owner if c else g.user.username}}"></div>
<div><label>Main phone</label><input name="phone" value="{{c.phone or '' if c else ''}}"></div><div><label>Main email</label><input name="email" type="email" value="{{c.email or '' if c else ''}}"></div><div><label>Website</label><input name="website" value="{{c.website or '' if c else ''}}"></div>
</div></section><section><h2>Billing &amp; shipping</h2><div class="grid">
<div><label>Billing address</label><textarea name="billing_address" rows="3">{{c.billing_address or '' if c else ''}}</textarea></div>
<div><label>Shipping address</label><textarea name="shipping_address" rows="3">{{c.shipping_address or '' if c else ''}}</textarea></div>
<div><label>Payment terms</label><select name="payment_terms"><option value="">—</option>{% for t in terms %}<option {% if c and c.payment_terms==t %}selected{% endif %}>{{t}}</option>{% endfor %}</select>
<label style="margin-top:12px"><input type="checkbox" name="tax_exempt" value="1" style="width:auto" {% if c and c.tax_exempt %}checked{% endif %}> Tax exempt</label></div>
</div></section>
{% if not c %}<section><h2>Primary contact</h2><div class="grid"><div><label>Name</label><input name="contact_name"></div><div><label>Title</label><input name="contact_title" placeholder="Biomed manager"></div><div></div><div><label>Phone</label><input name="contact_phone"></div><div><label>Email</label><input name="contact_email" type="email"></div></div></section>{% endif %}
<section><h2>Other</h2><div class="grid"><div><label>QuickBooks customer ID</label><input name="quickbooks_id" value="{{c.quickbooks_id or '' if c else ''}}" placeholder="For future sync"></div><div><label>Salesforce account ID</label><input name="salesforce_id" value="{{c.salesforce_id or '' if c else ''}}" placeholder="15 or 18 characters, starts with 001"><div class="muted" style="font-size:12px">From the account's page in Salesforce. Links this client to Salesforce; our CL- number stays the same.</div></div><div></div>
<div class="wide"><label>Notes</label><textarea name="notes" rows="3">{{c.notes or '' if c else ''}}</textarea></div></div></section>
<button>{{'Save changes' if c else 'Create client'}}</button></form>'''

DETAIL_PAGE = '''<p><a href="/clients">← Clients</a></p>
<div class="toolbar" style="justify-content:space-between"><div><h1>{{c.name}}</h1><div class="muted">{{c.client_code}} · {{c.account_type or 'No type'}} · <span class="pill {{c.status}}">{{c.status}}</span>{% if c.account_owner %} · Owner: {{c.account_owner}}{% endif %}</div></div>
<div class="toolbar">{% if can('client_edit') %}<a class="btn secondary" href="/clients/{{c.id}}/edit">Edit client</a>{% endif %}{% if can('rma_create') %}<a class="btn" href="/repairs/new?client={{c.client_code|urlencode}}">+ New RMA</a>{% endif %}</div></div>
<div class="cards"><div class="card"><span>Open repairs</span><strong>{{kpi.open}}</strong></div><div class="card"><span>Total repairs</span><strong>{{kpi.total}}</strong></div><div class="card"><span>Avg. repair turnaround</span><strong>{{kpi.avg_days if kpi.avg_days is not none else '—'}}{% if kpi.avg_days is not none %} <small>days</small>{% endif %}</strong></div><div class="card"><span>Contacts</span><strong>{{contacts|length}}</strong></div><div class="card"><span>Client since</span><strong style="font-size:17px">{{c.created_at|day}}</strong></div></div>
<nav class="tabs"><a href="#overview" class="on">Overview</a><a href="#contacts">Contacts ({{contacts|length}})</a><a href="#repairs">Repairs ({{repairs|length}})</a><a href="#loaners">Loaners</a><a href="#sales">Sales</a><a href="#history">Full history ({{timeline|length}})</a></nav>

<div class="tab on" id="t-overview"><section><div class="grid"><div class="wide" style="grid-column:span 2"><dl>
<dt>Main phone</dt><dd>{{c.phone or '—'}}</dd><dt>Main email</dt><dd>{% if c.email %}<a href="mailto:{{c.email}}">{{c.email}}</a>{% else %}—{% endif %}</dd><dt>Website</dt><dd>{{c.website or '—'}}</dd>
<dt>Billing address</dt><dd>{{c.billing_address or '—'}}</dd><dt>Shipping address</dt><dd>{{c.shipping_address or '—'}}</dd><dt>Payment terms</dt><dd>{{c.payment_terms or '—'}}{% if c.tax_exempt %} · Tax exempt{% endif %}</dd>
<dt>QuickBooks ID</dt><dd>{{c.quickbooks_id or 'Not linked'}}</dd><dt>Salesforce ID</dt><dd>{% if c.salesforce_id %}{{c.salesforce_id}}{% if sf_url %} · <a href="{{sf_url}}" target="_blank">Open in Salesforce</a>{% endif %}{% else %}Not linked{% endif %}</dd><dt>Notes</dt><dd>{{c.notes or '—'}}</dd></dl></div>
<div><h2>Primary contact</h2>{% if primary %}<p><strong>{{primary.name}}</strong><br>{{primary.title or ''}}<br>{{primary.phone or ''}}<br>{% if primary.email %}<a href="mailto:{{primary.email}}">{{primary.email}}</a>{% endif %}</p>{% else %}<p class="muted">None set.</p>{% endif %}
{% if can('client_edit') %}<h2 style="margin-top:18px">Log activity</h2><form method="post" action="/clients/{{c.id}}/activity"><select name="kind">{% for k in kinds %}<option>{{k}}</option>{% endfor %}</select><input name="subject" placeholder="Subject" required style="margin-top:8px"><textarea name="details" rows="3" placeholder="Details" style="margin-top:8px"></textarea><button style="margin-top:8px">Save activity</button></form>{% endif %}</div></div></section>
<section><h2>Recent activity</h2><div class="tl">{% for e in timeline[:6] %}<div class="ev {{e.cat}}"><strong>{{e.title}}</strong> <span class="muted">· {{e.when|daytime}}{% if e.actor %} · {{e.actor}}{% endif %}</span>{% if e.detail %}<div>{{e.detail}}</div>{% endif %}</div>{% else %}<p class="muted">No activity yet.</p>{% endfor %}</div><a href="#history">See full history →</a></section></div>

<div class="tab" id="t-contacts"><section><h2>Contacts</h2><table><tr><th>Name</th><th>Title</th><th>Phone</th><th>Email</th><th></th></tr>{% for p in contacts %}<tr><td><strong>{{p.name}}</strong>{% if p.is_primary %} <span class="pill Active">Primary</span>{% endif %}</td><td>{{p.title or ''}}</td><td>{{p.phone or ''}}</td><td>{% if p.email %}<a href="mailto:{{p.email}}">{{p.email}}</a>{% endif %}</td>
<td>{% if not p.is_primary and can('client_edit') %}<form method="post" action="/clients/{{c.id}}/contacts/{{p.id}}/primary" style="display:inline"><button class="secondary">Make primary</button></form> <form method="post" action="/clients/{{c.id}}/contacts/{{p.id}}/remove" style="display:inline"><button class="secondary">Remove</button></form>{% endif %}</td></tr>{% else %}<tr><td colspan="5">No contacts yet.</td></tr>{% endfor %}</table>
{% if can('client_edit') %}<h2 style="margin-top:20px">Add contact</h2><form method="post" action="/clients/{{c.id}}/contacts" class="grid"><div><label>Name *</label><input name="name" required></div><div><label>Title</label><input name="title"></div><div><label>Phone</label><input name="phone"></div><div><label>Email</label><input name="email" type="email"></div><div><label>&nbsp;</label><label><input type="checkbox" name="is_primary" value="1" style="width:auto"> Primary contact</label></div><div><label>&nbsp;</label><button>Add contact</button></div></form>{% endif %}</section></div>

<div class="tab" id="t-repairs"><section><h2>Customer repairs (RMAs)</h2><div style="overflow:auto"><table><tr><th>Inventory ID</th><th>RMA</th><th>Equipment</th><th>Serial</th><th>Stage</th><th>Received</th><th>Closed</th><th>Assigned</th></tr>{% for r in repairs %}<tr><td>{{r.inventory_code or 'Legacy'}}</td><td><a href="/repairs/{{r.id}}"><strong>{{r.rma}}</strong></a></td><td>{{r.manufacturer or ''}} {{r.model or ''}}</td><td>{{r.serial or ''}}</td><td><span class="pill">{{r.stage}}</span></td><td>{{r.received_date|day}}</td><td>{{r.completed_at|day if r.completed_at else ''}}{% if r.resolution %} · {{r.resolution}}{% endif %}</td><td>{{r.assigned_to or r.technician or ''}}</td></tr>{% else %}<tr><td colspan="8">No repairs for this client.</td></tr>{% endfor %}</table></div></section></div>

<div class="tab" id="t-loaners"><section><h2>Loaners</h2><p class="muted">Loaner tracking is the next module to be built. Loaners sent to this client will be listed here.</p></section></div>
<div class="tab" id="t-sales"><section><h2>Sales</h2><p class="muted">Sales records will be listed here once sales tracking is built.</p></section></div>

<div class="tab" id="t-history"><section><h2>Full history</h2><form class="toolbar" onsubmit="return false"><div><label>Show</label><select id="hf" onchange="filt()"><option value="">Everything</option><option>Activity</option><option>Repair</option><option>System</option></select></div></form>
<div class="tl" style="margin-top:16px">{% for e in timeline %}<div class="ev {{e.cat}}" data-cat="{{e.cat}}"><strong>{% if e.url %}<a href="{{e.url}}">{{e.title}}</a>{% else %}{{e.title}}{% endif %}</strong> <span class="muted">· {{e.cat}} · {{e.when|daytime}}{% if e.actor %} · {{e.actor}}{% endif %}</span>{% if e.detail %}<div>{{e.detail}}</div>{% endif %}</div>{% else %}<p class="muted">No history yet.</p>{% endfor %}</div></section></div>
<script>
function show(h){h=(h||'#overview').slice(1);if(!document.getElementById('t-'+h))h='overview';document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('on',t.id==='t-'+h));document.querySelectorAll('.tabs a').forEach(a=>a.classList.toggle('on',a.getAttribute('href')==='#'+h))}
window.addEventListener('hashchange',()=>show(location.hash));show(location.hash);
function filt(){const v=document.getElementById('hf').value;document.querySelectorAll('#t-history .ev').forEach(e=>e.style.display=(!v||e.dataset.cat===v)?'':'none')}
</script>'''


def client_form_values(f):
    vals = {k: clean(f.get(k)) for k in CLIENT_FIELDS}
    vals['tax_exempt'] = 1 if f.get('tax_exempt') == '1' else 0
    if vals['account_type'] not in ACCOUNT_TYPES:
        vals['account_type'] = 'Other'
    if vals['status'] not in ACCOUNT_STATUSES:
        vals['status'] = 'Active'
    if vals['payment_terms'] and vals['payment_terms'] not in PAYMENT_TERMS:
        vals['payment_terms'] = ''
    return vals


def register_client_routes(app, conn):

    @app.context_processor
    def client_picker():
        # Templates call client_options() to fill the client drop-down list.
        def client_options():
            c = conn()
            try:
                return c.execute("SELECT id,client_code,name FROM clients WHERE status<>'Inactive' ORDER BY name").fetchall()
            finally:
                c.close()
        return {'client_options': client_options}

    @app.get('/api/serial-check')
    def serial_check():
        c = conn()
        try:
            return jsonify(serial_matches(c, request.args.get('serial', '')))
        finally:
            c.close()

    @app.route('/clients')
    def clients_list():
        q, typ, status, owner = (clean(request.args.get(k)) for k in ('q', 'type', 'status', 'owner'))
        where, args = [], []
        if q:
            where.append("(c.name LIKE ? OR c.client_code LIKE ? OR c.salesforce_id LIKE ? OR c.phone LIKE ? OR c.email LIKE ? OR EXISTS (SELECT 1 FROM client_contacts p WHERE p.client_id=c.id AND (p.name LIKE ? OR p.email LIKE ? OR p.phone LIKE ?)))")
            args += ['%' + q + '%'] * 8
        if typ:
            where.append('c.account_type=?'); args.append(typ)
        if status:
            where.append('c.status=?'); args.append(status)
        if owner:
            where.append('c.account_owner=?'); args.append(owner)
        sql = '''SELECT c.*, (SELECT name FROM client_contacts p WHERE p.client_id=c.id AND p.active=1 ORDER BY is_primary DESC,id LIMIT 1) contact,
          (SELECT COUNT(*) FROM customer_repairs r WHERE r.client_id=c.id AND r.stage<>'Closed') open_rmas,
          (SELECT COUNT(*) FROM customer_repairs r WHERE r.client_id=c.id) total_rmas,
          (SELECT MAX(created_at) FROM client_activity a WHERE a.client_id=c.id) last_activity FROM clients c'''
        c = conn()
        rows = c.execute(sql + (' WHERE ' + ' AND '.join(where) if where else '') + ' ORDER BY c.name COLLATE NOCASE', args).fetchall()
        stats = c.execute('''SELECT COUNT(*) total, SUM(status='Active') active, SUM(status='Prospect') prospect,
          (SELECT COUNT(DISTINCT client_id) FROM customer_repairs WHERE stage<>'Closed' AND client_id IS NOT NULL) open_clients,
          (SELECT COUNT(*) FROM customer_repairs WHERE stage<>'Closed' AND client_id IS NOT NULL) open_rmas FROM clients''').fetchone()
        owners = [r[0] for r in c.execute("SELECT DISTINCT account_owner FROM clients WHERE COALESCE(account_owner,'')<>'' ORDER BY 1")]
        c.close()
        return page(LIST_PAGE, rows=rows, stats=stats, q=q, type=typ, status=status, owner=owner, owners=owners,
                    types=ACCOUNT_TYPES, statuses=ACCOUNT_STATUSES)

    @app.route('/clients/new', methods=['GET', 'POST'])
    def client_new():
        if request.method == 'POST':
            vals = client_form_values(request.form)
            if not vals['name']:
                flash('Client name is required.'); return redirect('/clients/new')
            c = conn()
            problem = salesforce_check(c, vals['salesforce_id'])
            if problem:
                c.close(); flash(problem); return redirect('/clients/new')
            dup = c.execute('SELECT id,client_code FROM clients WHERE name=? COLLATE NOCASE', (vals['name'],)).fetchone()
            if dup and request.form.get('allow_duplicate') != '1':
                c.close()
                flash(f"A client named \"{vals['name']}\" already exists ({dup['client_code']}). Open it instead, or rename the new one.")
                return redirect(f"/clients/{dup['id']}")
            name = vals.pop('name')
            cid = create_client(c, name, actor_name(), **vals)
            f = request.form
            if clean(f.get('contact_name')):
                c.execute('INSERT INTO client_contacts(client_id,name,title,phone,email,is_primary,created_at) VALUES (?,?,?,?,?,1,?)',
                          (cid, clean(f.get('contact_name')), clean(f.get('contact_title')), clean(f.get('contact_phone')), clean(f.get('contact_email')), now_iso()))
            c.commit(); code = c.execute('SELECT client_code FROM clients WHERE id=?', (cid,)).fetchone()[0]; c.close()
            flash(f'Client created: {code}')
            return redirect(f'/clients/{cid}')
        return page(FORM_PAGE, c=None, types=ACCOUNT_TYPES, statuses=ACCOUNT_STATUSES, terms=PAYMENT_TERMS)

    def get_client(c, cid):
        row = c.execute('SELECT * FROM clients WHERE id=?', (cid,)).fetchone()
        if row is None:
            c.close(); abort(404)
        return row

    @app.route('/clients/<int:cid>/edit', methods=['GET', 'POST'])
    def client_edit(cid):
        c = conn(); old = get_client(c, cid)
        if request.method == 'POST':
            vals = client_form_values(request.form)
            if not vals['name']:
                c.close(); flash('Client name is required.'); return redirect(f'/clients/{cid}/edit')
            problem = salesforce_check(c, vals['salesforce_id'], cid)
            if problem:
                c.close(); flash(problem); return redirect(f'/clients/{cid}/edit')
            changed = [k for k in CLIENT_FIELDS if str(old[k] if old[k] is not None else '') != str(vals[k])]
            if changed:
                c.execute(f"UPDATE clients SET {','.join(k + '=?' for k in CLIENT_FIELDS)},updated_at=? WHERE id=?",
                          [vals[k] for k in CLIENT_FIELDS] + [now_iso(), cid])
                detail = '; '.join(f"{k.replace('_', ' ')}: '{old[k] or ''}' → '{vals[k]}'" for k in changed if k != 'notes')
                if 'notes' in changed:
                    detail = (detail + '; notes updated').strip('; ')
                log(c, cid, 'System', 'Client details updated', detail)
                if 'name' in changed:   # keep the name shown on repairs in step
                    c.execute('UPDATE customer_repairs SET customer=? WHERE client_id=?', (vals['name'], cid))
                c.commit()
            c.close(); flash('Client saved' if changed else 'No changes'); return redirect(f'/clients/{cid}')
        c.close()
        return page(FORM_PAGE, c=old, types=ACCOUNT_TYPES, statuses=ACCOUNT_STATUSES, terms=PAYMENT_TERMS)

    @app.post('/clients/<int:cid>/contacts')
    def client_contact_add(cid):
        c = conn(); cl = get_client(c, cid); f = request.form; name = clean(f.get('name'))
        if not name:
            c.close(); flash('Contact name is required.'); return redirect(f'/clients/{cid}#contacts')
        primary = 1 if f.get('is_primary') == '1' or not c.execute('SELECT 1 FROM client_contacts WHERE client_id=? AND active=1', (cid,)).fetchone() else 0
        if primary:
            c.execute('UPDATE client_contacts SET is_primary=0 WHERE client_id=?', (cid,))
        c.execute('INSERT INTO client_contacts(client_id,name,title,phone,email,is_primary,created_at) VALUES (?,?,?,?,?,?,?)',
                  (cid, name, clean(f.get('title')), clean(f.get('phone')), clean(f.get('email')), primary, now_iso()))
        log(c, cid, 'System', 'Contact added', name + (' (primary)' if primary else ''))
        c.commit(); c.close(); flash('Contact added'); return redirect(f'/clients/{cid}#contacts')

    @app.post('/clients/<int:cid>/contacts/<int:pid>/<action>')
    def client_contact_action(cid, pid, action):
        c = conn(); get_client(c, cid)
        p = c.execute('SELECT * FROM client_contacts WHERE id=? AND client_id=?', (pid, cid)).fetchone()
        if p is None or action not in ('primary', 'remove'):
            c.close(); abort(404)
        if action == 'primary':
            c.execute('UPDATE client_contacts SET is_primary=(id=?) WHERE client_id=?', (pid, cid))
            log(c, cid, 'System', 'Primary contact changed', p['name'])
        else:
            c.execute('UPDATE client_contacts SET active=0 WHERE id=?', (pid,))   # kept for history
            log(c, cid, 'System', 'Contact removed', p['name'])
        c.commit(); c.close(); return redirect(f'/clients/{cid}#contacts')

    @app.post('/clients/<int:cid>/activity')
    def client_activity_add(cid):
        c = conn(); get_client(c, cid); f = request.form
        kind = f.get('kind') if f.get('kind') in ACTIVITY_KINDS else 'Note'
        if not clean(f.get('subject')):
            c.close(); flash('Subject is required.'); return redirect(f'/clients/{cid}')
        log(c, cid, kind, clean(f.get('subject')), clean(f.get('details')))
        c.commit(); c.close(); flash(kind + ' logged'); return redirect(f'/clients/{cid}')

    @app.route('/clients/<int:cid>')
    def client_detail(cid):
        c = conn(); cl = get_client(c, cid)
        contacts = c.execute('SELECT * FROM client_contacts WHERE client_id=? AND active=1 ORDER BY is_primary DESC,name', (cid,)).fetchall()
        repairs = c.execute("SELECT * FROM customer_repairs WHERE client_id=? ORDER BY CASE WHEN stage='Closed' THEN 1 ELSE 0 END, received_date DESC", (cid,)).fetchall()
        timeline = []
        for a in c.execute('SELECT * FROM client_activity WHERE client_id=?', (cid,)):
            timeline.append({'cat': 'System' if a['kind'] == 'System' else 'Activity', 'title': (a['kind'] + ': ' if a['kind'] != 'System' else '') + (a['subject'] or ''),
                             'detail': a['details'], 'when': a['created_at'], 'actor': a['actor'], 'url': None})
        # Repair ticket work log (repairs.py): receipts and stage moves show on the client timeline.
        for h in c.execute('''SELECT h.*, r.rma, r.inventory_code, r.model, r.manufacturer, r.id rid FROM repair_log h
                              JOIN customer_repairs r ON r.id=h.repair_id
                              WHERE r.client_id=? AND h.kind IN ('created','stage')''', (cid,)):
            eq = f"{h['manufacturer'] or ''} {h['model'] or ''}".strip()
            ticket = h['inventory_code'] or h['rma']
            title = f"{ticket} received ({eq})" if h['kind'] == 'created' else f"{ticket}: {h['old_stage']} → {h['new_stage']}"
            timeline.append({'cat': 'Repair', 'title': title, 'detail': h['text'] if h['kind'] == 'stage' else '', 'when': h['at'], 'actor': h['actor'] or '', 'url': f"/repairs/{h['rid']}"})
        timeline.sort(key=lambda e: e['when'] or '', reverse=True)
        done = [r for r in repairs if r['completed_at']]
        avg = None
        if done:
            from datetime import date
            avg = round(sum(max(0, (datetime.fromisoformat(r['completed_at']).date() - date.fromisoformat(r['received_date'])).days) for r in done) / len(done), 1)
        kpi = {'open': sum(1 for r in repairs if r['stage'] != 'Closed'), 'total': len(repairs), 'avg_days': avg}
        c.close()
        return page(DETAIL_PAGE, c=cl, sf_url=salesforce_url(cl['salesforce_id']), contacts=contacts, primary=contacts[0] if contacts and contacts[0]['is_primary'] else None,
                    repairs=repairs, timeline=timeline, kpi=kpi, kinds=ACTIVITY_KINDS)
