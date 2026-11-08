"""App tests with Flask's test client and a throwaway database: no accounts, anonymous votes, the open suggest form,
the secret mod link, moderation and the link checker. Run: python -m unittest discover tests"""

import json
import os
import tempfile
import unittest
from pathlib import Path

TMP = tempfile.mkdtemp()
KEY = "test-admin-key-0123456789"
os.environ.update(DATA_DIR=TMP, ADMIN_KEY=KEY, ADMIN_EMAIL="", SMTP_HOST="", SITE_URL="http://localhost")

import kit.server  # noqa: E402
from kit import auth, db, jobs, linkcheck, seed  # noqa: E402
from kit.server import app  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BASE = {"city": "zurich", "category": "daily", "kind": "Web", "description": "A useful thing for students."}


class AppTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        con = db.connect()
        db.init(con)
        seed.seed(con, json.loads((ROOT / "data" / "seed.json").read_text()))
        cls.con = con
        kit.server.reachable = lambda url, timeout: True  # tests never touch the network

    def setUp(self):
        auth.limiter.hits.clear()
        self.c = self.client()

    @staticmethod
    def client():
        c = app.test_client()
        c.get("/zurich")  # sets the csrf cookie
        return c

    def post(self, path, c=None, **data):
        c = c or self.c
        return c.post(path, data={"csrf": c.get_cookie("csrf").value, **data})

    def vote(self, item_id, c=None):
        c = c or self.c
        return c.post(
            f"/use/{item_id}", data={"csrf": c.get_cookie("csrf").value}, headers={"Accept": "application/json"}
        )

    def item_id(self, name):
        return self.con.execute("SELECT id FROM items WHERE name=?", (name,)).fetchone()[0]

    def mod(self):
        m = self.client()
        r = m.get(f"/mod/key/{KEY}")
        self.assertEqual(r.location, "/mod?m=mod")
        return m

    # ------------------------------------------------------------------ public
    def test_public_page_and_redirect(self):
        self.assertIn("/zurich", self.c.get("/").location)
        r = self.c.get("/lausanne")
        self.assertIn(b"Lausanne Student <em>Kit</em>", r.data)
        self.assertRegex(r.data, rb">(3[67]) of \1<")  # 36 seeded (+1 if the mod test ran first)
        self.assertIn("/lausanne", self.c.get("/").location)  # remembers the last city
        self.assertEqual(self.c.get("/atlantis").status_code, 404)
        self.assertIn("default-src 'self'", r.headers["Content-Security-Policy"])
        for gone in ("/login", "/mine", "/login/verify"):
            self.assertEqual(self.c.get(gone).status_code, 404, gone)

    def test_csrf_required(self):
        self.assertEqual(self.c.post("/suggest", data=BASE).status_code, 400)

    def test_anonymous_vote_toggles_once_per_browser(self):
        i = self.item_id("WOKO")
        self.assertEqual(self.vote(i).json, {"used": True, "count": 1})
        other = self.client()
        self.assertEqual(self.vote(i, other).json, {"used": True, "count": 2})  # another browser
        self.assertEqual(self.vote(i).json, {"used": False, "count": 1})  # the first one undoes
        self.assertIn(b'aria-pressed="true"', other.get("/zurich").data)
        self.assertEqual(self.vote(999999).status_code, 404)

    def test_vote_rate_limit_per_ip(self):
        i = self.item_id("JUWO")
        for _ in range(60):
            self.vote(i)
        self.assertEqual(self.vote(i).status_code, 429)

    def test_open_suggest_form(self):
        r = self.post("/suggest", name="X", url="http://insecure.ch", **BASE)
        self.assertEqual(r.status_code, 400)
        self.assertIn(b"https://", r.data)
        r = self.post("/suggest", name="Lausanne only", url="https://a.ch", universities="EPFL", **BASE)
        self.assertIn(b"Universities must belong", r.data)
        before = self.con.execute("SELECT count(*) FROM items").fetchone()[0]
        r = self.post("/suggest", name="Bot", url="https://bot.ch", website="spam", **BASE)  # honeypot
        self.assertIn("m=sent", r.location)
        self.assertEqual(self.con.execute("SELECT count(*) FROM items").fetchone()[0], before)
        for n in range(5):
            r = self.post("/suggest", name=f"Thing {n}", url=f"https://t{n}.ch", **BASE)
            self.assertIn("m=sent", r.location)
        r = self.post("/suggest", name="One too many", url="https://t9.ch", **BASE)
        self.assertIn(b"wait an hour", r.data)

    # ------------------------------------------------------------------ mod
    def test_mod_panel_is_hidden_without_the_key(self):
        self.assertEqual(self.c.get("/mod").status_code, 404)
        self.assertEqual(self.c.get("/mod/key/wrong").status_code, 404)
        self.assertEqual(self.c.get("/mod/stats").status_code, 404)
        self.assertNotIn(b"Moderation", self.c.get("/zurich").data)

    def test_mod_key_guessing_is_rate_limited(self):
        for _ in range(10):
            self.c.get("/mod/key/guess")
        self.assertEqual(self.c.get(f"/mod/key/{KEY}").status_code, 404)  # even the right key, for 15 min

    def test_mod_approve_and_reject(self):
        base = {**BASE, "city": "lausanne", "category": "campus", "kind": "App"}
        self.post("/suggest", name="Lake Map", url="https://lakemap.ch", universities="EPFL", **base)
        self.post("/suggest", name="Spam", url="https://spam.ch", **base)
        self.assertNotIn(b"Lake Map", self.c.get("/lausanne").data)

        m = self.mod()
        self.assertIn(b"Lake Map", m.get("/mod").data)
        lake, spam = self.item_id("Lake Map"), self.item_id("Spam")
        self.assertEqual(self.post(f"/mod/item/{spam}", m, action="reject", reason="").status_code, 400)
        self.post(f"/mod/item/{spam}", m, action="reject", reason="Not a student service")
        self.post(
            f"/mod/item/{lake}",
            m,
            action="approve",
            name="Lake Map",
            url="https://lakemap.ch",
            category="campus",
            kind="App",
            cities="lausanne",
            universities="EPFL",
            description="A useful thing for students.",
        )
        page = self.c.get("/lausanne").data
        self.assertIn(b"Lake Map", page)
        self.assertIn(b">New<", page)
        self.assertNotIn(b">Spam<", page)
        actions = [r[0] for r in self.con.execute("SELECT action FROM mod_log")]
        self.assertIn("approve", actions)
        self.assertIn("reject", actions)
        self.assertEqual(m.get("/mod/stats?days=7").status_code, 200)
        self.assertEqual(m.get("/mod/log").status_code, 200)
        self.post("/mod/logout", m)
        self.assertEqual(m.get("/mod").status_code, 404)

    def test_link_check_reports_after_two_failures(self):
        dead = self.item_id("Splitwise")
        for _ in range(3):
            jobs.linkcheck(self.con, check=lambda url, t: "splitwise" not in url)
        rows = self.con.execute("SELECT type FROM reports WHERE item_id=?", (dead,)).fetchall()
        self.assertEqual([r[0] for r in rows], ["dead_link"])  # one report, not one per run
        status = self.con.execute("SELECT status FROM items WHERE id=?", (dead,)).fetchone()[0]
        self.assertEqual(status, "approved")  # never auto-archived
        self.assertIn(b"Splitwise", self.mod().get("/mod/reports").data)

    def test_private_urls_are_never_fetched(self):
        for url in ("http://127.0.0.1/", "http://10.0.0.1/", "http://localhost:8800/", "file:///etc/passwd"):
            self.assertFalse(linkcheck.public(url), url)


if __name__ == "__main__":
    unittest.main()
