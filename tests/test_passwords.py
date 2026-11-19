"""Phase 8: password hashing (passwords.py) and the password rules."""

import pytest

from tradeledger.config import Config, ProductionConfig, TestingConfig
from tradeledger.passwords import (
    MAX_LENGTH,
    MIN_LENGTH,
    OWASP_MINIMUM,
    HashParameters,
    hash_password,
    needs_rehash,
    parameters_from_config,
    password_problem,
    stored_parameters,
    verify_password,
)

FAST = HashParameters(memory_kib=1024, iterations=1, lanes=1)


def test_production_uses_owasp_argon2id_parameters():
    """The fast test settings must never leak into real environments."""
    for config in (Config, ProductionConfig):
        assert parameters_from_config(vars(config) | vars(Config)) == OWASP_MINIMUM
    assert HashParameters(memory_kib=19456, iterations=2, lanes=1) == OWASP_MINIMUM
    assert parameters_from_config(vars(Config) | vars(TestingConfig)) == FAST


def test_hash_with_production_parameters_round_trips():
    stored = hash_password("a long correct password", OWASP_MINIMUM)
    assert stored.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    assert verify_password("a long correct password", stored)
    assert not verify_password("a long correct passwore", stored)


def test_hash_is_salted_and_contains_no_password():
    one, two = hash_password("same password here", FAST), hash_password("same password here", FAST)
    assert one != two  # different random salts
    assert "same password" not in one


@pytest.mark.parametrize("stored", ["", "plaintext", "$argon2i$v=19$m=1024,t=1,p=1$abc$def", None, 42])
def test_malformed_or_foreign_hashes_never_verify(stored):
    assert verify_password("anything at all", stored) is False


def test_non_text_password_never_verifies():
    stored = hash_password("text password 123", FAST)
    assert verify_password(None, stored) is False
    assert verify_password(b"text password 123", stored) is False


def test_unicode_is_normalised():
    """The same password typed as a precomposed 'é' or as 'e' + accent."""
    stored = hash_password("café au lait twice", FAST)
    assert verify_password("café au lait twice", stored)


def test_rehash_detection():
    old = hash_password("upgrade me please", FAST)
    assert stored_parameters(old) == FAST
    assert needs_rehash(old, OWASP_MINIMUM)
    assert not needs_rehash(old, FAST)
    assert needs_rehash("not a hash", FAST)


@pytest.mark.parametrize(
    "password, email, expected",
    [
        ("", None, "is required."),
        ("x" * (MIN_LENGTH - 1), None, f"must be at least {MIN_LENGTH} characters."),
        ("x" * (MAX_LENGTH + 1), None, f"must be {MAX_LENGTH} characters or fewer."),
        ("password1234", None, "is too common and easy to guess. Choose a less predictable one."),
        ("aaaaaaaaaaaaaaaa", None, "is too common and easy to guess. Choose a less predictable one."),
        ("trader@example.com", "Trader@Example.com", "must not be your email address."),
        ("four random words here", None, None),
        ("ünïcødé pässwörd ok", None, None),
        ("x" * MAX_LENGTH + "", None, "is too common and easy to guess. Choose a less predictable one."),
    ],
)
def test_password_rules(password, email, expected):
    assert password_problem(password, email=email) == expected


def test_no_composition_rules():
    """NIST SP 800-63B: length matters, forced symbols and capitals do not."""
    assert password_problem("all lower case words only") is None
