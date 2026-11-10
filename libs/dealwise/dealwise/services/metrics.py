"""Dashboard metrics. Everything is computed from the contracts the caller is
allowed to see, and ONLY from role='historical' documents (see
ContractService.list, which takes an explicit role). Proposals can never enter
these numbers. Nothing is estimated: a missing value is reported as missing."""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

from dealwise.constants import ROLE_HISTORICAL
from dealwise.security.context import UserContext
from dealwise.services.contracts import ContractService, ContractView

DEADLINE_HORIZON_DAYS = 90


@dataclass
class Deadline:
    on: date
    kind: str  # 'expiry' | 'renewal_notice'
    contract_id: str
    title: str
    vendor_name: Optional[str]
    days_left: int
    overdue: bool = False


@dataclass
class DashboardMetrics:
    as_of: date
    agreements: int = 0
    documents: int = 0
    vendors: int = 0
    by_status: dict[str, int] = field(default_factory=dict)
    expiring: dict[int, int] = field(default_factory=dict)
    value_by_currency: dict[str, Decimal] = field(default_factory=dict)
    agreements_without_value: int = 0
    by_category: list[tuple[str, int]] = field(default_factory=list)
    top_vendors: list[tuple[str, int]] = field(default_factory=list)
    deadlines: list[Deadline] = field(default_factory=list)
    recent: list[ContractView] = field(default_factory=list)
    missing_end_date: int = 0


def compute_dashboard(engine, user: UserContext, today: date | None = None, storage_root=None) -> DashboardMetrics:
    today = today or date.today()
    svc = ContractService(engine, user, storage_root)
    all_docs = svc.list(role=ROLE_HISTORICAL, view="all", today=today)
    agreements = [v for v in all_docs if v.c.relation_type != "amendment" and not v.superseded]

    m = DashboardMetrics(as_of=today, agreements=len(agreements), documents=len(all_docs))
    # vendors that appear on at least one visible *historical* document
    m.vendors = len({v.c.vendor_id for v in all_docs if v.c.vendor_id})
    for v in agreements:
        m.by_status[v.effective_status] = m.by_status.get(v.effective_status, 0) + 1
        if v.effective_value is None:
            m.agreements_without_value += 1
        else:
            cur = v.effective_currency or "?"
            m.value_by_currency[cur] = m.value_by_currency.get(cur, Decimal(0)) + v.effective_value
        if v.effective_end_date is None:
            m.missing_end_date += 1

    for window in (30, 60, 90):
        m.expiring[window] = sum(
            1
            for v in agreements
            if v.effective_status == "active"
            and v.effective_end_date is not None
            and 0 <= (v.effective_end_date - today).days <= window
        )

    cats: dict[str, int] = {}
    vend: dict[str, int] = {}
    for v in agreements:
        cats[v.c.category or "Uncategorised"] = cats.get(v.c.category or "Uncategorised", 0) + 1
        if v.vendor_name:
            vend[v.vendor_name] = vend.get(v.vendor_name, 0) + 1
    m.by_category = sorted(cats.items(), key=lambda kv: (-kv[1], kv[0].lower()))
    m.top_vendors = sorted(vend.items(), key=lambda kv: (-kv[1], kv[0].lower()))[:5]

    horizon = today + timedelta(days=DEADLINE_HORIZON_DAYS)
    for v in agreements:
        if v.effective_status != "active" or v.effective_end_date is None:
            continue
        end = v.effective_end_date
        if today <= end <= horizon:
            m.deadlines.append(Deadline(end, "expiry", v.id, v.c.title, v.vendor_name, (end - today).days))
        if v.c.auto_renews and v.c.renewal_notice_days is not None:
            notice = end - timedelta(days=v.c.renewal_notice_days)
            if today <= notice <= horizon:
                m.deadlines.append(
                    Deadline(notice, "renewal_notice", v.id, v.c.title, v.vendor_name, (notice - today).days)
                )
            elif notice < today <= end:
                m.deadlines.append(
                    Deadline(notice, "renewal_notice", v.id, v.c.title, v.vendor_name, (notice - today).days, True)
                )
    m.deadlines.sort(key=lambda d: (d.on, d.kind))
    m.recent = sorted(all_docs, key=lambda v: v.c.created_at, reverse=True)[:5]
    return m
