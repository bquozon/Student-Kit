"""App tests with Flask's test client and a throwaway database: login, CSRF, "I use this", suggest limits,
moderation, reports and the link checker. Run: python -m unittest discover tests"""

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ.update(DATA_DIR=TMP, ADMIN_EMAIL="mod@student.ethz.ch", SMTP_HOST="", SITE_URL="http://localhost")

from kit import auth, db, jobs, linkcheck, seed, settings  # noqa: E402
from kit.server import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def outbox_links() -> list[str]:
    p = Path(TMP) / "outbox.txt"
    return re.findall(r"/login/verify\?\S+", p.read_text()) if p.exists() else []


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        con = db.connect()
        db.init(con)
        seed.seed(con, json.loads((ROOT / "data" / "seed.json").read_text()))
        cls.con = con

    def setUp(self):
        auth.limiter.hits.clear()
        self.c = app.test_client()
        self.c.get("/zurich")  # sets the csrf cookie

    def csrf(self):
        return self.c.get_cookie("csrf").value

    def post(self, path, **data):
        return self.c.post(path, data={"csrf": self.csrf(), **data})

    def login(self, email):
        r = self.post("/login", email=email, next="/zurich")
        self.assertIn(b"We sent a login link", r.data)
        link = outbox_links()[-1]
        token = re.search(r"t=([^&\s]+)", link).group(1)
        r = self.post("/login/verify", t=token, next="/zurich")
        self.assertEqual(r.status_code, 302)
        return token

    def item_id(self, name):
        return self.con.execute("SELECT id FROM items WHERE name=?", (name,)).fetchone()[0]

    # ------------------------------------------------------------------ tests
    def test_public_page_and_redirect(self):
        self.assertIn("/zurich", self.c.get("/").location)
        r = self.c.get("/lausanne")
        self.assertIn(b"Lausanne Student <em>Kit</em>", r.data)
        self.assertRegex(r.data, rb">(3[67]) of \1<")  # 36 seeded (+1 if the mod test ran first)
        self.assertIn("/lausanne", self.c.get("/").location)  # remembers the last city
        self.assertEqual(self.c.get("/atlantis").status_code, 404)
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])

    def test_csrf_required(self):
        r = self.c.post("/login", data={"email": "a@ethz.ch"})
        self.assertEqual(r.status_code, 400)

    def test_login_rejects_other_domains_and_tokens_are_single_use(self):
        r = self.post("/login", email="someone@gmail.com")
        self.assertIn(b"university email", r.data)
        token = self.login("anna@student.ethz.ch")
        self.assertIn(b"Log out", self.c.get("/zurich").data)
        other = app.test_client()
        other.get("/zurich")
        r = other.post("/login/verify", data={"csrf": other.get_cookie("csrf").value, "t": token})
        self.assertEqual(r.status_code, 400)  # already used

    def test_login_rate_limit(self):
        for _ in range(3):
            self.assertIn(b"We sent", self.post("/login", email="rl@uzh.ch").data)
        self.assertIn(b"Too many", self.post("/login", email="rl@uzh.ch").data)

    def test_use_toggle_keeps_count(self):
        i = self.item_id("WOKO")
        r = self.c.post(f"/use/{i}", data={"csrf": self.csrf()}, headers={"Accept": "application/json"})
        self.assertEqual(r.status_code, 401)
        self.login("ben@uzh.ch")
        r = self.c.post(f"/use/{i}", data={"csrf": self.csrf()}, headers={"Accept": "application/json"})
        self.assertEqual(r.json, {"used": True, "count": 1})
        r = self.c.post(f"/use/{i}", data={"csrf": self.csrf()}, headers={"Accept": "application/json"})
        self.assertEqual(r.json, {"used": False, "count": 0})

    def test_suggest_validation_and_pending_limit(self):
        self.login("cleo@zhaw.ch")
        base = {"city": "zurich", "category": "daily", "kind": "Web", "description": "A useful thing for students."}
        r = self.post("/suggest", name="X", url="http://insecure.ch", **base)
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"https://", r.data)
        r = self.post("/suggest", name="Lausanne only", url="https://a.ch", universities="EPFL", **base)
        self.assertIn(b"Universities must belong", r.data)
        linkcheck_reachable = linkcheck.reachable
        try:
            import kit.server

            kit.server.reachable = lambda url, timeout: True
            for n in range(settings.MAX_PENDING):
                self.assertEqual(self.post("/suggest", name=f"Thing {n}", url=f"https://t{n}.ch", **base).status_code, 302)
            r = self.post("/suggest", name="One too many", url="https://t9.ch", **base)
            self.assertIn(b"already have 5", r.data)
        finally:
            kit.server.reachable = linkcheck_reachable
        self.assertIn(b"Waiting", self.c.get("/mine").data)

    def test_mod_approve_and_reject(self):
        import kit.server

        kit.server.reachable = lambda url, timeout: True
        self.login("dora@epfl.ch")
        base = {"city": "lausanne", "category": "campus", "kind": "App", "description": "Lake swimming spots map."}
        self.post("/suggest", name="Lake Map", url="https://lakemap.ch", universities="EPFL", **base)
        self.post("/suggest", name="Spam", url="https://spam.ch", **base)
        self.assertNotIn(b"Lake Map", self.c.get("/lausanne").data)
        self.assertEqual(self.c.get("/mod").status_code, 404)  # not a mod

        mod = app.test_client()
        mod.get("/zurich")
        self.c, me = mod, self.c
        self.login("mod@student.ethz.ch")
        self.assertIn(b"Lake Map", self.c.get("/mod").data)
        lake, spam = self.item_id("Lake Map"), self.item_id("Spam")
        r = self.post(f"/mod/item/{spam}", action="reject", reason="")
        self.assertEqual(r.status_code, 400)  # reason required
        self.post(f"/mod/item/{spam}", action="reject", reason="Not a student service")
        self.post(
            f"/mod/item/{lake}", action="approve", name="Lake Map", url="https://lakemap.ch", category="campus",
            kind="App", cities="lausanne", universities="EPFL", description="Lake swimming spots map.",
        )
        page = me.get("/lausanne").data
        self.assertIn(b"Lake Map", page)
        self.assertIn(b">New<", page)
        self.assertIn(b"Not a student service", me.get("/mine").data)
        self.assertIn("Lake Map", (Path(TMP) / "outbox.txt").read_text())
        actions = [r[0] for r in self.con.execute("SELECT action FROM mod_log")]
        self.assertIn("approve", actions)
        self.assertIn("reject", actions)
        self.assertEqual(self.c.get("/mod/stats?days=7").status_code, 200)

    def test_report_and_link_check(self):
        self.login("eli@uzh.ch")
        i = self.item_id("Ricardo")
        r = self.post(f"/report/{i}", type="wrong_info", back="/zurich")
        self.assertIn("m=reported", r.location)
        dead = self.item_id("Splitwise")
        for _ in range(2):
            jobs.linkcheck(self.con, check=lambda url, t: "splitwise" not in url)
        rows = self.con.execute("SELECT type, user_id FROM reports WHERE item_id=?", (dead,)).fetchall()
        self.assertEqual([(r["type"], r["user_id"]) for r in rows], [("dead_link", None)])
        jobs.linkcheck(self.con, check=lambda url, t: "splitwise" not in url)  # no duplicate report
        self.assertEqual(self.con.execute("SELECT count(*) FROM reports WHERE item_id=?", (dead,)).fetchone()[0], 1)
        status = self.con.execute("SELECT status FROM items WHERE id=?", (dead,)).fetchone()[0]
        self.assertEqual(status, "approved")  # never auto-archived

    def test_private_urls_are_never_fetched(self):
        for url in ("http://127.0.0.1/", "http://10.0.0.1/", "http://localhost:8800/", "file:///etc/passwd"):
            self.assertFalse(linkcheck.public(url), url)


if __name__ == "__main__":
    unittest.main()
