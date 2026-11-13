# CC Medical Inventory

Internal web app for tracking CC Medical's ultrasound equipment: stock, cores, customer repairs, customers and the product catalog. Built with Python (Flask) and one SQLite database file.

**Status: sandbox.** This repo (`SGccmed/ccmed-inv-sandbox`, forked from `bvcarli2/ccmedical-inventory`) runs on Render's free plan with fake demo data. Real data waits until we move to a paid plan (see the last section).

## What's in the app

The **home page** (click **CC Medical** at the top left of any page) has a big search bar, quick links, counts of what's waiting on you, your **Favorites** (☆ Favorite at the top right of any page) and the records you opened recently.

| Area | What it tracks | ID format |
|---|---|---|
| Stock (CC) | Units we own: shelf location, cost, status | `CC-MMDDYY-###` |
| Cores (CR) | Broken units we're fixing to sell, worked through ticket updates; converts to stock when ready | `CR-MMDDYY-###` |
| Customer repairs (RP) | Repair tickets for customer units, run like a helpdesk: 14 stages, team queues, work log | `RP-MMDDYY-###` + RMA # |
| Customers | Customer accounts, contacts, activity log, repair history | `CL-00001` |
| Product Database | Master list of systems, probes and parts | — |
| Users | Staff logins and roles | username |

Every page needs a staff login. Serial numbers are **not** unique keys: a matching serial anywhere shows an alert but never blocks saving, because different manufacturers can share serials.

## Files

| File | What it does |
|---|---|
| `app.py` | Main app: stock list (`/inventory`), receiving, backup/restore, spreadsheet import |
| `home.py`, `home.html` | Home page: search, work tiles, favorites and recent pages |
| `choices.py` | The pick lists: inventory locations and manufacturers (both locked). Edit the lists here to add or rename one |
| `costs.py` | Cost adjustments (Admins): corrects a cost or quoted price with a required reason, keeps the history |
| `cores.py` | Core repairs: ticket updates, statuses, costs, converting to stock |
| `formatting.py` | Pacific-time dates ("Oct 30, 2026"), safe display of rich text notes, work hours, sortable column headers |
| `page_tools.html` | Added to every page: rich text editor, red warning on negative costs, Start/End time totals, the ☆ Favorite button |
| `trix.js`, `trix.css` | Trix rich text editor (open source, MIT licence, by 37signals), served by the app itself |
| `repairs.py` | Customer repair tickets: the stages, who can move each one, queues, work log |
| `search.py` | Search across customers, tickets, stock and cores |
| `search_box.html` | The search box shown at the top of every sidebar |
| `salesforce.py` | Linking customers to their Salesforce Account IDs (CSV import) |
| `auth.py` | Logins, roles and permissions (`PERMISSIONS`), the Users page, loading staff accounts |
| `clients.py` | Customers pages (their HTML is inside this file) |
| `product_database.py` | Product Database pages (their HTML is inside this file) |
| `dashboard.html` | Stock list and single-item page |
| `inventory_entry.html` | + New Inventory receiving form |
| `repairs.html` | Repair queue, new ticket and ticket pages |
| `cores.html` | Core list and core page (ticket updates) |
| `seed_demo.py` | Builds `demo_inventory.db` with fake test data |
| `wsgi.py` | Starting point Render uses to launch the app |
| `requirements.txt` | Python packages: Flask, openpyxl, gunicorn, tzdata (time zones), nh3 (cleans rich text) |
| `render.yaml` | **Not used yet.** Settings for the future paid Render plan |
| `.gitignore` | Keeps the database, secrets and spreadsheets out of GitHub |

## Staff roles

Each account has one or more roles. The full rules are in `PERMISSIONS` in `auth.py` and are checked on the server for every action.

