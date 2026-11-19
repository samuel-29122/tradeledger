"""Password hashing and password rules (Phase 8).

Plain English
-------------
TradeLedger never stores your password. It stores a "hash": the result of
a deliberately slow, one-way calculation on the password plus a random
"salt". At sign-in the same calculation runs on what you typed; if the
results match, the password was right. Someone who steals the database
gets hashes, not passwords, and each guess they try costs them real time
and memory.

Technical summary
-----------------
Algorithm  Argon2id (winner of the Password Hashing Competition, RFC 9106),
           from the `cryptography` library maintained by the Python
           Cryptographic Authority. Nothing here invents cryptography: this
           module only chooses parameters and calls the library.
Parameters OWASP's recommended minimum for Argon2id: 19 MiB of memory,
           2 iterations, 1 lane (m=19456, t=2, p=1), with a 16-byte random
           salt and a 32-byte output. Memory-hardness makes each guess
           expensive on GPUs, not just slow.
Storage    The PHC string format, which records the parameters with the hash:
           $argon2id$v=19$m=19456,t=2,p=1$<salt>$<hash>
           so the parameters can be raised later; needs_rehash() spots old
           hashes and the login code upgrades them after a successful login.
Input      Passwords are Unicode-normalised (NFKC) before hashing, so the
           same password typed on different keyboards gives the same hash.

Alternatives considered: argon2-cffi (the other common Argon2 package; not
installable here), bcrypt (good, but limited to 72 bytes and not memory-
hard), Werkzeug's scrypt (memory-hard and already installed; a sound
fallback). Never a plain SHA-256: fast hashes are designed to be fast,
which is exactly wrong for passwords.
"""

import os
import re
import unicodedata
from dataclasses import dataclass

from cryptography.exceptions import InvalidKey
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

MIN_LENGTH = 12
MAX_LENGTH = 128
SALT_BYTES = 16
HASH_BYTES = 32

_PHC = re.compile(r"\$argon2id\$v=19\$m=(\d+),t=(\d+),p=(\d+)\$[A-Za-z0-9+/]+\$[A-Za-z0-9+/]+")

# A short list of passwords that appear at the top of every breach list.
# A real service would check a breached-password corpus (for example the
# k-anonymity "Pwned Passwords" range API); this list only stops the worst.
COMMON_PASSWORDS = frozenset(
    {
        "123456789012",
        "1234567890123",
        "password1234",
        "password12345",
        "passwordpassword",
        "qwertyuiopas",
        "qwerty123456",
        "iloveyou1234",
        "letmein12345",
        "trustno1trustno1",
        "tradeledger123",
        "tradeledger1234",
        "administrator1",
        "changeme1234",
        "welcome12345",
    }
)


@dataclass(frozen=True)
class HashParameters:
    memory_kib: int
    iterations: int
    lanes: int


OWASP_MINIMUM = HashParameters(memory_kib=19456, iterations=2, lanes=1)


def parameters_from_config(config):
    return HashParameters(
        memory_kib=int(config["PASSWORD_HASH_MEMORY_KIB"]),
        iterations=int(config["PASSWORD_HASH_ITERATIONS"]),
        lanes=int(config["PASSWORD_HASH_LANES"]),
    )


def _encode(password):
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def hash_password(password, params):
    """Return a PHC-format Argon2id hash of `password`."""
    if not isinstance(password, str):
        raise TypeError("password must be text")
    kdf = Argon2id(
        salt=os.urandom(SALT_BYTES),
        length=HASH_BYTES,
        iterations=params.iterations,
        lanes=params.lanes,
        memory_cost=params.memory_kib,
    )
    return kdf.derive_phc_encoded(_encode(password))


def verify_password(password, stored_hash):
    """True if `password` matches `stored_hash`. Never raises for a wrong
    password or a malformed hash: both are simply False. The comparison
    inside the library is constant-time."""
    if not isinstance(password, str) or not isinstance(stored_hash, str):
        return False
    if not _PHC.fullmatch(stored_hash):
        return False
    try:
        Argon2id.verify_phc_encoded(_encode(password), stored_hash)
    except (InvalidKey, ValueError):
        return False
    return True


def stored_parameters(stored_hash):
    match = _PHC.fullmatch(stored_hash or "")
    if not match:
        return None
    memory, iterations, lanes = (int(part) for part in match.groups())
    return HashParameters(memory_kib=memory, iterations=iterations, lanes=lanes)


def needs_rehash(stored_hash, params):
    """True when the stored hash used different (usually older, weaker) settings."""
    return stored_parameters(stored_hash) != params


def password_problem(password, *, email=None):
    """Why `password` is not acceptable (worded to follow the field label,
    e.g. "Password must be at least 12 characters."), or None.

    Rules follow NIST SP 800-63B: a minimum length, no composition rules
    ("one capital, one symbol"), no maximum below 64 characters, reject
    known-bad choices. Spaces and any Unicode characters are allowed.
    """
    if not isinstance(password, str) or password == "":
        return "is required."
    length = len(unicodedata.normalize("NFKC", password))
    if length < MIN_LENGTH:
        return f"must be at least {MIN_LENGTH} characters."
    if length > MAX_LENGTH:
        return f"must be {MAX_LENGTH} characters or fewer."
    lowered = password.strip().lower()
    if lowered in COMMON_PASSWORDS or len(set(lowered)) <= 2:
        return "is too common and easy to guess. Choose a less predictable one."
    if email and lowered == email.strip().lower():
        return "must not be your email address."
    return None
