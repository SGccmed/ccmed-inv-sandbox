"""Build a demo database full of FAKE test data.

Usage (from the app folder):
    python seed_demo.py                 -> creates demo_inventory.db
    python seed_demo.py my_demo.db      -> creates my_demo.db

The file has no user accounts, so uploading it through Admin -> Restore database
on the live site keeps your existing logins. All customers, serial numbers, RMAs,
costs and OEM part numbers here are invented for testing.
"""
import os, sys, random, sqlite3
from datetime import datetime, timedelta, timezone, date
from pathlib import Path

out = Path(sys.argv[1] if len(sys.argv) > 1 else 'demo_inventory.db').resolve()
if out.exists():
    print(f'{out.name} already exists; delete it first or choose another name.'); sys.exit(1)

# Point the app at the new file so its own schema code builds every table.
os.environ['CCM_DB_PATH'] = str(out)
os.environ.setdefault('CCM_SECRET', 'seed-only')  # don't write a .secret_key file
os.environ.pop('CCM_INITIAL_ADMIN_USER', None); os.environ.pop('CCM_INITIAL_ADMIN_PASSWORD', None)
import app as A                      # runs init_db(): units, repairs, cores, users tables
import product_database as P

rnd = random.Random(42)              # same data every run
NOW = datetime.now(timezone.utc).replace(microsecond=0)
def ts(days_ago, hours=0): return (NOW - timedelta(days=days_ago, hours=hours)).isoformat()
def d(days_ago): return (NOW - timedelta(days=days_ago)).date().isoformat()

c = A.conn()
c.executescript(P.SCHEMA)
for code, label in [('SYS','Ultrasound Systems'),('PRB','Ultrasound Probes'),('PTS','Parts / Accessories')]:
    c.execute('INSERT OR IGNORE INTO product_categories(code,label) VALUES (?,?)', (code, label))
for name in ['GE','Philips','Siemens','Canon/Toshiba','Samsung','Mindray','Fujifilm/SonoSite']:
    c.execute('INSERT OR IGNORE INTO product_manufacturers(name) VALUES (?)', (name,))

# ---------- Master product catalog ----------
SYSTEMS = [('GE','LOGIQ E10'),('GE','Vivid E95'),('GE','Voluson E8'),('Philips','EPIQ 7'),('Philips','Affiniti 70'),
           ('Siemens','ACUSON Sequoia'),('Canon/Toshiba','Aplio i800'),('Samsung','RS85'),('Mindray','Resona 7'),('Fujifilm/SonoSite','Edge II')]
PROBES = [('GE','C1-6-D',[0,2]),('GE','ML6-15-D',[0]),('GE','M5Sc-D',[1]),('Philips','C5-1',[3,4]),('Philips','eL18-4',[3]),
          ('Philips','X5-1',[3,4]),('Siemens','5C1',[5]),('Canon/Toshiba','PVI-475BX',[6]),('Samsung','CA1-7A',[7]),
          ('Mindray','SC5-1U',[8]),('Fujifilm/SonoSite','rC60xi',[9]),('Fujifilm/SonoSite','HFL50xp',[9])]
PARTS = [('GE','LOGIQ Keyboard Assembly'),('Philips','EPIQ Power Supply'),('Siemens','Probe Port Connector'),
         ('Fujifilm/SonoSite','Edge II Battery'),('GE','Trackball Assembly'),('Philips','Touchscreen Panel')]
prod_ids = {}
def add_product(cat, mfg, model, status):
    name = f'{mfg} {model}'
    pid = c.execute('INSERT INTO products(category,manufacturer,model,display_name,short_description,technical_specs,review_notes,review_status) VALUES (?,?,?,?,?,?,?,?)',
        (cat, mfg, model, name, f'DEMO DATA: placeholder description for {name}.', 'DEMO DATA: specs not researched.',
         'Fake test record', status)).lastrowid
    pn = f'DEMO-{rnd.randint(100000,999999)}'
    c.execute('INSERT INTO product_part_numbers(product_id,part_number,verification) VALUES (?,?,?)', (pid, pn, 'Pending Verification'))
    c.execute('INSERT INTO product_audit(product_id,action,old_value,new_value,actor,created_at) VALUES (?,?,?,?,?,?)',
              (pid, 'Demo seed', '', name, 'seed_demo', ts(30)))
    prod_ids[name] = pid; return pid
