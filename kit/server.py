"""Swiss Student Kit: Flask app served by waitress, one process (rate limits and the stats salt live in memory).

    python -m kit.server --host 127.0.0.1 --port 8800

Public:  /  /<city>  /login  /login/verify  /logout  /suggest  /suggest/check  /mine  /privacy
         POST /use/<id>  /report/<id>  /hit (anonymous stats)  GET /health
Mods:    /mod …  (see mod.py)
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import secrets
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, g, jsonify, make_response, redirect, render_template, request, url_for

from . import auth, db, forms, settings, stats
from .linkcheck import reachable
from .ranking import domain, is_new, pretty_url, rank, safe_next, with_msg

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 7 * 86400  # static files carry ?v=<hash>, so a new version shows at once
_static = Path(app.static_folder)
VERSION = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(_static.glob("*")) if p.is_file())).hexdigest()[:10]

FLASH = {
    "sent": "Thanks! A moderator will check it within a few days.",
    "sent_warn": "Thanks! A moderator will check it within a few days. Note: we couldn't reach that URL just now.",
    "reported": "Thanks for the report, a moderator will have a look.",
    "login": "You're logged in.",
    "logout": "You're logged out.",
    "saved": "Saved.",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "object-src 'none'"
)


def client_ip() -> str:
    # our only way in is the Caddy proxy, which sets X-Real-IP (deploy/install.sh); never trust X-Forwarded-For's left
    return request.headers.get("X-Real-IP") or request.remote_addr or "?"


def wants_json() -> bool:
    return "application/json" in request.headers.get("Accept", "")


def set_cookie(resp, name: str, value: str, days: int) -> None:
    resp.set_cookie(name, value, max_age=days * 86400, httponly=True, secure=settings.COOKIE_SECURE, samesite="Lax")


# ------------------------------------------------------------------ request lifecycle
@app.before_request
def before():
    g.new_csrf = False
    if request.endpoint == "static":
        return
    g.db = db.connect()
    g.user = auth.current_user(g.db, request.cookies.get(auth.COOKIE))
    g.csrf = request.cookies.get("csrf")
    if not g.csrf:
        g.csrf, g.new_csrf = secrets.token_urlsafe(24), True
    # every form post carries the csrf cookie's value (double submit); /hit only bumps anonymous counters
    if request.method == "POST" and request.endpoint != "hit":
        sent = request.form.get("csrf") or request.headers.get("X-CSRF-Token") or ""
        if g.new_csrf or not hmac.compare_digest(sent, g.csrf):
            abort(400, "This form expired. Go back, reload the page and try again.")


@app.teardown_request
def teardown(_exc):
    con = g.pop("db", None)
    if con:
        con.close()


@app.after_request
def headers(resp):
    if g.get("new_csrf"):
        set_cookie(resp, "csrf", g.csrf, 365)
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    if settings.COOKIE_SECURE:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.endpoint != "static":
        resp.headers.setdefault("Cache-Control", "no-store" if g.get("user") else "no-cache")
    return resp


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
def error(e):
    return render_template("error.html", e=e), e.code


@app.context_processor
def context():
    return {
        "user": g.get("user"),
        "csrf": g.get("csrf", ""),
        "flash": FLASH.get(request.args.get("m", "")),
        "asset": lambda p: url_for("static", filename=p, v=VERSION),
        "SITE_URL": settings.SITE_URL,
    }


@app.template_filter("thousands")
def thousands(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


@app.template_filter("day")
def day(ts: str | None) -> str:
    return datetime.fromisoformat(ts).strftime("%-d %b %Y") if ts else ""


def require_login():
    if not g.user:
        if wants_json():
            abort(make_response(jsonify(error="login"), 401))
        abort(redirect(url_for("login", next=request.full_path.rstrip("?"))))


# ------------------------------------------------------------------ public page
@app.get("/")
def home():
    con = g.db
    slugs = [r[0] for r in con.execute("SELECT slug FROM cities ORDER BY position")]
    slug = request.cookies.get("city")
    if slug not in slugs and g.user and g.user["university_code"]:
        row = con.execute(
            "SELECT c.slug FROM universities u JOIN cities c ON c.id=u.city_id WHERE u.code=?",
            (g.user["university_code"],),
        ).fetchone()
        slug = row[0] if row else None
    if slug not in slugs:
        slug = slugs[0] if slugs else abort(404)
    qs = request.query_string.decode()
    return redirect(f"/{slug}" + (f"?{qs}" if qs else ""))


def city_items(con, city_id: int) -> list[dict]:
    rows = con.execute(
        "SELECT i.id, i.name, i.url, i.description, i.kind, i.pinned, i.uses_count, i.approved_at, i.category_id,"
        " (SELECT group_concat(ifnull(university_code, 'ALL'), ' ') FROM"
        "   (SELECT university_code FROM item_universities WHERE item_id=i.id ORDER BY university_code)) unis"
        " FROM items i JOIN item_cities ic ON ic.item_id=i.id WHERE ic.city_id=? AND i.status='approved'",
        (city_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        unis = (d["unis"] or "ALL").split()
        d["unis"] = "ALL" if "ALL" in unis else " ".join(unis)
        d["tag"] = d["unis"].replace(" ", "+")
        d["dom"] = pretty_url(d["url"])
        d["new"] = is_new(d)
        out.append(d)
    return out


@app.get("/<slug>")
def city(slug: str):
    con = g.db
    c = con.execute("SELECT * FROM cities WHERE slug=?", (slug,)).fetchone() or abort(404)
    cities = con.execute("SELECT slug, label FROM cities ORDER BY position").fetchall()
    unis = [r[0] for r in con.execute("SELECT code FROM universities WHERE city_id=? ORDER BY rowid", (c["id"],))]
    u = request.args.get("u", "").upper()
    u = u if u in unis else ""
    q = request.args.get("q", "").strip()[:80]
    used = {r[0] for r in con.execute("SELECT item_id FROM uses WHERE user_id=?", (g.user["id"],))} if g.user else set()
    items = city_items(con, c["id"])
    sections, shown = [], 0
    for cat in con.execute("SELECT * FROM categories ORDER BY position"):
        its = rank([i for i in items if i["category_id"] == cat["id"]])
        for i in its:
            i["used"] = i["id"] in used
            i["s"] = f"{i['name']} {i['description']} {cat['label']} {cat['short_label']}".lower()
            i["hidden"] = bool(u and i["unis"] != "ALL" and u not in i["unis"].split()) or bool(
                q and q.lower() not in i["s"]
            )
            shown += not i["hidden"]
        if its:
            sections.append({"cat": cat, "cards": its, "hidden": all(i["hidden"] for i in its)})
    updated = con.execute("SELECT max(approved_at) FROM items WHERE status='approved'").fetchone()[0]
    stats.view(
        con,
        client_ip(),
        request.user_agent.string,
        request.referrer or "",
        request.headers.get("Accept-Language", ""),
        c["label"],
        request.host,
    )
    resp = make_response(
        render_template(
            "city.html",
            city=c,
            cities=cities,
            unis=unis,
            u=u,
            q=q,
            sections=sections,
            shown=shown,
            total=len(items),
            updated=updated,
        )
    )
    set_cookie(resp, "city", slug, 365)
    return resp


@app.post("/use/<int:item_id>")
def use(item_id: int):
    require_login()
    con = g.db
    if not con.execute("SELECT 1 FROM items WHERE id=? AND status='approved'", (item_id,)).fetchone():
        abort(404)
    if not auth.limiter.allow(f"use:{g.user['id']}", 120, 3600):
        abort(make_response(jsonify(error="slow down"), 429))
    with db.tx(con):
        cur = con.execute("DELETE FROM uses WHERE user_id=? AND item_id=?", (g.user["id"], item_id))
        if not cur.rowcount:
            con.execute("INSERT INTO uses (user_id, item_id) VALUES (?, ?)", (g.user["id"], item_id))
            stats.bump(con, "action", "Marked “I use this”")
    used = not cur.rowcount
    count = con.execute("SELECT uses_count FROM items WHERE id=?", (item_id,)).fetchone()[0]
    if wants_json():
        return jsonify(used=used, count=count)
    return redirect(safe_next(request.form.get("back")) + f"#i{item_id}")


@app.post("/report/<int:item_id>")
def report(item_id: int):
    require_login()
    con = g.db
    kind = request.form.get("type", "")
    if kind not in settings.REPORT_TYPES or not con.execute("SELECT 1 FROM items WHERE id=?", (item_id,)).fetchone():
        abort(400)
    if (
        auth.limiter.allow(f"report:{g.user['id']}", 20, 3600)
        and not con.execute(
            "SELECT 1 FROM reports WHERE item_id=? AND user_id=? AND type=? AND status='open'",
            (item_id, g.user["id"], kind),
        ).fetchone()
    ):
        con.execute(
            "INSERT INTO reports (item_id, user_id, type, message) VALUES (?, ?, ?, ?)",
            (item_id, g.user["id"], kind, request.form.get("message", "").strip()[:500]),
        )
        stats.bump(con, "action", "Reported a service")
    return redirect(with_msg(safe_next(request.form.get("back")), "reported"))


@app.post("/hit")
def hit():
    if auth.limiter.allow("hit:" + client_ip(), 300, 3600):
        try:
            h = json.loads(request.get_data(as_text=True) or "{}")
        except ValueError:
            h = None
        if isinstance(h, dict):

            def name(i):
                row = g.db.execute("SELECT name FROM items WHERE id=?", (i,)).fetchone() if str(i).isdigit() else None
                return row[0] if row else None

            stats.hit(g.db, request.user_agent.string, h, name)
    return "", 204


# ------------------------------------------------------------------ login
@app.route("/login", methods=["GET", "POST"])
def login():
    nxt = safe_next(request.values.get("next"))
    domains = sorted(db.email_domains(g.db))
    if request.method == "POST":
        email = request.form.get("email", "")
        err = auth.start_login(g.db, email, client_ip(), nxt)
        return render_template("login.html", sent=not err, error=err, email=email, next=nxt, domains=domains)
    return render_template("login.html", next=nxt, domains=domains)


@app.route("/login/verify", methods=["GET", "POST"])
def verify():
    token, nxt = request.values.get("t", ""), safe_next(request.values.get("next"))
    if request.method == "GET":  # a button, so mail scanners that open links don't use up the token
        return render_template("verify.html", t=token, next=nxt)
    sid = auth.finish_login(g.db, token)
    if not sid:
        return render_template("verify.html", expired=True), 400
    stats.bump(g.db, "action", "Logged in")
    resp = redirect(with_msg(nxt, "login"))
    set_cookie(resp, auth.COOKIE, sid, settings.SESSION_DAYS)
    return resp


@app.post("/logout")
def logout():
    auth.logout(g.db, request.cookies.get(auth.COOKIE))
    resp = redirect("/?m=logout")
    resp.delete_cookie(auth.COOKIE)
    return resp


# ------------------------------------------------------------------ suggesting
@app.route("/suggest", methods=["GET", "POST"])
def suggest():
    require_login()
    con = g.db
    opts = forms.form_options(con)
    pending = con.execute(
        "SELECT count(*) FROM items WHERE submitted_by=? AND status='pending'", (g.user["id"],)
    ).fetchone()[0]
    if request.method == "GET":
        return render_template("suggest.html", f={}, errors=[], pending=pending, max=settings.MAX_PENDING, **opts)
    city = request.form.get("city", "")
    slugs = [c["slug"] for c in opts["cities"]] if city == "all" else [city]
    d, errors = forms.clean_item(con, request.form, slugs)
    if pending >= settings.MAX_PENDING:
        errors.append(f"You already have {pending} suggestions waiting. Please wait until a moderator checks them.")
    if errors:
        return render_template(
            "suggest.html", f=request.form, errors=errors, pending=pending, max=settings.MAX_PENDING, **opts
        ), 400
    with db.tx(con):
        db.save_item(con, None, {**d, "status": "pending", "submitted_by": g.user["id"]})
        stats.bump(con, "action", "Suggested a service")
    return redirect(url_for("mine", m="sent" if reachable(d["url"], 5) else "sent_warn"))


@app.get("/suggest/check")
def suggest_check():
    """For the form's JS: existing items on the same domain, and whether the URL answers."""
    require_login()
    url = request.args.get("url", "").strip()
    dom = domain(url)
    dupes = [
        {"name": r["name"], "url": r["url"], "status": r["status"]}
        for r in g.db.execute("SELECT name, url, status FROM items WHERE status != 'rejected'")
        if dom and domain(r["url"]) == dom
    ]
    ok = None
    if url.startswith("https://") and auth.limiter.allow(f"check:{g.user['id']}", 30, 3600):
        ok = reachable(url, 5)
    return jsonify(dupes=dupes, reachable=ok)


@app.get("/mine")
def mine():
    require_login()
    rows = g.db.execute(
        "SELECT name, url, status, rejection_reason, created_at FROM items WHERE submitted_by=? ORDER BY id DESC",
        (g.user["id"],),
    ).fetchall()
    return render_template("mine.html", rows=rows)


@app.get("/privacy")
def privacy():
    return render_template("privacy.html")


@app.get("/health")
def health():
    g.db.execute("SELECT 1")
    return "ok"


from . import mod  # noqa: E402  (registers /mod routes on `app`)

app.register_blueprint(mod.bp)


def main() -> None:
    from waitress import serve

    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8800)
    a = ap.parse_args()
    con = db.connect()
    db.init(con)
    con.close()
    print(f"Swiss Student Kit on http://{a.host}:{a.port}", flush=True)
    serve(app, host=a.host, port=a.port, threads=8)


if __name__ == "__main__":
    main()
