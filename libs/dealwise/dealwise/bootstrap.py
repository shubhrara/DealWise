"""Start-up hooks: database setup, log redaction and credential checks."""

from dealwise import config
from dealwise.db import get_engine, init_db
from dealwise.security.logging import get_logger, install_on_root_handlers

log = get_logger("dealwise.bootstrap")

DEFAULT_ADMIN_PASSWORD = "admin"


def default_admin_in_use(engine=None) -> bool:
    """True if the configured admin account still accepts the default password."""
    from ktem.utils.passwords import verify_password
    from sqlalchemy import text
    from theflow.settings import settings

    engine = engine or get_engine()
    username = str(getattr(settings, "KH_FEATURE_USER_MANAGEMENT_ADMIN", "admin")).lower()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text('SELECT password FROM "user" WHERE username_lower = :u'), {"u": username}
            ).first()
    except Exception:
        return False
    return bool(row) and verify_password(DEFAULT_ADMIN_PASSWORD, row[0])


def run_startup_checks(user_management_enabled: bool) -> None:
    engine = get_engine()
    init_db(engine)
    install_on_root_handlers()
    if not user_management_enabled:
        log.warning(
            "User management is disabled: everyone who can reach this app is treated "
            "as an administrator. Do not store confidential contracts in this mode on "
            "a shared network."
        )
        return
    if default_admin_in_use(engine):
        msg = (
            "The administrator account still uses the default password. Change it in "
            "Settings, or set KH_FEATURE_USER_MANAGEMENT_PASSWORD before first start."
        )
        if config.require_strong_admin():
            raise RuntimeError(msg + " (DEALWISE_REQUIRE_STRONG_ADMIN is enabled.)")
        log.warning(msg)
