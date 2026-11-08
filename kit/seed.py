"""Import seed.json (idempotent): python -m kit.seed [path]

Cities, universities and categories are upserted by slug/code. Items are matched by (url, name): two seeded
services share a URL (PubliBike in Zürich and Lausanne). New items come in approved + pinned; existing ones get
their content refreshed but keep their status, pin and uses, so a mod's archive or unpin survives a re-seed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import db, settings


def seed(con, data: dict) -> dict:
    added = updated = 0
    with db.tx(con):
        for pos, c in enumerate(data["cities"]):
            con.execute(
                "INSERT INTO cities (slug, label, position) VALUES (?, ?, ?)"
                " ON CONFLICT(slug) DO UPDATE SET label=excluded.label, position=excluded.position",
                (c["slug"], c["label"], pos),
            )
            city_id = con.execute("SELECT id FROM cities WHERE slug=?", (c["slug"],)).fetchone()[0]
            for code in c["universities"]:
                con.execute(
                    "INSERT INTO universities (code, label, city_id) VALUES (?, ?, ?)"
                    " ON CONFLICT(code) DO UPDATE SET city_id=excluded.city_id",
                    (code, code, city_id),
                )
        for c in data["categories"]:
            con.execute(
                "INSERT INTO categories (slug, label, short_label, subtitle, icon_svg_paths, position)"
                " VALUES (:slug, :label, :short_label, :subtitle, :icon_svg_paths, :position)"
                " ON CONFLICT(slug) DO UPDATE SET label=excluded.label, short_label=excluded.short_label,"
                " subtitle=excluded.subtitle, icon_svg_paths=excluded.icon_svg_paths",
                c,
            )
        cats = {r["slug"]: r["id"] for r in con.execute("SELECT id, slug FROM categories")}
        cities = {r["slug"]: r["id"] for r in con.execute("SELECT id, slug FROM cities")}
        for it in data["items"]:
            d = {
                "category_id": cats[it["category"]],
                "name": it["name"],
                "url": it["url"],
                "description": it["description"],
                "kind": it["kind"],
                "city_ids": [cities[c] for c in it["cities"]],
                "universities": [u for u in it["universities"] if u != "ALL"],
                "status": "approved",
                "pinned": 1,
                "approved_at": con.execute("SELECT datetime('now')").fetchone()[0],
            }
            row = con.execute("SELECT id FROM items WHERE url=? AND name=?", (it["url"], it["name"])).fetchone()
            db.save_item(con, row["id"] if row else None, d)
            updated += bool(row)
            added += not row
    return {"added": added, "updated": updated}


def main() -> None:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else settings.SEED_FILE
    con = db.connect()
    db.init(con)
    print(seed(con, json.loads(path.read_text())))


if __name__ == "__main__":
    main()
