import json
import os
import re
import secrets
import sqlite3
from pathlib import Path
from datetime import datetime, timezone
from flask import request,render_template_string,redirect,url_for,flash,session,g
from openpyxl import load_workbook
from auth import is_admin, actor_name, add_user_block, can
from clients import resolve_client
import choices
SCHEMA="""CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, category TEXT NOT NULL, manufacturer TEXT,model TEXT NOT NULL,display_name TEXT NOT NULL,short_description TEXT,technical_specs TEXT,description_source TEXT,spec_source TEXT,review_notes TEXT,review_status TEXT NOT NULL DEFAULT 'Pending Review',active INTEGER NOT NULL DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(category,manufacturer,model));
CREATE TABLE IF NOT EXISTS product_part_numbers(id INTEGER PRIMARY KEY,product_id INTEGER NOT NULL,part_number TEXT NOT NULL,source_url TEXT,verification TEXT NOT NULL DEFAULT 'Pending Verification',active INTEGER NOT NULL DEFAULT 1,UNIQUE(product_id,part_number));
CREATE TABLE IF NOT EXISTS product_import_staging(token TEXT PRIMARY KEY,user_id INTEGER,rows_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS product_requests(id INTEGER PRIMARY KEY,category TEXT,manufacturer TEXT,model TEXT,part_number TEXT,notes TEXT,request_type TEXT DEFAULT 'New Product',status TEXT DEFAULT 'Pending',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS product_audit(id INTEGER PRIMARY KEY,product_id INTEGER,action TEXT,old_value TEXT,new_value TEXT,actor TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS product_compatibility(id INTEGER PRIMARY KEY,probe_id INTEGER NOT NULL,system_id INTEGER NOT NULL,source_url TEXT,UNIQUE(probe_id,system_id));
CREATE TABLE IF NOT EXISTS product_imports(id INTEGER PRIMARY KEY,filename TEXT,imported_at TEXT DEFAULT CURRENT_TIMESTAMP,added INTEGER,skipped INTEGER);
CREATE TABLE IF NOT EXISTS product_manufacturers(name TEXT PRIMARY KEY,active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS product_categories(code TEXT PRIMARY KEY,label TEXT NOT NULL,active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS stock_requests(          -- "Request this item" on a product page (v1.13)
    id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id), quantity INTEGER NOT NULL DEFAULT 1,
    client_id INTEGER, customer_name TEXT, note TEXT, requested_by TEXT NOT NULL, created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'Open', closed_by TEXT, closed_at TEXT);
CREATE INDEX IF NOT EXISTS idx_stock_requests_status ON stock_requests(status);"""
CATEGORIES={'SYS':'System','PRB':'Probe','PTS':'Part / accessory'}
def now_iso():return datetime.now(timezone.utc).isoformat(timespec='seconds')

# ---------- Linking stock items to catalog products (v1.13) ----------
# A stock item stores the model as typed ("GE C1-6-D REFURB"); units.product_id ties it to its catalog product so the
# product page can list its units. A unit matches when its text equals or starts with the product's display name, its
# "manufacturer model", or (when the manufacturer matches) its model. Longest name wins; nothing else is guessed.
def _n(x):return re.sub(r'[^A-Z0-9]+',' ',str(x or '').upper()).strip()
def product_matcher(c):
 keys=[]
 for p in c.execute('SELECT id,manufacturer,model,display_name FROM products'):
  for k in {_n(p['display_name']),_n(f"{p['manufacturer'] or ''} {p['model']}")}:
   if k:keys.append((k,p['id'],None))
  if _n(p['model']):keys.append((_n(p['model']),p['id'],_n(p['manufacturer'])))
 keys.sort(key=lambda t:-len(t[0]))
 def match(manufacturer,text):
  raw,full,mfr=_n(text),_n(f'{manufacturer or ""} {text}'),_n(manufacturer)
  for k,pid,need in keys:
   if need is None:
    if any(t==k or t.startswith(k+' ') for t in (raw,full)):return pid
   elif need and mfr==need and (raw==k or raw.startswith(k+' ')):return pid
  return None
 return match
