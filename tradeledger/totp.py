"""Two-step verification codes (TOTP, RFC 6238) and their storage (Phase 8.5).

Plain English
-------------
Two-step verification means signing in needs your password AND a six-digit
code from an authenticator app on your phone (Google Authenticator,
Microsoft Authenticator, 1Password, Aegis, ...). The app and TradeLedger
share a secret; both turn "secret + current time" into the same code,
which changes every 30 seconds. Someone who learns your password still
cannot sign in without your phone.

Technical summary
-----------------
Codes      TOTP as in RFC 6238 over HOTP (RFC 4226): HMAC-SHA1, 30-second
           steps, 6 digits, the defaults every authenticator app supports.
           Implemented with the standard library (hmac, hashlib, base64);
           the RFC 6238 test vectors are checked in tests/test_two_step.py.
Window     A code is accepted for the current step and one step either side
           (about +/- 30 s of clock difference).
Replay     The last accepted step is stored; a code for that step or an
           earlier one is refused, so an intercepted code cannot be reused.
Secret     20 random bytes (160 bits, as RFC 4226 recommends), shown to the
           user once in base32. In the database it is ENCRYPTED with AES-GCM
           (the `cryptography` package, already a dependency). The key is
           derived from SECRET_KEY with HKDF-SHA256, and the user id is bound
           in as associated data, so a secret copied onto another user's row
           does not decrypt. Rotating SECRET_KEY makes stored secrets
           unreadable: those users sign in with a recovery code and set
           two-step up again (or an operator runs disable-two-step).
Recovery   Ten single-use codes (60 random bits each, xxxx-xxxx-xxxx),
           stored as SHA-256 only. Generating new codes cancels the old set.

Why TOTP and not SMS or email codes: no telephone or email provider is
needed, SMS can be intercepted (SIM swapping), and TOTP works offline.
WebAuthn/passkeys would be stronger still, but need a JavaScript flow and
a library for attestation; they are listed as future work.
"""

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1
SECRET_BYTES = 20
RECOVERY_CODE_COUNT = 10
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o, 1/l/i
_STORAGE_VERSION = "v1"


# ---------- Codes ----------


def new_secret():
    """A new shared secret, base32 without padding (what apps accept)."""
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode("ascii").rstrip("=")


def _key_bytes(secret_b32):
    padded = secret_b32.upper() + "=" * (-len(secret_b32) % 8)
    return base64.b32decode(padded)


def hotp(key, counter, digits=DIGITS, digest=hashlib.sha1):
    """RFC 4226 HOTP value for `counter` (an int) as a zero-padded string."""
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def time_step(now):
    return int(now) // STEP_SECONDS


def code_at(secret_b32, now):
    return hotp(_key_bytes(secret_b32), time_step(now))


def normalise_code(text):
    return "".join(ch for ch in (text or "") if not ch.isspace() and ch != "-")


def verify_code(secret_b32, code, *, now, last_step=None):
    """The time step the code belongs to, or None if it is wrong, outside
    the window, or not newer than `last_step` (a replay)."""
    code = normalise_code(code)
    if len(code) != DIGITS or not code.isdigit():
        return None
    key = _key_bytes(secret_b32)
    current = time_step(now)
    for step in range(current - WINDOW, current + WINDOW + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(hotp(key, step), code):
            return step
    return None


def provisioning_uri(secret_b32, *, account, issuer):
    """The otpauth:// URI authenticator apps import (key URI format)."""
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )


def grouped(secret_b32):
    """ "ABCD EFGH ..." for reading the secret aloud or typing it."""
    return " ".join(secret_b32[i : i + 4] for i in range(0, len(secret_b32), 4))


# ---------- Storage of the secret ----------


def _aead(secret_key):
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=b"tradeledger-totp-v1", info=b"totp secret").derive(
        secret_key.encode("utf-8")
    )
    return AESGCM(key)


def encrypt_secret(secret_b32, *, secret_key, user_id):
    nonce = secrets.token_bytes(12)
    sealed = _aead(secret_key).encrypt(nonce, secret_b32.encode("ascii"), str(user_id).encode("ascii"))
    return f"{_STORAGE_VERSION}:" + base64.urlsafe_b64encode(nonce + sealed).decode("ascii")


def decrypt_secret(stored, *, secret_key, user_id):
    """The base32 secret, or None if it cannot be read (wrong key, wrong
    user, damaged value)."""
    if not isinstance(stored, str) or not stored.startswith(f"{_STORAGE_VERSION}:"):
        return None
    try:
        raw = base64.urlsafe_b64decode(stored.split(":", 1)[1].encode("ascii"))
        plain = _aead(secret_key).decrypt(raw[:12], raw[12:], str(user_id).encode("ascii"))
    except (InvalidTag, ValueError):
        return None
    return plain.decode("ascii")


# ---------- Recovery codes ----------


def new_recovery_codes(count=RECOVERY_CODE_COUNT):
    codes = []
    for _ in range(count):
        chars = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(12))
        codes.append(f"{chars[:4]}-{chars[4:8]}-{chars[8:]}")
    return codes


def recovery_hash(code):
    """SHA-256 of the code as typed, ignoring case, spaces and dashes."""
    return hashlib.sha256(normalise_code(code).lower().encode("utf-8")).hexdigest()


def looks_like_recovery_code(text):
    code = normalise_code(text).lower()
    return len(code) == 12 and all(ch in _RECOVERY_ALPHABET for ch in code)
