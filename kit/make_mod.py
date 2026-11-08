"""Make someone a moderator: python -m kit.make_mod email@ethz.ch

The email must be a university address (that's the only way to log in). Creates the user if needed."""

from __future__ import annotations

import sys

from . import db
from .ranking import university_for


def make_mod(con, email: str) -> None:
    email = email.strip().lower()
    uni = university_for(email, db.email_domains(con))
    if not uni:
        raise SystemExit(f"{email}: not a university email listed in universities.email_domains, so it can't log in.")
    con.execute(
        "INSERT INTO users (email, university_code, is_mod) VALUES (?, ?, 1)"
        " ON CONFLICT(email) DO UPDATE SET is_mod=1",
        (email, uni),
    )


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m kit.make_mod email@ethz.ch")
    con = db.connect()
    db.init(con)
    make_mod(con, sys.argv[1])
    print(f"{sys.argv[1]} is now a moderator.")


if __name__ == "__main__":
    main()
