import os
from datetime import date

import pytest
from dealwise import config
from dealwise.services import (
    Conflict, ContractFilters, DuplicateDocument, Forbidden, NotFound, ValidationError,
)  # fmt: skip


def test_create_and_get(svc_for, bob, add):
    svc = svc_for(bob)
    v = add(svc, "MSA", "Acme Corp", contract_value="12,500.50", currency="usd",
            start_date="2024-01-01", end_date="2024-12-31", category="IT", department="Ops")
    got = svc.get(v.id)
    assert got.c.currency == "USD" and str(got.c.contract_value) == "12500.50"
    assert got.vendor_name == "Acme Corp" and got.c.owner_id == "u-bob"
    assert got.c.file_sha256 and got.c.stored_path and got.lifecycle == "Original"


def test_value_requires_currency(svc_for, bob, add):
    with pytest.raises(ValidationError):
        add(svc_for(bob), contract_value="100")


def test_date_validation(svc_for, bob, add):
    with pytest.raises(ValidationError):
        add(svc_for(bob), start_date="2024-05-01", end_date="2024-01-01")
    with pytest.raises(ValidationError):
        add(svc_for(bob), start_date="01/05/2024")


def test_rejects_bad_files(svc_for, bob, tmp_path):
    svc = svc_for(bob)
    bad = tmp_path / "x.exe"
    bad.write_bytes(b"MZ")
    with pytest.raises(ValidationError):
        svc.create_contract(file_path=str(bad), original_filename="x.exe", title="t", vendor_name="V")
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"not a pdf")
    with pytest.raises(ValidationError):
        svc.create_contract(file_path=str(fake), original_filename="fake.pdf", title="t", vendor_name="V")
    empty = tmp_path / "e.pdf"
    empty.write_bytes(b"")
    with pytest.raises(ValidationError):
        svc.create_contract(file_path=str(empty), original_filename="e.pdf", title="t", vendor_name="V")


def test_size_limit(svc_for, bob, add, monkeypatch):
    monkeypatch.setenv("DEALWISE_MAX_UPLOAD_MB", "0")
    with pytest.raises(ValidationError):
        add(svc_for(bob))
    assert config.max_upload_bytes() == 0


def test_duplicate_upload_rejected_and_cleaned(svc_for, bob, make_pdf, root):
    svc = svc_for(bob)
    p = make_pdf("same")
    svc.create_contract(file_path=p, original_filename="a.pdf", title="A", vendor_name="V1")
    before = sum(len(f) for _, _, f in os.walk(root))
    with pytest.raises(DuplicateDocument):
        svc.create_contract(file_path=p, original_filename="b.pdf", title="B", vendor_name="V1")
    assert sum(len(f) for _, _, f in os.walk(root)) == before


def test_failed_create_leaves_no_file(svc_for, bob, make_pdf, root):
    svc = svc_for(bob)
    with pytest.raises(ValidationError):
        svc.create_contract(file_path=make_pdf(), original_filename="a.pdf", title="A",
                            vendor_name="V", contract_value="5")  # no currency
    assert not any(f for _, _, f in os.walk(root))


def test_filename_is_sanitised(svc_for, bob, make_pdf, root):
    v = svc_for(bob).create_contract(file_path=make_pdf(), original_filename="../../etc/passwd.pdf",
                                     title="T", vendor_name="V")
    assert ".." not in v.c.stored_path and v.c.file_name == "passwd.pdf"
    assert (root / v.c.stored_path).resolve().is_relative_to(root.resolve())


# ---- authorisation -----------------------------------------------------
def test_private_contract_hidden_from_others(svc_for, bob, carol, admin, add):
    bsvc = svc_for(bob)
    priv = add(bsvc, "Secret", "Hidden Ltd", visibility="private")
    pub = add(bsvc, "Public", "Open Ltd")
    csvc = svc_for(carol)
    with pytest.raises(NotFound):
        csvc.get(priv.id)
    assert [v.id for v in csvc.list()] == [pub.id]
    assert svc_for(admin).get(priv.id).id == priv.id
    assert {v.id for v in svc_for(admin).list()} == {priv.id, pub.id}


