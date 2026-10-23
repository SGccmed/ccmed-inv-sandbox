# CC Medical Inventory

Internal inventory system for CC Medical: stock (CC), cores (CR), customer repairs (RP), and the master product database. Built with Flask and SQLite.

Every page requires a staff login. Admins can add users, reset passwords, manage the product database, and download database backups.

## Run it on your own computer (Windows)

1. Install Python 3.10 or newer from python.org (tick "Add Python to PATH").
2. Open Command Prompt in this folder and install the requirements:
   ```
   pip install -r requirements.txt
   ```
3. Create your first admin account (it will ask for a password, at least 10 characters):
   ```
   python app.py create-admin yourname
   ```
4. Start the app and open http://127.0.0.1:5000 in your browser:
   ```
   python app.py
   ```

To import the existing inventory spreadsheet (sheet named "Location"):
```
python app.py import "C:\path\to\inventory.xlsx"
```

## Put it on the web with Render and Cloudflare

The app runs on Render. Its database lives on a Render **persistent disk**, because Render erases everything else on each restart or deploy. Persistent disks need a paid instance (the `starter` plan in `render.yaml`). On the free plan, your data would be lost.

### 1. Create the service on Render

1. In Render, click **New → Blueprint** and connect the `bvcarli2/ccmedical-inventory` repository. Render reads `render.yaml` and sets up the web service, the disk, and the settings.
2. Render asks for two values:
   - `CCM_INITIAL_ADMIN_USER`: your username, e.g. `bvcarli2`
   - `CCM_INITIAL_ADMIN_PASSWORD`: a strong password, at least 10 characters
3. Click **Apply**. When the deploy finishes, open the `https://ccmedical-inventory.onrender.com` address Render shows (yours may differ slightly) and sign in.
4. Change your password under **Change password** in the sidebar. You can then delete the two `CCM_INITIAL_ADMIN_...` values on the service's **Environment** page; they're only used while the site has no users.

### 2. Move your existing data over

1. Sign in as admin and go to **Product Database → Admin Mode → Restore from a backup**.
2. Upload the `inventory.db` file from the app folder on your computer.
3. The site downloads a safety copy of what it had before, then switches to your data. Your login is kept.

### 3. Use your own domain through Cloudflare

1. In Render, open the service, go to **Settings → Custom Domains**, and add the address you want, e.g. `inventory.yourcompany.com`.
2. In Cloudflare, open your domain's **DNS** page and add a **CNAME** record: name `inventory`, target the `...onrender.com` address from Render.
3. Set the record to **DNS only** (grey cloud) until Render shows the domain as verified and its certificate as issued.
4. After that you can switch it to **Proxied** (orange cloud) if you want Cloudflare's protection. If you do, set Cloudflare's **SSL/TLS** mode for the domain to **Full (strict)**.

### Updating the live site

Upload changed files to GitHub. Render redeploys automatically, and the data on the disk is untouched.

## Backups

Admins can download a full copy of the database from **Product Database → Admin Mode → Download database backup**. Do this regularly and keep copies somewhere safe; Render also takes daily snapshots of the disk. The live database is never stored on GitHub.

## Settings (optional environment variables)

| Variable | What it does |
|---|---|
| `CCM_DB_PATH` | Where the database file lives (default: `inventory.db` in this folder) |
| `CCM_SECRET` | Session signing key. If not set, a random one is created once and saved as `.secret_key` next to the database |
| `CCM_INITIAL_ADMIN_USER` / `CCM_INITIAL_ADMIN_PASSWORD` | Creates the first admin account when the site has no users yet |
| `CCM_SECURE_COOKIES` | `1` (default) sends login cookies only over HTTPS. `python app.py` turns this off automatically for local use |

## What stays off GitHub

`.gitignore` keeps the database, the secret key, and all spreadsheets (`.xlsx`, `.csv`) out of the repository, so company data isn't published even though the repo is public.
