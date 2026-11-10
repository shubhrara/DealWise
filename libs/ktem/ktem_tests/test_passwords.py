import hashlib

from ktem.utils.passwords import (
    LoginThrottle,
    hash_password,
    is_legacy_hash,
    needs_rehash,
    verify_password,
)


def test_new_hash_is_salted_and_verifies():
    h1, h2 = hash_password("S3cret!pw", iterations=1000), hash_password("S3cret!pw", iterations=1000)
    assert h1 != h2 and h1.startswith("pbkdf2_sha256$1000$")
    assert verify_password("S3cret!pw", h1) and not verify_password("wrong", h1)


def test_legacy_sha256_still_verifies_and_is_flagged():
    legacy = hashlib.sha256(b"admin").hexdigest()
    assert is_legacy_hash(legacy) and verify_password("admin", legacy)
    assert not verify_password("nope", legacy)
    assert needs_rehash(legacy)


def test_needs_rehash_respects_iterations():
    assert not needs_rehash(hash_password("x", iterations=5000), iterations=5000)
    assert needs_rehash(hash_password("x", iterations=1000), iterations=5000)


def test_malformed_hashes_do_not_verify():
    assert not verify_password("x", "")
    assert not verify_password("x", "pbkdf2_sha256$notanumber$abc$def")


def test_throttle_locks_and_resets():
    t = LoginThrottle(max_failures=3, window=60, lockout=60)
    for _ in range(2):
        t.record_failure("bob")
    assert not t.is_locked("bob")
    t.record_failure("bob")
    assert t.is_locked("bob") and not t.is_locked("alice")
    t.record_success("bob")
    assert not t.is_locked("bob")


def test_throttle_lockout_expires():
    t = LoginThrottle(max_failures=1, window=60, lockout=0)
    t.record_failure("bob")
    assert not t.is_locked("bob")
