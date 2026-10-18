"""Prepare persistent local secrets and emit shell-safe environment exports."""

import os
from pathlib import Path
import re
import secrets
import shlex
import socket
import ssl
import sys
from urllib.parse import urlsplit

import certifi
from cryptography.fernet import Fernet
from dotenv import dotenv_values, set_key


SYSTEM_CA_BUNDLES = (
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/ssl/ca-bundle.pem",
)


def select_ca_bundle(values: dict[str, str]) -> str:
    """Keep explicit trust settings, otherwise locate usable system CA roots."""
    configured = os.environ.get("SSL_CERT_FILE", values.get("SSL_CERT_FILE", ""))
    if configured:
        selected = configured
    else:
        candidates = (
            ssl.get_default_verify_paths().cafile,
            *SYSTEM_CA_BUNDLES,
            certifi.where(),
        )
        selected = next(
            (str(path) for path in candidates if path and Path(path).is_file()),
            "",
        )
    if not selected:
        raise ValueError("No CA certificate bundle found. Set SSL_CERT_FILE to a trusted PEM bundle.")
    try:
        ssl.create_default_context(cafile=selected)
    except (OSError, ssl.SSLError) as exc:
        raise ValueError(
            f"Cannot load CA certificates from {selected}. "
            "Set SSL_CERT_FILE to a readable, valid PEM CA bundle."
        ) from exc
    return selected


def prepare_environment(path: Path) -> dict[str, str]:
    dotenv = dotenv_values(path)
    values = {
        name: os.environ.get(name, value)
        for name, value in dotenv.items()
        if value is not None
    }
    for name in values:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
            raise ValueError(f"Invalid environment variable name in {path}: {name}")

    # OpenSSL's compiled-in CA file can be absent on immutable Linux systems.
    # urllib3 (and Boat SDK) honors SSL_CERT_FILE when loading default roots.
    # Export this for child processes, without persisting a machine-specific path.
    values["SSL_CERT_FILE"] = select_ca_bundle(values)

    file_encryption_keys = dotenv.get("TOKEN_ENCRYPTION_KEYS") or ""
    encryption_keys = os.environ.get("TOKEN_ENCRYPTION_KEYS", file_encryption_keys)
    if "TOKEN_ENCRYPTION_KEYS" in os.environ and not encryption_keys and file_encryption_keys:
        raise ValueError(
            "TOKEN_ENCRYPTION_KEYS is empty in the shell, but backend/.env contains a saved key. "
            "Unset the shell variable or restore the saved key to avoid losing access to stored credentials."
        )
    if encryption_keys:
        keys = [key.strip() for key in encryption_keys.split(",") if key.strip()]
        try:
            if not keys:
                raise ValueError("No encryption keys provided")
            for key in keys:
                Fernet(key.encode())
        except (ValueError, UnicodeError) as exc:
            raise ValueError(
                "TOKEN_ENCRYPTION_KEYS must contain valid Fernet keys. "
                "Restore the existing key used to encrypt stored credentials."
            ) from exc
    else:
        encryption_keys = Fernet.generate_key().decode()
        set_key(path, "TOKEN_ENCRYPTION_KEYS", encryption_keys)
        print("==> Generated and saved TOKEN_ENCRYPTION_KEYS", file=sys.stderr)
    values["TOKEN_ENCRYPTION_KEYS"] = encryption_keys

    file_hook_token = dotenv.get("HOOK_TOKEN") or ""
    hook_token = os.environ.get("HOOK_TOKEN", file_hook_token)
    if "HOOK_TOKEN" not in os.environ and (
        not hook_token or hook_token == "your-secret-token-here"
    ):
        hook_token = secrets.token_urlsafe(32)
        set_key(path, "HOOK_TOKEN", hook_token)
        print("==> Generated and saved HOOK_TOKEN", file=sys.stderr)
    values["HOOK_TOKEN"] = hook_token
    return values


def check_boat_tls(base_url: str) -> None:
    """Verify the configured HTTPS peer without sending credentials or requests."""
    target = urlsplit(base_url)
    if target.scheme == "http" and target.hostname:
        return
    if target.scheme != "https" or not target.hostname:
        raise ValueError("BOAT_BASE_URL must be a valid HTTP or HTTPS URL.")
    context = ssl.create_default_context()
    try:
        with socket.create_connection((target.hostname, target.port or 443), timeout=5) as connection:
            with context.wrap_socket(connection, server_hostname=target.hostname):
                pass
    except ssl.SSLCertVerificationError as exc:
        raise ValueError(
            f"Cannot verify the TLS certificate for {target.hostname}. "
            "Set SSL_CERT_FILE to a trusted PEM CA bundle. "
            "If your network inspects HTTPS, obtain its CA certificate from your network administrator."
        ) from exc
    except OSError as exc:
        raise ValueError(
            f"Cannot establish HTTPS to {target.hostname}. Check your network and BOAT_BASE_URL."
        ) from exc


def main() -> None:
    try:
        if sys.argv[1] == "--check-tls":
            check_boat_tls(os.environ.get("BOAT_BASE_URL", "https://boat.dev/api/v1"))
            return
        values = prepare_environment(Path(sys.argv[1]))
    except (OSError, ValueError) as exc:
        print(f"Cannot prepare development configuration: {exc}", file=sys.stderr)
        sys.exit(1)
    for name, value in values.items():
        print(f"export {name}={shlex.quote(value)}")


if __name__ == "__main__":
    main()
