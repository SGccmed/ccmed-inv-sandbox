import json
import os
import re
import secrets
import sqlite3
from pathlib import Path
from flask import request,render_template_string,redirect,url_for,flash,session,g
from openpyxl import load_workbook
from auth import is_admin, actor_name, add_user_block
SCHEMA="""CREATE TABLE IF NOT EXISTS products(id INTEGER PRIMARY KEY, category TEXT NOT NULL, manufacturer TEXT,model TEXT NOT NULL,display_name TEXT NOT NULL,short_description TEXT,technical_specs TEXT,description_source TEXT,spec_source TEXT,review_notes TEXT,review_status TEXT NOT NULL DEFAULT 'Pending Review',active INTEGER NOT NULL DEFAULT 1,created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(category,manufacturer,model));
CREATE TABLE IF NOT EXISTS product_part_numbers(id INTEGER PRIMARY KEY,product_id INTEGER NOT NULL,part_number TEXT NOT NULL,source_url TEXT,verification TEXT NOT NULL DEFAULT 'Pending Verification',active INTEGER NOT NULL DEFAULT 1,UNIQUE(product_id,part_number));
CREATE TABLE IF NOT EXISTS product_import_staging(token TEXT PRIMARY KEY,user_id INTEGER,rows_json TEXT NOT NULL,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS product_requests(id INTEGER PRIMARY KEY,category TEXT,manufacturer TEXT,model TEXT,part_number TEXT,notes TEXT,request_type TEXT DEFAULT 'New Product',status TEXT DEFAULT 'Pending',created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS product_audit(id INTEGER PRIMARY KEY,product_id INTEGER,action TEXT,old_value TEXT,new_value TEXT,actor TEXT,created_at TEXT DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE IF NOT EXISTS product_compatibility(id INTEGER PRIMARY KEY,probe_id INTEGER NOT NULL,system_id INTEGER NOT NULL,source_url TEXT,UNIQUE(probe_id,system_id));
CREATE TABLE IF NOT EXISTS product_imports(id INTEGER PRIMARY KEY,filename TEXT,imported_at TEXT DEFAULT CURRENT_TIMESTAMP,added INTEGER,skipped INTEGER);
CREATE TABLE IF NOT EXISTS product_manufacturers(name TEXT PRIMARY KEY,active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS product_categories(code TEXT PRIMARY KEY,label TEXT NOT NULL,active INTEGER DEFAULT 1);"""
STYLE='''<style>body{margin:0;background:#f3f6fa;color:#1b3043;font:14px/1.5 Segoe UI,Arial,sans-serif}aside{width:220px;background:#112a42;min-height:100vh;padding:22px 15px;position:fixed;box-sizing:border-box}aside a{display:block;color:#dce9f4;padding:11px;text-decoration:none;border-radius:7px}aside a:hover{background:#2b5f88}aside h2{color:white}main{margin-left:220px;padding:30px;max-width:1500px}section{background:white;border:1px solid #dfe7ef;border-radius:12px;padding:20px;margin:15px 0}input,select,textarea{padding:9px;border:1px solid #cbd8e2;border-radius:6px;max-width:100%;box-sizing:border-box}button,.btn{background:#245d86;color:white;border:0;border-radius:7px;padding:10px 14px;cursor:pointer;text-decoration:none;display:inline-block}table{width:100%;border-collapse:collapse}td,th{padding:10px;border-bottom:1px solid #e5edf3;text-align:left;vertical-align:top}th{background:#f0f5f9}a{color:#245d86}label{display:block;margin:9px 0 4px;font-weight:bold}.muted{color:#667c8d}.row{display:flex;gap:12px;flex-wrap:wrap;align-items:end}.row>*{max-width:100%}.msg{padding:10px;background:#e8f6eb;border-radius:8px}@media(max-width:700px){aside{position:static;width:auto;min-height:0}main{margin:0;padding:15px}}</style>'''
LAY='''<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>CC Medical | Product Database</title>'''+STYLE+'''</head><body><aside><h2>CC Medical</h2><a href="/">Inventory Dashboard</a><a href="/inventory/new">New Inventory</a><a href="/products">Product Database</a><a href="/cores">Core Repairs</a><a href="/repairs">Customer Repairs</a><a href="/products/admin">Admin Mode</a></aside><main>{% with msgs=get_flashed_messages() %}{% for m in msgs %}<p class="msg">{{m}}</p>{% endfor %}{% endwith %}{{body|safe}}</main></body></html>'''
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
  for name in ['GE','Philips','Siemens','Canon/Toshiba','Samsung','Mindray','Fujifilm/SonoSite']:c.execute('INSERT OR IGNORE INTO product_manufacturers(name) VALUES (?)',(name,))
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
 @app.route('/products')
 def products():
  c=conn();setup(c);seed(c);q=clean(request.args.get('q'));category=clean(request.args.get('category'));where=['1=1'];args=[]
  if q:where.append('''(p.display_name LIKE ? OR p.model LIKE ? OR p.manufacturer LIKE ? OR EXISTS(SELECT 1 FROM product_part_numbers pn WHERE pn.product_id=p.id AND pn.part_number LIKE ?))''');args+=['%'+q+'%']*4
  if category:where.append('p.category=?');args.append(category)
  rows=c.execute('SELECT p.*,(SELECT GROUP_CONCAT(part_number,", ") FROM product_part_numbers pn2 WHERE pn2.product_id=p.id AND pn2.active=1) parts FROM products p WHERE '+' AND '.join(where)+' ORDER BY p.display_name LIMIT 1000',args).fetchall();c.close()
  return render('''<h1>Product Database</h1><p class="muted">Master products only · No serial numbers, inventory costs or locations. Research is verified per source; unverified OEM part numbers remain blank.</p><section><form class="row"><input name="q" value="{{q}}" placeholder="Search manufacturer, model or OEM part number" style="min-width:320px"><select name="category"><option value="">All categories</option>{% for v in ['SYS','PRB','PTS'] %}<option value="{{v}}" {% if v==category %}selected{% endif %}>{{v}}</option>{% endfor %}</select><button>Search</button><a class="btn" href="/products/request">Request Product</a></form></section><section><h3>{{rows|length}} products shown</h3><div style="overflow:auto"><table><tr><th>Category</th><th>Manufacturer</th><th>Product</th><th>OEM Part Numbers</th><th>Review</th></tr>{% for p in rows %}<tr><td>{{p.category}}</td><td>{{p.manufacturer or 'Needs review'}}</td><td><a href="/products/{{p.id}}">{{p.display_name}}</a></td><td>{{p.parts or 'Not verified'}}</td><td>{{p.review_status}}</td></tr>{% endfor %}</table></div></section>''',rows=rows,q=q,category=category)
 @app.route('/products/<int:pid>')
 def detail(pid):
  c=conn();setup(c);p=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
  if not p:c.close();return 'Product not found',404
  parts=c.execute('SELECT * FROM product_part_numbers WHERE product_id=? ORDER BY active DESC,part_number',(pid,)).fetchall();history=c.execute('SELECT * FROM product_audit WHERE product_id=? ORDER BY id DESC',(pid,)).fetchall();systems=c.execute('SELECT s.* FROM products s JOIN product_compatibility pc ON s.id=pc.system_id WHERE pc.probe_id=?',(pid,)).fetchall();probes=c.execute('SELECT s.* FROM products s JOIN product_compatibility pc ON s.id=pc.probe_id WHERE pc.system_id=?',(pid,)).fetchall();sysopts=c.execute("SELECT id,display_name FROM products WHERE category='SYS' ORDER BY display_name").fetchall();c.close()
  return render('''<p><a href="/products">← Products</a></p><h1>{{p.display_name}}</h1><p>{{p.category}} · {{p.review_status}} · {{'Active' if p.active else 'Inactive'}}</p><section><h3>Manufacturer Description</h3><p>{{p.short_description or 'Not yet researched'}}</p>{% if p.description_source %}<a href="{{p.description_source}}" target="_blank" rel="noopener">Description source</a>{% endif %}<h3>Technical Specifications</h3><p>{{p.technical_specs or 'Not yet researched'}}</p>{% if p.spec_source %}<a href="{{p.spec_source}}" target="_blank" rel="noopener">Specifications source</a>{% endif %}</section><section><h3>OEM Part Numbers</h3>{% for n in parts %}<p>{{n.part_number}} — {{n.verification}}{% if not n.active %} — Inactive{% endif %} {% if n.source_url %}<a href="{{n.source_url}}">Source</a>{% endif %}</p>{% else %}<p>No verified OEM replacement part numbers. Product-page identifiers are not automatically OEM part numbers.</p>{% endfor %}<a href="/products/request?product_id={{p.id}}&request_type=Part Number">Request new OEM part number</a></section><section><h3>Compatibility</h3>{% for s in systems %}<p>System: <a href="/products/{{s.id}}">{{s.display_name}}</a></p>{% endfor %}{% for s in probes %}<p>Probe: <a href="/products/{{s.id}}">{{s.display_name}}</a></p>{% endfor %}{% if p.category=='PRB' %}<form method="post" action="/products/{{p.id}}/compatibility"><select name="system_id">{% for s in sysopts %}<option value="{{s.id}}">{{s.display_name}}</option>{% endfor %}</select><input name="source_url" placeholder="Compatibility source URL"><button>Add compatible system</button></form>{% endif %}</section><section><h3>Change History</h3>{% for h in history %}<p>{{h.created_at}} — {{h.action}} ({{h.actor}})</p>{% else %}<p>No edits recorded yet.</p>{% endfor %}</section>{% if is_admin %}<section><h3>Admin: OEM Part Numbers</h3><p class="muted">Add and maintain multiple OEM numbers for this master product. Only mark a number Verified after checking a reliable source. Deactivation preserves its audit history.</p><h4>Add OEM Part Number</h4><form method="post" action="/products/{{p.id}}/parts/add"><div class="row"><div><label>OEM Part Number</label><input name="part_number" required maxlength="120" placeholder="Manufacturer part number"></div><div><label>Verification</label><select name="verification"><option>Pending Verification</option><option>Verified</option></select></div><div style="flex:1;min-width:220px"><label>Source URL</label><input name="source_url" type="url" placeholder="https://manufacturer.example/..." style="width:100%"></div><button>Add Part Number</button></div></form><h4>Existing Part Numbers</h4>{% for n in parts %}<form method="post" action="/products/{{p.id}}/parts/{{n.id}}/edit" style="border-top:1px solid #e5edf3;padding:12px 0"><div class="row"><div><label>Part Number</label><input name="part_number" required maxlength="120" value="{{n.part_number}}"></div><div><label>Verification</label><select name="verification"><option {% if n.verification=='Pending Verification' %}selected{% endif %}>Pending Verification</option><option {% if n.verification=='Verified' %}selected{% endif %}>Verified</option><option {% if n.verification=='Admin Approved - source review required' %}selected{% endif %}>Admin Approved - source review required</option></select></div><div><label>Status</label><select name="active"><option value="1" {% if n.active %}selected{% endif %}>Active</option><option value="0" {% if not n.active %}selected{% endif %}>Inactive</option></select></div><div style="flex:1;min-width:220px"><label>Source URL</label><input name="source_url" type="url" value="{{n.source_url or ''}}" style="width:100%"></div><button>Save Part Number</button></div></form>{% else %}<p>No OEM part numbers recorded yet.</p>{% endfor %}</section><section><h3>Admin: Edit Product</h3><form method="post" action="/products/{{p.id}}/edit"><label>Category</label><select name="category">{% for x in ['SYS','PRB','PTS'] %}<option {% if x==p.category %}selected{% endif %}>{{x}}</option>{% endfor %}</select><label>Manufacturer</label><input name="manufacturer" value="{{p.manufacturer or ''}}"><label>Model</label><input name="model" value="{{p.model}}"><label>Display Name</label><input name="display_name" value="{{p.display_name}}"><label>Short Description</label><textarea name="short_description" rows="3" style="width:100%">{{p.short_description or ''}}</textarea><label>Technical Specifications</label><textarea name="technical_specs" rows="4" style="width:100%">{{p.technical_specs or ''}}</textarea><label>Description Source URL</label><input name="description_source" value="{{p.description_source or ''}}" style="width:100%"><label>Specifications Source URL</label><input name="spec_source" value="{{p.spec_source or ''}}" style="width:100%"><label>Review Status</label><select name="review_status"><option {% if p.review_status=='Pending Review' %}selected{% endif %}>Pending Review</option><option {% if p.review_status=='Approved' %}selected{% endif %}>Approved</option></select><label>Active</label><select name="active"><option value="1">Active</option><option value="0" {% if not p.active %}selected{% endif %}>Inactive</option></select><p><button>Save Changes</button></p></form></section>{% endif %}''',p=p,parts=parts,history=history,systems=systems,probes=probes,sysopts=sysopts,is_admin=admin())
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
  c.close();flash('OEM part number added');return redirect('/products/'+str(pid))
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
  c.close();flash('OEM part number saved');return redirect('/products/'+str(pid))
 @app.post('/products/<int:pid>/compatibility')
 def compatibility(pid):
  c=conn();setup(c);probe=c.execute('SELECT category FROM products WHERE id=?',(pid,)).fetchone();sid=request.form.get('system_id',type=int);system=c.execute('SELECT category FROM products WHERE id=?',(sid,)).fetchone() if sid else None
  if not probe or probe['category']!='PRB' or not system or system['category']!='SYS':c.close();return 'Select a probe and system',400
  c.execute('INSERT OR IGNORE INTO product_compatibility(probe_id,system_id,source_url) VALUES (?,?,?)',(pid,sid,clean(request.form.get('source_url'))));audit(c,pid,'Compatibility added','',str(sid));c.commit();c.close();return redirect('/products/'+str(pid))
 @app.route('/products/request',methods=['GET','POST'])
 def request_product():
  if request.method=='POST':
   c=conn();setup(c);f=request.form;typ=clean(f.get('request_type')) or 'New Product';model=clean(f.get('model'));part=clean(f.get('part_number'))
   if not model and not part:c.close();return 'Model or part number required',400
   c.execute('INSERT INTO product_requests(category,manufacturer,model,part_number,notes,request_type) VALUES (?,?,?,?,?,?)',(clean(f.get('category')),clean(f.get('manufacturer')),model,part,clean(f.get('notes')),typ));c.commit();c.close();flash('Request submitted for administrator review');return redirect('/products')
  return render('''<h1>Request Product / OEM Part Number</h1><section><form method="post"><label>Request Type</label><select name="request_type"><option>New Product</option><option {% if request_type=='Part Number' %}selected{% endif %}>Part Number</option><option>Correction</option></select><label>Category</label><select name="category"><option>PRB</option><option>SYS</option><option>PTS</option></select><label>Manufacturer</label><input name="manufacturer"><label>Model / Product Name</label><input name="model"><label>OEM Part Number (if applicable)</label><input name="part_number"><label>Notes / source URL / existing product ID</label><textarea name="notes" rows="3" style="width:100%">{% if product_id %}Existing product ID: {{product_id}}{% endif %}</textarea><p><button>Submit Request</button></p></form></section>''',request_type=request.args.get('request_type'),product_id=request.args.get('product_id'))
 @app.route('/products/admin')
 def product_admin():
  if not admin():return render('''<h1>Administrator access required</h1><section><p>Product database administration is limited to admin accounts. Ask an administrator to give your account admin access on the Users page.</p></section>'''),403
  c=conn();setup(c);pending=c.execute("SELECT * FROM product_requests WHERE status='Pending' ORDER BY id DESC").fetchall();c.close()
  return render('''<h1>Product Database Administration</h1><p><a href="/users">Manage users</a> · <a href="/admin/backup">Download database backup</a> · <a href="/admin/restore">Restore from a backup</a></p><section><h3>Pending Requests ({{pending|length}})</h3>{% for r in pending %}<form method="post" action="/products/admin/request/{{r.id}}" style="border-bottom:1px solid #ddd;padding:12px 0"><strong>#{{r.id}} {{r.request_type}}</strong> — {{r.manufacturer}} {{r.model}} {{r.part_number}}<p>{{r.notes}}</p><input name="product_id" placeholder="Existing Product ID (for part number)" type="number"><input name="source_url" placeholder="OEM source URL"><select name="action"><option value="approve">Approve</option><option value="reject">Reject</option></select><button>Apply</button></form>{% else %}<p>No pending requests.</p>{% endfor %}</section><section><h3>Excel Product Import</h3><p>Import requires an administrator-confirmed preview. Upload only a trusted XLSX file.</p><form method="post" action="/products/admin/import/preview" enctype="multipart/form-data"><input type="file" name="file" accept=".xlsx" required><button>Preview Import</button></form></section>''',pending=pending)
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
  c.execute('UPDATE product_requests SET status=? WHERE id=?',('Approved' if action=='approve' else 'Rejected',rid));c.commit();c.close();flash('Request processed');return redirect('/products/admin')
 @app.post('/products/<int:pid>/edit')
 def edit(pid):
  if not admin():return 'Admin access required',403
  c=conn();setup(c);old=c.execute('SELECT * FROM products WHERE id=?',(pid,)).fetchone()
  if not old:c.close();return 'Not found',404
  f=request.form;cat=clean(f.get('category'))
  if cat not in ('SYS','PRB','PTS'):c.close();return 'Invalid category',400
  fields=['category','manufacturer','model','display_name','short_description','technical_specs','description_source','spec_source','review_status','active'];new=[clean(f.get(x)) for x in fields];new[-1]=1 if new[-1]=='1' else 0
  try:c.execute('UPDATE products SET '+','.join(x+'=?' for x in fields)+' WHERE id=?',new+[pid]);audit(c,pid,'Product edited',str(dict(old)),str(dict(zip(fields,new))));c.commit()
  except sqlite3.IntegrityError:c.rollback();c.close();return 'Duplicate product exists',400
  c.close();return redirect('/products/'+str(pid))
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
  c.execute('INSERT INTO product_imports(filename,added,skipped) VALUES (?,?,?)',('Admin uploaded XLSX',added,len(rows)-added));c.commit();c.close();flash(f'Imported {added} new products; skipped {len(rows)-added} existing/duplicate products');return redirect('/products')
