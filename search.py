"""Search everything: clients, repair tickets, stock and cores.

- The box in every sidebar (search_box.html) asks /api/search for suggestions as you type.
- Pressing Enter opens /search. If exactly one record matches (or one matches exactly), it opens
  that record straight away with a note saying so; otherwise it shows results grouped by type.
"""
from flask import flash, jsonify, redirect, render_template_string, request

from auth import can
import clients as CL

GROUPS = ['Clients', 'Repair tickets', 'Stock', 'Cores']
PER_GROUP = 50


def squash(text):
    """Upper-case and remove spaces, for comparing serials and IDs."""
    return ''.join(str(text or '').split()).upper()


def run_search(c, q, limit=PER_GROUP):
    """Return {group: [hit, ...]}. Each hit: kind, label, sub, url, exact (True if a code/serial/name equals q)."""
    q = q.strip()
    like = '%' + q + '%'
    key = squash(q)
    key_like = '%' + key + '%'
    nospace = "REPLACE(UPPER(COALESCE({0},'')),' ','')"
    out = {g: [] for g in GROUPS}

    if can('client_view'):
        rows = c.execute(f"""
            SELECT c.*, (SELECT COUNT(*) FROM customer_repairs r WHERE r.client_id=c.id AND r.stage<>'Closed') open_rmas
            FROM clients c
            WHERE c.name LIKE ? OR c.client_code LIKE ? OR {nospace.format('c.salesforce_id')} LIKE ?
               OR c.phone LIKE ? OR c.email LIKE ?
            ORDER BY c.name LIMIT ?""", (like, like, key_like, like, like, limit)).fetchall()
        for r in rows:
            sub = ' · '.join(x for x in [r['client_code'], r['status'], f"rep: {r['account_owner']}" if r['account_owner'] else '',
                                        f"{r['open_rmas']} open repairs" if r['open_rmas'] else ''] if x)
            exact = key in (squash(r['name']), squash(r['client_code']), squash(r['salesforce_id']))
            out['Clients'].append({'kind': 'Client', 'label': r['name'], 'sub': sub, 'url': f"/clients/{r['id']}", 'exact': exact})

    rows = c.execute(f"""
        SELECT * FROM customer_repairs
        WHERE inventory_code LIKE ? OR rma LIKE ? OR {nospace.format('serial')} LIKE ? OR customer LIKE ?
           OR model LIKE ? OR manufacturer LIKE ? OR part_number LIKE ?
        ORDER BY CASE WHEN stage='Closed' THEN 1 ELSE 0 END, id DESC LIMIT ?""",
                     (like, like, key_like, like, like, like, like, limit)).fetchall()
    for r in rows:
        label = f"{r['inventory_code'] or r['rma']} · {(r['manufacturer'] or '')} {(r['model'] or '')}".strip()
        sub = ' · '.join(x for x in [r['rma'], r['customer'], f"S/N {r['serial']}" if r['serial'] else '', r['stage']] if x)
        exact = key in (squash(r['inventory_code']), squash(r['rma']), squash(r['serial']))
        out['Repair tickets'].append({'kind': 'Repair ticket', 'label': label, 'sub': sub, 'url': f"/repairs/{r['id']}", 'exact': exact})

    rows = c.execute(f"""
        SELECT * FROM units
        WHERE inventory_code LIKE ? OR {nospace.format('serial')} LIKE ? OR product_raw LIKE ? OR physical_location LIKE ?
        ORDER BY CASE WHEN status='Active' THEN 0 ELSE 1 END, id DESC LIMIT ?""",
                     (like, key_like, like, like, limit)).fetchall()
    for r in rows:
        label = f"{r['inventory_code'] or 'Legacy #' + str(r['id'])} · {r['product_raw']}"
        sub = ' · '.join(x for x in [f"S/N {r['serial']}" if r['serial'] else '', r['status'], r['physical_location'] or ''] if x)
        exact = key in (squash(r['inventory_code']), squash(r['serial']))
        out['Stock'].append({'kind': 'Stock', 'label': label, 'sub': sub, 'url': f"/unit/{r['id']}", 'exact': exact})

    rows = c.execute(f"""
        SELECT * FROM core_items
        WHERE inventory_code LIKE ? OR {nospace.format('serial')} LIKE ? OR product_raw LIKE ?
        ORDER BY id DESC LIMIT ?""", (like, key_like, like, limit)).fetchall()
    for r in rows:
        label = f"{r['inventory_code']} · {r['product_raw']}"
        sub = ' · '.join(x for x in [f"S/N {r['serial']}" if r['serial'] else '', r['stage']] if x)
        exact = key in (squash(r['inventory_code']), squash(r['serial']))
        out['Cores'].append({'kind': 'Core', 'label': label, 'sub': sub, 'url': f"/cores/{r['id']}", 'exact': exact})
    return out


