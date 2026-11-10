"""Helpers shared by DealWise Gradio pages."""

import gradio as gr
from dealwise.db import get_engine
from dealwise.security.context import Unauthenticated, UserContext, resolve_user
from dealwise.security.logging import get_logger
from dealwise.services.errors import DealWiseError
from sqlalchemy import text

log = get_logger("dealwise.ui")

SIGN_IN_MSG = "Please sign in to use DealWise."


def get_ctx(app, user_id) -> UserContext:
    """Resolve the signed-in user from the DB on every call (raises Unauthenticated)."""
    return resolve_user(get_engine(), user_id, bool(app.f_user_management))


def usernames(ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    try:
        with get_engine().connect() as conn:
            rows = conn.execute(text('SELECT id, username FROM "user"')).all()
        return {r[0]: r[1] for r in rows if r[0] in ids}
    except Exception:
        return {}


def warn(exc: Exception) -> None:
    """Show a user-safe message; log only the exception type (never content)."""
    if isinstance(exc, DealWiseError):
        gr.Warning(str(exc))
    elif isinstance(exc, Unauthenticated):
        gr.Warning(SIGN_IN_MSG)
    else:
        log.exception("Unexpected error in DealWise UI callback")
        gr.Warning("Something went wrong. Check the server log for the error type.")


def fmt_date(d) -> str:
    return d.isoformat() if d else ""
