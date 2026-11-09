-- Swiss Student Kit. Times are UTC text "YYYY-MM-DD HH:MM:SS" (SQLite's datetime('now')), so they compare as text.
CREATE TABLE IF NOT EXISTS cities (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS universities (
  code TEXT PRIMARY KEY,
  label TEXT NOT NULL,
  city_id INTEGER NOT NULL REFERENCES cities(id)
);

CREATE TABLE IF NOT EXISTS categories (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  label TEXT NOT NULL,
  short_label TEXT NOT NULL,
  subtitle TEXT NOT NULL DEFAULT '',
  icon_svg_paths TEXT NOT NULL DEFAULT '',
  position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS items (
  id INTEGER PRIMARY KEY,
  category_id INTEGER NOT NULL REFERENCES categories(id),
  name TEXT NOT NULL,
  url TEXT NOT NULL,
  description TEXT NOT NULL CHECK (length(description) <= 140),
  kind TEXT NOT NULL CHECK (kind IN ('Web', 'App', 'Web + app', 'Pass')),
  pinned INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected', 'archived')),
  submitted INTEGER NOT NULL DEFAULT 0,           -- 1 = came through the public suggest form
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  approved_at TEXT,
  rejection_reason TEXT,
  note TEXT,                                       -- the submitter's note to the mod
  uses_count INTEGER NOT NULL DEFAULT 0,           -- cache of COUNT(uses), kept by the triggers below
  link_fails INTEGER NOT NULL DEFAULT 0,           -- consecutive failed link checks
  link_checked_at TEXT
);
CREATE INDEX IF NOT EXISTS items_status ON items(status);

CREATE TABLE IF NOT EXISTS item_cities (
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  city_id INTEGER NOT NULL REFERENCES cities(id),
  PRIMARY KEY (item_id, city_id)
);

-- who the service is for; a NULL university_code row = ALL (any student in that city)
CREATE TABLE IF NOT EXISTS item_universities (
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  university_code TEXT REFERENCES universities(code)
);
CREATE UNIQUE INDEX IF NOT EXISTS item_universities_u ON item_universities(item_id, ifnull(university_code, ''));

-- the anonymous "I use this" vote: voter = SHA-256 of a random id kept in the visitor's cookie (no account)
CREATE TABLE IF NOT EXISTS uses (
  voter TEXT NOT NULL,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY (voter, item_id)
);
CREATE TRIGGER IF NOT EXISTS uses_add AFTER INSERT ON uses
  BEGIN UPDATE items SET uses_count = uses_count + 1 WHERE id = NEW.item_id; END;
CREATE TRIGGER IF NOT EXISTS uses_del AFTER DELETE ON uses
  BEGIN UPDATE items SET uses_count = uses_count - 1 WHERE id = OLD.item_id; END;

CREATE TABLE IF NOT EXISTS reports (
  id INTEGER PRIMARY KEY,
  item_id INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
  type TEXT NOT NULL CHECK (type IN ('dead_link', 'wrong_info', 'duplicate', 'other')),
  message TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'done')),
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- the mod's devices (logged in at /mod with ADMIN_KEY); id = SHA-256 of the cookie value
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mod_log (
  id INTEGER PRIMARY KEY,
  action TEXT NOT NULL,
  item_id INTEGER,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- anonymous daily counters for the stats page: no IP, no cookie (see stats.py)
CREATE TABLE IF NOT EXISTS stat (
  day TEXT NOT NULL,
  grp TEXT NOT NULL,
  key TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, grp, key)
);
