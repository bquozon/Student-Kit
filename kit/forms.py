"""Validation of an item form (suggest page and mod edits) and loading items for display."""

from __future__ import annotations

from . import settings
from .ranking import valid_https


def clean_item(con, form, city_slugs: list[str]) -> tuple[dict, list[str]]:
    """Form fields → the dict db.save_item takes, plus a list of errors (empty when valid)."""
    errors = []
    name = " ".join(form.get("name", "").split())
    url = form.get("url", "").strip()
    desc = " ".join(form.get("description", "").split())
    kind = form.get("kind", "")
    cat = con.execute("SELECT id FROM categories WHERE slug=?", (form.get("category", ""),)).fetchone()
    q = ",".join("?" * len(city_slugs))
    cities = (
        [r["id"] for r in con.execute(f"SELECT id FROM cities WHERE slug IN ({q})", city_slugs)] if city_slugs else []
    )
    unis = [u for u in form.getlist("universities") if u]
    allowed = (
        {
            r["code"]
            for r in con.execute(
                f"SELECT code FROM universities WHERE city_id IN ({','.join('?' * len(cities))})", cities
            )
        }
        if cities
        else set()
    )
    if "ALL" in unis:
        unis = []
    if not 2 <= len(name) <= 80:
        errors.append("Name: 2 to 80 characters.")
    if not valid_https(url):
        errors.append("URL: a full https:// address, e.g. https://www.woko.ch")
    if not 10 <= len(desc) <= 140:
        errors.append("Description: 10 to 140 characters.")
    if kind not in settings.KINDS:
        errors.append("Pick a kind.")
    if not cat:
        errors.append("Pick a category.")
    if not cities:
        errors.append("Pick a city.")
    if set(unis) - allowed:
        errors.append("Universities must belong to the chosen city.")
    d = {
        "name": name,
        "url": url,
        "description": desc,
        "kind": kind,
        "category_id": cat["id"] if cat else None,
        "city_ids": cities,
        "universities": sorted(set(unis)),
        "note": form.get("note", "").strip()[:500] or None,
    }
    return d, errors


def item_detail(con, item_id: int):
    """One item with its city slugs and university codes ([] = ALL), for the edit forms."""
    row = con.execute(
        "SELECT i.*, c.slug category FROM items i JOIN categories c ON c.id=i.category_id WHERE i.id=?",
        (item_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["cities"] = [
        r[0]
        for r in con.execute(
            "SELECT c.slug FROM item_cities ic JOIN cities c ON c.id=ic.city_id WHERE ic.item_id=? ORDER BY c.position",
            (item_id,),
        )
    ]
    d["universities"] = [
        r[0]
        for r in con.execute(
            "SELECT university_code FROM item_universities WHERE item_id=? AND university_code IS NOT NULL ORDER BY 1",
            (item_id,),
        )
    ]
    return d


def form_options(con) -> dict:
    """Lists the item forms need: cities with their universities, categories, kinds."""
    cities = [dict(r) for r in con.execute("SELECT * FROM cities ORDER BY position")]
    for c in cities:
        c["unis"] = [
            r[0] for r in con.execute("SELECT code FROM universities WHERE city_id=? ORDER BY rowid", (c["id"],))
        ]
    cats = con.execute("SELECT slug, label FROM categories ORDER BY position").fetchall()
    return {"cities": cities, "categories": cats, "kinds": settings.KINDS}
