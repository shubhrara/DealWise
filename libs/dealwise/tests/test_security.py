import logging

import pytest
from dealwise.db import ensure_columns
from dealwise.security import RedactingFilter, Unauthenticated, redact, resolve_user
from sqlalchemy import inspect, text


def test_redacts_secrets():
    s = redact("calling with api_key=sk-abcdefghijklmnopqrstuvwx and Authorization: Bearer abcdefghijklmnop1234")
    assert "sk-abcdefghijklmnopqrstuvwx" not in s and "abcdefghijklmnop1234" not in s
    assert "password=hunter2" not in redact("login password=hunter2 ok")
    assert "secretpw" not in redact("postgres://user:secretpw@host/db")
    assert redact("plain message, id=abc123") == "plain message, id=abc123"


def test_logging_filter_applies(caplog):
    logger = logging.getLogger("dealwise.test")
    logger.addFilter(RedactingFilter())
    with caplog.at_level(logging.INFO, logger="dealwise.test"):
        logger.info("token=%s", "supersecretvalue123")
    assert "supersecretvalue123" not in caplog.text


def test_resolve_user_reads_admin_flag_from_db(engine):
    assert resolve_user(engine, "u-admin").is_admin is True
    assert resolve_user(engine, "u-bob").is_admin is False
    with engine.begin() as c:
        c.execute(text('UPDATE "user" SET admin = 1 WHERE id = \'u-bob\''))
    assert resolve_user(engine, "u-bob").is_admin is True  # never cached


def test_unknown_or_missing_user_rejected(engine):
    with pytest.raises(Unauthenticated):
        resolve_user(engine, None)
    with pytest.raises(Unauthenticated):
        resolve_user(engine, "forged-id")


def test_single_user_mode_is_admin(engine):
    assert resolve_user(engine, None, user_management_enabled=False).is_admin


def test_audit_log_has_no_contract_content(svc_for, engine, bob, add):
    from dealwise.db.models import AuditLog
    from sqlmodel import Session, select

    add(svc_for(bob), "Top Secret Title", "Secret Vendor", notes="sensitive notes")
    with Session(engine) as s:
        dump = " ".join(str(r.detail) for r in s.exec(select(AuditLog)).all())
    assert "Top Secret" not in dump and "sensitive" not in dump


def test_ensure_columns_adds_missing(engine):
    with engine.begin() as c:
        c.execute(text("ALTER TABLE dw_contract DROP COLUMN index_file_id"))
    assert "index_file_id" not in {c["name"] for c in inspect(engine).get_columns("dw_contract")}
    ensure_columns(engine)
    assert "index_file_id" in {c["name"] for c in inspect(engine).get_columns("dw_contract")}
