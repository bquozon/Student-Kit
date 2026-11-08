"""Scheduled jobs, run by systemd timers on the VPS through `docker exec` (deploy/systemd/):

python -m kit.jobs digest      daily: email the mods if the queue or the reports aren't empty
python -m kit.jobs linkcheck   weekly: check every approved URL; 2 failures in a row → a dead_link report
python -m kit.jobs backup      nightly: copy the SQLite file to DATA_DIR/backups/ (keeps 14)
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date

from . import db, mail, settings
from .linkcheck import reachable

FAILS_FOR_REPORT = 2
KEEP_BACKUPS = 14


def digest(con) -> str:
    pending = con.execute("SELECT count(*) FROM items WHERE status='pending'").fetchone()[0]
    reports = con.execute("SELECT count(DISTINCT item_id) FROM reports WHERE status='open'").fetchone()[0]
    mods = [r[0] for r in con.execute("SELECT email FROM users WHERE is_mod=1")]
    if not (pending or reports) or not mods:
        return f"nothing to send ({pending} pending, {reports} reported)"
    mail.send(
        mods,
        f"Swiss Student Kit: {pending} to review, {reports} reported",
        f"{pending} suggestion(s) waiting: {settings.SITE_URL}/mod\n"
        f"{reports} item(s) with open reports: {settings.SITE_URL}/mod/reports\n",
    )
    return f"sent to {len(mods)} mod(s)"


def linkcheck(con, check=reachable) -> str:
    items = con.execute("SELECT id, url, link_fails FROM items WHERE status='approved'").fetchall()
    with ThreadPoolExecutor(8) as pool:
        results = list(pool.map(lambda r: check(r["url"], 10), items))
    reported = 0
    with db.tx(con):
        for r, ok in zip(items, results, strict=True):
            fails = 0 if ok else r["link_fails"] + 1
            con.execute("UPDATE items SET link_fails=?, link_checked_at=datetime('now') WHERE id=?", (fails, r["id"]))
            # never auto-archive: a mod decides. One open automatic report per item is enough.
            if (
                fails >= FAILS_FOR_REPORT
                and not con.execute(
                    "SELECT 1 FROM reports WHERE item_id=? AND type='dead_link' AND status='open' AND user_id IS NULL",
                    (r["id"],),
                ).fetchone()
            ):
                con.execute(
                    "INSERT INTO reports (item_id, type, message) VALUES (?, 'dead_link', ?)",
                    (r["id"], f"Automatic check: the link failed {fails} weeks in a row."),
                )
                reported += 1
    return f"checked {len(items)}, {results.count(False)} failing, {reported} new report(s)"


def backup(con) -> str:
    folder = settings.DATA_DIR / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / f"kit-{date.today().isoformat()}.sqlite"
    out = db.connect(dest)
    con.backup(out)  # consistent copy even while the site writes
    out.close()
    for old in sorted(folder.glob("kit-*.sqlite"))[:-KEEP_BACKUPS]:
        old.unlink()
    return f"wrote {dest}"


def main() -> None:
    jobs = {"digest": digest, "linkcheck": linkcheck, "backup": backup}
    if len(sys.argv) != 2 or sys.argv[1] not in jobs:
        raise SystemExit("usage: python -m kit.jobs digest|linkcheck|backup")
    con = db.connect()
    db.init(con)
    print(f"{sys.argv[1]}: {jobs[sys.argv[1]](con)}", flush=True)


if __name__ == "__main__":
    main()