sys_ids = [add_product('SYS', m, x, 'Approved' if i % 3 else 'Pending Review') for i, (m, x) in enumerate(SYSTEMS)]
for i, (m, x, compat) in enumerate(PROBES):
    pid = add_product('PRB', m, x, 'Approved' if i % 4 else 'Pending Review')
    for s in compat: c.execute('INSERT INTO product_compatibility(probe_id,system_id) VALUES (?,?)', (pid, sys_ids[s]))
for m, x in PARTS: add_product('PTS', m, x, 'Pending Review')
c.execute('INSERT INTO product_requests(category,manufacturer,model,notes,request_type,created_at) VALUES (?,?,?,?,?,?)',
          ('PRB','Mindray','L14-6WU','Demo request: seen on incoming trade-in','New Product', ts(2)))

# ---------- Shared inventory ID sequence (CC / CR / RP) ----------
def new_code(prefix, days_ago):
    cur = c.execute('INSERT INTO inventory_id_sequence(prefix,created_at) VALUES (?,?)', (prefix, ts(days_ago)))
    code = f"{prefix}-{(NOW - timedelta(days=days_ago)).strftime('%m%d%y')}-{cur.lastrowid:03d}"
    c.execute('UPDATE inventory_id_sequence SET generated_id=? WHERE seq=?', (code, cur.lastrowid)); return code
def serial(): return 'DM' + ''.join(rnd.choice('ABCDEFGHJKLMNPRSTUVWXYZ0123456789') for _ in range(8))
SHELVES = [f'{r}{n}' for r in 'ABCD' for n in range(1, 6)]
SUPPLIERS = ['Demo Medical Brokers', 'Sample Imaging Supply', 'Test Trade-In Co.', 'Placeholder Auctions']
ALL_MODELS = [(m, x, 'SYS') for m, x in SYSTEMS] + [(m, x, 'PRB') for m, x, _ in PROBES] + [(m, x, 'PTS') for m, x in PARTS]
def price(cat): return {'SYS': rnd.randrange(8000, 45000, 250), 'PRB': rnd.randrange(900, 7500, 50), 'PTS': rnd.randrange(80, 1800, 10)}[cat]

# ---------- Legacy units (as if imported from the old Excel "Location" sheet) ----------
LEGACY_LOCS = (['A1','A2','B3','C4','D1','B1/R2','A5','C2'] * 4) + ['SOLD']*6 + ['RENTAL']*3 + ['LOANER']*2 + ['PENDING']*2 + ['BACK ROOM','?']
for row, loc in enumerate(LEGACY_LOCS, start=3):
    m, x, cat = rnd.choice(ALL_MODELS)
    tag = rnd.choice(['', '', ' NEW', ' REFURB'])
    product = f'{m} {x}{tag}'
    s = serial() if rnd.random() > 0.08 else ''
    cost = None if rnd.random() < 0.07 else float(price(cat))
    status, physical = A.classify(loc)
    issues = []
    if not s: issues.append('Missing serial')
    if status == 'Needs review': issues.append('Unrecognized location/status')
    if status == 'Pending review': issues.append('Pending status')
    if cost is None: issues.append('Missing cost')
    cond = 'New (inferred)' if 'NEW' in product else 'Refurbished (inferred)' if 'REFURB' in product else 'Unspecified'
    c.execute('INSERT INTO units(source_sheet,source_row,manufacturer,product_raw,product_key,serial,cost,dom,notes,location_raw,physical_location,status,condition,review) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        ('Location', row, None, product, A.norm(product), s, cost, str(rnd.randint(2015, 2024)), 'Demo legacy record', loc, physical, status, cond, '; '.join(issues)))
# One duplicate serial so the review flag shows up
dup = c.execute("SELECT serial FROM units WHERE serial<>'' LIMIT 1").fetchone()[0]
c.execute("UPDATE units SET serial=? WHERE id=(SELECT MAX(id) FROM units WHERE status='Active')", (dup,))
c.execute("UPDATE units SET review=TRIM(COALESCE(review,'')||'; Duplicate serial (verify)','; ') WHERE serial=?", (dup,))

# ---------- New receiving batches (CC stock) ----------
for b_days in (45, 21, 9, 2):
    n = rnd.randint(3, 6)
    batch = c.execute('INSERT INTO receiving_batches(created_at,item_count) VALUES (?,?)', (ts(b_days), n)).lastrowid
    for _ in range(n):
        m, x, cat = rnd.choice(ALL_MODELS); shelf = rnd.choice(SHELVES)
        c.execute('INSERT INTO units(manufacturer,product_raw,product_key,serial,cost,dom,notes,location_raw,physical_location,status,condition,review,inventory_code,supplier,received_date,entry_batch_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (m, f'{m} {x}', A.norm(f'{m} {x}'), serial(), float(price(cat)), '', 'Demo receiving', shelf, shelf, rnd.choice(['Active']*5+['Rental']),
             rnd.choice(['New','Refurbished','Used - Good']), '', new_code('CC', b_days), rnd.choice(SUPPLIERS), d(b_days), batch))

