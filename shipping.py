"""Shipping queue (v1.16 placeholder): /shipping, under the Shipping (truck) group in the sidebar.

Until the Shipping rework (see claude/HANDOFF.md, roadmap), this page gathers what Shipping can already act on:
    Outbound   customer repairs in Ready to Ship, and those shipped in the last 14 days with carrier and tracking
               (v1.17); stock orders come with the sales work
    Inbound    customer repairs Returning from Partner, with the partner's tracking (v1.20); expected stock arrivals
               and PO numbers are still a placeholder; receiving is + New Inventory
    Loaners    stock items On loan or in Rental (real data); due dates and returns come with the loaner work
Who sees it: permission shipping_view (SuperAdmin, Admin, Sales, Shipping).
"""
from flask import request

from auth import can, simple_page
from repairs import days_since

TABS = [('outbound', 'Outbound'), ('inbound', 'Inbound'), ('loaners', 'Loaners')]

BODY = '''
<h1 style="margin-bottom:4px">Shipping queue</h1>
<p class="muted" style="margin:0 0 14px">A first version of the Shipping queue. Outbound repairs and items out on loan are real;
the rest is a placeholder for the Shipping rework (request → pick by shelf → packing slip → prepped → shipped → team notified).</p>
<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:14px">
  {% for key, label in tabs %}
    <a href="/shipping?tab={{ key }}" style="padding:7px 13px;border-radius:8px;text-decoration:none;font-weight:600;
      {% if key == tab %}background:#245d86;color:#ffffff{% else %}background:#e7f0f6;color:#245d86{% endif %}">{{ label }}{% if key == 'outbound' %} ({{ outbound|length }}){% elif key == 'inbound' %} ({{ inbound|length }}){% elif key == 'loaners' %} ({{ loaners|length }}){% endif %}</a>
  {% endfor %}
</div>

{% if tab == 'outbound' %}
  <div class="panel">
    <h2 style="margin:0 0 6px;font-size:17px">Customer repairs ready to ship</h2>
    <p class="muted" style="margin:0 0 10px">Sales released these to Shipping. Open the ticket, post a ticket update with the carrier and
      tracking number, then move it to Shipped.</p>
    <table>
      <tr><th>Ticket</th><th>RMA</th><th>Customer</th><th>Equipment</th><th>Location</th><th>Ready since</th></tr>
      {% for r in outbound %}
        <tr><td><a href="/repairs/{{ r.id }}">{{ r.inventory_code or r.rma }}</a></td><td>{{ r.rma }}</td><td>{{ r.customer }}</td>
          <td>{{ r.manufacturer or '' }} {{ r.model or '' }}{% if r.serial %} · S/N {{ r.serial }}{% endif %}</td><td>{{ r.location or '—' }}</td>
          <td>{{ r.stage_started_at|day }}</td></tr>
      {% else %}
        <tr><td colspan="6" class="muted">Nothing is waiting to ship.</td></tr>
      {% endfor %}
    </table>
  </div>
  <div class="panel">
    <h2 style="margin:0 0 6px;font-size:17px">Recently shipped</h2>
    <p class="muted" style="margin:0 0 10px">Customer repairs shipped in the last 14 days, with their tracking. Open a ticket marked
      <b>Not closed</b> and post a ticket update with Status update → Closed once it's delivered (v1.19).</p>
    <table>
      <tr><th>Ticket</th><th>Customer</th><th>Equipment</th><th>Shipped</th><th>Carrier</th><th>Tracking</th><th>Ticket</th></tr>
      {% for r in shipped %}
        <tr><td><a href="/repairs/{{ r.id }}">{{ r.inventory_code or r.rma }}</a></td><td>{{ r.customer }}</td>
          <td>{{ r.manufacturer or '' }} {{ r.model or '' }}</td><td>{{ r.shipped_at|day }}</td><td>{{ r.carrier or '—' }}</td>
          <td>{% if tracking_url(r.carrier, r.tracking_number) %}<a href="{{ tracking_url(r.carrier, r.tracking_number) }}" target="_blank" rel="noopener">{{ r.tracking_number }}</a>{% else %}{{ r.tracking_number or '—' }}{% endif %}</td>
          <td>{% if r.stage == 'Closed' %}<span class="muted">Closed</span>{% else %}<b>Not closed</b>{% endif %}</td></tr>
      {% else %}
        <tr><td colspan="7" class="muted">Nothing shipped in the last 14 days.</td></tr>
      {% endfor %}
    </table>
  </div>
  <div class="panel">
    <h2 style="margin:0 0 6px;font-size:17px">Stock orders</h2>
    <p class="muted" style="margin:0">Coming with the sales and outbound work: stock items requested or sold, picked by shelf location,
      with a packing slip filled from the customer record (address, contact, PO, estimate), marked prepped, then shipped with tracking.</p>
  </div>
{% elif tab == 'inbound' %}
  <div class="panel">
    <h2 style="margin:0 0 6px;font-size:17px">Repairs returning from a partner</h2>
    <p class="muted" style="margin:0 0 10px">On their way back to the office (v1.20). When one arrives, open the ticket and move it to
      Ready to Ship, or to In Repair if a tech should inspect it first.</p>
    <table>
      <tr><th>Ticket</th><th>Customer</th><th>Equipment</th><th>Sent back</th><th>Carrier</th><th>Tracking</th><th>Days</th></tr>
      {% for r in inbound %}
        <tr><td><a href="/repairs/{{ r.id }}">{{ r.inventory_code or r.rma }}</a></td><td>{{ r.customer }}</td>
          <td>{{ r.manufacturer or '' }} {{ r.model or '' }}{% if r.serial %} · S/N {{ r.serial }}{% endif %}</td>
          <td>{{ r.inbound_shipped_at|day if r.inbound_shipped_at else r.stage_started_at|day }}</td><td>{{ r.inbound_carrier or '—' }}</td>
          <td>{% if tracking_url(r.inbound_carrier, r.inbound_tracking) %}<a href="{{ tracking_url(r.inbound_carrier, r.inbound_tracking) }}" target="_blank" rel="noopener">{{ r.inbound_tracking }}</a>{% else %}{{ r.inbound_tracking or '—' }}{% endif %}</td>
          <td>{{ r.days }}</td></tr>
      {% else %}
        <tr><td colspan="7" class="muted">No repairs are on their way back from a partner.</td></tr>
      {% endfor %}
    </table>
  </div>
  <div class="panel">
    <h2 style="margin:0 0 6px;font-size:17px">Expected arrivals</h2>
    <p class="muted" style="margin:0 0 10px">Coming later: a list of what's on its way (new stock, loaners coming back, cores, customer repairs),
      tied to a PO or estimate, so receiving is a quick confirmation.</p>
    {% if can('receive') %}<a href="/inventory/new">+ New Inventory</a> <span class="muted">· receive items now</span>{% endif %}
  </div>
{% else %}
  <div class="panel">
    <h2 style="margin:0 0 6px;font-size:17px">Out on loan or rental</h2>
    <p class="muted" style="margin:0 0 10px">Stock items at the Loan or Rental location. Who has them, due dates and returns come with the loaner work.</p>
    <table>
      <tr><th>Item</th><th>Product</th><th>Serial</th><th>Status</th><th>Moved there</th></tr>
      {% for u in loaners %}
        <tr><td><a href="/unit/{{ u.id }}">{{ u.inventory_code or ('#' ~ u.id) }}</a></td><td>{{ u.product_raw }}</td><td>{{ u.serial or '—' }}</td>
          <td>{{ u.status }}</td><td>{{ u.moved|day if u.moved else '—' }}</td></tr>
      {% else %}
        <tr><td colspan="5" class="muted">Nothing is out on loan or rental.</td></tr>
      {% endfor %}
    </table>
  </div>
{% endif %}
'''