- **SuperAdmin**: everything, plus Admin accounts, database backup and restore.
- **Admin**: all inventory, customers, catalog and settings, linking customers to Salesforce. Manages Sales, Shipping and Technician accounts.
- **Sales**: customers, repair tickets, repair pricing and approvals, inventory lookups and purchase costs.
- **Shipping**: receiving, shelf moves, repair tickets, and shipping repairs back (Ready to Ship to Shipped).
- **Technician**: repair evaluation and repair work, ticket updates on repairs and cores (can enter parts costs but not see amounts), shelf moves, opening their own repair tickets (assigned to themselves). Can read the Product Database, Active and All lists, but has no Sold, Loaners or Rental lists; a single record still opens from search results or a serial alert. On a repair ticket a technician edits eval findings, location, assignment and updates; serial, model, part number, manufacturer, customer and RMA corrections are for Shipping, Sales or Admin. No costs or prices.

**View as (SuperAdmin):** the *View as…* menu at the bottom of the sidebar shows the app exactly as an Admin, Sales, Shipping or Technician account would see it. A yellow box shows while it's on; *Back to SuperAdmin* switches back.

There is always at least one active SuperAdmin, and nobody can change their own access.

## Repair tickets

Each customer repair is a ticket with one owner at each stage, like an IT helpdesk. The stage tells you whose turn it is, and each team has its own queue.

| Stage | Whose turn | Next |
|---|---|---|
| Received | Repair team | In Evaluation, or At Partner |
| In Evaluation | Repair team | Eval Complete, or Unrepairable |
| Eval Complete | Sales (adds pricing) | Awaiting Approval (warranty tickets go straight to Approved) |
| Awaiting Approval | Sales (on hold) | Approved, or Declined |
| Approved | Repair team | In Repair, or At Partner |
| In Repair | Repair team | Repair Complete, Waiting on Parts, or Unrepairable |
| Waiting on Parts | Repair team (on hold) | In Repair |
| Repair Complete | Sales | Ready to Ship |
| Ready to Ship | Shipping | Shipped |
| Shipped | Admin (invoice) | Closed |
| Closed | — | Admin can reopen |
| At Partner | Sales (on hold) | Eval Complete, Ready to Ship, or Shipped |
| Declined | Sales | Ready to Ship (returned free) |
| Unrepairable | Sales | Closed (exchanged), or Ready to Ship |

- **Moving a ticket:** only the team whose turn it is can move it on; Admins can move any ticket anywhere. Every move, update and edit goes in the ticket's work log with who and when.
- **Ticket updates:** day worked on, Start and End time (the Total in hours fills in by itself, e.g. 1.75 hrs), the update in rich text, parts cost and additional cost. All except the text are optional. The ticket shows the total time logged.
- **The queue** can be sorted by any column (click the header; click again to reverse). It opens longest-in-stage first and has a Received date column.
- **On hold** stages don't turn yellow or red.
- **RMA numbers** are created automatically (RMA-10001, ...) if left blank; the field can also hold a partner's RMA.
- **Closed tickets** record a resolution: Repaired, Warranty, Declined, Exchanged, or Returned unrepaired.
- **My tickets** shows repairs assigned to you plus repairs for customers you own as sales rep. The Sales rep and Assigned tech filters show anyone else's.
- Databases from before v1.05 are upgraded automatically: Open Repairs becomes Received, Evaled becomes Eval Complete, QC becomes In Repair, Completed becomes Closed.

## Core repairs

A core is worked like a ticket. Technicians post **ticket updates**: the day worked on (today by default), optional Start and End times (Total in hours is worked out for you), the update itself in a rich text box (bold, numbered and bulleted lists), parts cost, additional cost, and a status change. Updates are signed with the technician's name automatically; Admins can post on a technician's behalf.

| Status | Meaning |
|---|---|
| Needs Repair | Waiting for a technician |
| In Repair | Being worked on |
| Waiting on Parts | On hold until parts arrive (doesn't age) |
| On Hold | Paused for another reason, including cores we can't fix right now (doesn't age) |
| Ready for Stock | Fixed and tested; the update that sets it is the QC sign-off |
| Converted to Stock | An Admin converted it into a CC stock item carrying the full cost |

The core list can be sorted by any column and shows hours logged. Technicians can enter costs but never see amounts or totals; the cost summary is for Sales, Admin and SuperAdmin. Cores in the old QC status become In Repair automatically.

