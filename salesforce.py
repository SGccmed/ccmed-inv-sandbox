"""Salesforce account IDs on clients.

Salesforce stays the sales system (clients, contacts, tasks); this app keeps its own CL- numbers and stores
each client's Salesforce Account ID beside it, so the two can be linked.

Admins match IDs in bulk by uploading a Salesforce report exported as CSV (Account Name + Account ID).
The upload shows a preview first; nothing changes until "Link selected clients" is clicked.
"""
import csv
import io
import re

from flask import abort, flash, redirect, render_template_string, request

from auth import can
import clients as CL


SUFFIXES = r'\b(inc|llc|ltd|corp|corporation|co|company|pc|pllc|pa)\b'


def name_key(name):
    """Compare names loosely: lower case, no "Inc"/"LLC"-style endings, letters and digits only."""
    name = re.sub(SUFFIXES, ' ', str(name or '').lower().replace('&', ' and '))
    return re.sub(r'[^a-z0-9]', '', name)


def pick_columns(headers):
    """Find the name, Salesforce ID and (optional) CL- number columns in a Salesforce export."""
    low = {h: (h or '').strip().lower() for h in headers}
    name = next((h for h, l in low.items() if l in ('account name', 'name', 'client name', 'client')), None)
    name = name or next((h for h, l in low.items() if 'name' in l), None)
    sfid = next((h for h, l in low.items() if l in ('account id', 'salesforce id', 'salesforce account id', 'id', 'account id (18)',
                                                   'case safe id', '18 digit account id')), None)
    sfid = sfid or next((h for h, l in low.items() if 'id' in l.split() and 'client' not in l and 'parent' not in l), None)
    code = next((h for h, l in low.items() if l in ('cl number', 'client id', 'client code', 'cl', 'app client id')), None)
    return name, sfid, code


IMPORT_PAGE = '''
<p><a href="/clients">← Clients</a></p>
<h1>Link clients to Salesforce</h1>
<p class="muted">Salesforce stays the place for clients, contacts and tasks. Each client here keeps its CL- number and
  also stores its Salesforce Account ID, so the two systems can be linked.</p>
<section>
  <h2>1. Export from Salesforce</h2>
  <p>Ask the Salesforce admin for a report of Accounts with two columns, <strong>Account Name</strong> and
    <strong>Account ID</strong>, exported as CSV. A column with our CL- number is optional; when present it's used instead of the name.</p>
  <h2 style="margin-top:18px">2. Upload it</h2>
  <form method="post" enctype="multipart/form-data" class="toolbar">
    <div style="flex:1;min-width:240px"><label>CSV file</label><input type="file" name="file" accept=".csv,text/csv" required></div>
    <div><button>Preview matches</button></div>
  </form>
  <p class="muted">Nothing is saved at this step. Real client lists stay off the free sandbox; use the demo file for testing.</p>
</section>
{% if rows %}
<section>
  <h2>3. Check and link</h2>
  <div class="cards">
    <div class="card"><span>Ready to link</span><strong>{{counts.new}}</strong></div>
    <div class="card"><span>Already linked</span><strong>{{counts.same}}</strong></div>
    <div class="card"><span>Would change an ID</span><strong>{{counts.change}}</strong></div>
    <div class="card"><span>No match in the app</span><strong>{{counts.missing}}</strong></div>
    <div class="card"><span>Problems</span><strong>{{counts.bad}}</strong></div>
  </div>
  <form method="post" action="/clients/salesforce/apply">
    <div style="overflow:auto"><table>
      <tr><th></th><th>Salesforce name</th><th>Salesforce ID</th><th>App client</th><th>Result</th></tr>
      {% for r in rows %}
        <tr>
          <td>{% if r.status in ('new', 'change') %}<input type="checkbox" name="link" value="{{r.client_id}}|{{r.sfid}}" style="width:auto" {% if r.status=='new' %}checked{% endif %}>{% endif %}</td>
          <td>{{r.name}}</td>
          <td class="nw">{{r.sfid}}</td>
          <td>{% if r.client_id %}<a href="/clients/{{r.client_id}}">{{r.client_code}} · {{r.client_name}}</a>{% else %}—{% endif %}</td>
          <td>{{r.note}}</td>
        </tr>
      {% endfor %}
    </table></div>
    <p class="muted">Ticked rows are linked. Rows that would replace an existing ID are not ticked; tick them only if Salesforce is right.
      Unmatched names can be linked by hand on the client's Edit page.</p>
    <button>Link selected clients</button>
  </form>
</section>
{% endif %}
'''


