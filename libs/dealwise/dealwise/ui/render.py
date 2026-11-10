"""HTML for the dashboard. All user-controlled strings are escaped."""

from datetime import date
from decimal import Decimal
from html import escape as e

from dealwise.services.metrics import DashboardMetrics

_STATUS_ORDER = ["active", "draft", "expired", "terminated"]


def money(value: Decimal | None, currency: str | None) -> str:
    if value is None:
        return "-"
    return f"{currency or ''} {value:,.2f}".strip()


def _fig(n, label, sub=""):
    sub_html = f'<span class="s">{e(sub)}</span>' if sub else ""
    return f'<div class="dw-fig"><span class="n">{n}</span><span class="l">{e(label)}</span>{sub_html}</div>'


def _deadline_row(d) -> str:
    kind = "Contract expires" if d.kind == "expiry" else "Renewal notice deadline"
    if d.overdue:
        chip = f'<span class="dw-chip late">passed {abs(d.days_left)} day(s) ago</span>'
        sub = "Auto-renewal notice window has closed; the contract is still running"
    else:
        cls = "soon" if d.days_left <= 30 else ""
        label = "today" if d.days_left == 0 else f"in {d.days_left} day(s)"
        chip = f'<span class="dw-chip {cls}">{label}</span>'
        sub = e(d.vendor_name or "No vendor recorded")
    return (
        '<div class="dw-ledger-row">'
        f'<div class="dw-date"><b>{d.on.day}</b><span>{d.on.strftime("%b %Y")}</span></div>'
        f'<div class="dw-what"><b>{e(d.title)}</b><span>{e(kind)}. {sub}</span></div>{chip}</div>'
    )


def dashboard_html(m: DashboardMetrics, scope_note: str = "") -> str:
    if m.documents == 0:
        return (
            '<div class="dw-root"><div class="dw-empty"><h3>No contracts yet</h3>'
            "Add your first contract in the Contracts tab. The figures here are computed from "
            "the contracts stored in DealWise; nothing is shown until there is data."
            "</div></div>"
        )
    active = m.by_status.get("active", 0)
    status_line = ", ".join(
        f"{m.by_status[s]} {s}" for s in _STATUS_ORDER if m.by_status.get(s)
    )
    figs = "".join(
        [
            _fig(m.agreements, "Agreements", f"{m.documents} documents incl. amendments"),
            _fig(active, "Active", status_line),
            _fig(m.expiring.get(90, 0), "Expiring within 90 days",
                 f"{m.expiring.get(30, 0)} within 30, {m.expiring.get(60, 0)} within 60"),
            _fig(m.vendors, "Vendors with contracts"),
        ]
    )
    ledger = "".join(_deadline_row(d) for d in m.deadlines) or (
        '<div class="dw-empty">No contract expiries or renewal-notice deadlines in the next '
        "90 days.</div>"
    )
    if m.value_by_currency:
        values = "".join(
            f'<div class="dw-kv"><span>{e(cur)}</span><span>{val:,.2f}</span></div>'
            for cur, val in sorted(m.value_by_currency.items())
        )
        values += (
            '<p class="dw-muted" style="margin:6px 0 0">Currencies are shown separately; '
            "DealWise does not convert between them.</p>"
        )
    else:
        values = '<p class="dw-muted">No contract values recorded yet.</p>'
    top = max((n for _, n in m.by_category), default=1)
    cats = "".join(
        f'<div class="dw-bar"><span>{e(name)}</span><i style="width:{100 * n / top:.0f}%"></i>'
        f"<span>{n}</span></div>"
        for name, n in m.by_category[:6]
    )
    recent = "".join(
        f'<div class="dw-kv"><span>{e(v.c.title)}</span><span>{v.c.created_at:%d %b}</span></div>'
        for v in m.recent
    )
    notes = []
    if m.agreements_without_value:
        notes.append(f"{m.agreements_without_value} agreement(s) have no recorded value and are not in the totals.")
    if m.missing_end_date:
        notes.append(f"{m.missing_end_date} agreement(s) have no end date, so they cannot appear in expiry counts.")
    if scope_note:
        notes.append(scope_note)
    notes_html = "".join(f"<li>{e(n)}</li>" for n in notes)
    return (
        '<div class="dw-root">'
        f'<div class="dw-asof">As of {m.as_of:%d %B %Y}. Figures cover historical contracts you have access to.</div>'
        f'<div class="dw-figures">{figs}</div>'
        '<div class="dw-cols">'
        f"<section><h3>Deadlines, next 90 days</h3>{ledger}</section>"
        '<div class="dw-side">'
        f"<section><h3>Contract value</h3>{values}</section>"
        f"<section><h3>By category</h3>{cats}</section>"
        f"<section><h3>Recently added</h3>{recent}</section>"
        "</div></div>"
        f'<ul class="dw-notes">{notes_html}</ul>'
        "</div>"
    )


def message_html(text: str, bad: bool = False) -> str:
    cls = "dw-callout bad" if bad else "dw-callout"
    return f'<div class="dw-root"><div class="{cls}">{e(text)}</div></div>'
