"""One-off CLI to create an additional VidFactory user (there is no signup UI by design).

Run inside the deployed container so it picks up the same config/DB as the app, e.g.:

    docker exec -it <container> python scripts/create_user.py <username>

Prompts for a password (not echoed) and inserts the user directly via the app's own DB session —
same engine/migrations as the running app, so this is safe to run against a live database.
"""

from __future__ import annotations

import getpass
import sys

sys.path.insert(0, "src")

from sqlalchemy.orm import Session  # noqa: E402

from vidfactory.core.auth import hash_password  # noqa: E402
from vidfactory.database.db import get_engine, init_db  # noqa: E402
from vidfactory.database.models import User  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/create_user.py <username>", file=sys.stderr)
        raise SystemExit(1)
    username = sys.argv[1]

    init_db()
    with Session(get_engine()) as db:
        if db.query(User).filter(User.username == username).first() is not None:
            print(f"User {username!r} already exists.", file=sys.stderr)
            raise SystemExit(1)

        password = getpass.getpass(f"Password for {username}: ")
        confirm = getpass.getpass("Confirm password: ")
        if password != confirm:
            print("Passwords did not match.", file=sys.stderr)
            raise SystemExit(1)

        db.add(User(username=username, password_hash=hash_password(password)))
        db.commit()
    print(f"Created user {username!r}.")


if __name__ == "__main__":
    main()
