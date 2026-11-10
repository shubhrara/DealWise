import itertools

import pytest
from dealwise.db import init_db
from dealwise.security.context import UserContext
from dealwise.services import ContractService
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

_counter = itertools.count(1)


@pytest.fixture()
def engine():
    eng = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    with eng.begin() as conn:  # minimal stand-in for ktem's user table
        conn.execute(
            text('CREATE TABLE "user" (id TEXT PRIMARY KEY, username TEXT, admin BOOLEAN)')
        )
        for uid, name, admin in [("u-admin", "admin", 1), ("u-bob", "bob", 0), ("u-carol", "carol", 0)]:
            conn.execute(
                text('INSERT INTO "user" VALUES (:i, :n, :a)'), {"i": uid, "n": name, "a": admin}
            )
    init_db(eng)
    return eng


@pytest.fixture()
def admin():
    return UserContext("u-admin", True, username="admin")


@pytest.fixture()
def bob():
    return UserContext("u-bob", False, username="bob")


@pytest.fixture()
def carol():
    return UserContext("u-carol", False, username="carol")


@pytest.fixture()
def root(tmp_path):
    r = tmp_path / "store"
    r.mkdir()
    return r


@pytest.fixture()
def svc_for(engine, root):
    def make(user):
        return ContractService(engine, user, root)

    return make


@pytest.fixture()
def make_pdf(tmp_path):
    def make(label=None):
        n = next(_counter)
        p = tmp_path / f"doc{n}.pdf"
        p.write_bytes(b"%PDF-1.4\n% test document " + (label or f"n{n}").encode() + b"\n%%EOF\n")
        return str(p)

    return make


@pytest.fixture()
def add(make_pdf):
    """add(svc, title, vendor, **fields) -> ContractView"""

    def _add(svc, title="Master Services Agreement", vendor="Acme Corp", **kw):
        return svc.create_contract(
            file_path=make_pdf(), original_filename=f"{title}.pdf", title=title, vendor_name=vendor, **kw
        )

    return _add
