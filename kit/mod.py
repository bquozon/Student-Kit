"""/mod: moderation queue, reports, all items, categories, stats, log. Owner only; every action goes to mod_log.

No login form: opening /mod/key/<ADMIN_KEY> once unlocks the panel on that device (a 90-day cookie)."""

from __future__ import annotations

from flask import Blueprint, abort, g, redirect, render_template, request, url_for

from . import auth, db, forms, settings, stats
from .ranking import safe_next, with_msg

bp = Blueprint("mod", __name__, url_prefix="/mod")


@bp.before_request
def only_mods():
    if not g.is_mod and request.endpoint != "mod.unlock":
        abort(404)  # the panel doesn't exist for anyone else


@bp.get("/key/<key>")
def unlock(key: str):
    sid = auth.open_mod_session(g.db, key, auth.client_ip())
    if not sid:
        abort(404)
    resp = redirect("/mod?m=mod")  # off the secret URL at once, so it doesn't stay in the address bar
    auth.set_cookie(resp, auth.MOD_COOKIE, sid, settings.SESSION_DAYS)
    return resp


@bp.post("/logout")
def logout():
    auth.close_mod_session(g.db, request.cookies.get(auth.MOD_COOKIE))
    resp = redirect("/?m=logout")
    resp.delete_cookie(auth.MOD_COOKIE)
    return resp


def back(default: str) -> str:
    return with_msg(safe_next(request.form.get("back"), default), "saved")


# ------------------------------------------------------------------ queue
@bp.get("")
def queue():
    con = g.db
    ids = [r[0] for r in con.execute("SELECT id FROM items WHERE status='pending' ORDER BY created_at, id")]
    approved = con.execute("SELECT id, name FROM items WHERE status='approved' ORDER BY name COLLATE NOCASE").fetchall()
    return render_template(
        "mod/queue.html",
        tab="queue",
        items=[forms.item_detail(con, i) for i in ids],
        approved=approved,
        **forms.form_options(con),
    )


@bp.post("/item/<int:item_id>")
def item_action(item_id: int):
    con = g.db
    item = forms.item_detail(con, item_id) or abort(404)
    action = request.form.get("action", "")
    if action in ("save", "approve"):
        d, errors = forms.clean_item(con, request.form, request.form.getlist("cities"))
        if errors:
            return render_template(
                "mod/edit.html",
                tab="items",
                item={
                    **item,
                    **request.form.to_dict(),
                    "cities": request.form.getlist("cities"),
                    "universities": request.form.getlist("universities"),
                },
                errors=errors,
                **forms.form_options(con),
            ), 400
        with db.tx(con):
            db.save_item(con, item_id, d)
            if action == "approve":
                con.execute(
                    "UPDATE items SET status='approved', approved_at=datetime('now'), rejection_reason=NULL WHERE id=?",
                    (item_id,),
                )
            db.log(con, action, item_id, d["name"])
    elif action in ("reject", "merge"):
        if action == "merge":
            target = con.execute(
                "SELECT name FROM items WHERE id=?", (request.form.get("merge_into", type=int),)
            ).fetchone()
            if not target:
                abort(400, "Pick the existing item to merge into.")
            reason = f"duplicate of {target['name']}"
        else:
            reason = " ".join(request.form.get("reason", "").split())[:300]
            if not reason:
                abort(400, "A reason is required to reject (it's kept in the log).")
        with db.tx(con):
            con.execute("UPDATE items SET status='rejected', rejection_reason=? WHERE id=?", (reason, item_id))
            db.log(con, action, item_id, f"{item['name']}: {reason}")
    elif action in ("pin", "unpin"):
        with db.tx(con):
            con.execute("UPDATE items SET pinned=? WHERE id=?", (int(action == "pin"), item_id))
            db.log(con, action, item_id, item["name"])
    elif action in ("archive", "restore"):
        with db.tx(con):
            con.execute(
                "UPDATE items SET status=? WHERE id=?", ("archived" if action == "archive" else "approved", item_id)
            )
            if action == "archive":  # an archived item needs no more fixing
                con.execute("UPDATE reports SET status='done' WHERE item_id=? AND status='open'", (item_id,))
            db.log(con, action, item_id, item["name"])
    else:
        abort(400)
    return redirect(back(url_for("mod.queue")))


# ------------------------------------------------------------------ reports
@bp.get("/reports")
def reports():
    con = g.db
    rows = con.execute(
        "SELECT r.*, i.name, i.url, i.status item_status, i.link_fails FROM reports r JOIN items i ON i.id=r.item_id"
        " WHERE r.status='open' ORDER BY r.item_id, r.created_at"
    ).fetchall()
    groups: dict[int, dict] = {}
    for r in rows:
        groups.setdefault(r["item_id"], {"item": r, "reports": []})["reports"].append(r)
    return render_template("mod/reports.html", tab="reports", groups=groups.values(), types=settings.REPORT_TYPES)


@bp.post("/reports/<int:item_id>/done")
def reports_done(item_id: int):
    with db.tx(g.db):
        n = g.db.execute("UPDATE reports SET status='done' WHERE item_id=? AND status='open'", (item_id,)).rowcount
        db.log(g.db, "reports done", item_id, f"{n} report(s)")
    return redirect(back(url_for("mod.reports")))


