"""DealWise UI: tabs, theme and branding applied on top of the Kotaemon app."""

from pathlib import Path

import gradio as gr
from dealwise import config

ASSETS = Path(__file__).parent / "assets"
DEFAULT_TAB = "dw-overview-tab"

_STORAGE_JS_OLD = """  globalThis.setStorage = (key, value) => {
    localStorage.setItem(key, value);
  };
  globalThis.getStorage = (key, value) => {
    item = localStorage.getItem(key);
    return item ? item : value;
  };
  globalThis.removeFromStorage = (key) => {
    localStorage.removeItem(key);
  };"""

# DealWise: keep the sign-in credentials for the browser session only.
_STORAGE_JS_NEW = """  const dwCred = (key) => key === "username" || key === "password";
  globalThis.setStorage = (key, value) => {
    (dwCred(key) ? sessionStorage : localStorage).setItem(key, value);
  };
  globalThis.getStorage = (key, value) => {
    if (dwCred(key)) { localStorage.removeItem(key); }  // purge legacy persisted credentials
    item = (dwCred(key) ? sessionStorage : localStorage).getItem(key);
    return item ? item : value;
  };
  globalThis.removeFromStorage = (key) => {
    localStorage.removeItem(key);
    sessionStorage.removeItem(key);
  };"""


def apply_branding(app) -> None:
    """Theme, CSS, favicon and session-only credential storage."""
    from ktem.assets import KotaemonTheme

    class DealWiseTheme(KotaemonTheme):
        def __init__(self):
            super().__init__(
                font=("ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "Helvetica Neue", "Arial", "sans-serif"),
                font_mono=("ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"),
            )
            self.name = "dealwise"

    app._theme = DealWiseTheme()
    app._css = app._css + "\n" + (ASSETS / "dealwise.css").read_text()
    app._favicon = str(ASSETS / "favicon.svg")
    if not config.persist_login() and _STORAGE_JS_OLD in app._js:
        app._js = app._js.replace(_STORAGE_JS_OLD, _STORAGE_JS_NEW)


def build_tabs(app) -> None:
    """Create the DealWise tabs. Must be called inside ``with gr.Tabs()``."""
    from .contracts import ContractsPage
    from .overview import OverviewPage
    from .security_page import SecurityPage
    from .vendors import VendorsPage

    visible = not app.f_user_management
    app._default_tab = DEFAULT_TAB
    specs = [
        ("dw-overview-tab", "Overview", "dw_overview", OverviewPage),
        ("dw-contracts-tab", "Contracts", "dw_contracts", ContractsPage),
        ("dw-vendors-tab", "Vendors", "dw_vendors", VendorsPage),
    ]
    for tab_id, label, attr, page_cls in specs:
        with gr.Tab(label, elem_id=tab_id, id=tab_id, visible=visible) as tab:
            app._tabs[tab_id] = tab
            setattr(app, attr, page_cls(app, tab))


def build_security_tab(app) -> None:
    """Security & Privacy sits next to Settings; call inside ``gr.Tabs()``."""
    from .security_page import SecurityPage

    with gr.Tab("Security & Privacy", elem_id="dw-security-tab", id="dw-security-tab",
                visible=not app.f_user_management) as tab:
        app._tabs["dw-security-tab"] = tab
        app.dw_security = SecurityPage(app, tab)
