"""Create or promote the first MarketMind administrator after a Supabase invite.

Run only from a trusted backend shell after `alembic upgrade head`. This script
does not create an identity-provider user and never accepts passwords or tokens.
"""

from __future__ import annotations

import argparse
import asyncio
from uuid import UUID

from sqlalchemy import select

from app.database import Session, User


async def bootstrap(auth_subject: str, email: str, display_name: str) -> None:
    async with Session() as db:
        existing_subject = (await db.execute(select(User).where(User.auth_subject == auth_subject))).scalar_one_or_none()
        existing_email = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if existing_subject and existing_email and existing_subject.id != existing_email.id:
            raise RuntimeError("The supplied subject and email resolve to different users; stop and investigate.")
        user = existing_subject or existing_email
        if user is None:
            user = User(
                auth_subject=auth_subject,
                email=email,
                display_name=display_name,
                role="ADMIN",
                email_verified=True,
            )
            db.add(user)
            action = "created"
        else:
            user.auth_subject = auth_subject
            user.email = email
            user.display_name = display_name or user.display_name
            user.role = "ADMIN"
            user.is_active = True
            action = "promoted"
        await db.commit()
        print(f"Administrator {action}: {user.email} ({user.id})")


def main() -> None:
    parser = argparse.ArgumentParser(description="Bootstrap the first MarketMind administrator.")
    parser.add_argument("--auth-subject", required=True, help="UUID from Supabase Auth's user record")
    parser.add_argument("--email", required=True, help="Verified administrator email address")
    parser.add_argument("--display-name", default="", help="Optional display name")
    parser.add_argument("--confirm", action="store_true", help="Required acknowledgement before changing the database")
    args = parser.parse_args()
    if not args.confirm:
        parser.error("--confirm is required")
    try:
        UUID(args.auth_subject)
    except ValueError as error:
        parser.error("--auth-subject must be a UUID from Supabase Auth")
        raise error
    email = args.email.strip().lower()
    if "@" not in email or len(email) > 320:
        parser.error("--email must be a valid email address")
    asyncio.run(bootstrap(args.auth_subject, email, args.display_name.strip()[:120]))


if __name__ == "__main__":
    main()
