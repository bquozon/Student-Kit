"""Swiss Student Kit: Flask app served by waitress, one process (rate limits and the stats salt live in memory).

    python -m kit.server --host 127.0.0.1 --port 8800

No accounts: anyone browses, votes ("I use this", one per browser) and suggests links; only the owner moderates.

Public:  /  /<city>  /suggest  /suggest/check  /privacy  POST /use/<id>  /hit (anonymous stats)  GET /health
Mod:     /mod …  (see mod.py; log in at /mod with ADMIN_KEY)
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
from .auth import client_ip, set_cookie
from .linkcheck import reachable
from .ranking import domain, is_new, pretty_url, rank, safe_next

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 7 * 86400  # static files carry ?v=<hash>, so a new version shows at once
_static = Path(app.static_folder)
VERSION = hashlib.sha256(b"".join(p.read_bytes() for p in sorted(_static.glob("*")) if p.is_file())).hexdigest()[:10]

FLASH = {
    "sent": "Thanks! A moderator will check it within a few days.",
    "sent_warn": "Thanks! A moderator will check it within a few days. Note: we couldn't reach that URL just now.",
    "mod": "Logged in on this device.",
    "logout": "Logged out.",
    "saved": "Saved.",
}
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'; "
    "object-src 'none'"
)


def wants_json() -> bool:
    return "application/json" in request.headers.get("Accept", "")


# ------------------------------------------------------------------ request lifecycle
@app.before_request
def before():
    g.new_csrf = False
    if request.endpoint == "static":
        return
    g.db = db.connect()
    g.is_mod = auth.is_mod(g.db, request.cookies.get(auth.MOD_COOKIE))
    # like polyuni: open ?notrack=1 once on your own devices (?notrack=0 undoes it); logged-in mods never count
    g.notrack = request.args.get("notrack") if request.args.get("notrack") in ("0", "1") else None
    g.tracked = not g.is_mod and (g.notrack or request.cookies.get("notrack")) != "1"
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
    if g.get("notrack") == "1":
        set_cookie(resp, "notrack", "1", 400)  # browsers cap cookies at 400 days
    elif g.get("notrack") == "0":
        resp.delete_cookie("notrack")
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    if settings.COOKIE_SECURE:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    if request.endpoint != "static":
        resp.headers.setdefault("Cache-Control", "no-store" if g.get("is_mod") else "no-cache")
    return resp


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(413)
def error(e):
    return render_template("error.html", e=e), e.code


@app.context_processor
def context():
    return {
        "is_mod": g.get("is_mod"),
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


# ------------------------------------------------------------------ public page
@app.get("/")
def home():
    con = g.db
    slugs = [r[0] for r in con.execute("SELECT slug FROM cities ORDER BY position")]
    slug = request.cookies.get("city")
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
    voter = request.cookies.get(auth.VOTER_COOKIE)
    used = {r[0] for r in con.execute("SELECT item_id FROM uses WHERE voter=?", (auth.sha(voter),))} if voter else set()
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
    if g.tracked:
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
    """The anonymous vote: toggles for this browser (voter cookie, created on the first vote).
    shortcut: clearing cookies allows voting again; the per-IP limit caps that. Add accounts if votes get gamed."""
    con = g.db
    if not con.execute("SELECT 1 FROM items WHERE id=? AND status='approved'", (item_id,)).fetchone():
        abort(404)
    if not auth.limiter.allow("use:" + client_ip(), 60, 3600):
        abort(make_response(jsonify(error="Too many votes from your network, try again later."), 429))
    voter = request.cookies.get(auth.VOTER_COOKIE) or secrets.token_urlsafe(24)
    with db.tx(con):
        cur = con.execute("DELETE FROM uses WHERE voter=? AND item_id=?", (auth.sha(voter), item_id))
        if not cur.rowcount:
            con.execute("INSERT INTO uses (voter, item_id) VALUES (?, ?)", (auth.sha(voter), item_id))
            stats.bump(con, "action", "Marked “I use this”")
    used = not cur.rowcount
    count = con.execute("SELECT uses_count FROM items WHERE id=?", (item_id,)).fetchone()[0]
    back = safe_next(request.form.get("back")) + f"#i{item_id}"
    resp = jsonify(used=used, count=count) if wants_json() else redirect(back)
    set_cookie(resp, auth.VOTER_COOKIE, voter, 365 * 2)
    return resp


@app.post("/hit")
def hit():
    if g.tracked and auth.limiter.allow("hit:" + client_ip(), 300, 3600):
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


# ------------------------------------------------------------------ suggesting
@app.route("/suggest", methods=["GET", "POST"])
def suggest():
    """Open to anyone, no account: a honeypot field, a per-IP limit and a cap on the whole queue keep spam out."""
    con = g.db
    opts = forms.form_options(con)
    if request.method == "GET":
        return render_template("suggest.html", f={}, errors=[], **opts)
    if request.form.get("website"):  # hidden field only bots fill in: pretend it worked
        return redirect(url_for("suggest", m="sent"))
    city = request.form.get("city", "")
    slugs = [c["slug"] for c in opts["cities"]] if city == "all" else [city]
    d, errors = forms.clean_item(con, request.form, slugs)
    pending = con.execute("SELECT count(*) FROM items WHERE status='pending'").fetchone()[0]
    if pending >= settings.MAX_PENDING:
        errors.append("The moderation queue is full right now. Please come back in a few days.")
    if not errors and not auth.limiter.allow("suggest:" + client_ip(), *settings.SUGGEST_PER_IP):
        errors.append("Thanks, that's a lot of suggestions! Please wait an hour before sending more.")
    if errors:
        return render_template("suggest.html", f=request.form, errors=errors, **opts), 400
    with db.tx(con):
        db.save_item(con, None, {**d, "status": "pending", "submitted": 1})
        stats.bump(con, "action", "Suggested a service")
    return redirect(url_for("suggest", m="sent" if reachable(d["url"], 5) else "sent_warn"))


@app.get("/suggest/check")
def suggest_check():
    """For the form's JS: existing items on the same domain, and whether the URL answers."""
    url = request.args.get("url", "").strip()
    dom = domain(url)
    dupes = [
        {"name": r["name"], "url": r["url"], "status": r["status"]}
        for r in g.db.execute("SELECT name, url, status FROM items WHERE status IN ('approved', 'pending')")
        if dom and domain(r["url"]) == dom
    ]
    ok = None
    if url.startswith("https://") and auth.limiter.allow("check:" + client_ip(), 30, 3600):
        ok = reachable(url, 5)
    return jsonify(dupes=dupes, reachable=ok)


@app.get("/privacy")
def privacy():
    return render_template("privacy.html")


@app.get("/robots.txt")
def robots():
    body = f"User-agent: *\nDisallow: /mod\nDisallow: /suggest/check\n\nSitemap: {settings.SITE_URL}/sitemap.xml\n"
    return body, {"Content-Type": "text/plain"}


@app.get("/sitemap.xml")
def sitemap():
    paths = [r[0] for r in g.db.execute("SELECT slug FROM cities ORDER BY position")] + ["suggest", "privacy"]
    urls = "".join(f"<url><loc>{settings.SITE_URL}/{p}</loc></url>" for p in paths)
    body = f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
    return body, {"Content-Type": "application/xml"}


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
