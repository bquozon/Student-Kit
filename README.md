# Student-Kit

A community-curated directory of the most useful apps and websites for students in Swiss university cities. v1 has Zürich (ETH, UZH, ZHAW) and Lausanne (EPFL, UNIL). Students suggest services, moderators approve them, and verified students mark the ones they use.

It uses the same stack and deploy setup as [polyuni.ch](https://polyuni.ch): **Flask served by waitress** (one process), Jinja templates rendered on the server, **SQLite** (Python's built-in `sqlite3`, parameterized queries only), and one Docker container behind the VPS's existing Caddy container.

## Layout

| Path | What it does |
|---|---|
| `kit/server.py` | Public routes, login, CSRF, security headers (CSP, HSTS) |
| `kit/mod.py` | `/mod`: queue, reports, all items, categories, stats, log |
| `kit/auth.py` | Magic-link login, sessions, in-memory rate limits |
| `kit/ranking.py` | Pure helpers: card order, "New" badge, domain normalisation, email → university |
| `kit/forms.py` | Item form validation shared by the suggest page and the mod edits |
| `kit/stats.py` | Cookie-free visit counters (same approach as polyuni's `stats.py`) |
| `kit/jobs.py` | `digest`, `linkcheck`, `backup`, run by systemd timers |
| `kit/linkcheck.py` | "Is this URL alive?" check (public IPs only) |
| `kit/schema.sql` | The data model |
| `kit/seed.py`, `kit/make_mod.py` | Seed import, moderator CLI |
| `kit/templates/`, `kit/static/` | HTML, `style.css` (from the design reference), one small `app.js`, self-hosted fonts |
| `data/seed.json` | 2 cities, 8 categories, 77 items, plus the university email domains |
| `deploy/` | `install.sh`, `compose.yml`, systemd timers |
| `tests/` | Unit and app tests, plus a Playwright end-to-end test |

## Local dev

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/playwright install chromium        # only for the end-to-end test
cp .env.example .env                          # set ADMIN_EMAIL to your university email
.venv/bin/python -m kit.seed                  # creates data/kit.sqlite, imports seed.json, makes ADMIN_EMAIL a mod
.venv/bin/python -m kit.server                # http://localhost:8800
```

If `SMTP_HOST` is empty, emails aren't sent. They're appended to `data/outbox.txt`, so you can copy your login link from there.

Tests and lint (the same settings as polyuni: ruff, 120 columns):

```bash
.venv/bin/python -m unittest discover tests && ruff check . && ruff format --check .
```

## Settings (`.env`, or `/etc/student-kit.env` on the VPS)

| Variable | Meaning |
|---|---|
| `SITE_URL` | Public address. `https://` turns on Secure cookies and HSTS |
| `ADMIN_EMAIL` | Gets moderator rights at every seed run. It must be a university email |
| `SMTP_HOST` `SMTP_PORT` `SMTP_USER` `SMTP_PASS` `MAIL_FROM` | Outgoing mail. Port 465 uses SSL, any other port uses STARTTLS |
| `DATA_DIR` | Where `kit.sqlite`, `backups/` and `outbox.txt` live. The container uses `/data` |

## Data

- **Seed:** `python -m kit.seed [file]` (on the VPS: `docker exec student-kit python -m kit.seed`). It's idempotent:
  - Cities, universities and categories are upserted by slug or code.
  - Items are matched by URL + name, because two seeded items share `publibike.ch`.
  - New items come in approved and pinned. On a re-seed, existing items get their text updated, but a mod's archive, unpin or the "I use this" counts are never touched.
- **Add a city:** add it and its universities (with `email_domains`) to `seed.json`, then re-seed. No code change is needed.
- **Make a moderator:** `python -m kit.make_mod someone@student.ethz.ch`. On the VPS, run it through `docker exec student-kit …`.
- **Ranking:**
  - Pinned items come first (by name), then the rest by uses, newest first on ties.
  - "New" means fewer than 3 uses and approved in the last 14 days. Pinned (curated) items never get the badge, because otherwise every seeded item would show it for the first two weeks.

## Deploy (VPS, next to polyuni.ch)

1. Point the domain's DNS at the VPS.
2. On the VPS:

   ```bash
   git clone https://github.com/bquozon/Student-Kit.git ~/Student-Kit
   cd ~/Student-Kit && sudo bash deploy/install.sh kit.example.ch
   ```

3. Fill SMTP and `ADMIN_EMAIL` in `/etc/student-kit.env` (`sudo nano /etc/student-kit.env`), then run the same command again.

`install.sh` copies the code to `/opt/student-kit` and builds the `student-kit` image. It then starts the container on the network of the Caddy container that holds ports 80/443 (solidtime's, the same one polyuni.ch uses) and runs the seed. It also installs three systemd timers and adds a single site block to that Caddyfile. Before reloading, it validates the Caddyfile, and if Caddy rejects the change it restores the backup. Re-running it keeps `data/` and the settings file.

To update: `cd ~/Student-Kit && git pull && sudo bash deploy/install.sh kit.example.ch`

## Scheduled jobs

| Timer | When | What |
|---|---|---|
| `kit-digest.timer` | daily 08:00 | Emails the mods if suggestions or reports are waiting |
| `kit-linkcheck.timer` | Mondays 04:00 | HEAD then GET (10 s timeout) on every approved URL. After 2 failures in a row, it opens a `dead_link` report. It never archives anything |
| `kit-backup.timer` | nightly 03:15 | Consistent copy of the SQLite file to `/opt/student-kit/data/backups/` (keeps 14) |

Useful commands: `systemctl list-timers 'kit-*'`, `journalctl -u 'kit-job@*' -n 50`, `docker logs -f student-kit`.
Run a job now: `sudo systemctl start kit-job@linkcheck`.

**Backups:** the nightly copies stay on the same disk. Also copy them off the server now and then, for example:

```bash
rsync -a vps:/opt/student-kit/data/backups/ ~/student-kit-backups/
```

To restore, stop the container, copy a backup over `data/kit.sqlite`, then start the container again.

## Privacy and security

- The app stores only the email, the university and the uses. Stats are anonymous daily counters: no cookie, no IP stored, and the visitor hash salt is thrown away daily.
- Login is a magic link sent only to listed university domains:
  - The token is 32 random bytes; only its SHA-256 is stored. It's valid 15 minutes and works once.
  - The link opens a confirmation button, so email scanners can't use the token up.
  - Sessions last 90 days, with HttpOnly, Secure and SameSite=Lax cookies.
  - Rate limits: 3 links per email per 15 min, 20 per IP per hour.
- Every POST carries a CSRF token (double submit). Jinja escapes all output, and a strict CSP allows no inline scripts and no third-party requests.
- The suggest form's URL check and the link checker only fetch public IPs, including after redirects.