def register_salesforce_routes(app, conn):
    layout = CL.LAYOUT

    def render(**ctx):
        return render_template_string(layout, body=render_template_string(IMPORT_PAGE, **ctx))

    @app.route('/clients/salesforce', methods=['GET', 'POST'])
    def salesforce_import():
        if not can('client_import'):
            abort(403)
        if request.method == 'GET':
            return render(rows=None)
        f = request.files.get('file')
        try:
            text = f.read().decode('utf-8-sig') if f else ''
        except UnicodeDecodeError:
            flash('That file is not a UTF-8 CSV. Export it from Salesforce as CSV and try again.')
            return redirect('/clients/salesforce')
        reader = csv.DictReader(io.StringIO(text))
        name_col, id_col, code_col = pick_columns(reader.fieldnames or [])
        if not id_col or not (name_col or code_col):
            flash('Could not find the Account Name and Account ID columns in that file.')
            return redirect('/clients/salesforce')
        c = conn()
        clients = c.execute('SELECT id, client_code, name, salesforce_id FROM clients').fetchall()
        by_name = {}
        for cl in clients:
            by_name.setdefault(name_key(cl['name']), []).append(cl)
        by_code = {cl['client_code'].upper(): cl for cl in clients if cl['client_code']}
        by_sfid = {cl['salesforce_id'].strip(): cl for cl in clients if (cl['salesforce_id'] or '').strip()}
        c.close()
        rows, seen = [], {}
        counts = {'new': 0, 'same': 0, 'change': 0, 'missing': 0, 'bad': 0}
        for rec in reader:
            name = (rec.get(name_col) or '').strip() if name_col else ''
            sfid = (rec.get(id_col) or '').strip()
            code = (rec.get(code_col) or '').strip().upper() if code_col else ''
            if not name and not sfid:
                continue
            row = {'name': name, 'sfid': sfid, 'client_id': None, 'client_code': '', 'client_name': ''}
            match = by_code.get(code) if code else None
            if match is None:
                found = by_name.get(name_key(name), [])
                match = found[0] if len(found) == 1 else None
                if len(found) > 1:
                    row.update(status='bad', note='Two app clients share this name; link it by hand')
            if match is not None:
                row.update(client_id=match['id'], client_code=match['client_code'], client_name=match['name'])
            if 'status' in row:
                pass
            elif CL.salesforce_id_problem(sfid):
                row.update(status='bad', note=CL.salesforce_id_problem(sfid))
            elif sfid in seen:
                row.update(status='bad', note=f'Same ID appears twice in the file (also on “{seen[sfid]}”)')
            elif match is None:
                row.update(status='missing', note='No client with this name in the app')
            elif sfid in by_sfid and by_sfid[sfid]['id'] != match['id']:
                other = by_sfid[sfid]
                row.update(status='bad', note=f"ID already used by {other['client_code']} · {other['name']}")
            elif (match['salesforce_id'] or '').strip() == sfid:
                row.update(status='same', note='Already linked')
            elif (match['salesforce_id'] or '').strip():
                row.update(status='change', note=f"Would replace {match['salesforce_id']}")
            else:
                row.update(status='new', note='Ready to link')
            seen.setdefault(sfid, name)
            counts[row['status']] += 1
            rows.append(row)
        order = {'new': 0, 'change': 1, 'bad': 2, 'missing': 3, 'same': 4}
        rows.sort(key=lambda r: (order[r['status']], r['name'].lower()))
        return render(rows=rows, counts=counts)

    @app.post('/clients/salesforce/apply')
    def salesforce_apply():
        if not can('client_import'):
            abort(403)
        c = conn()
        linked, skipped = 0, 0
        for value in request.form.getlist('link'):
            cid, _, sfid = value.partition('|')
            if not cid.isdigit() or CL.salesforce_id_problem(sfid):
                skipped += 1
                continue
            taken = c.execute('SELECT id FROM clients WHERE salesforce_id=? AND id<>?', (sfid, int(cid))).fetchone()
            old = c.execute('SELECT salesforce_id FROM clients WHERE id=?', (int(cid),)).fetchone()
            if taken or old is None:
                skipped += 1
                continue
            c.execute('UPDATE clients SET salesforce_id=?, updated_at=? WHERE id=?', (sfid, CL.now_iso(), int(cid)))
            CL.log(c, int(cid), 'System', 'Salesforce ID linked', f"salesforce id: '{old['salesforce_id'] or ''}' → '{sfid}' (CSV import)")
            linked += 1
        c.commit()
        c.close()
        flash(f'Linked {linked} clients to Salesforce.' + (f' Skipped {skipped} (ID invalid or already in use).' if skipped else ''))
        return redirect('/clients')
