# CC Medical Inventory

Internal web app for tracking CC Medical's ultrasound equipment: stock, cores, customer repairs, clients and the product catalog. Built with Python (Flask) and one SQLite database file.

**Status: sandbox.** This repo (`SGccmed/ccmed-inv-sandbox`, forked from `bvcarli2/ccmedical-inventory`) runs on Render's free plan with fake demo data. Real data waits until we move to a paid plan (see the last section).

## What's in the app

| Area | What it tracks | ID format |
|---|---|---|
| Stock (CC) | Units we own: shelf location, cost, status | `CC-MMDDYY-###` |
| Cores (CR) | Broken units we're fixing to sell; converts to stock after QC | `CR-MMDDYY-###` |
| Customer repairs (RP) | Repair tickets for customer units, run like a helpdesk: 14 stages, team queues, work log | `RP-MMDDYY-###` + RMA # |
| Clients | Customer accounts, contacts, activity log, repair history | `CL-00001` |
| Product Database | Master list of systems, probes and parts | — |
| Users | Staff logins and roles | username |

Every page needs a staff login. Serial numbers are **not** unique keys: a matching serial anywhere shows an alert but never blocks saving, because different manufacturers can share serials.

## Files

| File | What it does |
|---|---|
| `app.py` | Main app: stock, receiving, cores, backup/restore, spreadsheet import |
| `repairs.py` | Customer repair tickets: the stages, who can move each one, queues, work log |
| `auth.py` | Logins, roles and permissions (`PERMISSIONS`), the Users page, loading accounts from `users.env` |
| `clients.py` | Clients pages (their HTML is inside this file) |
| `product_database.py` | Product Database pages (their HTML is inside this file) |
| `dashboard.html` | Stock list and single-item page |
| `inventory_entry.html` | + New Inventory receiving form |
| `repairs.html` | Repair queue, new ticket and ticket pages |
| `cores.html` | Core repairs pages |
| `seed_demo.py` | Builds `demo_inventory.db` with fake test data |
| `wsgi.py` | Starting point Render uses to launch the app |
| `requirements.txt` | Python packages: Flask, openpyxl, gunicorn |
| `render.yaml` | **Not used yet.** Settings for the future paid Render plan |
| `.gitignore` | Keeps the database, secrets and spreadsheets out of GitHub |

## Staff roles

Each account has one or more roles. The full rules are in `PERMISSIONS` in `auth.py` and are checked on the server for every action.

- **SuperAdmin**: everything, plus Admin accounts, database backup and restore.
- **Admin**: all inventory, clients, catalog and settings. Manages Sales, Shipping and Technician accounts.
- **Sales**: clients, repair tickets, repair pricing and approvals, inventory lookups and purchase costs.
- **Shipping**: receiving, shelf moves, repair tickets, and shipping repairs back (Ready to Ship to Shipped).
- **Technician**: repair evaluation and repair work, core labor/parts/QC, shelf moves. No costs or prices.

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

- **Moving a ticket:** only the team whose turn it is can move it on; Admins can move any ticket anywhere. Every move, note and edit goes in the ticket's work log with who and when.
- **On hold** stages don't turn yellow or red.
- **RMA numbers** are created automatically (RMA-10001, ...) if left blank; the field can also hold a partner's RMA.
- **Closed tickets** record a resolution: Repaired, Warranty, Declined, Exchanged, or Returned unrepaired.
- **My tickets** shows repairs assigned to you plus repairs for clients you own as sales rep. The Sales rep and Assigned tech filters show anyone else's.
- Databases from before v1.05 are upgraded automatically: Open Repairs becomes Received, Evaled becomes Eval Complete, QC becomes In Repair, Completed becomes Closed.

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
| Secret File | `users.env` (staff accounts, see below) |

Leave `CCM_DB_PATH` unset on the free plan. Once `users.env` is in place, remove `CCM_INITIAL_ADMIN_USER` and `CCM_INITIAL_ADMIN_PASSWORD` if they're still set.

### Free plan limits: data gets wiped

The free plan has no permanent disk. After about 15 idle minutes the site sleeps, and the database is erased on wake-up and on every deploy.

- **Staff logins** come back automatically from `users.env`.
- **Demo data** must be re-uploaded: sign in as SuperAdmin, go to `/admin/restore` and upload `demo_inventory.db`.

Never enter real client or inventory data while on the free plan.

### Staff accounts file (`users.env`)

Add it in Render under **Environment → Secret Files**. The app reads `/etc/secrets/users.env` (or the path in `CCM_USERS_FILE`). One line per person:

```
CCM_USER_<LABEL>=username | Roles | password | Full name
```

Example: `CCM_USER_SHIP=shipping | Shipping | a-long-password | Pat Lee`. Several roles are separated with commas. Accounts are only created, never changed, and lines whose password still says `CHANGE_ME` are skipped. Passwords need at least 10 characters. Never commit this file to GitHub.

## Demo data

```
python seed_demo.py
```

This builds `demo_inventory.db`: 67 stock items, 8 cores, 21 repair tickets (every stage and path), 11 clients and 28 products, all fake. It has no user accounts, so restoring it keeps the current logins.

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
| `CCM_USERS_FILE` | Where to find `users.env` (default: `/etc/secrets/users.env`) |
| `CCM_INITIAL_ADMIN_USER` / `CCM_INITIAL_ADMIN_PASSWORD` | Creates a first SuperAdmin when the site has no users |
| `CCM_SECURE_COOKIES` | `1` (default) sends login cookies only over HTTPS. `python app.py` turns this off for local use |

## What stays off GitHub

The repo is public, so `.gitignore` keeps out the database (`*.db`), the secret key, `users.env`, and all spreadsheets (`.xlsx`, `.xls`, `.csv`).

## Later: moving to a paid plan

Before real data goes in, the site moves to a paid Render plan with a permanent disk. `render.yaml` already describes that setup (Starter plan, 1 GB disk at `/var/data`, database at `/var/data/inventory.db`). At that point:

1. Create the service from **New → Blueprint** using `render.yaml`, or add a disk to the existing service and set `CCM_DB_PATH=/var/data/inventory.db`.
2. Restore the real data at `/admin/restore`.
3. Optionally add a custom domain through Cloudflare: a CNAME record pointing to the `onrender.com` address, set to DNS only until Render verifies it.
