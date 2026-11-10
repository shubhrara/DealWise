from datetime import date
from decimal import Decimal

from dealwise.services import ContractService, compute_dashboard

TODAY = date(2025, 1, 1)


def dash(engine, user, root):
    return compute_dashboard(engine, user, TODAY, root)


def test_empty_dashboard_has_no_invented_numbers(engine, bob, root):
    m = dash(engine, bob, root)
    assert (m.agreements, m.documents, m.vendors) == (0, 0, 0)
    assert m.value_by_currency == {} and m.deadlines == [] and m.top_vendors == []


def test_counts_status_and_values(svc_for, engine, bob, root, add):
    s = svc_for(bob)
    add(s, "A", "V1", start_date="2024-01-01", end_date="2025-01-20", contract_value="1000", currency="USD")
    add(s, "B", "V2", end_date="2026-01-01", contract_value="500", currency="USD", category="IT")
    add(s, "C", "V2", end_date="2024-06-01", contract_value="200", currency="EUR")
    add(s, "D", "V3", status="terminated")
    add(s, "E", "V3", status="draft")
    m = dash(engine, bob, root)
    assert m.agreements == 5
    assert m.by_status == {"active": 2, "expired": 1, "terminated": 1, "draft": 1}
    # never summed across currencies
    assert m.value_by_currency == {"USD": Decimal("1500.00"), "EUR": Decimal("200.00")}
    assert m.agreements_without_value == 2
    assert m.expiring == {30: 1, 60: 1, 90: 1}
    assert m.missing_end_date == 2
    assert dict(m.top_vendors)["V2"] == 2 and m.vendors == 3
    assert ("Uncategorised", 4) in m.by_category


def test_amendments_not_counted_as_agreements_but_change_terms(svc_for, engine, bob, root, add):
    s = svc_for(bob)
    o = add(s, "MSA", end_date="2025-03-01", contract_value="1000", currency="USD")
    add(s, "Amd", relation_type="amendment", parent_contract_id=o.id,
        effective_from="2024-06-01", contract_value="1400", currency="USD", end_date="2025-09-01")
    m = dash(engine, bob, root)
    assert m.agreements == 1 and m.documents == 2
    assert m.value_by_currency == {"USD": Decimal("1400.00")}
    assert m.expiring[90] == 0  # end date moved out by the amendment


def test_superseded_not_double_counted(svc_for, engine, bob, root, add):
    s = svc_for(bob)
    o = add(s, "v1", contract_value="100", currency="USD", end_date="2026-01-01")
    add(s, "v2", relation_type="revision", parent_contract_id=o.id, contract_value="150",
        currency="USD", end_date="2026-01-01")
    m = dash(engine, bob, root)
    assert m.agreements == 1 and m.value_by_currency == {"USD": Decimal("150.00")}


def test_deadlines(svc_for, engine, bob, root, add):
    s = svc_for(bob)
    add(s, "Auto", "V", end_date="2025-02-15", auto_renews=True, renewal_notice_days=30,
        contract_value="1", currency="USD")
    add(s, "Passed", "V2", end_date="2025-01-20", auto_renews=True, renewal_notice_days=60)
    m = dash(engine, bob, root)
    kinds = [(d.title, d.kind, d.overdue) for d in m.deadlines]
    assert ("Auto", "renewal_notice", False) in kinds and ("Auto", "expiry", False) in kinds
    assert ("Passed", "renewal_notice", True) in kinds
    assert m.deadlines == sorted(m.deadlines, key=lambda d: (d.on, d.kind))


# ---- historical vs proposal integrity [R4] --------------------------------
def test_proposals_never_enter_historical_metrics(svc_for, engine, bob, root, add):
    s = svc_for(bob)
    add(s, "Signed", "V", end_date="2026-01-01", contract_value="100", currency="USD")
    add(s, "Proposed", "Prop Vendor", role="proposal", end_date="2025-01-10",
        contract_value="999999", currency="USD", category="Big")
    m = dash(engine, bob, root)
    assert (m.agreements, m.documents, m.vendors) == (1, 1, 1)
    assert m.value_by_currency == {"USD": Decimal("100.00")}
    assert m.expiring[30] == 0 and m.deadlines == [] and m.top_vendors == [("V", 1)]
    assert [v.c.title for v in s.list()] == ["Signed"]
    assert [v.c.title for v in s.list(role="proposal")] == ["Proposed"]


def test_cannot_link_proposal_into_historical_lineage(svc_for, bob, add):
    import pytest
    from dealwise.services import ValidationError

    s = svc_for(bob)
    hist = add(s, "H")
    with pytest.raises(ValidationError):
        add(s, "P", role="proposal", relation_type="amendment", parent_contract_id=hist.id)


def test_db_rejects_invalid_role(engine, bob):
    import pytest
    from dealwise.db.models import Contract
    from sqlalchemy.exc import IntegrityError
    from sqlmodel import Session

    with Session(engine) as s:
        s.add(Contract(title="x", lineage_id="x", owner_id="o", role="something"))
        with pytest.raises(IntegrityError):
            s.commit()


# ---- authorisation of aggregates ----------------------------------------
def test_dashboard_excludes_contracts_user_cannot_see(svc_for, engine, bob, carol, root, add):
    add(svc_for(bob), "Private", "Hidden", visibility="private", contract_value="5000",
        currency="USD", end_date="2025-01-15")
    add(svc_for(bob), "Shared", "Open", contract_value="10", currency="USD")
    c = dash(engine, carol, root)
    assert c.agreements == 1 and c.value_by_currency == {"USD": Decimal("10.00")}
    assert c.vendors == 1 and c.deadlines == [] and [v.c.title for v in c.recent] == ["Shared"]
    b = dash(engine, bob, root)
    assert b.agreements == 2 and b.value_by_currency == {"USD": Decimal("5010.00")}