def register_shipping_routes(app, conn):
    @app.get('/shipping')
    def shipping():
        tab = request.args.get('tab', 'outbound')
        if tab not in dict(TABS):
            tab = 'outbound'
        c = conn()
        try:
            outbound = c.execute("""SELECT * FROM customer_repairs WHERE stage='Ready to Ship'
                                    ORDER BY stage_started_at, id""").fetchall()
            # v1.20: repairs on their way back from a partner
            inbound = [dict(r, days=days_since(r['stage_started_at'])) for r in c.execute(
                """SELECT * FROM customer_repairs WHERE stage='Returning from Partner' ORDER BY stage_started_at, id""")]
            shipped = c.execute("""SELECT * FROM customer_repairs WHERE shipped_at IS NOT NULL
                                   AND shipped_at >= datetime('now', '-14 days') ORDER BY shipped_at DESC""").fetchall()
            loaners = c.execute("""SELECT u.*, (SELECT MAX(m.created_at) FROM movements m WHERE m.unit_id=u.id
                                    AND m.new_location IN ('Loan','Rental')) moved
                                   FROM units u WHERE u.status IN ('On loan','Rental') ORDER BY u.status, u.product_raw""").fetchall()
        finally:
            c.close()
        return simple_page('Shipping queue', BODY, width=1150, tabs=TABS, tab=tab, outbound=outbound, shipped=shipped, inbound=inbound, loaners=loaners, can=can)