def link_units(c):
 """Link stock items that have no product yet. Runs at start-up, after receiving and after catalog changes."""
 match=product_matcher(c)
 for u in c.execute('SELECT id,manufacturer,product_raw FROM units WHERE product_id IS NULL').fetchall():
  pid=match(u['manufacturer'],u['product_raw'])
  if pid:c.execute('UPDATE units SET product_id=? WHERE id=?',(pid,u['id']))
def ensure_product_schema(c):
 """Product tables, the units.product_id link, and linking any stock items not linked yet. Safe on every start."""
 c.executescript(SCHEMA)
 if 'product_id' not in {r[1] for r in c.execute('PRAGMA table_info(units)')}:
  c.execute('ALTER TABLE units ADD COLUMN product_id INTEGER')
 link_units(c)
STYLE='''<style>body{margin:0;background:#f3f6fa;color:#1b3043;font:14px/1.5 Segoe UI,Arial,sans-serif}aside{width:220px;background:#112a42;min-height:100vh;padding:22px 15px;position:fixed;box-sizing:border-box}aside a{display:block;color:#dce9f4;padding:11px;text-decoration:none;border-radius:7px}aside a:hover{background:#2b5f88}aside h2{color:white}main{margin-left:0;padding:30px;max-width:1500px}section{background:white;border:1px solid #dfe7ef;border-radius:12px;padding:20px;margin:15px 0}input,select,textarea{padding:9px;border:1px solid #cbd8e2;border-radius:6px;max-width:100%;box-sizing:border-box}button,.btn{background:#245d86;color:white;border:0;border-radius:7px;padding:10px 14px;cursor:pointer;text-decoration:none;display:inline-block}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #e5edf3;text-align:left;vertical-align:top}th{background:#f0f5f9}a{color:#245d86}label{display:block;margin:9px 0 4px;font-weight:bold}.muted{color:#667c8d}.row{display:flex;gap:12px;flex-wrap:wrap;align-items:end}.row>*{max-width:100%}.msg{padding:10px;background:#e8f6eb;border-radius:8px}@media(max-width:700px){aside{position:static;width:auto;min-height:0}main{margin:0;padding:15px}}</style>'''
LAY='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>CC Medical | Product Database</title>'''+STYLE+'''</head><body><!--sidebar--><main>{% with msgs=get_flashed_messages() %}{% for m in msgs %}<p class="msg">{{m}}</p>{% endfor %}{% endwith %}{{body|safe}}</main></body></html>'''
LAY=add_user_block(LAY)
def render(body,**kwargs):
 from flask import render_template_string
 return render_template_string(LAY,body=render_template_string(body,**kwargs))
