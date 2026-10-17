"""Passphrase hashing, tokens, pairing codes and CSRF values.

Only stdlib: scrypt for the passphrase (slow, memory-hard), SHA-256 digests for
high-entropy tokens and codes (a fast hash is enough when the input cannot be
guessed), and `secrets` for every random value.
"""

import base64
import hashlib
import hmac
import secrets

# 64 MiB and roughly 0.1 s per check on a laptop: slow for an offline guesser,
# cheap enough for one login.
SCRYPT_N = 2**16
SCRYPT_R = 8
SCRYPT_P = 1
_SCRYPT_MAXMEM = 128 * 1024 * 1024
_HASH_SCHEME = "scrypt"

MIN_PASSPHRASE_LENGTH = 10
MAX_PASSPHRASE_LENGTH = 1024

SESSION_TOKEN_PREFIX = "ods_"

# No 0/O, 1/I/L: a code is read off one screen and typed on another.
PAIRING_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
PAIRING_CODE_LENGTH = 8  # 31**8, about 2**39.6


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _scrypt(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(
        passphrase.encode("utf-8"), salt=salt, n=n, r=r, p=p, maxmem=_SCRYPT_MAXMEM, dklen=32
    )


class WeakPassphrase(ValueError):
    pass


def check_passphrase_strength(passphrase: str) -> None:
    if len(passphrase) < MIN_PASSPHRASE_LENGTH:
        raise WeakPassphrase(f"Use at least {MIN_PASSPHRASE_LENGTH} characters.")
    if len(passphrase) > MAX_PASSPHRASE_LENGTH:
        raise WeakPassphrase(f"Use at most {MAX_PASSPHRASE_LENGTH} characters.")


def hash_passphrase(passphrase: str) -> str:
    salt = secrets.token_bytes(16)
    digest = _scrypt(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"{_HASH_SCHEME}${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(digest)}"


def verify_passphrase(passphrase: str, stored: str | None) -> bool:
    """Constant-time compare; a malformed or missing hash never matches."""
    if not stored or len(passphrase) > MAX_PASSPHRASE_LENGTH:
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != _HASH_SCHEME:
            return False
        n_value, r_value, p_value = int(n), int(r), int(p)
        # Bounded so a tampered row cannot make one login allocate gigabytes.
        if not (2**10 <= n_value <= 2**20 and 1 <= r_value <= 16 and 1 <= p_value <= 4):
            return False
        expected = _unb64(digest)
        actual = _scrypt(passphrase, _unb64(salt), n_value, r_value, p_value)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def same_digest(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("ascii"), b.encode("ascii"))


def new_session_token() -> str:
    return SESSION_TOKEN_PREFIX + secrets.token_urlsafe(32)


def looks_like_session_token(value: str) -> bool:
    return value.startswith(SESSION_TOKEN_PREFIX) and 20 <= len(value) <= 200


def csrf_token_for(session_token: str) -> str:
    """Derived from the cookie's token, so nothing extra is stored: only a page
    that was told it (by GET /auth/session, readable only by an allowed origin)
    can send it back."""
    return _b64(hashlib.sha256(b"csrf\x00" + session_token.encode("utf-8")).digest())


def new_pairing_code() -> str:
    return "".join(secrets.choice(PAIRING_ALPHABET) for _ in range(PAIRING_CODE_LENGTH))


def normalize_pairing_code(raw: str) -> str:
    """Case, spaces and dashes do not matter."""
    return "".join(ch for ch in raw.upper() if ch.isalnum())[:64]


def format_pairing_code(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"
