import pytest
from dealwise.db.models import Contract
from dealwise.services import Conflict, Forbidden, ValidationError, VendorService, normalize_name
from sqlmodel import Session, select


def test_normalize():
    assert normalize_name("Acme, Inc.") == normalize_name("ACME corp") == "acme"
    assert normalize_name("The Widget Company Ltd") == "widget"
    assert normalize_name("Müller & Söhne GmbH") == "muller and sohne"
    assert normalize_name("A.B.C. L.L.C.") == "abc"


def test_exact_and_alias_reuse_no_duplicates(svc_for, bob, add, engine):
    svc = svc_for(bob)
    a = add(svc, "c1", "Acme Corp")
    b = add(svc, "c2", "ACME, Inc.")
    assert a.c.vendor_id == b.c.vendor_id
    vs = VendorService(engine, bob)
    vs.update(a.c.vendor_id, aliases=["Acme Holdings"])
    c = add(svc, "c3", "acme holdings")
    assert c.c.vendor_id == a.c.vendor_id
    assert len(vs.list()) == 1 and vs.list()[0].contract_count == 3


def test_near_match_needs_confirmation(svc_for, bob, add):
    svc = svc_for(bob)
    add(svc, "c1", "Globex Industries")
    with pytest.raises(Conflict):
        add(svc, "c2", "Globex Industires")  # typo
    v = add(svc, "c2", "Globex Industires", force_new_vendor=True)
    assert v.vendor_name == "Globex Industires"


def test_near_match_does_not_leak_hidden_vendor(svc_for, bob, carol, add):
    add(svc_for(bob), "secret", "Stealth Dynamics", visibility="private")
    # carol cannot see that vendor, so she gets no suggestion that reveals it
    v = add(svc_for(carol), "c", "Stealth Dynamicz")
    assert v.vendor_name == "Stealth Dynamicz"
    # but an exact match silently reuses the existing record (no duplicate, no disclosure)
    w = add(svc_for(carol), "c2", "Stealth Dynamics")
    assert w.vendor_name == "Stealth Dynamics"


def test_vendor_list_only_shows_visible(svc_for, bob, carol, engine, add, admin):
    add(svc_for(bob), "p", "Hidden Co", visibility="private")
    add(svc_for(bob), "q", "Visible Co")
    assert [v.name for v in VendorService(engine, carol).list()] == ["Visible Co"]
    assert {v.name for v in VendorService(engine, admin).list()} == {"Hidden Co", "Visible Co"}
    hidden_id = [v.id for v in VendorService(engine, admin).list() if v.name == "Hidden Co"][0]
    from dealwise.services import NotFound
    with pytest.raises(NotFound):
        VendorService(engine, carol).update(hidden_id, notes="x")


def test_vendor_counts_respect_visibility(svc_for, bob, carol, engine, add):
    add(svc_for(bob), "a", "Same Co")
    add(svc_for(bob), "b", "Same Co", visibility="private")
    assert VendorService(engine, carol).list()[0].contract_count == 1
    assert VendorService(engine, bob).list()[0].contract_count == 2


def test_merge_admin_only_and_repoints(svc_for, bob, admin, engine, add):
    a = add(svc_for(bob), "a", "Initech")
    b = add(svc_for(bob), "b", "Initrode", force_new_vendor=True)
    with pytest.raises(Forbidden):
        VendorService(engine, bob).merge(b.c.vendor_id, a.c.vendor_id)
    VendorService(engine, admin).merge(b.c.vendor_id, a.c.vendor_id)
    with Session(engine) as s:
        assert {c.vendor_id for c in s.exec(select(Contract)).all()} == {a.c.vendor_id}
    vs = VendorService(engine, admin)
    summary = vs.list()
    assert len(summary) == 1 and "Initrode" in summary[0].aliases
    assert add(svc_for(bob), "c", "Initrode").c.vendor_id == a.c.vendor_id  # alias now resolves


def test_rename_keeps_alias_and_blocks_clash(svc_for, bob, engine, add):
    a = add(svc_for(bob), "a", "Old Name LLC")
    b = add(svc_for(bob), "b", "Totally Different")
    vs = VendorService(engine, bob)
    with pytest.raises(Conflict):
        vs.update(b.c.vendor_id, name="Old Name")
    vs.update(a.c.vendor_id, name="New Name")
    assert add(svc_for(bob), "c", "Old Name LLC").c.vendor_id == a.c.vendor_id


def test_delete_vendor_rules(svc_for, bob, admin, engine, add):
    a = add(svc_for(bob), "a", "Used Co")
    vs = VendorService(engine, admin)
    with pytest.raises(Conflict):
        vs.delete(a.c.vendor_id)
    free = VendorService(engine, bob).create("Unused Co")
    with pytest.raises(Forbidden):
        VendorService(engine, bob).delete(free.id)
    vs.delete(free.id)


def test_empty_vendor_name(svc_for, bob, add):
    with pytest.raises(ValidationError):
        add(svc_for(bob), "a", "   ")