# ------------------------------------------------------------------ all items
@bp.get("/items")
def items():
    con = g.db
    status = request.args.get("status", "")
    q = request.args.get("q", "").strip()
    sql = (
        "SELECT i.*, c.label category,"
        " (SELECT group_concat(ci.label, ', ') FROM item_cities ic JOIN cities ci ON ci.id=ic.city_id"
        "  WHERE ic.item_id=i.id) cities"
        " FROM items i JOIN categories c ON c.id=i.category_id WHERE 1=1"
    )
    args: list = []
    if status:
        sql += " AND i.status=?"
        args.append(status)
    if q:
        sql += " AND (i.name LIKE ? OR i.url LIKE ?)"
        args += [f"%{q}%"] * 2
    rows = con.execute(sql + " ORDER BY c.position, i.name COLLATE NOCASE", args).fetchall()
    return render_template("mod/items.html", tab="items", rows=rows, status=status, q=q)


@bp.get("/items/<int:item_id>")
def edit(item_id: int):
    item = forms.item_detail(g.db, item_id) or abort(404)
    return render_template("mod/edit.html", tab="items", item=item, errors=[], **forms.form_options(g.db))


# ------------------------------------------------------------------ categories
@bp.get("/categories")
def categories():
    rows = g.db.execute(
        "SELECT c.*, (SELECT count(*) FROM items i WHERE i.category_id=c.id AND i.status='approved') n"
        " FROM categories c ORDER BY position, id"
    ).fetchall()
    return render_template("mod/categories.html", tab="categories", rows=rows)


@bp.post("/categories/<int:cat_id>/move")
def move_category(cat_id: int):
    con = g.db
    with db.tx(con):
        ids = [r[0] for r in con.execute("SELECT id FROM categories ORDER BY position, id")]
        if cat_id not in ids:
            abort(404)
        i = ids.index(cat_id)
        j = i - 1 if request.form.get("dir") == "up" else i + 1
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
            con.executemany("UPDATE categories SET position=? WHERE id=?", list(enumerate(ids)))
            db.log(con, "move category", None, f"{cat_id} {request.form.get('dir')}")
    return redirect(url_for("mod.categories"))


# ------------------------------------------------------------------ stats + log
@bp.get("/stats")
def stats_page():
    con = g.db
    days = request.args.get("days", 30, type=int)
    days = days if days in (1, 7, 30, 90, 365) else 30
    s = stats.summary(con, days)
    since = s["start"]

    def one(sql, *a):
        return con.execute(sql, a).fetchone()[0]

    numbers = {
        "voters": one("SELECT count(DISTINCT voter) FROM uses"),
        "new_voters": one("SELECT count(DISTINCT voter) FROM uses WHERE created_at >= ?", since),
        "uses": one("SELECT count(*) FROM uses"),
        "new_uses": one("SELECT count(*) FROM uses WHERE created_at >= ?", since),
        "suggested": one("SELECT count(*) FROM items WHERE submitted=1 AND created_at >= ?", since),
        "pending": one("SELECT count(*) FROM items WHERE status='pending'"),
        "reports": one("SELECT count(*) FROM reports WHERE status='open'"),
        "approved": one("SELECT count(*) FROM items WHERE status='approved'"),
    }
    tables = {
        "Votes in this period": con.execute(
            "SELECT i.name, count(*) n FROM uses u JOIN items i ON i.id=u.item_id WHERE u.created_at >= ?"
            " GROUP BY i.id ORDER BY n DESC, i.name LIMIT 15",
            (since,),
        ).fetchall(),
        "Most used services (all time)": con.execute(
            "SELECT name, uses_count FROM items WHERE status='approved' AND uses_count > 0"
            " ORDER BY uses_count DESC, name LIMIT 15"
        ).fetchall(),
        "Services per city": con.execute(
            "SELECT c.label, count(*) FROM item_cities ic JOIN cities c ON c.id=ic.city_id"
            " JOIN items i ON i.id=ic.item_id WHERE i.status='approved' GROUP BY c.id ORDER BY c.position"
        ).fetchall(),
        "Items by status": con.execute("SELECT status, count(*) FROM items GROUP BY 1 ORDER BY 2 DESC").fetchall(),
        "Failing links (consecutive failed checks)": con.execute(
            "SELECT name, link_fails FROM items WHERE link_fails > 0 AND status='approved' ORDER BY link_fails DESC"
        ).fetchall(),
    }
    peak = max([v for _, v, _ in s["series"]] + [1]) if days > 1 else max([n for _, n in s["hours"]] + [1])
    return render_template(
        "mod/stats.html", tab="stats", s=s, days=days, n=numbers, tables=tables, groups=stats.GROUPS, peak=peak
    )


@bp.get("/log")
def log():
    rows = g.db.execute(
        "SELECT l.*, i.name FROM mod_log l LEFT JOIN items i ON i.id=l.item_id ORDER BY l.id DESC LIMIT 300"
    ).fetchall()
    return render_template("mod/log.html", tab="log", rows=rows)