## Locations and manufacturers

Every location and manufacturer field (receiving, shelf transfers, repair tickets, product requests and edits) is a type-to-search drop-down. The lists live in `choices.py`.

- **Locations** must come from the list: A1–A4, B1–B4, C1–C4, T1, UG, 2D, Loan, Rental, Repair, ACER, LEN, INT, PBO. Anything else turns the field red and greys out the save button, and the server refuses it too. Typing in lower case is fine (`a1` is saved as `A1`).
- **Loan and Rental** also set a stock item's status (On loan / Rental); moving it to any other location makes it Active again. They're only offered for stock (CC) items, and technicians can't move items to or from them. Cores and repair tickets use the other locations.
- **Repair** is a single location for now; it can be split into racks later by adding them to the list.
- **Manufacturers** are a fixed list too (since v1.11): GE, Philips, Canon/Toshiba, SonoSite, BK, Siemens/Acuson, Samsung. Common variants ("Cannon", "Toshiba", "Acuson", "Fujifilm SonoSite") are saved with the list's spelling; any other make is refused, so a wrong manufacturer can't end up on a record. To receive a make that isn't listed, add it to `MANUFACTURERS` in `choices.py` first. The field can still be left blank.
- Records saved before v1.10/v1.11 keep their old location or manufacturer until someone changes that field.

## Notes and costs

