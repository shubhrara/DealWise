"""Runtime configuration, read from environment variables (never hard-coded)."""

from pathlib import Path

from decouple import config


def max_upload_bytes() -> int:
    return config("DEALWISE_MAX_UPLOAD_MB", default=50, cast=int) * 1024 * 1024


def persist_login() -> bool:
    """Keep browser credentials across browser restarts (less secure)."""
    return config("DEALWISE_PERSIST_LOGIN", default=False, cast=bool)


def require_strong_admin() -> bool:
    """Refuse to start while the default admin/admin credentials are valid."""
    return config("DEALWISE_REQUIRE_STRONG_ADMIN", default=False, cast=bool)


def enabled() -> bool:
    return config("DEALWISE_ENABLED", default=True, cast=bool)


def storage_root(base: str | Path | None = None) -> Path:
    """Directory for original contract files (not served by Gradio)."""
    if base is None:
        from theflow.settings import settings

        base = getattr(settings, "KH_FILESTORAGE_PATH", "./dealwise_files")
    root = Path(base) / "dealwise"
    root.mkdir(parents=True, exist_ok=True)
    return root
