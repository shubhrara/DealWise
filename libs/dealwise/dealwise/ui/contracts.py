import os
import time
from datetime import date
from pathlib import Path

import gradio as gr
from dealwise.constants import RELATION_TYPES, STATUS_SUPERSEDED, STATUSES
from dealwise.db import get_engine
from dealwise.security.context import Unauthenticated
from dealwise.services import ContractFilters, ContractService, VendorService
from dealwise.services.contracts import parse_date
from dealwise.services.errors import DealWiseError
from ktem.app import BasePage

from .common import SIGN_IN_MSG, fmt_date, get_ctx, usernames, warn
from .render import money

LIST_HEADERS = ["Ref", "Title", "Vendor", "Category", "Status", "Value", "Expires", "Lifecycle", "Access"]
ALL = "All"
STATUS_CHOICES = [ALL] + list(STATUSES) + [STATUS_SUPERSEDED]
SORTS = {"Recently updated": "updated", "Title": "title", "Vendor": "vendor",
         "Expiry date": "expiry", "Value (highest)": "value"}
EXPIRING = {"Any time": None, "Within 30 days": 30, "Within 60 days": 60, "Within 90 days": 90}
VIEWS = {"Current agreements": "current", "All documents (incl. amendments, superseded)": "all"}
RELATION_LABELS = {"original": "Original agreement", "amendment": "Amendment",
                   "renewal": "Renewal", "revision": "Revised version (replaces another)"}
VIS_CHOICES = [("Everyone in the organisation", "organization"), ("Only me (and administrators)", "private")]

# Order MUST match the creation order of components in build_form().
FORM_FIELDS = ["title", "vendor", "force_new", "client", "category", "department", "status",
               "value", "currency", "signed", "start", "end", "auto_renews", "notice_days",
               "visibility", "payment_terms", "notes", "effective_from", "effective_to"]


def download_dir() -> Path:
    base = os.environ.get("GRADIO_TEMP_DIR") or None
    d = Path(base or "/tmp") / "dealwise_downloads"
    d.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 3600  # purge old plaintext copies
    for p in d.rglob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass
    return d


def build_form():
    f = {}
    f["title"] = gr.Textbox(label="Title", placeholder="Defaults to the file name if left empty")
    with gr.Row():
        f["vendor"] = gr.Dropdown(label="Vendor", choices=[], allow_custom_value=True, scale=3,
                                  info="Pick an existing vendor or type a new one")
        f["force_new"] = gr.Checkbox(label="Create as a new vendor even if a similar name exists", scale=2)
    with gr.Row():
        f["client"] = gr.Textbox(label="Client / our entity")
        f["category"] = gr.Dropdown(label="Category", choices=[], allow_custom_value=True)
        f["department"] = gr.Dropdown(label="Department", choices=[], allow_custom_value=True)
        f["status"] = gr.Dropdown(label="Status", choices=list(STATUSES), value="active")
    with gr.Row():
        f["value"] = gr.Textbox(label="Contract value", placeholder="e.g. 125000.00")
        f["currency"] = gr.Textbox(label="Currency", placeholder="USD, EUR, INR ...", max_lines=1)
        f["signed"] = gr.Textbox(label="Signed date", placeholder="YYYY-MM-DD")
        f["start"] = gr.Textbox(label="Start date", placeholder="YYYY-MM-DD")
        f["end"] = gr.Textbox(label="End / expiry date", placeholder="YYYY-MM-DD")
    with gr.Row():
        f["auto_renews"] = gr.Checkbox(label="Renews automatically")
        f["notice_days"] = gr.Textbox(label="Renewal notice (days before end)", max_lines=1)
        f["visibility"] = gr.Radio(label="Who can see this contract", choices=VIS_CHOICES, value="organization")
    f["payment_terms"] = gr.Textbox(label="Payment terms (as entered by you)", lines=2)
    f["notes"] = gr.Textbox(label="Notes", lines=2)
    with gr.Row():
        f["effective_from"] = gr.Textbox(label="This document governs from", placeholder="defaults to start date")
        f["effective_to"] = gr.Textbox(label="This document governs until", placeholder="defaults to end date")
    assert list(f) == FORM_FIELDS, "FORM_FIELDS is out of sync with build_form()"
    return f