def test_hidden_contract_indistinguishable_from_missing(svc_for, bob, carol, add):
    priv = add(svc_for(bob), visibility="private")
    with pytest.raises(NotFound) as hidden:
        svc_for(carol).get(priv.id)
    with pytest.raises(NotFound) as missing:
        svc_for(carol).get("does-not-exist")
    assert str(hidden.value) == str(missing.value)


def test_only_owner_or_admin_can_edit_delete(svc_for, bob, carol, admin, add):
    v = add(svc_for(bob))
    with pytest.raises(Forbidden):
        svc_for(carol).update_contract(v.id, {"title": "hacked"})
    with pytest.raises(Forbidden):
        svc_for(carol).delete_contract(v.id)
    assert svc_for(bob).update_contract(v.id, {"title": "ok"}).c.title == "ok"
    svc_for(admin).update_contract(v.id, {"notes": "admin note"})
    svc_for(admin).delete_contract(v.id)


def test_download_requires_visibility(svc_for, bob, carol, add, tmp_path):
    v = add(svc_for(bob), visibility="private")
    with pytest.raises(NotFound):
        svc_for(carol).export_file(v.id, tmp_path)
    out = svc_for(bob).export_file(v.id, tmp_path)
    assert out.exists() and out.read_bytes().startswith(b"%PDF")


def test_cannot_make_visible_by_stealing(svc_for, bob, carol, add):
    v = add(svc_for(bob), visibility="private")
    with pytest.raises(NotFound):
        svc_for(carol).update_contract(v.id, {"visibility": "organization"})


def test_org_isolation(engine, root, bob, add):
    from dealwise.security.context import UserContext
    from dealwise.services import ContractService

    add(ContractService(engine, bob, root))
    other = UserContext("u-bob", False, org_id="other-org")
    assert ContractService(engine, other, root).list() == []


# ---- delete / update ----------------------------------------------------
def test_delete_removes_file(svc_for, bob, add, root):
    v = add(svc_for(bob))
    svc_for(bob).delete_contract(v.id)
    assert not any(f for _, _, f in os.walk(root))
    with pytest.raises(NotFound):
        svc_for(bob).get(v.id)


def test_update_validation_and_unknown_fields(svc_for, bob, add):
    svc = svc_for(bob)
    v = add(svc)
    with pytest.raises(ValidationError):
        svc.update_contract(v.id, {"owner_id": "u-admin"})
    with pytest.raises(ValidationError):
        svc.update_contract(v.id, {"title": "  "})
    with pytest.raises(ValidationError):
        svc.update_contract(v.id, {"contract_value": "10"})  # currency missing


# ---- lineage -------------------------------------------------------------
def test_amendment_renewal_revision_lineage(svc_for, bob, add):
    svc = svc_for(bob)
    o = add(svc, "MSA", start_date="2023-01-01", end_date="2023-12-31",
            contract_value="1000", currency="USD")
    a = add(svc, "MSA Amendment 1", relation_type="amendment", parent_contract_id=o.id,
            effective_from="2023-06-01", end_date="2024-06-30", contract_value="1500", currency="USD")
    r = add(svc, "MSA Renewal", relation_type="renewal", parent_contract_id=o.id,
            start_date="2024-07-01", end_date="2025-06-30", contract_value="1800", currency="USD")
    lin = svc.lineage(o.id)
    assert [v.c.version_number for v in lin] == [1, 2, 3]
    assert {v.c.lineage_id for v in lin} == {o.id}
    assert a.c.parent_contract_id == o.id and r.c.version_number == 3
    # amendment overrides value + end date of the original
    got = svc.get(o.id, today=date(2023, 8, 1))
    assert str(got.effective_value) == "1500.00" and got.effective_end_date == date(2024, 6, 30)


