"""Staff accounts and login for CC Medical Inventory.

Every page requires a signed-in user. Each user has one or more roles
(SuperAdmin, Admin, Sales, Shipping, Technician); PERMISSIONS below decides what
each role may do, and every protected route is checked on the server. Passwords are stored as salted hashes, never
in plain text.
"""
import os
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

from flask import (abort, flash, g, redirect, render_template_string, request,
                   session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

USER_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT,
    password_hash TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY,
    username TEXT NOT NULL COLLATE NOCASE,
    ip TEXT,
    attempted_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_attempts_user ON login_attempts(username, attempted_at);
"""

MIN_PASSWORD_LENGTH = 10
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
PUBLIC_ENDPOINTS = {"login", "healthz", "static"}


def _now():
    return datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat(timespec="seconds")


# --- Roles and permissions -------------------------------------------------

ROLES = ["SuperAdmin", "Admin", "Sales", "Shipping", "Technician"]
ADMIN_ROLES = {"SuperAdmin", "Admin"}
ROLE_HELP = {
    "SuperAdmin": "Everything, including admin accounts, database restore and backups",
    "Admin": "All inventory, clients, catalog and settings; manages Sales/Shipping/Technician staff",
    "Sales": "Clients, repair tickets, pricing and approvals; sees purchase costs",
    "Shipping": "Receiving, shelf moves, repair tickets, and shipping repairs back",
    "Technician": "Repair evaluation and repair stages, core labor/parts/QC, shelf moves",
}
_SA, _A, _S, _SH, _T = ROLES
PERMISSIONS = {
    "view_cost":      {_SA, _A, _S},          # purchase / repair costs and totals
    "receive":        {_SA, _A, _SH},         # New Inventory
    "move":           {_SA, _A, _SH, _T},     # shelf transfers
    "unit_repair":    {_SA, _A, _T},          # internal repair record on a stock item
    "client_view":    {_SA, _A, _S, _SH},
    "client_edit":    {_SA, _A, _S},
    "client_import":  {_SA, _A},              # link clients to Salesforce IDs from a CSV
    "rma_create":     {_SA, _A, _S, _SH},
    "rma_edit":       {_SA, _A, _S, _SH, _T}, # edit ticket details and add work-log notes
    # Repair tickets (repairs.py). Who may MOVE a ticket depends on whose turn it is: see STAGES in repairs.py.
    "repair_assign":  {_SA, _A, _T},          # assign a tech to a ticket
    "repair_eval":    {_SA, _A, _T},          # write eval findings
    "repair_price":   {_SA, _A, _S},          # quoted price and QuickBooks estimate number
    "repair_invoice": {_SA, _A},              # invoice number, resolution
    "core_work":      {_SA, _A, _T},          # labor/parts, stage, QC
    "core_convert":   {_SA, _A},
    "catalog_admin":  {_SA, _A},
    "users_manage":   {_SA, _A},              # Admins manage non-admin staff only
    "admins_manage":  {_SA},
    "backup":         {_SA},
    "restore":        {_SA},
}


def roles_of(u):
    if not u:
        return set()
    raw = u["roles"] if "roles" in u.keys() else None
    return {r for r in (raw or "").split(",") if r in ROLES}


def can(perm, u=None):
    u = u if u is not None else current_user()
    return bool(roles_of(u) & PERMISSIONS.get(perm, set()))


def clean_roles(values):
    """Accept 'Sales, Shipping' or a list; return canonical comma string in ROLES order."""
    if isinstance(values, str):
        values = values.split(",")
    wanted = {v.strip().lower() for v in values if v and v.strip()}
    unknown = wanted - {r.lower() for r in ROLES}
    if unknown:
        raise ValueError("Unknown role(s): " + ", ".join(sorted(unknown)) + ". Use: " + ", ".join(ROLES))
    return ",".join(r for r in ROLES if r.lower() in wanted)


def ensure_user_schema(c):
    c.executescript(USER_SCHEMA)
    if "roles" not in {r[1] for r in c.execute("PRAGMA table_info(users)")}:
        c.execute("ALTER TABLE users ADD COLUMN roles TEXT")
    # Accounts from before roles existed: old admins become SuperAdmin; other staff stay view-only until given a role.
    c.execute("UPDATE users SET roles='SuperAdmin' WHERE roles IS NULL AND is_admin=1")
    c.execute("UPDATE users SET roles='' WHERE roles IS NULL")
    sync_is_admin(c)


def sync_is_admin(c):
    # is_admin is kept for older code paths (product catalog admin): true for SuperAdmin or Admin.
    c.execute("UPDATE users SET is_admin=CASE WHEN (','||COALESCE(roles,'')||',') LIKE '%,SuperAdmin,%' OR (','||COALESCE(roles,'')||',') LIKE '%,Admin,%' THEN 1 ELSE 0 END")


def seed_users_from_env(c):
    """Create accounts listed as CCM_USER_<anything>=username | Roles | password | Full name.

    Read from real environment variables and from a Render Secret File
    (CCM_USERS_FILE, default /etc/secrets/users.env). Existing accounts are never
    changed, so passwords people set themselves are not overwritten.
    """
    entries = {k: v for k, v in os.environ.items() if k.startswith("CCM_USER_")}
    path = os.environ.get("CCM_USERS_FILE") or "/etc/secrets/users.env"
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith("CCM_USER_") and "=" in line:
                    k, v = line.split("=", 1)
                    entries.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    except OSError:
        pass
    for key, value in sorted(entries.items()):
        parts = [p.strip() for p in value.split("|")]
        if len(parts) < 3:
            print(f"[users] {key}: expected 'username | Roles | password | Full name'; skipped")
            continue
        username, roles, password = parts[0], parts[1], parts[2]
        full_name = parts[3] if len(parts) > 3 else ""
        if "CHANGE_ME" in password.upper():
            print(f"[users] {key}: password still CHANGE_ME; skipped")
            continue
        if c.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
            continue
        try:
            create_user(c, username, password, full_name, roles=roles)
            print(f"[users] created {username} ({roles})")
        except ValueError as e:
            print(f"[users] {key}: {e}")


def validate_password(password):
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return None


def create_user(c, username, password, full_name="", is_admin=False, roles=None):
    username = (username or "").strip()
    if not username or len(username) > 60 or any(ch.isspace() for ch in username):
        raise ValueError("Username is required and cannot contain spaces.")
    problem = validate_password(password)
    if problem:
        raise ValueError(problem)
    roles = clean_roles(roles if roles is not None else ("SuperAdmin" if is_admin else ""))
    try:
        c.execute(
            "INSERT INTO users(username, full_name, password_hash, is_admin, created_at, roles) VALUES (?,?,?,?,?,?)",
            (username, (full_name or "").strip(), generate_password_hash(password),
             1 if set(roles.split(",")) & ADMIN_ROLES else 0, _iso(_now()), roles),
        )
    except sqlite3.IntegrityError:
        raise ValueError("That username is already taken.")


def current_user():
    return getattr(g, "user", None)


def is_admin():
    """SuperAdmin or Admin."""
    return bool(roles_of(current_user()) & ADMIN_ROLES)


def actor_name():
    u = current_user()
    return u["username"] if u else "Unknown"


def admin_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not is_admin():
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def requires(perm):
    def deco(view):
        @wraps(view)
        def wrapper(*args, **kwargs):
            if not can(perm):
                abort(403)
            return view(*args, **kwargs)
        return wrapper
    return deco


# --- Page chrome -----------------------------------------------------------

USER_BLOCK = """<div style="margin-top:auto;padding:16px 13px 0;border-top:1px solid #ffffff22;font-size:12px;color:#b7c8d7">
{% if g.user %}Signed in as <b style="color:#fff">{{ g.user.username }}</b><br><span>{{ (g.user.roles or 'View only').replace(',', ' · ') }}</span><br>
<a href="/account/password" style="color:#dce9f4;padding:6px 0;display:inline">Change password</a>
{% if can('users_manage') %} · <a href="/users" style="color:#dce9f4;padding:6px 0;display:inline">Users</a>{% endif %}
<form method="post" action="/logout" style="margin:8px 0 0"><button style="background:#2b5f88;color:#fff;border:0;border-radius:7px;padding:7px 12px;cursor:pointer;font:inherit;margin:0">Sign out</button></form>{% endif %}
</div>"""


SEARCH_BOX = (Path(__file__).resolve().parent / "search_box.html").read_text(encoding="utf-8")
PAGE_TOOLS = (Path(__file__).resolve().parent / "page_tools.html").read_text(encoding="utf-8")   # rich text + cost checks


def add_user_block(template_html):
    """Add the search box under the sidebar title, the shared page tools, and the signed-in user panel."""
    m = re.search(r"<aside[^>]*>.*?(</h2>|</small></div>)", template_html, re.S)
    if m:
        template_html = template_html[:m.end()] + SEARCH_BOX + template_html[m.end():]
    return template_html.replace("</aside>", PAGE_TOOLS + USER_BLOCK + "</aside>", 1)


SIMPLE_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>CC Medical | {{ title }}</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#f3f6fa;color:#182b3c;font:14px/1.5 system-ui,Segoe UI,Arial}
.wrap{max-width:{{ width or 420 }}px;margin:7vh auto;padding:0 16px}
.brand{font-weight:750;font-size:22px;color:#112a42;margin-bottom:18px}
.panel{background:#fff;border:1px solid #dfe7ef;border-radius:12px;padding:24px;margin-bottom:18px}
h1{font-size:21px;margin:0 0 16px}label{display:block;font-weight:600;font-size:12px;margin:12px 0 4px}
input,select{width:100%;padding:10px;border:1px solid #cdd9e4;border-radius:8px;font:inherit}
input[type=checkbox]{width:auto}
input:focus,select:focus,button:focus-visible{outline:2px solid #245d86;outline-offset:1px}
button{border:0;background:#245d86;color:#fff;border-radius:8px;padding:10px 16px;cursor:pointer;font:inherit;margin-top:16px}
.secondary{background:#e7f0f6;color:#245d86}.danger{background:#a23b3b}
.msg{padding:11px 13px;background:#fff6e5;border:1px solid #f0ddb8;border-radius:8px;margin-bottom:14px}
table{width:100%;border-collapse:collapse}td,th{padding:9px;border-bottom:1px solid #eaf0f5;text-align:left;vertical-align:middle}
th{font-size:12px;color:#667a8e}a{color:#245d86}.muted{color:#697b8d;font-size:12px}
.inline{display:flex;gap:6px;align-items:center;flex-wrap:wrap}.inline input{width:170px}.inline button{margin:0;padding:7px 11px}
</style></head><body><div class="wrap"><div class="brand">CC Medical Inventory</div>
{% with msgs=get_flashed_messages() %}{% for m in msgs %}<div class="msg">{{ m }}</div>{% endfor %}{% endwith %}
{{ body|safe }}</div></body></html>"""


def simple_page(title, body, width=None, **ctx):
    inner = render_template_string(body, **ctx)
    return render_template_string(SIMPLE_PAGE, title=title, body=inner, width=width)


# --- Routes ----------------------------------------------------------------

# Which permission each page/action needs: (endpoint, method) -> permission. "*" = any method.
ENDPOINT_PERMISSIONS = {
    ("inventory_new", "*"): "receive",
    ("transfer", "POST"): "move",
    ("repair", "POST"): "unit_repair",
    ("repair_new", "*"): "rma_create",
    ("repair_update", "POST"): "rma_edit",
    ("core_update", "POST"): "core_work",
    ("core_convert", "POST"): "core_convert",
    ("backup_download", "*"): "backup",
    ("restore_upload", "*"): "restore",
    ("clients_list", "*"): "client_view",
    ("client_detail", "*"): "client_view",
    ("client_new", "*"): "client_edit",
    ("client_edit", "*"): "client_edit",
    ("client_contact_add", "POST"): "client_edit",
    ("client_contact_action", "POST"): "client_edit",
    ("client_activity_add", "POST"): "client_edit",
    ("compatibility", "POST"): "catalog_admin",
    ("users", "*"): "users_manage",
}

def register_auth(app, conn):
    @app.before_request
    def load_user_and_protect():
        g.user = None
        uid = session.get("user_id")
        if uid:
            c = conn()
            try:
                u = c.execute("SELECT * FROM users WHERE id=? AND active=1", (uid,)).fetchone()
            finally:
                c.close()
            if u:
                g.user = u
            else:
                session.clear()

        # Reject form posts that come from another website (CSRF protection,
        # on top of the SameSite cookie setting).
        if request.method == "POST":
            source = request.headers.get("Origin") or request.headers.get("Referer")
            if source and urlparse(source).netloc != request.host:
                abort(403)

        if request.endpoint in PUBLIC_ENDPOINTS:
            return None
        if g.user is None:
            if request.method == "GET":
                return redirect(url_for("login", next=request.full_path.rstrip("?")))
            abort(401)
        perm = ENDPOINT_PERMISSIONS.get((request.endpoint, request.method)) or ENDPOINT_PERMISSIONS.get((request.endpoint, "*"))
        if perm and not can(perm):
            abort(403)
        return None

    @app.context_processor
    def permission_helpers():
        return {"can": can, "user_roles": lambda: roles_of(current_user())}

    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if app.config.get("SESSION_COOKIE_SECURE"):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    @app.get("/healthz")
    def healthz():
        return "ok"

    @app.route("/login", methods=["GET", "POST"])
    def login():
        nxt = request.args.get("next", "")
        # Only allow redirects back into this site.
        if not nxt.startswith("/") or nxt.startswith("//"):
            nxt = "/"
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            c = conn()
            try:
                cutoff = _iso(_now() - timedelta(minutes=LOCKOUT_MINUTES))
                fails = c.execute(
                    "SELECT COUNT(*) FROM login_attempts WHERE username=? AND attempted_at>?",
                    (username, cutoff),
                ).fetchone()[0]
                if fails >= MAX_FAILED_ATTEMPTS:
                    flash(f"Too many failed attempts. Try again in {LOCKOUT_MINUTES} minutes.")
                    return redirect(url_for("login", next=nxt))
                u = c.execute("SELECT * FROM users WHERE username=? AND active=1", (username,)).fetchone()
                if u and check_password_hash(u["password_hash"], password):
                    c.execute("DELETE FROM login_attempts WHERE username=?", (username,))
                    c.execute("UPDATE users SET last_login_at=? WHERE id=?", (_iso(_now()), u["id"]))
                    c.commit()
                    session.clear()
                    session.permanent = True
                    session["user_id"] = u["id"]
                    return redirect(nxt)
                c.execute(
                    "INSERT INTO login_attempts(username, ip, attempted_at) VALUES (?,?,?)",
                    (username, request.remote_addr, _iso(_now())),
                )
                c.execute("DELETE FROM login_attempts WHERE attempted_at<?", (_iso(_now() - timedelta(days=7)),))
                c.commit()
            finally:
                c.close()
            flash("Username or password is incorrect.")
            return redirect(url_for("login", next=nxt))
        return simple_page("Sign in", """
<div class="panel"><h1>Sign in</h1>
<form method="post"><label for="u">Username</label><input id="u" name="username" autocomplete="username" required autofocus>
<label for="p">Password</label><input id="p" type="password" name="password" autocomplete="current-password" required>
<button>Sign in</button></form></div>
<p class="muted">Ask an administrator if you need an account or forgot your password.</p>""")

    @app.post("/logout")
    def logout():
        session.clear()
        flash("You have been signed out.")
        return redirect(url_for("login"))

    @app.route("/account/password", methods=["GET", "POST"])
    def change_password():
        if request.method == "POST":
            f = request.form
            if not check_password_hash(g.user["password_hash"], f.get("current") or ""):
                flash("Current password is incorrect.")
            elif f.get("new") != f.get("confirm"):
                flash("New passwords do not match.")
            elif validate_password(f.get("new")):
                flash(validate_password(f.get("new")))
            else:
                c = conn()
                c.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(f["new"]), g.user["id"]))
                c.commit()
                c.close()
                flash("Password changed.")
                return redirect("/")
            return redirect(url_for("change_password"))
        return simple_page("Change password", """
<p><a href="/">Back to inventory</a></p><div class="panel"><h1>Change password</h1>
<form method="post"><label>Current password</label><input type="password" name="current" autocomplete="current-password" required>
<label>New password (at least {{ n }} characters)</label><input type="password" name="new" autocomplete="new-password" required minlength="{{ n }}">
<label>Confirm new password</label><input type="password" name="confirm" autocomplete="new-password" required minlength="{{ n }}">
<button>Change password</button></form></div>""", n=MIN_PASSWORD_LENGTH)

    @app.route("/users", methods=["GET", "POST"])
    def users():
        c = conn()
        super_admin = can("admins_manage")
        try:
            if request.method == "POST":
                f = request.form
                action = f.get("action")
                target = f.get("user_id", type=int)
                try:
                    def check_target(row, new_roles=None):
                        if row is None:
                            raise ValueError("User not found.")
                        if row["id"] == g.user["id"]:
                            raise ValueError("You can't change your own access. Ask another SuperAdmin.")
                        touches_admin = roles_of(row) & ADMIN_ROLES or (new_roles is not None and set(new_roles.split(",")) & ADMIN_ROLES)
                        if touches_admin and not super_admin:
                            raise ValueError("Only a SuperAdmin can create or change Admin and SuperAdmin accounts.")

                    def would_remove_last_superadmin(row, still_super):
                        if "SuperAdmin" not in roles_of(row) or still_super:
                            return False
                        n = c.execute("SELECT COUNT(*) FROM users WHERE active=1 AND (','||COALESCE(roles,'')||',') LIKE '%,SuperAdmin,%'").fetchone()[0]
                        return n <= 1

                    row = c.execute("SELECT * FROM users WHERE id=?", (target,)).fetchone() if target else None
                    if action == "create":
                        roles = clean_roles(f.getlist("roles"))
                        if set(roles.split(",")) & ADMIN_ROLES and not super_admin:
                            raise ValueError("Only a SuperAdmin can create Admin or SuperAdmin accounts.")
                        create_user(c, f.get("username"), f.get("password"), f.get("full_name"), roles=roles)
                        flash(f"Created user {f.get('username').strip()} ({roles.replace(',', ', ') or 'view only'}). Give them their temporary password and ask them to change it.")
                    elif action == "reset":
                        check_target(row)
                        problem = validate_password(f.get("password"))
                        if problem:
                            raise ValueError(problem)
                        c.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(f["password"]), target))
                        flash(f"Password reset for {row['username']}.")
                    elif action == "roles":
                        roles = clean_roles(f.getlist("roles"))
                        check_target(row, roles)
                        if would_remove_last_superadmin(row, "SuperAdmin" in roles.split(",")):
                            raise ValueError("There must always be at least one active SuperAdmin.")
                        c.execute("UPDATE users SET roles=?, full_name=? WHERE id=?", (roles, (f.get("full_name") or "").strip(), target))
                        flash(f"Updated {row['username']}: {roles.replace(',', ', ') or 'view only'}.")
                    elif action in ("deactivate", "activate"):
                        check_target(row)
                        if action == "deactivate" and would_remove_last_superadmin(row, False):
                            raise ValueError("There must always be at least one active SuperAdmin.")
                        c.execute("UPDATE users SET active=? WHERE id=?", (1 if action == "activate" else 0, target))
                        flash(f"{row['username']} {'reactivated' if action == 'activate' else 'deactivated'}.")
                    else:
                        raise ValueError("Unknown action.")
                    sync_is_admin(c)
                    c.commit()
                except ValueError as e:
                    c.rollback()
                    flash(str(e))
                return redirect(url_for("users"))
            rows = c.execute("SELECT * FROM users ORDER BY active DESC, username").fetchall()
        finally:
            c.close()
        assignable = ROLES if super_admin else [r for r in ROLES if r not in ADMIN_ROLES]
        return simple_page("Users", """
<p><a href="/">Back to inventory</a></p>
<div class="panel"><h1>Add a user</h1><form method="post"><input type="hidden" name="action" value="create">
<label>Username</label><input name="username" required maxlength="60">
<label>Full name (shown to staff)</label><input name="full_name" maxlength="120">
<label>Temporary password (at least {{ n }} characters)</label><input name="password" type="password" required minlength="{{ n }}" autocomplete="new-password">
<label>Roles (tick one or more)</label>
{% for r in assignable %}<label style="display:flex;align-items:center;gap:7px;font-weight:400;font-size:14px;margin:4px 0"><input type="checkbox" name="roles" value="{{ r }}"><span><b>{{ r }}</b> <span class="muted">· {{ help[r] }}</span></span></label>{% endfor %}
<button>Add user</button></form></div>
<div class="panel"><h1>Users</h1>{% if not super_admin %}<p class="muted">Admins can manage Sales, Shipping and Technician accounts. Admin and SuperAdmin accounts are managed by a SuperAdmin.</p>{% endif %}<div style="overflow-x:auto"><table>
<tr><th>Username</th><th>Name &amp; roles</th><th>Status</th><th>Last sign-in</th><th>Password / access</th></tr>
{% for u in rows %}{% set urole = (u.roles or '').split(',') %}{% set locked = u.id == g.user.id or (not super_admin and ('Admin' in urole or 'SuperAdmin' in urole)) %}<tr>
<td><b>{{ u.username }}</b></td>
<td>{% if locked %}{{ u.full_name or '' }}<br><span class="muted">{{ (u.roles or 'View only').replace(',', ', ') }}</span>{% else %}
<form method="post" style="min-width:330px"><input type="hidden" name="action" value="roles"><input type="hidden" name="user_id" value="{{ u.id }}">
<input name="full_name" value="{{ u.full_name or '' }}" placeholder="Full name" style="margin-bottom:6px">
<div style="display:flex;flex-wrap:wrap;gap:4px 14px">{% for r in assignable %}<label style="display:inline-flex;align-items:center;gap:5px;font-weight:400;font-size:13px;margin:0"><input type="checkbox" name="roles" value="{{ r }}" {% if r in urole %}checked{% endif %}>{{ r }}</label>{% endfor %}</div>
<button class="secondary" style="margin-top:6px;padding:6px 10px">Save</button></form>{% endif %}</td>
<td>{{ 'Active' if u.active else 'Deactivated' }}</td>
<td class="muted">{{ u.last_login_at|daytime if u.last_login_at else 'Never' }}</td>
<td>{% if u.id == g.user.id %}<span class="muted">This is you</span>{% elif locked %}<span class="muted">SuperAdmin only</span>{% else %}<div class="inline">
<form method="post" class="inline"><input type="hidden" name="action" value="reset"><input type="hidden" name="user_id" value="{{ u.id }}">
<input type="password" name="password" placeholder="New password" minlength="{{ n }}" required autocomplete="new-password"><button class="secondary">Reset password</button></form>
<form method="post"><input type="hidden" name="action" value="{{ 'deactivate' if u.active else 'activate' }}"><input type="hidden" name="user_id" value="{{ u.id }}"><button class="{{ 'danger' if u.active else '' }}">{{ 'Deactivate' if u.active else 'Reactivate' }}</button></form>
</div>{% endif %}</td></tr>{% endfor %}
</table></div></div>""", width=1250, rows=rows, n=MIN_PASSWORD_LENGTH, assignable=assignable, help=ROLE_HELP, super_admin=super_admin)

    @app.errorhandler(403)
    def forbidden(_e):
        return simple_page("Not allowed", """<div class="panel"><h1>Not allowed</h1>
<p>Your account doesn't have access to this page, or the request came from outside this site.</p><p><a href="/">Back to inventory</a></p></div>"""), 403

    @app.errorhandler(401)
    def unauthorized(_e):
        return simple_page("Signed out", """<div class="panel"><h1>Your session ended</h1>
<p>Sign in again, then repeat what you were doing.</p><p><a href="/login">Sign in</a></p></div>"""), 401
