"""End-to-end in a real browser: a student suggests a service, a mod approves it, it shows on the public page.
Needs the dev packages (pip install -r requirements-dev.txt && playwright install chromium); skipped without them."""

import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    sync_playwright = None

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipIf(sync_playwright is None, "playwright not installed")
class SubmitApproveVisible(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = Path(tempfile.mkdtemp())
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        cls.base = f"http://127.0.0.1:{port}"
        env = {
            **os.environ,
            "DATA_DIR": str(cls.data),
            "SITE_URL": cls.base,
            "SMTP_HOST": "",
            "ADMIN_EMAIL": "mod@student.ethz.ch",
            "SEED_FILE": str(ROOT / "data" / "seed.json"),
        }
        subprocess.run([sys.executable, "-m", "kit.seed"], cwd=ROOT, env=env, check=True, capture_output=True)
        cls.server = subprocess.Popen(
            [sys.executable, "-m", "kit.server", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(50):
            try:
                urllib.request.urlopen(cls.base + "/health", timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()
        cls.server.terminate()
        cls.server.wait(5)

    def login(self, email):
        page = self.browser.new_context().new_page()
        page.goto(self.base + "/login")
        page.fill("#email", email)
        page.click("text=Send me a login link")
        expect(page.locator("text=We sent a login link")).to_be_visible()
        link = re.findall(r"http\S+/login/verify\?\S+", (self.data / "outbox.txt").read_text())[-1]
        page.goto(link)
        page.click("text=Log me in")
        expect(page.locator("text=You're logged in.")).to_be_visible()
        return page

    def test_flow(self):
        student = self.login("lea@student.ethz.ch")
        student.goto(self.base + "/suggest")
        student.fill("#name", "Mensa Roulette")
        student.fill("#url", "https://example.com/mensa")
        student.press("#url", "Tab")  # runs the duplicate/reachability check; wait so the layout settles
        expect(student.locator("#urlcheck")).not_to_contain_text("Checking", timeout=15000)
        student.select_option("#category", "campus")
        student.check('#unis input[value="ETH"]')
        student.check('input[name="kind"][value="App"]')
        student.fill("#description", "Picks a random mensa for lunch so your group stops arguing.")
        student.click("text=Send suggestion")
        expect(student.locator("text=A moderator will check it")).to_be_visible()
        expect(student.locator("td", has_text="Mensa Roulette")).to_be_visible()

        mod = self.login("mod@student.ethz.ch")
        mod.goto(self.base + "/mod")
        card = mod.locator("form.box", has_text="Mensa Roulette")
        card.locator("button[value=approve]").click()
        expect(mod.locator("text=The queue is empty")).to_be_visible()

        visitor = self.browser.new_context().new_page()
        visitor.goto(self.base + "/zurich?u=ETH")
        new_card = visitor.locator("article.card", has_text="Mensa Roulette")
        expect(new_card).to_be_visible()
        expect(new_card.locator(".tag.new")).to_be_visible()
        visitor.goto(self.base + "/zurich?u=ZHAW")
        expect(visitor.locator("article.card", has_text="Mensa Roulette")).to_be_hidden()

        # the student marks it, without a page reload
        student.goto(self.base + "/zurich")
        button = student.locator("article.card", has_text="Mensa Roulette").locator("button.use")
        button.click()
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(button).to_contain_text("1")
        self.assertIn("approved", json.dumps((self.data / "outbox.txt").read_text()))


if __name__ == "__main__":
    unittest.main()
