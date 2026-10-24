"""Pinned official ACP distribution, installed only on an explicit setup action."""

import asyncio
import hashlib
import json
import platform
import shutil
import stat
import sys
import tempfile
import zipfile
from pathlib import Path

import httpx

from app.core.config import settings

VERSION = "1.4.3"
# https://github.com/agentclientprotocol/registry/blob/main/antigravity-acp/agent.json
ASSETS = {
    ("darwin", "arm64"): ("macos", "darwin-arm64", "c57db0a9b8b712daa9c3c17cdb990a61842ea569f279e8bd79d7aa85a303b20b"),
    ("darwin", "x86_64"): ("macos", "darwin-x86_64", "9392dbbdad7f0663dbfb5a0bf641297b0cf80d2ee9cd2d49a616bbbc7378731d"),
    ("linux", "x86_64"): ("linux", "linux-x86_64", "0634ce5306ba92c932945455e7a83fda78e2d99c33fa8aa6ee0566206a382714"),
    ("linux", "arm64"): ("linux", "linux-arm64", "05c153443b0b1175151c760eeddea538950bc9843e317d54786a1f03670182bc"),
}
class InstallError(ValueError):
    pass


MAX_ARCHIVE_BYTES = 1024**3
MAX_EXPANDED_BYTES = 3 * 1024**3
MEMBERS = ("agy_acp_server.par", "localharness_external")


def data_root() -> Path:
    return settings.SQLITE_FILE.parent / "antigravity"


def asset():
    arch = platform.machine().lower()
    arch = {"aarch64": "arm64", "amd64": "x86_64"}.get(arch, arch)
    found = ASSETS.get((sys.platform, arch))
    if found is None:
        raise InstallError("Antigravity managed installs support macOS and Linux on Intel and ARM64.")
    family, target, digest = found
    return f"https://dl.google.com/agy-extensions/releases/{family}/agy-acp-server-{VERSION}-{target}.zip", digest


def runtime_dir() -> Path:
    return data_root() / "runtimes" / asset()[1]


def installed() -> bool:
    try:
        root = runtime_dir()
        record = json.loads((root / "install.json").read_text())
        return record["sha256"] == asset()[1] and all(
            (root / name).is_file() and not (root / name).is_symlink()
            and (root / name).stat().st_size == record["sizes"][name] for name in MEMBERS
        )
    except (OSError, ValueError, KeyError, TypeError):
        return False


def extract_archive(archive: Path, destination: Path, digest: str) -> dict:
    if archive.stat().st_size > MAX_ARCHIVE_BYTES:
        raise InstallError("The Antigravity download exceeded its size limit.")
    with archive.open("rb") as source:
        if hashlib.file_digest(source, "sha256").hexdigest() != digest:
            raise InstallError("The Antigravity download failed its SHA-256 check.")
    with zipfile.ZipFile(archive) as package:
        entries = package.infolist()
        if len(entries) != len(MEMBERS) or {e.filename for e in entries} != set(MEMBERS):
            raise InstallError("The Antigravity archive contains unexpected files.")
        if sum(e.file_size for e in entries) > MAX_EXPANDED_BYTES:
            raise InstallError("The Antigravity archive exceeds its expanded size limit.")
        sizes = {}
        for entry in entries:
            kind = stat.S_IFMT(entry.external_attr >> 16)
            if kind not in (0, stat.S_IFREG) or entry.flag_bits & 1 or entry.file_size <= 0:
                raise InstallError("The Antigravity archive contains an unsafe file.")
            path = destination / entry.filename
            with package.open(entry) as source, path.open("xb") as target:
                shutil.copyfileobj(source, target, 1024 * 1024)
            path.chmod(0o700)
            sizes[entry.filename] = entry.file_size
        return sizes


async def install(validate) -> None:
    url, digest = asset()
    root = runtime_dir()
    if installed():
        return
    root.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if shutil.disk_usage(root.parent).free < MAX_ARCHIVE_BYTES + MAX_EXPANDED_BYTES:
        raise InstallError("Antigravity installation needs 4 GiB of free disk space.")
    with tempfile.TemporaryDirectory(prefix=".install-", dir=root.parent) as temporary:
        stage = Path(temporary)
        archive = stage / "download.zip"
        async with httpx.AsyncClient(timeout=httpx.Timeout(1200, connect=30), follow_redirects=False) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                count = 0
                with archive.open("xb") as target:
                    async for chunk in response.aiter_bytes():
                        count += len(chunk)
                        if count > MAX_ARCHIVE_BYTES:
                            raise InstallError("The Antigravity download exceeded its size limit.")
                        target.write(chunk)
        candidate = stage / "runtime"
        candidate.mkdir(mode=0o700)
        # Extraction is bounded and runs outside the event loop; cancellation must
        # wait for the worker before TemporaryDirectory removes its files.
        worker = asyncio.create_task(asyncio.to_thread(extract_archive, archive, candidate, digest))
        try:
            sizes = await asyncio.shield(worker)
        except asyncio.CancelledError:
            await worker
            raise
        await validate(candidate)
        (candidate / "install.json").write_text(json.dumps({"sha256": digest, "sizes": sizes}))
        try:
            candidate.rename(root)
        except OSError:
            if not installed():
                raise
