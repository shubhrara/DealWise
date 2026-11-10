import gradio as gr
from dealwise.services import compute_dashboard
from dealwise.security.context import Unauthenticated
from dealwise.db import get_engine
from ktem.app import BasePage

from . import render
from .common import SIGN_IN_MSG, get_ctx, warn


class OverviewPage(BasePage):
    def __init__(self, app, tab):
        self._app = app
        self.tab = tab
        self.on_building_ui()

    def on_building_ui(self):
        self.html = gr.HTML(render.message_html(SIGN_IN_MSG))
        self.refresh_btn = gr.Button("Refresh", size="sm", scale=0)

    def refresh(self, user_id):
        try:
            ctx = get_ctx(self._app, user_id)
            return render.dashboard_html(compute_dashboard(get_engine(), ctx))
        except Unauthenticated:
            return render.message_html(SIGN_IN_MSG)
        except Exception as exc:
            warn(exc)
            return render.message_html("The dashboard could not be loaded.", bad=True)

    def on_subscribe_public_events(self):
        self._app.subscribe_event(
            "onSignIn",
            {"fn": self.refresh, "inputs": [self._app.user_id], "outputs": [self.html], "show_progress": "hidden"},
        )
        self._app.subscribe_event(
            "onSignOut",
            {"fn": self.refresh, "inputs": [self._app.user_id], "outputs": [self.html], "show_progress": "hidden"},
        )

    def on_register_events(self):
        args = dict(fn=self.refresh, inputs=[self._app.user_id], outputs=[self.html], show_progress="hidden")
        self.refresh_btn.click(**args)
        self.tab.select(**args)

    def _on_app_created(self):
        self._app.app.load(
            self.refresh, inputs=[self._app.user_id], outputs=[self.html], show_progress="hidden"
        )
