from dataclasses import dataclass

from dealwise.constants import DEFAULT_ORG_ID
from sqlalchemy import text


class AuthError(Exception):
    """Base class for authentication / authorisation failures."""


class Unauthenticated(AuthError):
    """No valid signed-in user."""


@dataclass(frozen=True)
class UserContext:
    user_id: str
    is_admin: bool
    org_id: str = DEFAULT_ORG_ID
    username: str = ""


def resolve_user(
    engine, user_id: str | None, user_management_enabled: bool = True
) -> UserContext:
    """Build the caller's context from the *database* on every request.

    ``user_id`` comes from Gradio's server-side session state (set only by the
    login function). The admin flag is re-read each time, never cached.
    """
    if not user_management_enabled:
        # single-user mode: the implicit local user administers everything
        return UserContext(user_id=user_id or "default", is_admin=True, username="local")
    if not user_id:
        raise Unauthenticated("Please sign in.")
    with engine.connect() as conn:
        row = conn.execute(
            text('SELECT id, username, admin FROM "user" WHERE id = :id'),
            {"id": user_id},
        ).first()
    if row is None:
        raise Unauthenticated("Please sign in.")
    return UserContext(user_id=row[0], is_admin=bool(row[2]), username=row[1] or "")