# Location transfers and internal repair orders on some active units
active = [r[0] for r in c.execute("SELECT id FROM units WHERE status='Active' ORDER BY id")]
for uid in rnd.sample(active, 10):
    old = c.execute('SELECT physical_location FROM units WHERE id=?', (uid,)).fetchone()[0]; new = rnd.choice(SHELVES)
    c.execute('INSERT INTO movements(unit_id,old_location,new_location,created_at,note) VALUES (?,?,?,?,?)',
              (uid, old, new, (NOW - timedelta(days=rnd.randint(1, 40))).strftime('%Y-%m-%d %H:%M:%S'), 'Demo shelf reorganization [seed_demo]'))
    c.execute('UPDATE units SET physical_location=? WHERE id=?', (new, uid))
for uid in rnd.sample(active, 4):
    c.execute('INSERT INTO repair_orders(unit_id,repair_type,status,notes) VALUES (?,?,?,?)',
              (uid, rnd.choice(['Internal refurbishment','Customer repair']), rnd.choice(['Open','Closed']), 'Demo repair order [seed_demo]'))

# ---------- Customer repairs (RP) across every stage ----------
CUSTOMERS = ['Demo Valley Imaging', 'Sample Regional Hospital', 'Test Women\'s Clinic', 'Placeholder Cardiology Group',
             'Example Vet Hospital', 'Fictional Ortho Partners', 'Mock Urgent Care', 'Pretend Mobile Ultrasound']
TECHS = ['Tech A', 'Tech B', 'Tech C']
plan = [('Open Repairs', 1), ('Open Repairs', 5), ('Open Repairs', 9), ('Evaled', 2), ('Evaled', 6), ('In Repair', 1),
        ('In Repair', 4), ('In Repair', 11), ('QC', 0), ('QC', 3), ('Ready to Ship', 1), ('Ready to Ship', 8),
        ('Completed', 0), ('Completed', 0), ('Completed', 0), ('Completed', 0)]
for i, (stage, stage_days) in enumerate(plan, start=1):
    m, x, cat = rnd.choice([t for t in ALL_MODELS if t[2] != 'PTS'])
    idx = A.REPAIR_STAGES.index(stage)
    received = stage_days + idx * rnd.randint(2, 4) + rnd.randint(0, 3)
    code = new_code('RP', received)
    path = A.REPAIR_STAGES[:idx + 1]
    # stage start times walking forward from the received date
    starts = [ts(received)]
    for k in range(1, len(path)):
        frac = k / (len(path) - 1) if len(path) > 1 else 1
        starts.append(ts(round(received - (received - stage_days) * frac)))
    completed = starts[-1] if stage == 'Completed' else None
    rid = c.execute('INSERT INTO customer_repairs(rma,customer,manufacturer,model,part_number,serial,location,received_date,technician,notes,stage,stage_started_at,created_at,updated_at,completed_at,inventory_code) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (f'RMA-DEMO-{1000+i}', rnd.choice(CUSTOMERS), m, x, '', serial(), rnd.choice(['R1','R2','R3']), d(received), rnd.choice(TECHS),
         rnd.choice(['Image dropout on elements', 'Cracked lens', 'Will not power on', 'Cable strain relief torn', 'Intermittent connector fault']),
         stage, starts[-1], starts[0], starts[-1], completed, code)).lastrowid
    prev = None
    for st, when in zip(path, starts):
        c.execute('INSERT INTO customer_repair_history(repair_id,old_stage,new_stage,changed_at,note) VALUES (?,?,?,?,?)',
                  (rid, prev, st, when, 'RMA created' if prev is None else 'Demo stage change')); prev = st

