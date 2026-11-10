from html import escape as e

import gradio as gr
from dealwise import config, privacy
from dealwise.bootstrap import default_admin_in_use
from dealwise.security.context import Unauthenticated
from ktem.app import BasePage
from theflow.settings import settings

from .common import SIGN_IN_MSG, get_ctx, warn
from .render import message_html

EXTERNAL_SCRIPTS = [
    ("cdn.skypack.dev", "PDF viewer used by the Chat / Files tabs"),
    ("cdnjs.cloudflare.com", "Mention/autocomplete library"),
    ("cdn.jsdelivr.net", "Mind-map and search libraries"),
]


class SecurityPage(BasePage):
    def __init__(self, app, tab):
        self._app = app
        self.tab = tab
        self.on_building_ui()

    def on_building_ui(self):
        self.html = gr.HTML(message_html(SIGN_IN_MSG))
        self.refresh_btn = gr.Button("Refresh", size="sm", scale=0)

    def _providers_table(self) -> str:
        from ktem.embeddings.manager import embedding_models_manager
        from ktem.llms.manager import llms
        from ktem.rerankings.manager import reranking_models_manager

        rows = privacy.describe_providers(
            {"LLM": llms, "Embedding": embedding_models_manager, "Reranking": reranking_models_manager}
        )
        if not rows:
            return "<p class='dw-muted'>No models are configured.</p>"
        body = "".join(
            f"<tr><td>{e(r['kind'])}</td><td>{e(r['name'])}{' (default)' if r['default'] else ''}</td>"
            f"<td>{e(r['provider'])}</td><td>{e(r['model'])}</td><td>{e(r['host'] or '-')}</td>"
            f"<td><b>{e(r['location'])}</b></td></tr>"
            for r in rows
        )
        return (
            "<table style='width:100%;border-collapse:collapse;font-size:13px'><thead><tr>"
            "<th align=left>Type</th><th align=left>Name</th><th align=left>Provider</th>"
            "<th align=left>Model</th><th align=left>Endpoint host</th><th align=left>Location</th>"
            f"</tr></thead><tbody>{body}</tbody></table>"
            "<p class='dw-muted'>'External' means data sent to that model leaves this server for a "
            "third-party service. 'Unknown' should be treated as external. API keys are never displayed.</p>"
        )

    def refresh(self, user_id):
        try:
            ctx = get_ctx(self._app, user_id)
        except Unauthenticated:
            return message_html(SIGN_IN_MSG)
        try:
            parts = ['<div class="dw-root">']
            flows = "".join(
                f"<tr><td>{e(f['feature'])}</td><td>{'Yes' if f['sends_contract_content_to_models'] else '<b>No</b>'}</td>"
                f"<td>{e(f['detail'])}</td></tr>" for f in privacy.DATA_FLOWS
            )
            parts.append(
                "<h3>Is contract content sent to an AI model?</h3>"
                "<table style='width:100%;border-collapse:collapse;font-size:13px'><thead><tr>"
                "<th align=left>DealWise feature</th><th align=left>Sends contract content</th>"
                f"<th align=left>Detail</th></tr></thead><tbody>{flows}</tbody></table>"
                "<p class='dw-muted'>Every later feature that sends content to a model will be listed here "
                "before it ships. The upstream Chat and Files tabs do send retrieved text to the model "
                "selected in Settings.</p>"
            )
            parts.append("<h3 style='margin-top:24px'>Configured models</h3>" + self._providers_table())
            parts.append(
                "<h3 style='margin-top:24px'>Access control</h3><ul>"
                f"<li>Signed in as <b>{e(ctx.username or ctx.user_id)}</b> ({'administrator' if ctx.is_admin else 'standard user'}).</li>"
                "<li>Contracts marked <b>Only me</b> are visible to their owner and administrators. "
                "Dashboard totals, vendor lists and search results only include contracts you may see.</li>"
                "<li>Only a contract's owner or an administrator can edit or delete it.</li>"
                "<li>Original files are stored outside any web-served folder; downloads are re-checked "
                "against your permissions and every download is recorded in the audit log.</li>"
                "<li>Passwords use salted PBKDF2; repeated failed sign-ins are temporarily locked out.</li>"
                "<li>Logs record ids and field names only, never contract text; secrets are masked.</li></ul>"
            )
            if ctx.is_admin:
                warnings = []
                if not self._app.f_user_management:
                    warnings.append("User management is OFF: everyone who can reach this app acts as an administrator.")
                elif default_admin_in_use():
                    warnings.append("The administrator account still uses the default password. Change it in Settings now.")
                if getattr(settings, "KH_GRADIO_SHARE", False):
                    warnings.append("KH_GRADIO_SHARE is on: the app is exposed through a public Gradio link.")
                if config.persist_login():
                    warnings.append("DEALWISE_PERSIST_LOGIN is on: credentials stay in the browser after it closes.")
                if warnings:
                    parts.append(
                        "<h3 style='margin-top:24px'>Administrator warnings</h3>"
                        + "".join(f'<div class="dw-callout bad">{e(w)}</div>' for w in warnings)
                    )
                else:
                    parts.append("<h3 style='margin-top:24px'>Administrator warnings</h3><p class='dw-muted'>None.</p>")
            ext = "".join(f"<li>{e(h)}: {e(why)}</li>" for h, why in EXTERNAL_SCRIPTS)
            parts.append(
                "<h3 style='margin-top:24px'>Third-party scripts loaded by your browser</h3>"
                "<p class='dw-muted'>These come from the upstream Kotaemon interface. They do not receive "
                f"contract content, but they are fetched from the internet.</p><ul>{ext}</ul>"
            )
            parts.append("</div>")
            return "".join(parts)
        except Exception as exc:
            warn(exc)
            return message_html("The security summary could not be loaded.", bad=True)

    def on_subscribe_public_events(self):
        d = {"fn": self.refresh, "inputs": [self._app.user_id], "outputs": [self.html], "show_progress": "hidden"}
        self._app.subscribe_event("onSignIn", d)
        self._app.subscribe_event("onSignOut", d)

    def on_register_events(self):
        args = dict(fn=self.refresh, inputs=[self._app.user_id], outputs=[self.html], show_progress="hidden")
        self.refresh_btn.click(**args)
        self.tab.select(**args)
