"""Pure lifecycle logic over lists of Contract rows (no database access).

Rules (documented in DEALWISE_AUDIT.md section 4, [R1]):
* base documents: original / renewal / revision; amendments modify a base.
* a document is *superseded* when a revision names it as parent.
* an amendment's value / end date, when given, override its base's from the
  amendment's effective date; blank means "unchanged".
* "active version on D": latest base with effective_from <= D, active if its
  effective_to is empty or >= D; plus amendments in force on D.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from dealwise.constants import (
    BASE_RELATIONS,
    REL_AMENDMENT,
    REL_REVISION,
    STATUS_SUPERSEDED,
)


def eff_from(c) -> Optional[date]:
    return c.effective_from or c.start_date


def eff_to(c) -> Optional[date]:
    return c.effective_to or c.end_date


def is_base(c) -> bool:
    return c.relation_type in BASE_RELATIONS


def superseded_ids(contracts) -> set[str]:
    return {
        c.parent_contract_id
        for c in contracts
        if c.relation_type == REL_REVISION and c.parent_contract_id
    }


def root_base(c, by_id: dict):
    """Walk parent links up to the base document an amendment applies to."""
    seen = set()
    cur = c
    while cur is not None and not is_base(cur) and cur.id not in seen:
        seen.add(cur.id)
        cur = by_id.get(cur.parent_contract_id)
    return cur if cur is not None and is_base(cur) else None


@dataclass
class ResolvedTerms:
    value: Optional[Decimal]
    currency: Optional[str]
    end_date: Optional[date]
    amendment_ids: list[str] = field(default_factory=list)


def resolve_terms(base, contracts, today: date) -> ResolvedTerms:
    """Value / end date of an agreement after applying amendments in force."""
    by_id = {c.id: c for c in contracts}
    amends = [
        a
        for a in contracts
        if a.relation_type == REL_AMENDMENT
        and (rb := root_base(a, by_id)) is not None
        and rb.id == base.id
        and (eff_from(a) is None or eff_from(a) <= today)
    ]
    amends.sort(key=lambda a: (eff_from(a) or date.min, a.version_number))
    value, currency, end = base.contract_value, base.currency, base.end_date
    used = []
    for a in amends:
        if a.contract_value is not None:
            value, currency = a.contract_value, a.currency or currency
            used.append(a.id)
        if a.end_date is not None:
            end = a.end_date
            if a.id not in used:
                used.append(a.id)
    return ResolvedTerms(value, currency, end, used)


def effective_status(c, today: date, superseded: set[str], end: Optional[date] = None) -> str:
    if c.id in superseded:
        return STATUS_SUPERSEDED
    if c.status in ("draft", "terminated"):
        return c.status
    end = end if end is not None else c.end_date
    if end is not None and end < today:
        return "expired"
    return c.status


def lifecycle_label(c) -> str:
    if c.relation_type == "original":
        return "Original"
    return f"{c.relation_type.capitalize()} v{c.version_number}"


def current_agreements(contracts) -> list:
    """Base documents not replaced by a revision. Amendments are not
    agreements in their own right."""
    sup = superseded_ids(contracts)
    return [c for c in contracts if is_base(c) and c.id not in sup]


@dataclass
class ActiveVersion:
    base: Optional[object]
    amendments: list
    notes: list[str]


def active_version_on(lineage_docs, on: date) -> ActiveVersion:
    notes: list[str] = []
    by_id = {c.id: c for c in lineage_docs}
    undated = [c for c in lineage_docs if eff_from(c) is None]
    if undated:
        notes.append(
            f"{len(undated)} document(s) have no start/effective date and were "
            "ignored for this lookup."
        )
    bases = [c for c in lineage_docs if is_base(c) and eff_from(c) and eff_from(c) <= on]
    if not bases:
        notes.append("No base document is effective on this date.")
        return ActiveVersion(None, [], notes)
    chosen = max(bases, key=lambda c: (eff_from(c), c.version_number))
    if eff_to(chosen) is not None and eff_to(chosen) < on:
        notes.append(
            f"The latest base document ended on {eff_to(chosen).isoformat()}; "
            "no version is recorded as active on this date."
        )
        return ActiveVersion(None, [], notes)
    amends = [
        a
        for a in lineage_docs
        if a.relation_type == REL_AMENDMENT
        and eff_from(a) is not None
        and eff_from(a) <= on
        and (eff_to(a) is None or eff_to(a) >= on)
        and (rb := root_base(a, by_id)) is not None
        and rb.id == chosen.id
    ]
    amends.sort(key=lambda a: (eff_from(a), a.version_number))
    return ActiveVersion(chosen, amends, notes)
