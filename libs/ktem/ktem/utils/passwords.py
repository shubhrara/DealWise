"""Password hashing and login throttling.

DealWise change (not part of upstream Kotaemon): upstream stored passwords as
unsalted SHA-256 hex digests. New hashes use salted PBKDF2-HMAC-SHA256.
Legacy SHA-256 hashes still verify and are upgraded on the next successful
login (see ``needs_rehash``).
"""

import base64
import hashlib
import hmac
import secrets
import threading
import time

ALGORITHM = "pbkdf2_sha256"
DEFAULT_ITERATIONS = 600_000


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def hash_password(password: str, iterations: int | None = None) -> str:
    """Return a salted PBKDF2 hash: ``pbkdf2_sha256$<iters>$<salt>$<hash>``."""
    iterations = iterations or DEFAULT_ITERATIONS
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64(salt)}${_b64(digest)}"


def is_legacy_hash(stored: str) -> bool:
    return not (stored or "").startswith(ALGORITHM + "$")


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of a PBKDF2 or legacy SHA-256 hash."""
    if not stored:
        return False
    if is_legacy_hash(stored):
        legacy = hashlib.sha256(password.encode()).hexdigest()
        return hmac.compare_digest(legacy, stored)
    try:
        _, iters, salt_b64, hash_b64 = stored.split("$")
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(iters))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest, expected)


def needs_rehash(stored: str, iterations: int | None = None) -> bool:
    """True for legacy hashes or hashes with fewer iterations than current."""
    if is_legacy_hash(stored):
        return True
    try:
        return int(stored.split("$")[1]) < (iterations or DEFAULT_ITERATIONS)
    except (IndexError, ValueError):
        return True


class LoginThrottle:
    """In-memory lockout after repeated failed logins for the same username.

    Per-process only: adequate for a single-instance deployment; use a shared
    store (or an identity provider) if running several replicas.
    """

    def __init__(self, max_failures: int = 5, window: int = 300, lockout: int = 60):
        self.max_failures = max_failures
        self.window = window
        self.lockout = lockout
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def is_locked(self, key: str) -> bool:
        with self._lock:
            until = self._locked_until.get(key)
            if until is None:
                return False
            if time.time() >= until:
                self._locked_until.pop(key, None)
                self._failures.pop(key, None)
                return False
            return True

    def record_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < self.window]
            recent.append(now)
            self._failures[key] = recent
            if len(recent) >= self.max_failures:
                self._locked_until[key] = now + self.lockout

    def record_success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)
            self._locked_until.pop(key, None)
