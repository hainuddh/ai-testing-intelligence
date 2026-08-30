import argparse
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import engine
from app.models import User
from app.security import hash_password


def create_admin(username: str, password: str) -> None:
    with Session(engine) as session:
        existing = session.scalar(select(User).where(User.username == username))
        if existing:
            raise SystemExit(f"User {username!r} already exists")
        session.add(
            User(
                username=username,
                password_hash=hash_password(password),
                role="admin",
                is_active=True,
            )
        )
        session.commit()


def reset_password(username: str, password: str) -> None:
    with Session(engine) as session:
        user = session.scalar(select(User).where(User.username == username))
        if user is None:
            raise SystemExit(f"User {username!r} does not exist")
        user.password_hash = hash_password(password)
        user.is_active = True
        session.commit()


def prompt_password() -> str:
    password = getpass("Password: ")
    if len(password) < 8:
        raise SystemExit("Password must contain at least 8 characters")
    if password != getpass("Confirm password: "):
        raise SystemExit("Passwords do not match")
    return password


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap application data")
    parser.add_argument("username")
    parser.add_argument("password", nargs="?")
    parser.add_argument("--reset-password", action="store_true")
    args = parser.parse_args()
    password = args.password or prompt_password()
    if args.reset_password:
        reset_password(args.username, password)
    else:
        create_admin(args.username, password)


if __name__ == "__main__":
    main()
