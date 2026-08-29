import argparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.models import User
from app.security import hash_password


def create_admin(username: str, password: str) -> None:
    Base.metadata.create_all(engine)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap application data")
    parser.add_argument("username")
    parser.add_argument("password")
    args = parser.parse_args()
    create_admin(args.username, args.password)


if __name__ == "__main__":
    main()