def clean(x):return str(x or '').strip()
def key(x):return re.sub(r'\s+',' ',clean(x)).upper()
def admin():return is_admin()
def register_product_routes(app,conn,base):
 def setup(c):
  c.executescript(SCHEMA)
  # Older databases were created without the part-number 'active' column.
  if 'active' not in {r['name'] for r in c.execute('PRAGMA table_info(product_part_numbers)')}:
   c.execute('ALTER TABLE product_part_numbers ADD COLUMN active INTEGER NOT NULL DEFAULT 1')
  for code,label in [('SYS','Ultrasound Systems'),('PRB','Ultrasound Probes'),('PTS','Parts / Accessories')]:c.execute('INSERT OR IGNORE INTO product_categories(code,label) VALUES (?,?)',(code,label))
  for name in choices.MANUFACTURERS:c.execute('INSERT OR IGNORE INTO product_manufacturers(name) VALUES (?)',(name,))
  c.commit()
 def audit(c,pid,action,old,new):c.execute('INSERT INTO product_audit(product_id,action,old_value,new_value,actor) VALUES (?,?,?,?,?)',(pid,action,old,new,actor_name()))
 def seed(c):
  if c.execute('SELECT COUNT(*) FROM products').fetchone()[0]:return
  path=base/'CC_Medical_Product_Master_Research.xlsx'
  if not path.exists():return
  w=load_workbook(path,read_only=True,data_only=True);s=w['Master Product Preview']
  for r in s.iter_rows(min_row=2,values_only=True):
   pid,cat,mfg,model,display,parts,short,descurl,confidence,notes,status,*tail=(list(r)+[None]*15)[:15]
   if not model:continue
   tech=tail[0] if len(tail)>0 else None;specurl=tail[1] if len(tail)>1 else None
   c.execute('INSERT OR IGNORE INTO products(category,manufacturer,model,display_name,short_description,technical_specs,description_source,spec_source,review_notes,review_status) VALUES (?,?,?,?,?,?,?,?,?,?)',(cat or 'PTS',mfg,model,display or model,short,tech,descurl,specurl,notes,'Pending Review' if status!='Approved' else 'Approved'))
  c.commit()
 # ---------- Product list (with open stock requests) and one product's stock / details / edit ----------
 PAGE=add_user_block((base/'products.html').read_text(encoding='utf-8'))
 @app.context_processor
 def product_helpers():
  # The + New Inventory form suggests catalog models in its Model / Product box.
  def product_options():
   c=conn()
   try:return c.execute('SELECT model,display_name FROM products WHERE active=1 ORDER BY display_name').fetchall()
   finally:c.close()
  return {'product_options':product_options}
 GROUPS=[('available','Available'),('out','On loan / rental'),('sold','Sold')]
 GROUP_STATUS={'available':('Active',),'out':('On loan','Rental'),'sold':('Sold archive',)}
 def page(**ctx):
  return render_template_string(PAGE,categories=CATEGORIES,**ctx)
 def user_names(c):
  return {r['username']:(r['full_name'] or r['username']) for r in c.execute('SELECT username,full_name FROM users')}
 def can_close(r):
  # Admins, the person who asked, or the sales rep who owns the customer it's for.
  return can('stock_request_close') or r['requested_by']==g.user['username'] or bool(r['account_owner'] and r['account_owner']==g.user['username'])
 def requests_query(c,status,limit=200):
  order='sr.created_at' if status=='Open' else 'sr.closed_at DESC'
  return c.execute(f"""SELECT sr.*,p.display_name,p.manufacturer,p.category,cl.account_owner,
     (SELECT COUNT(*) FROM units u WHERE u.product_id=p.id AND u.status='Active') available
     FROM stock_requests sr JOIN products p ON p.id=sr.product_id LEFT JOIN clients cl ON cl.id=sr.client_id
     WHERE sr.status=? ORDER BY {order} LIMIT {int(limit)}""",(status,)).fetchall()
 @app.route('/products')
 def products():
  c=conn();setup(c);seed(c);q=clean(request.args.get('q'));category=clean(request.args.get('category'));mfr=clean(request.args.get('mfr'))
  instock=request.args.get('instock')=='1';where=['1=1'];args=[]
  if q:where.append("(p.display_name LIKE ? OR p.model LIKE ? OR p.manufacturer LIKE ? OR EXISTS(SELECT 1 FROM product_part_numbers pn WHERE pn.product_id=p.id AND pn.part_number LIKE ?))");args+=['%'+q+'%']*4
  if category in CATEGORIES:where.append('p.category=?');args.append(category)
  if mfr:where.append('p.manufacturer=?');args.append(mfr)
  rows=c.execute("""SELECT p.*,
     (SELECT COUNT(*) FROM units u WHERE u.product_id=p.id AND u.status='Active') available,
     (SELECT COUNT(*) FROM units u WHERE u.product_id=p.id AND u.status IN ('On loan','Rental')) out_now,
     (SELECT COUNT(*) FROM units u WHERE u.product_id=p.id AND u.status='Sold archive') sold
     FROM products p WHERE """+' AND '.join(where)+' ORDER BY p.active DESC, p.display_name COLLATE NOCASE LIMIT 1000',args).fetchall()
  if instock:rows=[r for r in rows if r['available']]
  open_requests=[dict(r,can_close=can_close(r)) for r in requests_query(c,'Open')]
  show_closed=request.args.get('closed')=='1'
  closed_requests=requests_query(c,'Closed',limit=50) if show_closed else []
  names=user_names(c);c.close()
  return page(mode='list',rows=rows,q=q,category=category,mfr=mfr,instock=instock,open_requests=open_requests,
              closed_requests=closed_requests,show_closed=show_closed,names=names)
 def product_context(c,pid):
  p=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
  if not p:return None
  return dict(p=p,parts=c.execute('SELECT * FROM product_part_numbers WHERE product_id=? ORDER BY active DESC,part_number',(pid,)).fetchall(),
   history=c.execute('SELECT * FROM product_audit WHERE product_id=? ORDER BY id DESC',(pid,)).fetchall(),
   systems=c.execute('SELECT s.* FROM products s JOIN product_compatibility pc ON s.id=pc.system_id WHERE pc.probe_id=?',(pid,)).fetchall(),
   probes=c.execute('SELECT s.* FROM products s JOIN product_compatibility pc ON s.id=pc.probe_id WHERE pc.system_id=?',(pid,)).fetchall())
 @app.route('/products/<int:pid>')
 def detail(pid):
  c=conn();setup(c);ctx=product_context(c,pid)
  if not ctx:c.close();return 'Product not found',404
  if request.args.get('tab')=='details':
   c.close();return page(mode='details',**ctx)
  # Stock: units of this product by group (technicians only see what's available), sub-filtered by location or serial.
  groups=GROUPS if can('restricted_stock') else GROUPS[:1]
  group=request.args.get('group') if request.args.get('group') in dict(groups) else 'available'
  counts={k:c.execute("SELECT COUNT(*) FROM units WHERE product_id=? AND status IN (%s)"%','.join('?'*len(GROUP_STATUS[k])),(pid,*GROUP_STATUS[k])).fetchone()[0] for k,_ in groups}
  statuses=GROUP_STATUS[group];marks=','.join('?'*len(statuses))
  loc=clean(request.args.get('loc'));sn=clean(request.args.get('sn'))
  sort=request.args.get('sort') if request.args.get('sort') in ('location','serial','received') else 'location'
  locations=[r[0] for r in c.execute(f"SELECT DISTINCT COALESCE(physical_location,location_raw) l FROM units WHERE product_id=? AND status IN ({marks}) AND COALESCE(physical_location,location_raw,'')<>'' ORDER BY l",(pid,*statuses))]
  where,args=f'product_id=? AND status IN ({marks})',[pid,*statuses]
  if loc:where+=' AND COALESCE(physical_location,location_raw)=?';args.append(loc)
  if sn:where+=" AND REPLACE(UPPER(serial),' ','') LIKE ?";args.append('%'+sn.replace(' ','').upper()+'%')
  order={'location':"COALESCE(physical_location,location_raw) COLLATE NOCASE, serial",'serial':'serial COLLATE NOCASE','received':"COALESCE(received_date,'') DESC, id DESC"}[sort]
  units=c.execute(f'SELECT * FROM units WHERE {where} ORDER BY {order}',args).fetchall()
  product_requests=c.execute("SELECT COUNT(*) FROM stock_requests WHERE product_id=? AND status='Open'",(pid,)).fetchone()[0]
  c.close()
  return page(mode='stock',groups=groups,group=group,counts=counts,units=units,locations=locations,loc=loc,sn=sn,sort=sort,
              product_requests=product_requests,**ctx)
 @app.get('/products/<int:pid>/edit')
 def edit_page(pid):
  if not can('catalog_admin'):return 'Admin access required',403
  c=conn();setup(c);ctx=product_context(c,pid)
  if not ctx:c.close();return 'Product not found',404
  ctx['sysopts']=c.execute("SELECT id,display_name FROM products WHERE category='SYS' ORDER BY display_name").fetchall();c.close()
  return page(mode='edit',**ctx)
 # ---------- Stock requests: "Request this item" on a product, ticked off on the Product Database page ----------
 @app.post('/products/<int:pid>/request-item')
 def request_item(pid):
  if not can('stock_request'):return 'Not allowed',403
  f=request.form;c=conn();setup(c)
  if not c.execute('SELECT 1 FROM products WHERE id=?',(pid,)).fetchone():c.close();return 'Product not found',404
  try:qty=int(f.get('quantity') or 1)
  except ValueError:qty=0
  if not 1<=qty<=999:c.close();flash('Enter a quantity from 1 to 999.');return redirect(f'/products/{pid}#request')
  client=None
  if clean(f.get('customer')):
   client=resolve_client(c,f.get('customer'))
   if client is None:c.close();flash('Pick the customer from the list, or leave it blank.');return redirect(f'/products/{pid}#request')
  c.execute("INSERT INTO stock_requests(product_id,quantity,client_id,customer_name,note,requested_by,created_at,status) VALUES (?,?,?,?,?,?,?,'Open')",
            (pid,qty,client['id'] if client else None,client['name'] if client else None,clean(f.get('note'))[:500],g.user['username'],now_iso()))
  c.commit();c.close()
  flash('Request sent. It is listed on the Product Database page until it is ticked off.')
  return redirect(f'/products/{pid}')
 @app.post('/products/requests/<int:rid>/close')
 def request_close(rid):
  c=conn();setup(c)
  r=c.execute('SELECT sr.*,cl.account_owner FROM stock_requests sr LEFT JOIN clients cl ON cl.id=sr.client_id WHERE sr.id=?',(rid,)).fetchone()
  if not r:c.close();return 'Request not found',404
  if not can_close(r):c.close();return "Only an Admin, the requester or the customer's sales rep can close this request.",403
  if r['status']=='Open':
   c.execute("UPDATE stock_requests SET status='Closed',closed_by=?,closed_at=? WHERE id=?",(g.user['username'],now_iso(),rid));c.commit()
   flash('Request closed.')
  c.close();return redirect('/products#requests')
 @app.post('/products/<int:pid>/parts/add')
 def add_oem_part(pid):
  if not admin():return 'Admin access required',403
  number=key(request.form.get('part_number'));source=clean(request.form.get('source_url'));verification=clean(request.form.get('verification'))
  if not number or len(number)>120:return 'Valid OEM part number required',400
  if verification not in ('Pending Verification','Verified'):return 'Invalid verification status',400
  if source and not source.startswith(('https://','http://')):return 'Source must be an HTTP(S) URL',400
  if verification=='Verified' and not source:return 'A source URL is required to mark an OEM part number Verified',400
  c=conn();setup(c)
  if not c.execute('SELECT id FROM products WHERE id=?',(pid,)).fetchone():c.close();return 'Product not found',404
  try:
   c.execute('INSERT INTO product_part_numbers(product_id,part_number,source_url,verification,active) VALUES (?,?,?,?,1)',(pid,number,source,verification))
   audit(c,pid,'OEM part number added','',str({'part_number':number,'source_url':source,'verification':verification,'active':1}));c.commit()
  except sqlite3.IntegrityError:c.rollback();c.close();return 'This part number already exists on this product. Edit or reactivate it below.',400
  c.close();flash('OEM part number added');return redirect(f'/products/{pid}/edit')
 @app.post('/products/<int:pid>/parts/<int:part_id>/edit')
 def edit_oem_part(pid,part_id):
  if not admin():return 'Admin access required',403
  number=key(request.form.get('part_number'));source=clean(request.form.get('source_url'));verification=clean(request.form.get('verification'));active=clean(request.form.get('active'))
  if not number or len(number)>120:return 'Valid OEM part number required',400
  if verification not in ('Pending Verification','Verified','Admin Approved - source review required'):return 'Invalid verification status',400
  if active not in ('0','1'):return 'Invalid active status',400
  if source and not source.startswith(('https://','http://')):return 'Source must be an HTTP(S) URL',400
  if verification=='Verified' and not source:return 'A source URL is required to mark an OEM part number Verified',400
  c=conn();setup(c);old=c.execute('SELECT * FROM product_part_numbers WHERE id=? AND product_id=?',(part_id,pid)).fetchone()
  if not old:c.close();return 'Part number not found',404
  before={k:old[k] for k in ('part_number','source_url','verification','active')};after={'part_number':number,'source_url':source,'verification':verification,'active':int(active)}
  try:
   if before!=after:
    c.execute('UPDATE product_part_numbers SET part_number=?,source_url=?,verification=?,active=? WHERE id=? AND product_id=?',(number,source,verification,int(active),part_id,pid))
    audit(c,pid,'OEM part number edited',str(before),str(after));c.commit()
  except sqlite3.IntegrityError:c.rollback();c.close();return 'This part number already exists on this product',400
  c.close();flash('OEM part number saved');return redirect(f'/products/{pid}/edit')
 @app.post('/products/<int:pid>/compatibility')
 def compatibility(pid):
  if not can('catalog_admin'):return 'Admin access required',403
  c=conn();setup(c);probe=c.execute('SELECT category FROM products WHERE id=?',(pid,)).fetchone();sid=request.form.get('system_id',type=int);system=c.execute('SELECT category FROM products WHERE id=?',(sid,)).fetchone() if sid else None
  if not probe or probe['category']!='PRB' or not system or system['category']!='SYS':c.close();return 'Select a probe and system',400
  c.execute('INSERT OR IGNORE INTO product_compatibility(probe_id,system_id,source_url) VALUES (?,?,?)',(pid,sid,clean(request.form.get('source_url'))));audit(c,pid,'Compatibility added','',str(sid));c.commit();c.close();return redirect(f'/products/{pid}/edit')
 @app.route('/products/request',methods=['GET','POST'])
 def request_product():
  if request.method=='POST':
   c=conn();setup(c);f=request.form;typ=clean(f.get('request_type')) or 'New Product';model=clean(f.get('model'));part=clean(f.get('part_number'))
   if not model and not part:c.close();return 'Model or part number required',400
   mfg,problem=choices.check_manufacturer(f.get('manufacturer'))
   if problem:c.close();return problem,400
   c.execute('INSERT INTO product_requests(category,manufacturer,model,part_number,notes,request_type) VALUES (?,?,?,?,?,?)',(clean(f.get('category')),mfg,model,part,clean(f.get('notes')),typ));c.commit();c.close();flash('Request submitted for administrator review');return redirect('/products')
  return render('''<h1>Suggest a new product or OEM part number</h1><p class="muted">This is for adding to the catalog. To ask for a unit, open the product and use <strong>Request this item</strong>.</p><section><form method="post"><label>Request Type</label><select name="request_type"><option>New Product</option><option {% if request_type=='Part Number' %}selected{% endif %}>Part Number</option><option>Correction</option></select><label>Category</label><select name="category"><option>PRB</option><option>SYS</option><option>PTS</option></select><label>Manufacturer</label><input name="manufacturer" list="dl-manufacturers" data-strict autocomplete="off" placeholder="Pick from the list"><label>Model / Product Name</label><input name="model"><label>OEM Part Number (if applicable)</label><input name="part_number"><label>Notes / source URL / existing product ID</label><textarea name="notes" rows="3" style="width:100%" data-rich>{% if product_id %}Existing product ID: {{product_id}}{% endif %}</textarea><p><button>Submit Request</button></p></form></section>''',request_type=request.args.get('request_type'),product_id=request.args.get('product_id'))
 @app.route('/products/admin')
 def product_admin():
  if not admin():return render('''<h1>Administrator access required</h1><section><p>Product database administration is limited to admin accounts. Ask an administrator to give your account admin access on the Users page.</p></section>'''),403
  c=conn();setup(c);pending=c.execute("SELECT * FROM product_requests WHERE status='Pending' ORDER BY id DESC").fetchall();c.close()
  return render('''<h1>Product Database Administration</h1><p><a href="/users">Manage users</a> · <a href="/clients/salesforce">Link customers to Salesforce</a> · <a href="/admin/backup">Download database backup</a> · <a href="/admin/restore">Restore from a backup</a></p><section><h3>Pending Requests ({{pending|length}})</h3>{% for r in pending %}<form method="post" action="/products/admin/request/{{r.id}}" style="border-bottom:1px solid #ddd;padding:12px 0"><strong>#{{r.id}} {{r.request_type}}</strong> — {{r.manufacturer}} {{r.model}} {{r.part_number}}<div>{{r.notes|rich}}</div><input name="product_id" placeholder="Existing Product ID (for part number)" type="number"><input name="source_url" placeholder="OEM source URL"><select name="action"><option value="approve">Approve</option><option value="reject">Reject</option></select><button>Apply</button></form>{% else %}<p>No pending requests.</p>{% endfor %}</section><section><h3>Excel Product Import</h3><p>Import requires an administrator-confirmed preview. Upload only a trusted XLSX file.</p><form method="post" action="/products/admin/import/preview" enctype="multipart/form-data"><input type="file" name="file" accept=".xlsx" required><button>Preview Import</button></form></section>''',pending=pending)
 @app.post('/products/admin/request/<int:rid>')
 def approve_request(rid):
  if not admin():return 'Admin access required',403
  c=conn();setup(c);r=c.execute('SELECT * FROM product_requests WHERE id=? AND status="Pending"',(rid,)).fetchone()
  if not r:c.close();return 'Request not found',404
  action=request.form.get('action')
  if action=='approve':
   if r['request_type']=='Part Number':
    pid=request.form.get('product_id',type=int);p=c.execute('SELECT id FROM products WHERE id=?',(pid,)).fetchone() if pid else None
    if not p or not r['part_number']:c.close();return 'Select valid existing product ID and part number',400
    c.execute('INSERT OR IGNORE INTO product_part_numbers(product_id,part_number,source_url,verification) VALUES (?,?,?,?)',(pid,key(r['part_number']),clean(request.form.get('source_url')),'Admin Approved - source review required'));audit(c,pid,'Part number approved','',r['part_number'])
   else:
    cat=r['category'] if r['category'] in ('SYS','PRB','PTS') else 'PTS';mfg=clean(r['manufacturer']);model=clean(r['model'])
    if not model:c.close();return 'Model required',400
    existing=c.execute('SELECT id FROM products WHERE category=? AND COALESCE(manufacturer,"")=? AND model=?',(cat,mfg,model)).fetchone()
    if existing:pid=existing['id']
    else:pid=c.execute('INSERT INTO products(category,manufacturer,model,display_name,review_status) VALUES (?,?,?,?,?)',(cat,mfg,model,(mfg+' '+model).strip(),'Approved')).lastrowid
    audit(c,pid,'Request approved','',model)
  c.execute('UPDATE product_requests SET status=? WHERE id=?',('Approved' if action=='approve' else 'Rejected',rid));link_units(c);c.commit();c.close();flash('Request processed');return redirect('/products/admin')
 @app.post('/products/<int:pid>/edit')
 def edit(pid):
  if not admin():return 'Admin access required',403
  c=conn();setup(c);old=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
  if not old:c.close();return 'Not found',404
  f=request.form;cat=clean(f.get('category'))
  if cat not in ('SYS','PRB','PTS'):c.close();return 'Invalid category',400
  fields=['category','manufacturer','model','display_name','short_description','technical_specs','description_source','spec_source','review_status','active'];new=[clean(f.get(x)) for x in fields];new[-1]=1 if new[-1]=='1' else 0
  new[1],problem=choices.check_manufacturer(new[1],old['manufacturer'])  # unchanged old value kept; a new one must be on the list
  if problem:c.close();return problem,400
  try:c.execute('UPDATE products SET '+','.join(x+'=?' for x in fields)+' WHERE id=?',new+[pid]);audit(c,pid,'Product edited',str(dict(old)),str(dict(zip(fields,new))));link_units(c);c.commit()
  except sqlite3.IntegrityError:c.rollback();c.close();return 'Duplicate product exists',400
  c.close();flash('Product saved');return redirect(f'/products/{pid}/edit')
 @app.post('/products/admin/import/preview')
 def import_preview():
  if not admin():return 'Admin access required',403
  f=request.files.get('file')
  if not f or not f.filename.lower().endswith('.xlsx'):return 'XLSX required',400
  import io
  data=f.read(5_000_001)
  if len(data)>5_000_000:return 'File too large (5 MB max)',400
  try:w=load_workbook(io.BytesIO(data),read_only=True,data_only=True)
  except Exception:return 'Invalid XLSX',400
  s=w['Master Product Preview'] if 'Master Product Preview' in w.sheetnames else w.active;headers=[str(x or '').strip() for x in next(s.values)]
  required=['Category','Manufacturer','Model / Product Name','Display Name']
  if not all(x in headers for x in required):return 'Missing columns: '+', '.join(x for x in required if x not in headers),400
  idx={v:i for i,v in enumerate(headers)};rows=[]
  for vals in list(s.values)[1:501]:
   v=lambda k:clean(vals[idx[k]]) if k in idx and idx[k]<len(vals) else ''
   if not v('Model / Product Name'):continue
   if v('Category') not in ('SYS','PRB','PTS'):continue
   rows.append({'category':v('Category'),'manufacturer':v('Manufacturer'),'model':v('Model / Product Name'),'display_name':v('Display Name'),'short_description':v('Product Description (Web Research)'),'description_source':v('Description Source URL'),'technical_specs':v('Technical Specifications'),'spec_source':v('Specifications Source URL'),'review_notes':v('Review Notes')})
  token=secrets.token_urlsafe(16)
  c=conn();setup(c)
  c.execute("DELETE FROM product_import_staging WHERE created_at<datetime('now','-1 day')")
  c.execute('INSERT INTO product_import_staging(token,user_id,rows_json) VALUES (?,?,?)',(token,g.user['id'],json.dumps(rows)));c.commit()
  session['product_import_token']=token
  existing={(r['category'],key(r['manufacturer']),key(r['model'])) for r in c.execute('SELECT category,manufacturer,model FROM products')};c.close();new=sum((r['category'],key(r['manufacturer']),key(r['model'])) not in existing for r in rows)
  return render('''<h1>Import Preview</h1><section><p>{{rows|length}} valid rows; {{new}} possible new products; {{rows|length-new}} existing or duplicate rows. Only new products are inserted; existing product data is never overwritten.</p><p>OEM part numbers are not imported without separate verification.</p><form method="post" action="/products/admin/import/confirm"><button>Confirm Import</button></form><p><a href="/products/admin">Cancel</a></p></section><section><table><tr><th>Category</th><th>Manufacturer</th><th>Model</th></tr>{% for r in rows[:40] %}<tr><td>{{r.category}}</td><td>{{r.manufacturer}}</td><td>{{r.model}}</td></tr>{% endfor %}</table></section>''',rows=rows,new=new)
 @app.post('/products/admin/import/confirm')
 def import_confirm():
  if not admin():return 'Admin access required',403
  token=session.pop('product_import_token',None)
  c=conn();setup(c)
  staged=c.execute('SELECT rows_json FROM product_import_staging WHERE token=? AND user_id=?',(token,g.user['id'])).fetchone() if token else None
  if staged is None:c.close();return 'No import preview found. Upload the file again.',400
  rows=json.loads(staged['rows_json']);c.execute('DELETE FROM product_import_staging WHERE token=?',(token,));added=0
  for r in rows:
   existing=c.execute('SELECT id FROM products WHERE category=? AND COALESCE(manufacturer,"")=? AND UPPER(model)=?',(r['category'],r['manufacturer'],key(r['model']))).fetchone()
   if existing:continue
   try:
    pid=c.execute('INSERT INTO products(category,manufacturer,model,display_name,short_description,technical_specs,description_source,spec_source,review_notes) VALUES (?,?,?,?,?,?,?,?,?)',(r['category'],r['manufacturer'],r['model'],r['display_name'],r['short_description'],r['technical_specs'],r['description_source'],r['spec_source'],r['review_notes'])).lastrowid;audit(c,pid,'Excel import','',r['display_name']);added+=1
   except sqlite3.IntegrityError:pass
  c.execute('INSERT INTO product_imports(filename,added,skipped) VALUES (?,?,?)',('Admin uploaded XLSX',added,len(rows)-added));link_units(c);c.commit();c.close();flash(f'Imported {added} new products; skipped {len(rows)-added} existing/duplicate products');return redirect('/products')
