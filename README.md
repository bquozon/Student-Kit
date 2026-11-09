# Student-Kit

A community-curated directory of the most useful apps and websites for students in Swiss university cities. v1 has Zürich (ETH, UZH, ZHAW) and Lausanne (EPFL, UNIL).

**There are no accounts.** Anyone can browse, mark the services they use ("I use this", one vote per browser) and suggest a new one. Only the owner moderates.

It uses the same stack and deploy setup as [polyuni.ch](https://polyuni.ch): **Flask served by waitress** (one process), Jinja templates rendered on the server, **SQLite** (Python's built-in `sqlite3`, parameterized queries only), and one Docker container behind the VPS's existing Caddy container.

## Layout

| Path | What it does |
|---|---|
| `kit/server.py` | Public routes: city pages, the anonymous vote, the suggest form, CSRF and security headers |
| `kit/mod.py` | `/mod`: the login form, queue, reports, all items, categories, stats, log |
| `kit/auth.py` | The mod and voter cookies, in-memory rate limits |
| `kit/ranking.py` | Pure helpers: card order, "New" badge, domain normalisation, safe redirects |
| `kit/forms.py` | Item form validation shared by the suggest page and the mod edits |
| `kit/stats.py` | Cookie-free visit counters (same approach as polyuni's `stats.py`) |
| `kit/jobs.py` | `digest`, `linkcheck` and `backup`, run by systemd timers |
| `kit/linkcheck.py` | "Is this URL alive?" check (public IPs only) |
| `kit/schema.sql` | The data model |
| `kit/seed.py` | Seed import |
| `kit/templates/`, `kit/static/` | HTML, `style.css` (from the design reference), one small `app.js`, self-hosted fonts |
| `data/seed.json` | 2 cities, 8 categories, 77 items |
| `deploy/` | `install.sh`, `compose.yml`, systemd timers |
| `tests/` | Unit and app tests, plus a Playwright end-to-end test |

## Local dev

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/playwright install chromium     # only for the end-to-end test
cp .env.example .env                       # set ADMIN_KEY
.venv/bin/python -m kit.seed               # creates data/kit.sqlite and imports seed.json
.venv/bin/python -m kit.server             # http://localhost:8800, mod: http://localhost:8800/mod (log in with ADMIN_KEY)
```

Tests and lint (the same settings as polyuni: ruff, 120 columns):

```bash
.venv/bin/python -m unittest discover tests && ruff check . && ruff format --check .
```

## Settings (`.env`, or `/etc/student-kit.env` on the VPS)

| Variable | Meaning |
|---|---|
| `SITE_URL` | Public address. `https://` turns on Secure cookies and HSTS |
| `ADMIN_KEY` | The mod password you type at `/mod`. If it's empty, the mod panel is off. The installer generates it |
| `ADMIN_EMAIL`, `SMTP_*`, `MAIL_FROM` | **Optional.** A daily email when suggestions or link reports are waiting. For kMail: `mail.infomaniak.com`, port 465, your full address and password (an app password if you use two-factor login) |
| `DATA_DIR` | Where `kit.sqlite`, `backups/` and `outbox.txt` live. The container uses `/data` |

## How it works

- **Moderation:**
  - Open `https://<domain>/mod` and log in with `ADMIN_KEY`. It sets a 90-day cookie on that device. Ten wrong tries lock logins from that IP for 15 minutes.
  - Anyone else gets a 404 on every `/mod` page. Wrong keys are rate-limited (10 per IP per 15 min).
  - The "Log out" button in the top bar ends the session on that device.
- **Votes:**
  - The first vote gives the browser a random id in a cookie, and only its SHA-256 is stored. Voting again on the same item removes the vote.
  - Each IP gets 60 votes per hour. Someone who clears cookies can vote again; that's the price of having no accounts.
- **Suggestions:**
  - Open to anyone. A hidden honeypot field catches bots, each IP gets 5 suggestions per hour, and the form closes while 100 are waiting.
  - The form warns about existing items on the same domain and about URLs that don't answer.
  - Everything waits in `/mod` until you approve it.
- **Reports** come only from the weekly link check: after 2 failures in a row it opens a `dead_link` report in `/mod/reports`. It never archives anything.
- **Ranking:**
  - Pinned items come first (by name), then the rest by votes, newest first on ties.
  - "New" means fewer than 3 votes and approved in the last 14 days. Pinned (curated) items never get the badge.
- **Seed:** `python -m kit.seed [file]` (on the VPS: `docker exec student-kit python -m kit.seed`). It's idempotent:
  - Items are matched by URL + name, because two seeded items share `publibike.ch`.
  - New items come in approved and pinned.
  - On a re-seed, existing items get their text updated, but your archive, unpin and the votes are never touched.
- **Add a city:** add it and its universities to `seed.json`, then re-seed. No code change is needed.

## Deploy (VPS, next to polyuni.ch)

1. Point the domain's DNS at the VPS.
2. On the VPS:

   ```bash
   git clone https://github.com/bquozon/Student-Kit.git ~/Student-Kit
   cd ~/Student-Kit && sudo bash deploy/install.sh kit.example.ch
   ```

3. The installer prints your mod link at the end. Open it once on each device.

`install.sh` copies the code to `/opt/student-kit` and builds the `student-kit` image. It then starts the container on the network of the Caddy container that holds ports 80/443 (solidtime's, the same one polyuni.ch uses) and runs the seed. It also installs three systemd timers and adds a single site block to that Caddyfile. Before reloading, it validates the Caddyfile, and if Caddy rejects the change it restores the backup. Re-running it keeps `data/`, the settings file and your `ADMIN_KEY`.

To update: `cd ~/Student-Kit && git pull && sudo bash deploy/install.sh kit.example.ch`

## Scheduled jobs

| Timer | When | What |
|---|---|---|
| `kit-digest.timer` | daily 08:00 | Emails `ADMIN_EMAIL` if suggestions or reports are waiting (only if SMTP is set) |
| `kit-linkcheck.timer` | Mondays 04:00 | HEAD then GET (10 s timeout) on every approved URL. After 2 failures in a row, it opens a `dead_link` report |
| `kit-backup.timer` | nightly 03:15 | Consistent copy of the SQLite file to `/opt/student-kit/data/backups/` (keeps 14) |

Useful commands: `systemctl list-timers 'kit-*'`, `journalctl -u 'kit-job@*' -n 50`, `docker logs -f student-kit`.
Run a job now: `sudo systemctl start kit-job@linkcheck`.

**Backups:** the nightly copies stay on the same disk. Also copy them off the server now and then, for example:

```bash
rsync -a vps:/opt/student-kit/data/backups/ ~/student-kit-backups/
```

To restore, stop the container, copy a backup over `data/kit.sqlite`, then start the container again.

## Privacy and security

- No personal data: no emails, no IPs, no accounts.
- Stats are anonymous daily counters, with a visitor hash salt that's thrown away daily.
- Votes are linked only to a hash of a random cookie id.
- Every POST carries a CSRF token (double submit). Jinja escapes all output, and a strict CSP allows no inline scripts and no third-party requests.
- The suggest form's URL check and the link checker only fetch public IPs, including after redirects.
