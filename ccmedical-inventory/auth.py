"""Staff accounts and login for CC Medical Inventory.

Every page requires a signed-in user. Admins can manage users and use the
product database admin tools. Passwords are stored as salted hashes, never
in plain text.
"""
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
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


def ensure_user_schema(c):
    c.executescript(USER_SCHEMA)


def validate_password(password):
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Password must be at least {MIN_PASSWORD_LENGTH} characters."
    return None


def create_user(c, username, password, full_name="", is_admin=False):
    username = (username or "").strip()
    if not username or len(username) > 60 or any(ch.isspace() for ch in username):
        raise ValueError("Username is required and cannot contain spaces.")
    problem = validate_password(password)
    if problem:
        raise ValueError(problem)
    try:
        c.execute(
            "INSERT INTO users(username, full_name, password_hash, is_admin, created_at) VALUES (?,?,?,?,?)",
            (username, (full_name or "").strip(), generate_password_hash(password), 1 if is_admin else 0, _iso(_now())),
        )
    except sqlite3.IntegrityError:
        raise ValueError("That username is already taken.")


def current_user():
    return getattr(g, "user", None)


def is_admin():
    u = current_user()
    return bool(u and u["is_admin"])


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


# --- Page chrome -----------------------------------------------------------

USER_BLOCK = """<div style="margin-top:auto;padding:16px 13px 0;border-top:1px solid #ffffff22;font-size:12px;color:#b7c8d7">
{% if g.user %}Signed in as <b style="color:#fff">{{ g.user.username }}</b>{% if g.user.is_admin %} (admin){% endif %}<br>
<a href="/account/password" style="color:#dce9f4;padding:6px 0;display:inline">Change password</a>
{% if g.user.is_admin %} · <a href="/users" style="color:#dce9f4;padding:6px 0;display:inline">Users</a>{% endif %}
<form method="post" action="/logout" style="margin:8px 0 0"><button style="background:#2b5f88;color:#fff;border:0;border-radius:7px;padding:7px 12px;cursor:pointer;font:inherit;margin:0">Sign out</button></form>{% endif %}
</div>"""


def add_user_block(template_html):
    """Insert the signed-in user panel at the bottom of a page's sidebar."""
    return template_html.replace("</aside>", USER_BLOCK + "</aside>", 1)


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
        return None

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
    @admin_required
    def users():
        c = conn()
        try:
            if request.method == "POST":
                f = request.form
                action = f.get("action")
                target = f.get("user_id", type=int)
                try:
                    if action == "create":
                        create_user(c, f.get("username"), f.get("password"), f.get("full_name"), f.get("is_admin") == "1")
                        flash(f"Created user {f.get('username').strip()}. Give them their temporary password and ask them to change it.")
                    elif action == "reset":
                        problem = validate_password(f.get("password"))
                        if problem:
                            raise ValueError(problem)
                        c.execute("UPDATE users SET password_hash=? WHERE id=?", (generate_password_hash(f["password"]), target))
                        flash("Password reset.")
                    elif action in ("deactivate", "activate", "make_admin", "remove_admin"):
                        if target == g.user["id"]:
                            raise ValueError("You can't change your own access. Ask another admin.")
                        column, value = {"deactivate": ("active", 0), "activate": ("active", 1),
                                         "make_admin": ("is_admin", 1), "remove_admin": ("is_admin", 0)}[action]
                        c.execute(f"UPDATE users SET {column}=? WHERE id=?", (value, target))
                        flash("User updated.")
                    else:
                        raise ValueError("Unknown action.")
                    c.commit()
                except ValueError as e:
                    c.rollback()
                    flash(str(e))
                return redirect(url_for("users"))
            rows = c.execute("SELECT * FROM users ORDER BY active DESC, username").fetchall()
        finally:
            c.close()
        return simple_page("Users", """
<p><a href="/">Back to inventory</a></p>
<div class="panel"><h1>Add a user</h1><form method="post"><input type="hidden" name="action" value="create">
<label>Username</label><input name="username" required maxlength="60">
<label>Full name</label><input name="full_name" maxlength="120">
<label>Temporary password (at least {{ n }} characters)</label><input name="password" type="password" required minlength="{{ n }}" autocomplete="new-password">
<label><input type="checkbox" name="is_admin" value="1"> Administrator (can manage users and the product database)</label>
<button>Add user</button></form></div>
<div class="panel"><h1>Users</h1><div style="overflow-x:auto"><table>
<tr><th>Username</th><th>Name</th><th>Role</th><th>Status</th><th>Last sign-in</th><th>Actions</th></tr>
{% for u in rows %}<tr><td>{{ u.username }}</td><td>{{ u.full_name or '' }}</td>
<td>{{ 'Admin' if u.is_admin else 'Staff' }}</td><td>{{ 'Active' if u.active else 'Deactivated' }}</td>
<td class="muted">{{ (u.last_login_at or 'Never')[:16].replace('T',' ') }}</td>
<td>{% if u.id != g.user.id %}<div class="inline">
<form method="post" class="inline"><input type="hidden" name="action" value="reset"><input type="hidden" name="user_id" value="{{ u.id }}">
<input type="password" name="password" placeholder="New password" minlength="{{ n }}" required autocomplete="new-password"><button class="secondary">Reset password</button></form>
<form method="post"><input type="hidden" name="action" value="{{ 'remove_admin' if u.is_admin else 'make_admin' }}"><input type="hidden" name="user_id" value="{{ u.id }}"><button class="secondary">{{ 'Remove admin' if u.is_admin else 'Make admin' }}</button></form>
<form method="post"><input type="hidden" name="action" value="{{ 'deactivate' if u.active else 'activate' }}"><input type="hidden" name="user_id" value="{{ u.id }}"><button class="{{ 'danger' if u.active else '' }}">{{ 'Deactivate' if u.active else 'Reactivate' }}</button></form>
</div>{% else %}<span class="muted">This is you</span>{% endif %}</td></tr>{% endfor %}
</table></div></div>""", width=1100, rows=rows, n=MIN_PASSWORD_LENGTH)

    @app.errorhandler(403)
    def forbidden(_e):
        return simple_page("Not allowed", """<div class="panel"><h1>Not allowed</h1>
<p>Your account doesn't have access to this page, or the request came from outside this site.</p><p><a href="/">Back to inventory</a></p></div>"""), 403

    @app.errorhandler(401)
    def unauthorized(_e):
        return simple_page("Signed out", """<div class="panel"><h1>Your session ended</h1>
<p>Sign in again, then repeat what you were doing.</p><p><a href="/login">Sign in</a></p></div>"""), 401
