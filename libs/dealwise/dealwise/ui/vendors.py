import gradio as gr
from dealwise.constants import ROLE_HISTORICAL
from dealwise.db import get_engine
from dealwise.security.context import Unauthenticated
from dealwise.services import ContractFilters, ContractService, VendorService
from ktem.app import BasePage

from .common import SIGN_IN_MSG, fmt_date, get_ctx, warn
from .render import money

HEADERS = ["Ref", "Vendor", "Contracts", "Also known as", "Website"]


class VendorsPage(BasePage):
    def __init__(self, app, tab):
        self._app = app
        self.tab = tab
        self.on_building_ui()

    def on_building_ui(self):
        self.rows_state = gr.State({})
        self.selected = gr.State(None)
        gr.Markdown(
            "One record per vendor. Contracts are linked to a vendor record, so every contract with "
            "the same vendor appears together. DealWise matches names ignoring case, punctuation and "
            "suffixes such as Inc/Ltd/LLC, and asks before creating a vendor with a very similar name."
        )
        with gr.Row():
            self.search = gr.Textbox(label="Search vendors", scale=3)
            self.btn_refresh = gr.Button("Refresh", scale=0)
        self.info = gr.Markdown("")
        self.table = gr.Dataframe(headers=HEADERS, datatype=["str"] * len(HEADERS), interactive=False,
                                  wrap=True, col_count=(len(HEADERS), "fixed"))
        with gr.Group(visible=False) as self.detail:
            self.head = gr.Markdown("")
            self.name = gr.Textbox(label="Vendor name")
            self.website = gr.Textbox(label="Website")
            self.aliases = gr.Textbox(label="Also known as (comma separated)",
                                      info="Other names this vendor appears under in contracts")
            self.notes = gr.Textbox(label="Notes", lines=2)
            self.btn_save = gr.Button("Save vendor", variant="primary")
            gr.Markdown("**Contracts with this vendor** (only those you have access to)")
            self.contracts = gr.Dataframe(headers=["Title", "Lifecycle", "Status", "Value", "Expires"],
                                          datatype=["str"] * 5, interactive=False, wrap=True,
                                          col_count=(5, "fixed"))
            gr.Markdown("Vendor performance is derived only from outcomes recorded against contracts; "
                        "none is inferred from contract text.")
        with gr.Accordion("Add a vendor", open=False):
            self.new_name = gr.Textbox(label="Vendor name")
            self.new_site = gr.Textbox(label="Website")
            self.new_force = gr.Checkbox(label="Create even if a similar vendor exists")
            self.btn_add = gr.Button("Add vendor")
        with gr.Accordion("Merge duplicate vendors (administrators)", open=False):
            gr.Markdown("Moves all contracts from the first vendor to the second and keeps the first "
                        "name as an alias. This cannot be undone.")
            self.merge_src = gr.Dropdown(label="Duplicate vendor (will be removed)", choices=[])
            self.merge_tgt = gr.Dropdown(label="Keep this vendor", choices=[])
            self.btn_merge = gr.Button("Merge", variant="stop")

    def _vs(self, user_id):
        return VendorService(get_engine(), get_ctx(self._app, user_id))

    def refresh(self, user_id, search):
        try:
            vs = self._vs(user_id)
            items = vs.list(search or "")
            allv = vs.list()
        except Unauthenticated:
            return [], {}, SIGN_IN_MSG, gr.update(), gr.update()
        except Exception as exc:
            warn(exc)
            return [], {}, "", gr.update(), gr.update()
        rows, mapping = [], {}
        for i, s in enumerate(items):
            mapping[str(i)] = s.id
            rows.append([s.id[:8], s.name, str(s.contract_count), ", ".join(s.aliases), s.website or ""])
        choices = [(s.name, s.id) for s in allv]
        info = f"{len(rows)} vendor(s). Click a row to edit it." if rows else "No vendors yet. They are created when you add contracts, or you can add one below."
        return rows, mapping, info, gr.update(choices=choices), gr.update(choices=choices)

    def open_detail(self, evt: gr.SelectData, mapping, user_id):
        blank = [gr.update(visible=False), None, "", "", "", "", "", []]
        try:
            row_index = evt.index[0] if isinstance(evt.index, (tuple, list)) else evt.index
            vid = (mapping or {}).get(str(row_index))
            if not vid:
                return blank
            ctx = get_ctx(self._app, user_id)
            s = VendorService(get_engine(), ctx).get(vid)
            cs = ContractService(get_engine(), ctx).list(ContractFilters(vendor_id=vid), view="all", sort="title")
        except Exception as exc:
            warn(exc)
            return blank
        rows = [[v.c.title, v.lifecycle, v.effective_status, money(v.effective_value, v.effective_currency),
                 fmt_date(v.effective_end_date)] for v in cs]
        return [gr.update(visible=True), vid, f"### {s.name}", s.name, s.website or "",
                ", ".join(s.aliases), s.notes or "", rows]

    def save(self, user_id, vid, name, website, aliases, notes):
        try:
            self._vs(user_id).update(
                vid, name=name, website=website, notes=notes,
                aliases=[a for a in (aliases or "").split(",")],
            )
            gr.Info("Vendor saved.")
        except Exception as exc:
            warn(exc)

    def add(self, user_id, name, site, force):
        try:
            self._vs(user_id).create(name, website=site, force_new=force)
            gr.Info("Vendor added.")
            return "", "", False
        except Exception as exc:
            warn(exc)
            return gr.update(), gr.update(), gr.update()

    def merge(self, user_id, src, tgt):
        try:
            if not src or not tgt:
                gr.Warning("Choose both vendors.")
                return
            self._vs(user_id).merge(src, tgt)
            gr.Info("Vendors merged.")
        except Exception as exc:
            warn(exc)

    def on_subscribe_public_events(self):
        d = {"fn": self.refresh, "inputs": [self._app.user_id, self.search],
             "outputs": [self.table, self.rows_state, self.info, self.merge_src, self.merge_tgt],
             "show_progress": "hidden"}
        self._app.subscribe_event("onSignIn", d)
        self._app.subscribe_event("onSignOut", d)

    def on_register_events(self):
        args = dict(fn=self.refresh, inputs=[self._app.user_id, self.search],
                    outputs=[self.table, self.rows_state, self.info, self.merge_src, self.merge_tgt],
                    show_progress="hidden")
        self.btn_refresh.click(**args)
        self.search.submit(**args)
        self.tab.select(**args)
        self.table.select(self.open_detail, inputs=[self.rows_state, self._app.user_id],
                          outputs=[self.detail, self.selected, self.head, self.name, self.website,
                                   self.aliases, self.notes, self.contracts], show_progress="hidden")
        self.btn_save.click(self.save, inputs=[self._app.user_id, self.selected, self.name, self.website,
                                               self.aliases, self.notes], outputs=None).then(**args)
        self.btn_add.click(self.add, inputs=[self._app.user_id, self.new_name, self.new_site, self.new_force],
                           outputs=[self.new_name, self.new_site, self.new_force]).then(**args)
        self.btn_merge.click(self.merge, inputs=[self._app.user_id, self.merge_src, self.merge_tgt],
                             outputs=None).then(**args)
