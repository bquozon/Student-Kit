"""End-to-end in a real browser: a visitor suggests a service, the mod approves it, it shows and gets a vote.
Needs the dev packages (pip install -r requirements-dev.txt && playwright install chromium); skipped without them."""

import os
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
            "ADMIN_KEY": "e2e-secret-key-0123456789",
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

    def test_flow(self):
        visitor = self.browser.new_context().new_page()
        visitor.goto(self.base + "/suggest")
        visitor.fill("#name", "Mensa Roulette")
        visitor.fill("#url", "https://example.com/mensa")
        visitor.press("#url", "Tab")  # runs the duplicate/reachability check; wait so the layout settles
        expect(visitor.locator("#urlcheck")).not_to_contain_text("Checking", timeout=15000)
        visitor.select_option("#category", "campus")
        visitor.check('#unis input[value="ETH"]')
        visitor.check('input[name="kind"][value="App"]')
        visitor.fill("#description", "Picks a random mensa for lunch so your group stops arguing.")
        visitor.click("text=Send suggestion")
        expect(visitor.locator("text=A moderator will check it")).to_be_visible()

        mod = self.browser.new_context().new_page()
        mod.goto(self.base + "/mod")
        mod.fill("#key", "e2e-secret-key-0123456789")
        mod.click("text=Log in")
        expect(mod.locator("text=Logged in on this device")).to_be_visible()
        card = mod.locator("form.box", has_text="Mensa Roulette")
        card.locator("button[value=approve]").click()
        expect(mod.locator("text=The queue is empty")).to_be_visible()

        visitor.goto(self.base + "/zurich?u=ETH")
        new_card = visitor.locator("article.card", has_text="Mensa Roulette")
        expect(new_card).to_be_visible()
        expect(new_card.locator(".tag.new")).to_be_visible()
        button = new_card.locator("button.use")
        button.click()  # anonymous vote, no page reload
        expect(button).to_have_attribute("aria-pressed", "true")
        expect(button).to_contain_text("1")
        visitor.reload()
        expect(visitor.locator("article.card", has_text="Mensa Roulette").locator("button.use")).to_have_attribute(
            "aria-pressed", "true"
        )
        visitor.goto(self.base + "/zurich?u=ZHAW")
        expect(visitor.locator("article.card", has_text="Mensa Roulette")).to_be_hidden()


if __name__ == "__main__":
    unittest.main()