RESULTS_PAGE = '''
<div class="toolbar" style="justify-content:space-between">
  <div>
    <h1>Search</h1>
    <div class="muted">Clients, repair tickets, stock and cores. Serials match with or without spaces.</div>
  </div>
</div>
<section>
  <form class="toolbar" action="/search">
    <div style="flex:1;min-width:240px">
      <label>Search for</label>
      <input type="hidden" name="all" value="1">
      <input name="q" value="{{q}}" placeholder="Client name, CL- number, ticket, RMA, serial, model, Salesforce ID…" autofocus>
    </div>
    <div><button>Search</button></div>
  </form>
</section>
{% if q and total == 0 %}
  <section>
    <p>Nothing matches “{{q}}”. Try fewer letters, part of a serial, or the client's CL- number.</p>
  </section>
{% endif %}
{% for group in groups %}
  {% if results[group] %}
    <section>
      <h2>{{group}} <span class="muted">· {{results[group]|length}}{% if results[group]|length >= per_group %}+ (showing the first {{per_group}}){% endif %}</span></h2>
      <table>
        {% for h in results[group] %}
          <tr>
            <td><a href="{{h.url}}"><strong>{{h.label}}</strong></a></td>
            <td class="muted">{{h.sub}}</td>
          </tr>
        {% endfor %}
      </table>
    </section>
  {% endif %}
{% endfor %}
'''


def register_search_routes(app, conn):
    layout = CL.LAYOUT.replace('<a class="active" href="/clients">', '<a href="/clients">')

    @app.get('/api/search')
    def search_api():
        q = request.args.get('q', '').strip()
        if len(q) < 2:
            return jsonify([])
        c = conn()
        try:
            results = run_search(c, q, limit=4)
        finally:
            c.close()
        hits = [h for g in GROUPS for h in results[g]]
        hits.sort(key=lambda h: not h['exact'])      # exact matches first
        return jsonify([{k: h[k] for k in ('kind', 'label', 'sub', 'url')} for h in hits[:8]])

    @app.get('/search')
    def search_page():
        q = request.args.get('q', '').strip()
        show_all = request.args.get('all') == '1'     # "See all results": never jump to a single record
        results = {g: [] for g in GROUPS}
        if q:
            c = conn()
            try:
                results = run_search(c, q)
            finally:
                c.close()
            hits = [h for g in GROUPS for h in results[g]]
            exact = [h for h in hits if h['exact']]
            if len(exact) == 1 and not show_all:
                if len(hits) > 1:
                    flash(f'Search: opened the exact match for “{q}”. {len(hits) - 1} other records partly match; '
                          f'pick “See all results” under the search box to list them.')
                else:
                    flash(f'Search: opened the only match for “{q}”.')
                return redirect(exact[0]['url'])
            if len(hits) == 1 and not show_all:
                flash(f'Search: opened the only match for “{q}”.')
                return redirect(hits[0]['url'])
        total = sum(len(v) for v in results.values())
        body = render_template_string(RESULTS_PAGE, q=q, results=results, groups=GROUPS, total=total, per_group=PER_GROUP)
        return render_template_string(layout, body=body)