- **Rich text:** every note and description box (ticket updates, repair intake notes, eval findings, customer notes and activity, product descriptions and requests) is a rich text editor. Ctrl+B / Ctrl+I work, and numbered or bulleted lists continue when you press Enter. Notes are stored as HTML and cleaned of anything unsafe every time they're shown. Older plain-text notes still display normally.
- **Adjusting a cost (Admin, SuperAdmin):** a stock item's cost, a core's purchase cost, the parts or additional cost on one core update, and a repair's quoted price can each be corrected on the record's page. A reason is required; the old and new amounts, who and when are listed under **Cost adjustments** (Sales and Admins see it; technicians don't). A converted core is adjusted through its stock item.
- **Sorting:** the stock list, repair queue and core list sort by clicking a column header.
- **Costs:** a negative or non-numeric cost turns the field red with a warning and greys out the form's submit button until it's fixed. The server also refuses negative costs.

## Dates

Every date is shown in Pacific time as **Oct 30, 2026**; work logs and updates add the time (**Oct 30, 2026 · 2:15 PM**). Dates are stored in UTC.

## Search

The box at the top of every sidebar searches customers (name, CL- number, Salesforce ID, phone, email), repair tickets (ticket, RMA, serial, customer, model), stock and cores. Serials match with or without spaces.

- **Suggestions** appear as you type; use the arrow keys and Enter, or click one.
- **Enter** with nothing highlighted runs the full search. If only one record matches, or one matches exactly (a full ticket number, serial or CL- number), it opens that record directly with a note saying so.
- **Several matches** (for example "San Diego") open a results page grouped into customers, repair tickets, stock and cores. "See all results" at the bottom of the suggestions always shows that page.
- Technicians don't see customers in search, matching their other permissions.

## Customers and Salesforce

Salesforce stays the system for customers, contacts and tasks; this app owns inventory and repairs. Each customer keeps its own CL- number and also stores its **Salesforce Account ID** (15 or 18 letters and digits, starting with 001). The ID must be unique per customer.

- **One at a time:** Customers → open a customer → Edit → *Salesforce account ID*.
- **In bulk (Admins):** Customers → **Link to Salesforce** (also in Product Database → Admin Mode). Upload a Salesforce report exported as CSV with **Account Name** and **Account ID** columns. The preview shows which customers will be linked, which are already linked, IDs that would change, names with no match, and bad rows. Nothing is saved until you click *Link selected customers*. Each link is recorded in the customer's history.
- **Open in Salesforce:** set `CCM_SALESFORCE_URL` (for example `https://yourcompany.lightning.force.com`) and each linked customer page gets an *Open in Salesforce* link.
- Keep real customer lists off the free sandbox. The demo data has fake IDs (`001DM…`), and `salesforce_demo_export.csv` is a fake export for testing the import.

## How the live site works (Render free plan)

GitHub is the master copy of the code. Render rebuilds the site automatically every time a change is committed to `main`.

### Updating the site

1. On GitHub, open the repo and choose **Add file → Upload files**.
2. Drag in the changed files (they replace the old ones) and commit to `main`.
3. In Render, watch the deploy log until it shows gunicorn "Listening" (a few minutes).

### Render settings

The service was created with **New → Web Service** (not Blueprint, because `render.yaml` asks for a paid plan).

| Setting | Value |
|---|---|
| Instance type | Free |
| Build command | `pip install -r requirements.txt` |
| Start command | `gunicorn wsgi:application --workers 1 --threads 4 --timeout 120` |
| Health check path | `/healthz` |
| Environment variables | `PYTHON_VERSION` = `3.12.7`, `CCM_SECRET` = a long random string |

Leave `CCM_DB_PATH` unset on the free plan.

### Free plan limits: data gets wiped

The free plan has no permanent disk. After about 15 idle minutes the site sleeps, and the database is erased on wake-up and on every deploy.

- **Demo data** must be re-uploaded: sign in as SuperAdmin, go to `/admin/restore` and upload `demo_inventory.db`.
- **Favorites, recent pages and cost adjustments** are in the database too, so they reset with it.

Never enter real customer or inventory data while on the free plan.

## Demo data

```
python seed_demo.py
```

This builds `demo_inventory.db`: 67 stock items, 8 cores, 21 repair tickets (every stage and path), 11 customers and 28 products, all fake. It has no user accounts, so restoring it keeps the current logins.

## Run it on your own computer (Windows)

1. Install Python 3.10 or newer from python.org (tick "Add Python to PATH").
2. Open Command Prompt in this folder and install the requirements:
   ```
   pip install -r requirements.txt
   ```
3. Create your first account (a SuperAdmin; it asks for a password of 10+ characters):
   ```
   python app.py create-admin yourname
   ```
4. Start the app, then open http://127.0.0.1:5000:
   ```
   python app.py
   ```

To import an existing inventory spreadsheet (sheet named "Location"):

```
python app.py import "C:\path\to\inventory.xlsx"
```

## Backups

A SuperAdmin can download the whole database from **Product Database → Admin Mode → Download database backup**, and replace it at `/admin/restore`. The restore page downloads a safety copy of the current data first.

## Optional settings (environment variables)

| Variable | What it does |
|---|---|
| `CCM_SECRET` | Key that signs login sessions. Set it on Render so logins survive restarts |
| `CCM_DB_PATH` | Where the database file lives (default: `inventory.db` next to `app.py`) |
| `CCM_INITIAL_ADMIN_USER` / `CCM_INITIAL_ADMIN_PASSWORD` | Creates a first SuperAdmin when the site has no users |
| `CCM_SALESFORCE_URL` | Your Salesforce address, e.g. `https://yourcompany.lightning.force.com`; adds *Open in Salesforce* links on customer pages |
| `CCM_SECURE_COOKIES` | `1` (default) sends login cookies only over HTTPS. `python app.py` turns this off for local use |

## What stays off GitHub

The repo is public, so `.gitignore` keeps out the database (`*.db`), the secret key, the staff accounts file, and all spreadsheets (`.xlsx`, `.xls`, `.csv`).

## Later: moving to a paid plan

Before real data goes in, the site moves to a paid Render plan with a permanent disk. `render.yaml` already describes that setup (Starter plan, 1 GB disk at `/var/data`, database at `/var/data/inventory.db`). At that point:

1. Create the service from **New → Blueprint** using `render.yaml`, or add a disk to the existing service and set `CCM_DB_PATH=/var/data/inventory.db`.
2. Restore the real data at `/admin/restore`.
3. Optionally add a custom domain through Cloudflare: a CNAME record pointing to the `onrender.com` address, set to DNS only until Render verifies it.
