"""Unit tests: ranking, the New badge, domain normalisation, safe redirects.
Run: python -m unittest discover tests"""

import unittest
from datetime import datetime, timezone

from kit.ranking import domain, is_new, pretty_url, rank, safe_next, valid_https, with_msg


def item(name, pinned=0, uses=0, approved="2026-11-01 10:00:00"):
    return {"name": name, "pinned": pinned, "uses_count": uses, "approved_at": approved}


class Ranking(unittest.TestCase):
    def test_pinned_first_by_name_then_uses_then_newest(self):
        items = [
            item("zeta", uses=5),
            item("Beta", pinned=1, uses=0),
            item("old", uses=9, approved="2026-01-01 00:00:00"),
            item("new", uses=9, approved="2026-10-01 00:00:00"),
            item("alpha", pinned=1, uses=100),
            item("none", uses=0, approved=None),
        ]
        self.assertEqual([i["name"] for i in rank(items)], ["alpha", "Beta", "new", "old", "zeta", "none"])

    def test_new_badge(self):
        now = datetime(2026, 11, 8, tzinfo=timezone.utc)
        self.assertTrue(is_new(item("a", uses=2, approved="2026-11-01 00:00:00"), now))
        self.assertFalse(is_new(item("a", uses=3, approved="2026-11-01 00:00:00"), now))  # 3 uses: not new
        self.assertFalse(is_new(item("a", uses=0, approved="2026-10-20 00:00:00"), now))  # older than 14 days
        self.assertFalse(is_new(item("a", pinned=1, approved="2026-11-07 00:00:00"), now))  # curated
        self.assertFalse(is_new(item("a", approved=None), now))


class Domains(unittest.TestCase):
    def test_normalize(self):
        for url in ("https://www.woko.ch/", "http://WOKO.ch/rooms", "woko.ch", " https://woko.ch "):
            self.assertEqual(domain(url), "woko.ch", url)
        self.assertEqual(domain("https://moodle.epfl.ch/x"), "moodle.epfl.ch")
        self.assertNotEqual(domain("https://woko.ch.evil.com"), "woko.ch")

    def test_pretty_and_https(self):
        self.assertEqual(pretty_url("https://www.sbb.ch/"), "sbb.ch")
        self.assertTrue(valid_https("https://www.asvz.ch"))
        for bad in ("http://asvz.ch", "https://localhost", "javascript:alert(1)", "https://user@asvz.ch", "asvz.ch"):
            self.assertFalse(valid_https(bad), bad)


class Redirects(unittest.TestCase):
    def test_safe_next(self):
        self.assertEqual(safe_next("/zurich?u=ETH"), "/zurich?u=ETH")
        for bad in ("https://evil.com", "//evil.com", "/\\evil.com", "", None, "zurich"):
            self.assertEqual(safe_next(bad), "/", bad)
        self.assertEqual(with_msg("/zurich?u=ETH&m=old", "sent"), "/zurich?u=ETH&m=sent")


if __name__ == "__main__":
    unittest.main()
