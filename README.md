# CC Medical Inventory

Internal web app for tracking CC Medical's ultrasound equipment: stock, cores, customer repairs, clients and the product catalog. Built with Python (Flask) and one SQLite database file.

**Status: sandbox.** This repo (`SGccmed/ccmed-inv-sandbox`, forked from `bvcarli2/ccmedical-inventory`) runs on Render's free plan with fake demo data. Real data waits until we move to a paid plan (see the last section).

## What's in the app

| Area | What it tracks | ID format |
|---|---|---|
| Stock (CC) | Units we own: shelf location, cost, status | `CC-MMDDYY-###` |
| Cores (CR) | Broken units we're fixing to sell; converts to stock after QC | `CR-MMDDYY-###` |
| Customer repairs (RP) | Customer units in for repair (RMAs), moving through 6 stages | `RP-MMDDYY-###` + RMA # |
| Clients | Customer accounts, contacts, activity log, repair history | `CL-00001` |
| Product Database | Master list of systems, probes and parts | — |
| Users | Staff logins and roles | username |

Every page needs a staff login. Serial numbers are **not** unique keys: a matching serial anywhere shows an alert but never blocks saving, because different manufacturers can share serials.

## Files

| File | What it does |
|---|---|
| `app.py` | Main app: stock, receiving, cores, customer repairs, backup/restore, spreadsheet import |
| `auth.py` | Logins, roles and permissions (`PERMISSIONS`), the Users page, loading accounts from `users.env` |
| `clients.py` | Clients pages (their HTML is inside this file) |
| `product_database.py` | Product Database pages (their HTML is inside this file) |
| `dashboard.html` | Stock list and single-item page |
| `inventory_entry.html` | + New Inventory receiving form |
| `repairs.html` | Customer repairs (RMA) pages |
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
- **Sales**: clients, RMAs, inventory lookups and purchase costs.
- **Shipping**: receiving, shelf moves, RMAs, and moving repairs from Ready to Ship to Completed.
- **Technician**: repair stages up to Ready to Ship, core labor/parts/QC, shelf moves. No costs or clients.

There is always at least one active SuperAdmin, and nobody can change their own access.

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

This builds `demo_inventory.db`: 67 stock items, 8 cores, 16 customer repairs, 11 clients and 28 products, all fake. It has no user accounts, so restoring it keeps the current logins.

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
