"""Log redaction. DealWise code logs ids and field names only, never contract
text or extracted values; this filter additionally masks secrets that might
appear in exception messages or third-party log lines."""

import logging
import re

_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"), "[REDACTED_KEY]"),
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "[REDACTED_KEY]"),
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{12,}"), "Bearer [REDACTED]"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|secret|token|password|passwd|authorization)"
            r"(\"?\s*[:=]\s*\"?)([^\s\"',;&]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"://([^/\s:@]+):([^/\s@]+)@"), r"://\1:[REDACTED]@"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # never break logging
            pass
        return True


def get_logger(name: str = "dealwise") -> logging.Logger:
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger


def install_on_root_handlers() -> None:
    """Best effort: also mask secrets in records emitted by other libraries."""
    for handler in logging.getLogger().handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