def _to_changes(vals: dict) -> dict:
    notice = (vals["notice_days"] or "").strip()
    if notice and not notice.isdigit():
        raise DealWiseError("Renewal notice must be a whole number of days.")
    return {
        "title": vals["title"], "client_name": vals["client"], "category": vals["category"],
        "department": vals["department"], "status": vals["status"],
        "contract_value": vals["value"], "currency": vals["currency"],
        "signed_date": vals["signed"], "start_date": vals["start"], "end_date": vals["end"],
        "auto_renews": vals["auto_renews"], "renewal_notice_days": int(notice) if notice else None,
        "payment_terms": vals["payment_terms"], "notes": vals["notes"],
        "visibility": vals["visibility"], "effective_from": vals["effective_from"],
        "effective_to": vals["effective_to"],
    }


class ContractsPage(BasePage):
    def __init__(self, app, tab):
        self._app = app
        self.tab = tab
        self.on_building_ui()

    # ------------------------------------------------------------ UI --
    def on_building_ui(self):
        self.rows_state = gr.State({})
        self.selected_id = gr.State(None)
        with gr.Tabs():
            with gr.Tab("Repository"):
                with gr.Row():
                    self.f_search = gr.Textbox(label="Search", placeholder="Title, vendor, client, notes, file name...", scale=3)
                    self.f_vendor = gr.Dropdown(label="Vendor", choices=[("All vendors", "")], value="", scale=2)
                    self.f_category = gr.Dropdown(label="Category", choices=[ALL], value=ALL, scale=1)
                    self.f_department = gr.Dropdown(label="Department", choices=[ALL], value=ALL, scale=1)
                with gr.Row():
                    self.f_status = gr.Dropdown(label="Status", choices=STATUS_CHOICES, value=ALL)
                    self.f_expiring = gr.Dropdown(label="Expiring", choices=list(EXPIRING), value="Any time")
                    self.f_view = gr.Dropdown(label="Show", choices=list(VIEWS), value="Current agreements")
                    self.f_sort = gr.Dropdown(label="Sort by", choices=list(SORTS), value="Recently updated")
                    self.btn_refresh = gr.Button("Refresh", scale=0, min_width=100)
                self.list_info = gr.Markdown("")
                self.table = gr.Dataframe(headers=LIST_HEADERS, datatype=["str"] * len(LIST_HEADERS),
                                          interactive=False, wrap=True,
                                          col_count=(len(LIST_HEADERS), "fixed"))
                with gr.Group(visible=False) as self.detail:
                    self.detail_head = gr.Markdown("")
                    self.edit_form = build_form()
                    with gr.Row():
                        self.btn_save = gr.Button("Save changes", variant="primary")
                        self.btn_download = gr.Button("Download original file")
                        self.confirm_delete = gr.Checkbox(label="Confirm: permanently delete this contract and its stored file")
                        self.btn_delete = gr.Button("Delete", variant="stop")
                    self.download_file = gr.File(label="Original file", interactive=False, visible=False)
                    gr.Markdown("### Versions and lifecycle")
                    self.lineage_md = gr.Markdown("")
                    with gr.Row():
                        self.rel_type = gr.Dropdown(label="This document is a(n)", choices=[(RELATION_LABELS[r], r) for r in RELATION_TYPES], value="original")
                        self.rel_parent = gr.Dropdown(label="...of contract", choices=[], info="Required unless this is an original agreement")
                        self.btn_relink = gr.Button("Update link", scale=0)
                    with gr.Row():
                        self.active_on = gr.Textbox(label="Which version was active on", placeholder="YYYY-MM-DD", scale=2)
                        self.btn_active = gr.Button("Look up", scale=0)
                    self.active_md = gr.Markdown("")
            with gr.Tab("Add contract"):
                gr.Markdown("Upload a signed contract (PDF, Word, Excel, text or scanned image). "
                            "The file is stored on this server; **no content is sent to any AI model** in this version.")
                self.up_file = gr.File(label="Contract file", file_count="single", type="filepath")
                self.add_form = build_form()
                with gr.Row():
                    self.up_rel = gr.Radio(label="This upload is", choices=[(RELATION_LABELS[r], r) for r in RELATION_TYPES], value="original")
                    self.up_parent = gr.Dropdown(label="...linked to existing contract", choices=[], info="Needed for amendments, renewals and revisions")
                self.btn_upload = gr.Button("Save contract", variant="primary")
                self.up_status = gr.Markdown("")

    # --------------------------------------------------------- helpers --
    def _svc(self, user_id) -> ContractService:
        return ContractService(get_engine(), get_ctx(self._app, user_id))

    def _choices(self, svc: ContractService):
        vendors = VendorService(get_engine(), svc.user).list()
        facets = svc.facets()
        docs = svc.list(view="all", sort="title")
        parents = [(f"{v.c.title} ({v.vendor_name or 'no vendor'}) [{v.lifecycle}]", v.id) for v in docs if v.can_edit]
        return vendors, facets, parents

    def _form_values(self, v) -> list:
        c = v.c
        d = {
            "title": c.title, "vendor": v.vendor_name or "", "force_new": False,
            "client": c.client_name or "", "category": c.category or "",
            "department": c.department or "", "status": c.status,
            "value": "" if c.contract_value is None else f"{c.contract_value:.2f}",
            "currency": c.currency or "", "signed": fmt_date(c.signed_date),
            "start": fmt_date(c.start_date), "end": fmt_date(c.end_date),
            "auto_renews": c.auto_renews,
            "notice_days": "" if c.renewal_notice_days is None else str(c.renewal_notice_days),
            "visibility": c.visibility, "payment_terms": c.payment_terms or "",
            "notes": c.notes or "", "effective_from": fmt_date(c.effective_from),
            "effective_to": fmt_date(c.effective_to),
        }
        return [d[k] for k in FORM_FIELDS]

    # -------------------------------------------------------- listing --
    def list_contracts(self, user_id, search, vendor, category, department, status, expiring, view, sort):
        empty = ([], {}, "", gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update(), gr.update())
        try:
            svc = self._svc(user_id)
            flt = ContractFilters(
                search=search or "", vendor_id=vendor or None,
                category=None if category in (None, ALL) else category,
                department=None if department in (None, ALL) else department,
                status=None if status in (None, ALL) else status,
                expiring_within_days=EXPIRING.get(expiring),
            )
            views = svc.list(flt, view=VIEWS.get(view, "current"), sort=SORTS.get(sort, "updated"))
            vendors, facets, parents = self._choices(svc)
        except Unauthenticated:
            return ([], {}, SIGN_IN_MSG) + empty[3:]
        except Exception as exc:
            warn(exc)
            return empty
        rows, mapping = [], {}
        for v in views:
            c = v.c
            ref = c.id[:8]
            mapping[ref] = c.id
            rows.append([
                ref, c.title, v.vendor_name or "", c.category or "", v.effective_status,
                money(v.effective_value, v.effective_currency),
                fmt_date(v.effective_end_date), v.lifecycle,
                "Private" if c.visibility == "private" else "Organisation",
            ])
        info = f"{len(rows)} contract(s). Click a row to view or edit it." if rows else (
            "No contracts match. Add one in the 'Add contract' tab, or clear the filters.")
        vendor_choices = [("All vendors", "")] + [(f"{s.name} ({s.contract_count})", s.id) for s in vendors]
        names = [s.name for s in vendors]
        return self._pack(rows, mapping, info, vendor_choices, facets, names, parents)

    def _pack(self, rows, mapping, info, vendor_choices, facets, names, parents):
        return (
            rows, mapping, info,
            gr.update(choices=vendor_choices),
            gr.update(choices=[ALL] + facets["categories"]),
            gr.update(choices=[ALL] + facets["departments"]),
            gr.update(choices=names),
            gr.update(choices=names),
            gr.update(choices=facets["categories"]),
            gr.update(choices=facets["departments"]),
        )

    # ---------------------------------------------------------- detail --
    def open_detail(self, evt: gr.SelectData, mapping, user_id):
        n_form = len(FORM_FIELDS)
        hidden = [gr.update(visible=False), None, ""] + [gr.update()] * n_form + [gr.update()] * 7
        try:
            ref = (evt.row_value or [None])[0]
            cid = (mapping or {}).get(ref)
            if not cid:
                return hidden
            svc = self._svc(user_id)
            v = svc.get(cid)
            lineage = svc.lineage(cid)
            _, _, parents = self._choices(svc)
        except Exception as exc:
            warn(exc)
            return hidden
        owner = usernames({v.c.owner_id}).get(v.c.owner_id, "unknown")
        head = f"### {v.c.title}\nOwner: **{owner}**. Status: **{v.effective_status}**. " + (
            "" if v.can_edit else "You can view this contract but only its owner or an administrator can change it.")
        table = ["| Version | Type | Title | Effective | Value | Ends |", "|---|---|---|---|---|---|"]
        for x in lineage:
            mark = " (this)" if x.id == cid else ""
            table.append(
                f"| v{x.c.version_number} | {x.lifecycle}{' (superseded)' if x.superseded else ''} | "
                f"{x.c.title.replace('|', '/')}{mark} | {fmt_date(x.c.effective_from or x.c.start_date) or '-'} | "
                f"{money(x.c.contract_value, x.c.currency)} | {fmt_date(x.c.end_date) or '-'} |")
        parent_choices = [p for p in parents if p[1] != cid]
        editable = gr.update(interactive=v.can_edit)
        return [
            gr.update(visible=True), cid, head, *self._form_values(v),
            "\n".join(table),
            gr.update(value=v.c.relation_type, interactive=v.can_edit),
            gr.update(choices=parent_choices, value=v.c.parent_contract_id, interactive=v.can_edit),
            editable, editable, gr.update(value=None, visible=False), "",
        ]

    def detail_outputs(self):
        return [self.detail, self.selected_id, self.detail_head, *self.edit_form.values(),
                self.lineage_md, self.rel_type, self.rel_parent, self.btn_save, self.btn_delete,
                self.download_file, self.active_md]

    # ----------------------------------------------------- mutations --
    def save(self, user_id, cid, *vals):
        vals = dict(zip(FORM_FIELDS, vals))
        try:
            svc = self._svc(user_id)
            svc.update_contract(cid, _to_changes(vals), vendor_name=vals["vendor"],
                                force_new_vendor=vals["force_new"])
            gr.Info("Changes saved.")
        except Exception as exc:
            warn(exc)

    def delete(self, user_id, cid, confirmed):
        if not confirmed:
            gr.Warning("Tick the confirmation box to delete this contract.")
            return gr.update()
        try:
            self._svc(user_id).delete_contract(cid)
            gr.Info("Contract deleted.")
            return gr.update(visible=False)
        except Exception as exc:
            warn(exc)
            return gr.update()

    def download(self, user_id, cid):
        try:
            path = self._svc(user_id).export_file(cid, download_dir())
            return gr.update(value=str(path), visible=True)
        except Exception as exc:
            warn(exc)
            return gr.update(visible=False)

    def relink(self, user_id, cid, rel, parent):
        try:
            self._svc(user_id).set_lineage(cid, rel, parent or None)
            gr.Info("Version link updated.")
        except Exception as exc:
            warn(exc)

    def active_lookup(self, user_id, cid, on):
        try:
            d = parse_date(on, "Date")
            if d is None:
                return "Enter a date (YYYY-MM-DD)."
            av = self._svc(user_id).active_version_on(cid, d)
        except Exception as exc:
            warn(exc)
            return ""
        if av.base is None:
            return "**No active version recorded for that date.** " + " ".join(av.notes)
        lines = [f"On **{d.isoformat()}** the governing document was **{av.base.title}** ({av.base.relation_type} v{av.base.version_number})."]
        if av.amendments:
            lines.append("Amendments in force: " + "; ".join(f"{a.title} (from {fmt_date(a.effective_from or a.start_date)})" for a in av.amendments) + ".")
        lines += av.notes
        return "\n\n".join(lines)

    def upload(self, user_id, file_path, rel, parent, *vals):
        vals = dict(zip(FORM_FIELDS, vals))
        if not file_path:
            return "**Choose a file first.**", gr.update(), gr.update()
        try:
            svc = self._svc(user_id)
            changes = _to_changes(vals)
            title = changes.pop("title")
            v = svc.create_contract(
                file_path=file_path, original_filename=os.path.basename(file_path), title=title or "",
                vendor_name=vals["vendor"], force_new_vendor=vals["force_new"],
                relation_type=rel, parent_contract_id=parent or None, **changes,
            )
        except DealWiseError as exc:
            return f"**Not saved:** {exc}", gr.update(), gr.update()
        except Exception as exc:
            warn(exc)
            return "**Not saved.** See the notification for details.", gr.update(), gr.update()
        return f"Saved **{v.c.title}** as {v.lifecycle.lower()}. Find it in the Repository tab.", gr.update(value=None), "ok"

    def clear_add_form(self, flag):
        if flag != "ok":
            return [gr.update()] * len(FORM_FIELDS)
        defaults = dict(status="active", visibility="organization", force_new=False, auto_renews=False)
        return [defaults.get(k, "") for k in FORM_FIELDS]

    # ---------------------------------------------------------- events --
    def on_subscribe_public_events(self):
        self._app.subscribe_event("onSignIn", self._refresh_def())
        self._app.subscribe_event("onSignOut", self._refresh_def())

    def _filter_inputs(self):
        return [self._app.user_id, self.f_search, self.f_vendor, self.f_category, self.f_department,
                self.f_status, self.f_expiring, self.f_view, self.f_sort]

    def _list_outputs(self):
        return [self.table, self.rows_state, self.list_info, self.f_vendor, self.f_category,
                self.f_department, self.edit_form["vendor"], self.add_form["vendor"],
                self.add_form["category"], self.add_form["department"]]

    def _refresh_def(self):
        return {"fn": self.list_contracts, "inputs": self._filter_inputs(),
                "outputs": self._list_outputs(), "show_progress": "hidden"}

    def on_register_events(self):
        args = self._refresh_def()
        self.btn_refresh.click(**args)
        self.f_search.submit(**args)
        self.tab.select(**args)
        for dd in (self.f_vendor, self.f_category, self.f_department, self.f_status,
                   self.f_expiring, self.f_view, self.f_sort):
            dd.input(**args)

        self.table.select(self.open_detail, inputs=[self.rows_state, self._app.user_id],
                          outputs=self.detail_outputs(), show_progress="hidden")

        form = list(self.edit_form.values())
        self.btn_save.click(self.save, inputs=[self._app.user_id, self.selected_id, *form], outputs=None
                            ).then(**args)
        self.btn_delete.click(self.delete, inputs=[self._app.user_id, self.selected_id, self.confirm_delete],
                              outputs=[self.detail]).then(**args)
        self.btn_download.click(self.download, inputs=[self._app.user_id, self.selected_id],
                                outputs=[self.download_file])
        self.btn_relink.click(self.relink, inputs=[self._app.user_id, self.selected_id, self.rel_type, self.rel_parent],
                              outputs=None).then(**args)
        self.btn_active.click(self.active_lookup,
                              inputs=[self._app.user_id, self.selected_id, self.active_on],
                              outputs=[self.active_md])

        flag = gr.State("")
        add_inputs = [self._app.user_id, self.up_file, self.up_rel, self.up_parent, *self.add_form.values()]
        self.btn_upload.click(self.upload, inputs=add_inputs,
                              outputs=[self.up_status, self.up_file, flag]).then(
            self.clear_add_form, inputs=[flag], outputs=list(self.add_form.values())
        ).then(**args).then(self._parents, inputs=[self._app.user_id], outputs=[self.up_parent])
        self.tab.select(self._parents, inputs=[self._app.user_id], outputs=[self.up_parent])

    def _parents(self, user_id):
        try:
            _, _, parents = self._choices(self._svc(user_id))
            return gr.update(choices=parents)
        except Exception:
            return gr.update(choices=[])