# ---------- Cores (CR) in every stage ----------
core_plan = ['Needs Repair', 'Needs Repair', 'Needs Repair', 'In Repair', 'In Repair', 'QC', 'Ready for Stock', 'Converted to Stock']
for i, stage in enumerate(core_plan):
    m, x, cat = rnd.choice([t for t in ALL_MODELS if t[2] == 'PRB'])
    age = rnd.randint(5, 50); code = new_code('CR', age); orig = float(rnd.randrange(150, 1500, 25))
    cid = c.execute('INSERT INTO core_items(inventory_code,manufacturer,product_raw,serial,location,supplier,original_cost,stage,notes,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',
        (code, m, f'{m} {x}', serial(), rnd.choice(['C1','C2','C3']), rnd.choice(SUPPLIERS), orig, stage, 'Demo core', ts(age))).lastrowid
    if stage != 'Needs Repair':
        for t in range(rnd.randint(1, 3)):
            hours = rnd.choice([0.5, 1, 1.5, 2, 3]); rate = 65.0
            c.execute('INSERT INTO core_transactions(core_id,performed_at,technician,description,labor_method,labor_hours,labor_rate,labor_cost,parts_cost,other_cost) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (cid, ts(age - t - 1), rnd.choice(TECHS), rnd.choice(['Replaced lens', 'Re-terminated cable', 'Element test and repair', 'Housing reseal']),
                 'hourly', hours, rate, round(hours * rate, 2), float(rnd.randrange(0, 400, 10)), 0.0))
    if stage in ('Ready for Stock', 'Converted to Stock'):
        c.execute('UPDATE core_items SET qc_result=?,qc_technician=?,qc_date=?,qc_notes=? WHERE id=?', ('Passed', 'Tech B', ts(2), 'Demo QC pass', cid))
    if stage == 'Converted to Stock':
        core = c.execute('SELECT * FROM core_items WHERE id=?', (cid,)).fetchone()
        rc = c.execute('SELECT COALESCE(SUM(labor_cost+parts_cost+other_cost),0) FROM core_transactions WHERE core_id=?', (cid,)).fetchone()[0]
        uid = c.execute('INSERT INTO units(manufacturer,product_raw,product_key,serial,cost,notes,location_raw,physical_location,status,condition,review,inventory_code,supplier,received_date) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (m, core['product_raw'], A.norm(core['product_raw']), core['serial'], round(orig + rc, 2), 'Converted from ' + code, 'A3', 'A3', 'Active', 'Refurbished', '', new_code('CC', 1), core['supplier'], d(1))).lastrowid
        c.execute('UPDATE core_items SET converted_unit_id=? WHERE id=?', (uid, cid))

# ---------- Clients (accounts, contacts, activity) ----------
CLIENT_INFO = {
 'Demo Valley Imaging': ('Imaging Center', 'Active', 'Net 30', 'jsmith'), 'Sample Regional Hospital': ('Hospital', 'Active', 'Net 45', 'jsmith'),
 "Test Women's Clinic": ("OB/GYN / Women's Health", 'Active', 'Net 30', 'mlee'), 'Placeholder Cardiology Group': ('Cardiology', 'Active', 'Net 30', 'mlee'),
 'Example Vet Hospital': ('Veterinary', 'Active', 'Credit card on file', 'jsmith'), 'Fictional Ortho Partners': ('Clinic / Physician Office', 'On Hold', 'Prepaid', 'mlee'),
 'Mock Urgent Care': ('Clinic / Physician Office', 'Active', 'Due on receipt', 'jsmith'), 'Pretend Mobile Ultrasound': ('Mobile Ultrasound', 'Active', 'Net 15', 'mlee'),
 'Sample Biomed Services': ('Biomed / ISO', 'Prospect', '', 'jsmith'), 'Imaginary Equipment Brokers': ('Dealer / Broker', 'Prospect', '', 'mlee'),
 'Old Demo Clinic': ('Clinic / Physician Office', 'Inactive', 'Net 30', 'jsmith')}
FIRST = ['Alex', 'Jordan', 'Taylor', 'Casey', 'Morgan', 'Riley', 'Jamie', 'Drew', 'Avery', 'Quinn', 'Reese', 'Skyler']
LAST = ['Sample', 'Demo', 'Tester', 'Example', 'Mockley', 'Placeholder']
TITLES = ['Biomed Manager', 'Lead Sonographer', 'Practice Manager', 'Purchasing', 'Accounts Payable', 'Clinical Director']
client_ids = {}
for i, (name, (typ, status, terms, owner)) in enumerate(CLIENT_INFO.items(), start=1):
    age = rnd.randint(90, 700); street = rnd.randint(100, 9999); slug = name.lower().replace("'", '').replace(' ', '')[:18]
    addr = f'{street} Demo Street\nSuite {rnd.randint(1, 400)}\nSampletown, CA 90000'
    cid = c.execute("""INSERT INTO clients(name,account_type,status,phone,email,website,billing_address,shipping_address,payment_terms,tax_exempt,account_owner,notes,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (name, typ, status, f'(555) 01{i:02d}-{rnd.randint(1000, 9999)}', f'info@{slug}.example', f'www.{slug}.example',
        addr, addr if rnd.random() < 0.6 else addr.replace('Suite', 'Receiving Dock'), terms, 1 if typ == 'Hospital' else 0, owner,
        'DEMO client: fake contact details.', ts(age), ts(age))).lastrowid
    c.execute('UPDATE clients SET client_code=? WHERE id=?', (f'CL-{cid:05d}', cid)); client_ids[name] = cid
    c.execute('INSERT INTO client_activity(client_id,kind,subject,details,actor,created_at) VALUES (?,?,?,?,?,?)', (cid, 'System', 'Client created', f'Client record created for {name}', owner, ts(age)))
    for k in range(rnd.randint(1, 3)):
        nm = f'{rnd.choice(FIRST)} {rnd.choice(LAST)}'
        c.execute('INSERT INTO client_contacts(client_id,name,title,phone,email,is_primary,created_at) VALUES (?,?,?,?,?,?,?)',
                  (cid, nm, TITLES[(i + k) % len(TITLES)], f'(555) 02{i:02d}-{rnd.randint(1000, 9999)}', nm.lower().replace(' ', '.') + f'@{slug}.example', 1 if k == 0 else 0, ts(age)))
    for k in range(rnd.randint(1, 4)):
        kind, subj = rnd.choice([('Call', 'Called about probe availability'), ('Email', 'Sent quote for refurbished probe'), ('Quote', 'Quote Q-DEMO-%d sent' % rnd.randint(100, 999)),
                                 ('Meeting', 'Site visit / equipment review'), ('Note', 'Prefers shipping via ground'), ('Follow-up', 'Follow up on PM contract')])
        c.execute('INSERT INTO client_activity(client_id,kind,subject,details,actor,created_at) VALUES (?,?,?,?,?,?)',
                  (cid, kind, subj, 'Demo activity entry.', owner, ts(rnd.randint(1, min(age, 80)), rnd.randint(0, 9))))
# Link the demo repairs to their client records
for name, cid in client_ids.items():
    c.execute('UPDATE customer_repairs SET client_id=? WHERE customer=?', (cid, name))
# Every item so far is ours (blue tape); add a few customer-owned (orange tape) units held on site
c.execute("UPDATE units SET tape='Blue'"); c.execute("UPDATE core_items SET tape='Blue'")
for k, name in enumerate(['Demo Valley Imaging', 'Sample Regional Hospital', 'Sample Regional Hospital', 'Pretend Mobile Ultrasound', 'Example Vet Hospital']):
    m, x, cat = rnd.choice([t for t in ALL_MODELS if t[2] != 'PTS']); age = rnd.randint(3, 40)
    c.execute("""INSERT INTO units(manufacturer,product_raw,product_key,serial,cost,dom,notes,location_raw,physical_location,status,condition,review,inventory_code,supplier,received_date,tape,owner_client_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (m, f'{m} {x}', A.norm(f'{m} {x}'), serial(), 0.0, '', rnd.choice(['Consignment - sell on behalf of client', 'Held for client (storage)', 'Client trade-in pending appraisal']),
        'D5', 'D5', 'Active', 'Used - Good', '', new_code('CC', age), '', d(age), 'Orange', client_ids[name]))
# Re-use one existing serial on a different manufacturer's product to show the S/N alert
src = c.execute("SELECT serial FROM units WHERE inventory_code LIKE 'CC-%' AND serial<>'' ORDER BY id LIMIT 1").fetchone()[0]
c.execute("UPDATE core_items SET serial=? WHERE id=(SELECT MIN(id) FROM core_items)", (src,))

c.commit()
counts = {t: c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in
          ['units', 'movements', 'repair_orders', 'customer_repairs', 'core_items', 'core_transactions', 'products', 'clients', 'client_contacts', 'client_activity', 'users']}
c.close()
print(f'Created {out}'); print(counts)
