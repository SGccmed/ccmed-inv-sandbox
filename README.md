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

## Put it on the web with PythonAnywhere

PythonAnywhere runs Flask apps and keeps the SQLite database file safely between restarts. A paid plan is needed for a custom domain such as inventory.ccmedical.com; otherwise the address is `yourname.pythonanywhere.com`.

1. Create an account at pythonanywhere.com.
2. Open a **Bash console** (Consoles tab) and run:
   ```
   git clone https://github.com/bvcarli2/ccmedical-inventory.git
   cd ccmedical-inventory
   pip3.10 install --user -r requirements.txt
   python3.10 app.py create-admin yourname
   ```
3. Go to the **Web** tab, click **Add a new web app**, choose **Manual configuration**, then **Python 3.10**.
4. On the Web tab, set **Source code** and **Working directory** to `/home/YOURUSERNAME/ccmedical-inventory`.
5. Click the **WSGI configuration file** link, delete everything in it, and replace it with:
   ```python
   import sys
   sys.path.insert(0, '/home/YOURUSERNAME/ccmedical-inventory')
   from wsgi import application
   ```
   Save the file.
6. Back on the Web tab, turn on **Force HTTPS**, then click **Reload**.
7. Visit `https://YOURUSERNAME.pythonanywhere.com` and sign in.

### Moving your existing data to the server

If you've been using the app on your computer, your data is in `inventory.db` in this folder. Upload that file on PythonAnywhere's **Files** tab into `/home/YOURUSERNAME/ccmedical-inventory/`, replacing the one there. Then run the `create-admin` command from step 2 again (the uploaded database won't have any users yet) and click **Reload** on the Web tab.

### Updating the live site after changes

Upload the changed files to GitHub, then in a PythonAnywhere Bash console:
```
cd ccmedical-inventory
git pull
```
Then click **Reload** on the Web tab.

## Backups

Admins can download a full copy of the database from **Product Database → Admin Mode → Download database backup** (or `/admin/backup`). Do this regularly and keep copies somewhere safe. The live database is never stored on GitHub.

## Settings (optional environment variables)

| Variable | What it does |
|---|---|
| `CCM_DB_PATH` | Where the database file lives (default: `inventory.db` in this folder) |
| `CCM_SECRET` | Session signing key. If not set, a random one is created once and saved as `.secret_key` next to the database |
| `CCM_SECURE_COOKIES` | `1` (default) sends login cookies only over HTTPS. `python app.py` turns this off automatically for local use |

## What stays off GitHub

`.gitignore` keeps the database, the secret key, and all spreadsheets (`.xlsx`, `.csv`) out of the repository, so company data isn't published even though the repo is public.
