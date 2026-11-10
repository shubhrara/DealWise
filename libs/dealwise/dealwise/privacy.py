"""Transparency about where data could be processed.

``describe_providers`` reports each configured LLM / embedding / reranking
model with its provider class, model name and endpoint *host*. API keys and
any other part of the model spec are never returned.
"""

import ipaddress
import re
from urllib.parse import urlparse

_LOCAL_CLASSES = ("llamacpp", "fastembed", "huggingface")
_EXTERNAL_CLASSES = ("anthropic", "gemini", "google", "cohere", "voyage", "mistral")
_ENDPOINT_KEYS = ("base_url", "azure_endpoint", "endpoint", "api_base", "url")


def _host(spec: dict) -> str:
    for key in _ENDPOINT_KEYS:
        val = spec.get(key)
        if val and isinstance(val, str):
            parsed = urlparse(val if "://" in val else f"//{val}")
            return (parsed.hostname or "").lower()
    return ""


def is_local_host(host: str) -> bool:
    if not host:
        return False
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
        return ip.is_loopback or ip.is_private or ip.is_link_local
    except ValueError:
        return False


def classify(spec: dict) -> tuple[str, str]:
    """Return (host, 'Local' | 'External' | 'Unknown'). Unknown must be treated
    as external."""
    type_name = str(spec.get("__type__", "")).split(".")[-1].lower()
    host = _host(spec)
    if host:
        return host, ("Local" if is_local_host(host) else "External")
    if any(k in type_name for k in _LOCAL_CLASSES):
        return "", "Local"
    if any(k in type_name for k in _EXTERNAL_CLASSES):
        return "(provider default)", "External"
    if "openai" in type_name:
        return "api.openai.com (default)", "External"
    return "", "Unknown"


def _model_name(spec: dict) -> str:
    for key in ("model", "model_name", "azure_deployment", "deployment"):
        if spec.get(key):
            return str(spec[key])
    return ""


def describe_providers(managers: dict) -> list[dict]:
    """``managers`` maps a label ('LLM', 'Embedding', ...) to a manager whose
    ``info()`` returns {name: {'spec': {...}, 'default': bool}}."""
    rows = []
    for kind, mgr in managers.items():
        try:
            info = mgr.info()
        except Exception:
            continue
        for name, item in info.items():
            spec = item.get("spec", {}) if isinstance(item, dict) else {}
            host, where = classify(spec)
            rows.append(
                {
                    "kind": kind,
                    "name": name,
                    "provider": str(spec.get("__type__", "")).split(".")[-1],
                    "model": re.sub(r"\s+", " ", _model_name(spec)),
                    "host": host,
                    "location": where,
                    "default": bool(item.get("default")) if isinstance(item, dict) else False,
                }
            )
    return rows


# What DealWise itself sends to models, kept next to the code that does it.
# Phase 1 sends nothing; later phases must add entries here when they add calls.
DATA_FLOWS = [
    {
        "feature": "Contract upload, metadata, vendors, dashboard, search",
        "phase": "1",
        "sends_contract_content_to_models": False,
        "detail": "Files are stored on this server; metadata is stored in the local database. "
        "No model is called.",
    },
]