def test_active_version_on_date(svc_for, bob, add):
    svc = svc_for(bob)
    o = add(svc, "MSA", start_date="2023-01-01", end_date="2023-12-31")
    a = add(svc, "Amd", relation_type="amendment", parent_contract_id=o.id,
            effective_from="2023-06-01", effective_to="2023-12-31")
    rev = add(svc, "MSA rev", relation_type="revision", parent_contract_id=o.id,
              start_date="2024-01-01", end_date="2024-12-31")
    av = svc.active_version_on(o.id, date(2023, 7, 1))
    assert av.base.id == o.id and [x.id for x in av.amendments] == [a.id]
    assert svc.active_version_on(o.id, date(2023, 3, 1)).amendments == []
    assert svc.active_version_on(o.id, date(2024, 5, 1)).base.id == rev.id
    none = svc.active_version_on(o.id, date(2022, 5, 1))
    assert none.base is None and none.notes
    assert svc.get(o.id).superseded and svc.get(o.id).effective_status == "superseded"


def test_gap_between_versions_reports_no_active(svc_for, bob, add):
    svc = svc_for(bob)
    o = add(svc, start_date="2023-01-01", end_date="2023-06-30")
    add(svc, "Renewal", relation_type="renewal", parent_contract_id=o.id, start_date="2023-09-01")
    av = svc.active_version_on(o.id, date(2023, 7, 15))
    assert av.base is None and any("ended" in n for n in av.notes)


def test_lineage_validation(svc_for, bob, carol, add):
    svc = svc_for(bob)
    o = add(svc)
    with pytest.raises(ValidationError):
        add(svc, "x", relation_type="amendment")  # no parent
    with pytest.raises(ValidationError):
        add(svc, "y", parent_contract_id=o.id)  # original with parent
    with pytest.raises(Forbidden):
        add(svc_for(carol), "z", relation_type="amendment", parent_contract_id=o.id)


def test_delete_blocked_when_versions_exist_and_relink(svc_for, bob, add):
    svc = svc_for(bob)
    o = add(svc)
    a = add(svc, "A1", relation_type="amendment", parent_contract_id=o.id)
    with pytest.raises(Conflict):
        svc.delete_contract(o.id)
    other = add(svc, "Other", "Other Co")
    moved = svc.set_lineage(a.id, "amendment", other.id)
    assert moved.c.lineage_id == other.c.lineage_id
    svc.delete_contract(o.id)
    standalone = svc.set_lineage(a.id, "original", None)
    assert standalone.c.lineage_id == a.id and standalone.c.version_number == 1


# ---- search / filter ----------------------------------------------------
def test_search_and_filters(svc_for, bob, add):
    svc = svc_for(bob)
    add(svc, "Cloud Hosting", "Acme Corp", category="IT", department="Ops", end_date="2999-01-01")
    add(svc, "Office Cleaning", "CleanCo", category="Facilities", department="Admin",
        notes="Weekly service", status="terminated")
    assert [v.c.title for v in svc.list(ContractFilters(search="cloud acme"))] == ["Cloud Hosting"]
    assert [v.c.title for v in svc.list(ContractFilters(search="weekly"))] == ["Office Cleaning"]
    assert [v.c.title for v in svc.list(ContractFilters(category="it"))] == ["Cloud Hosting"]
    assert [v.c.title for v in svc.list(ContractFilters(status="terminated"))] == ["Office Cleaning"]
    vid = svc.list(ContractFilters(search="cleanco"))[0].c.vendor_id
    assert len(svc.list(ContractFilters(vendor_id=vid))) == 1
    assert svc.list(ContractFilters(search="nonexistent")) == []
    assert svc.facets()["categories"] == ["Facilities", "IT"]


def test_expiring_filter(svc_for, bob, add):
    svc = svc_for(bob)
    t = date(2025, 1, 1)
    add(svc, "Soon", end_date="2025-01-20")
    add(svc, "Later", end_date="2025-06-01")
    add(svc, "Past", end_date="2024-01-01")
    got = svc.list(ContractFilters(expiring_within_days=30), today=t)
    assert [v.c.title for v in got] == ["Soon"]
    assert [v.c.title for v in svc.list(ContractFilters(status="expired"), today=t)] == ["Past"]
