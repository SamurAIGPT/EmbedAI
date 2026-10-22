"""Read-only browsing of a conversation's workspace (F08, docs/EVERYDAY.md).

Security invariants, checked on every call:
- the requested path is relative, with no `..`, backslash, NUL or drive;
- the canonical target (symlinks resolved) is inside the canonical root;
- a file is opened with O_NOFOLLOW and its inode must match the canonical
  path after opening, so a swap between the check and the open is refused;
- secret files (keys, .env, VCS and cloud credentials) are never read.

A folder download (`zip_folder`) applies the same checks to every file it
adds, leaves out what a listing would hide and names that are unsafe inside a
zip (backslashes, control or bidi characters, invalid UTF-8), does not follow
folder links, and refuses a folder over ZIP_MAX_FILES files or ZIP_MAX_BYTES
in total.
"""

import fnmatch
import mimetypes
import os
import re
import stat
import tempfile
import time
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

LIST_MAX_ENTRIES = 500
TEXT_PREVIEW_BYTES = 256 * 1024
IMAGE_PREVIEW_BYTES = 10 * 1024 * 1024
DOWNLOAD_MAX_BYTES = 50 * 1024 * 1024
ZIP_MAX_BYTES = 200 * 1024 * 1024  # total of the files, before compression
ZIP_MAX_FILES = 2000
PATH_MAX_CHARS = 1024

SECRET_DIRS = {".git", ".ssh", ".aws", ".gnupg"}
SECRET_NAMES = (
    ".env",
    ".env.*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    "id_ecdsa*",
)
IMAGE_MIMES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
TEXT_EXTENSIONS = {
    ".txt", ".md", ".csv", ".tsv", ".json", ".log", ".yaml", ".yml", ".xml", ".html", ".css",
    ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".py", ".sh", ".sql", ".toml", ".ini", ".cfg",
    ".rs", ".go", ".java", ".kt", ".swift", ".c", ".h", ".cpp", ".hpp", ".rb", ".php", ".lock",
}
_DRIVE = re.compile(r"^[A-Za-z]:")


class FileAccessError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _outside() -> FileAccessError:
    return FileAccessError(403, "outside_workspace", "That path is outside this conversation's workspace.")


def is_secret(parts: tuple[str, ...]) -> bool:
    lowered = [part.lower() for part in parts]
    if any(part in SECRET_DIRS for part in lowered[:-1]) or (lowered and lowered[-1] in SECRET_DIRS):
        return True
    return bool(lowered) and any(fnmatch.fnmatchcase(lowered[-1], pattern) for pattern in SECRET_NAMES)


def clean_relative(raw: str | None) -> tuple[str, ...]:
    """The path's parts, or FileAccessError(400) for anything but a plain
    relative path. Empty means the root."""
    value = (raw or "").strip()
    if len(value) > PATH_MAX_CHARS or "\x00" in value or "\\" in value or _DRIVE.match(value):
        raise FileAccessError(400, "bad_path", "Use a relative path inside the workspace.")
    if value.startswith("/"):
        raise FileAccessError(400, "bad_path", "Use a relative path inside the workspace.")
    parts = tuple(part for part in PurePosixPath(value).parts if part not in ("", "."))
    if any(part == ".." for part in parts):
        raise FileAccessError(400, "bad_path", "Use a relative path inside the workspace.")
    return parts


def _contained(root: Path, candidate: Path) -> bool:
    return candidate == root or root in candidate.parents


def canonical_root(workspace: str | None) -> Path:
    if not workspace:
        raise FileAccessError(404, "no_workspace", "This conversation has no workspace.")
    try:
        root = Path(os.path.realpath(workspace))
    except OSError:
        raise FileAccessError(404, "no_workspace", "This conversation's workspace is missing.") from None
    if not root.is_dir():
        raise FileAccessError(404, "no_workspace", "This conversation's workspace is missing.")
    return root


def resolve(workspace: str | None, raw: str | None) -> tuple[Path, Path, tuple[str, ...]]:
    """(root, canonical target, relative parts). Refuses escapes and secrets."""
    root = canonical_root(workspace)
    parts = clean_relative(raw)
    if is_secret(parts):
        raise FileAccessError(403, "secret_file", "Secret files (keys, .env, credentials) are never shown.")
    target = Path(os.path.realpath(root.joinpath(*parts)))
    if not _contained(root, target):
        raise _outside()
    if not os.path.lexists(target):
        raise FileAccessError(404, "not_found", "No such file or folder in the workspace.")
    rel = target.relative_to(root).parts
    if is_secret(rel):
        raise FileAccessError(403, "secret_file", "Secret files (keys, .env, credentials) are never shown.")
    return root, target, parts


@dataclass
class Entry:
    name: str
    path: str
    type: str  # dir | file | hidden
    bytes: int | None
    modified: float | None


def list_dir(workspace: str | None, raw: str | None) -> tuple[str, list[Entry], bool]:
    root, target, parts = resolve(workspace, raw)
    if not target.is_dir():
        raise FileAccessError(400, "not_a_folder", "That is a file, not a folder.")
    rel_base = target.relative_to(root).parts
    entries: list[Entry] = []
    truncated = False
    try:
        names = sorted(os.listdir(target), key=lambda n: (n.lower(), n))
    except OSError:
        raise FileAccessError(403, "unreadable", "That folder can't be read.") from None
    for name in names:
        rel = (*rel_base, name)
        rel_path = "/".join(rel)
        child = target / name
        try:
            real = Path(os.path.realpath(child))
            if not _contained(root, real):
                continue  # a link out of the workspace is not shown
            if is_secret(rel) or is_secret(real.relative_to(root).parts):
                entry = Entry(name, rel_path, "hidden", None, None)
            else:
                info = os.stat(real)
                if stat.S_ISDIR(info.st_mode):
                    entry = Entry(name, rel_path, "dir", None, info.st_mtime)
                elif stat.S_ISREG(info.st_mode):
                    entry = Entry(name, rel_path, "file", info.st_size, info.st_mtime)
                else:
                    continue
        except OSError:
            continue
        if len(entries) >= LIST_MAX_ENTRIES:
            truncated = True
            break
        entries.append(entry)
    entries.sort(key=lambda e: (e.type != "dir", e.name.lower()))
    return "/".join(rel_base), entries, truncated


def mime_of(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    if ext in TEXT_EXTENSIONS:
        return "text/plain"
    guessed, _ = mimetypes.guess_type(name)
    return guessed or "application/octet-stream"


@dataclass
class OpenedFile:
    name: str
    path: str
    bytes: int
    mime: str
    data: bytes
    truncated: bool


def _open_confined(root: Path, target: Path) -> tuple[int, os.stat_result, Path]:
    """Open a regular file inside `root` without following a final link, and
    check that what was opened is still the canonical file inside the root.
    The caller closes the descriptor."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
    except IsADirectoryError:
        raise FileAccessError(400, "not_a_file", "That is a folder, not a file.") from None
    except OSError:
        raise FileAccessError(404, "not_found", "No such file in the workspace.") from None
    try:
        opened = os.fstat(fd)
        if stat.S_ISDIR(opened.st_mode):
            raise FileAccessError(400, "not_a_file", "That is a folder, not a file.")
        if not stat.S_ISREG(opened.st_mode):
            raise FileAccessError(400, "not_a_file", "Only regular files can be read.")
        after = Path(os.path.realpath(target))
        if not _contained(root, after):
            raise _outside()
        if is_secret(after.relative_to(root).parts):
            # A folder on the path was swapped for a link to a secret one
            # after the path was checked.
            raise FileAccessError(403, "secret_file", "Secret files (keys, .env, credentials) are never shown.")
        now = os.stat(after)
        if (now.st_dev, now.st_ino) != (opened.st_dev, opened.st_ino):
            raise FileAccessError(409, "file_changed", "The file changed while it was being opened. Try again.")
    except BaseException:
        os.close(fd)
        raise
    return fd, opened, after


def read_file(workspace: str | None, raw: str | None, *, max_bytes: int, whole: bool) -> OpenedFile:
    """Read a file inside the workspace. `whole`: refuse (413) a file larger
    than `max_bytes` instead of returning its first `max_bytes`."""
    root, target, _ = resolve(workspace, raw)
    fd, opened, after = _open_confined(root, target)
    try:
        if whole and opened.st_size > max_bytes:
            raise FileAccessError(
                413, "too_large", f"The file is over the {max_bytes // (1024 * 1024)} MB limit for this."
            )
        chunks = []
        remaining = max_bytes
        while remaining > 0:
            chunk = os.read(fd, min(remaining, 1024 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    return OpenedFile(
        name=after.name,
        path="/".join(after.relative_to(root).parts),
        bytes=opened.st_size,
        mime=mime_of(after.name),
        data=data,
        truncated=opened.st_size > len(data),
    )


def preview(workspace: str | None, raw: str | None) -> dict:
    """A text preview (first 256 KB, UTF-8 with replacement) or an image."""
    root, target, _ = resolve(workspace, raw)
    mime = mime_of(target.name)
    if mime in IMAGE_MIMES:
        opened = read_file(workspace, raw, max_bytes=IMAGE_PREVIEW_BYTES, whole=True)
        return {"kind": "image", "file": opened}
    opened = read_file(workspace, raw, max_bytes=TEXT_PREVIEW_BYTES, whole=False)
    if b"\x00" in opened.data[:8192]:
        return {"kind": "binary", "file": opened}
    return {"kind": "text", "file": opened, "text": opened.data.decode("utf-8", errors="replace")}


def download_name(name: str) -> str:
    """A Content-Disposition value with an ASCII fallback and a UTF-8 name."""
    from urllib.parse import quote

    clean = "".join("_" if unicodedata.category(ch) in ("Cc", "Cf") else ch for ch in name) or "download"
    fallback = (
        unicodedata.normalize("NFKD", clean).encode("ascii", "replace").decode("ascii").replace('"', "_")
        .replace("\\", "_").replace("?", "_")[:180]
        or "download"
    )
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(clean, safe='')}"


def _too_big_folder() -> FileAccessError:
    return FileAccessError(
        413,
        "too_large",
        f"That folder is over the zip limit ({ZIP_MAX_FILES} files or "
        f"{ZIP_MAX_BYTES // (1024 * 1024)} MB). Download a smaller folder.",
    )


def safe_zip_name(name: str) -> bool:
    """Whether a file or folder name can go into a zip unchanged: valid
    UTF-8, no backslash, colon-drive prefix, control or format character
    (bidi overrides, zero-width marks), and nothing clean_relative() refuses.
    Such names are left out rather than rewritten."""
    try:
        name.encode("utf-8")
        clean_relative(name)
    except (UnicodeEncodeError, FileAccessError):
        return False
    if name in ("", ".", "..") or "/" in name or "\\" in name or _DRIVE.match(name):
        return False
    return not any(unicodedata.category(ch) in ("Cc", "Cf") for ch in name)


def _zip_members(root: Path, folder: Path) -> tuple[list[tuple[str, Path]], int]:
    """(name inside the zip, canonical path) for every file a zip of `folder`
    may contain: what a listing would show as a file, in every subfolder,
    without following folder links, and the number of files and folders left
    out because their names are unsafe in a zip. Refuses (413) over the caps."""
    members: list[tuple[str, Path]] = []
    skipped = 0
    total = 0
    top = folder.name if folder.name and safe_zip_name(folder.name) else "workspace"
    for current, dirs, names in os.walk(folder, followlinks=False):
        here = Path(current)
        kept = []
        for d in sorted(dirs):
            if is_secret((*here.relative_to(root).parts, d)):
                continue
            if not safe_zip_name(d):
                skipped += 1
                continue
            kept.append(d)
        dirs[:] = kept
        for name in sorted(names):
            child = here / name
            rel = (*child.relative_to(root).parts,)
            try:
                real = Path(os.path.realpath(child))
                if not _contained(root, real) or is_secret(rel) or is_secret(real.relative_to(root).parts):
                    continue
                info = os.stat(real)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if not safe_zip_name(name):
                skipped += 1
                continue
            members.append(("/".join((top, *child.relative_to(folder).parts)), real))
            total += info.st_size
            if len(members) > ZIP_MAX_FILES or total > ZIP_MAX_BYTES:
                raise _too_big_folder()
    return members, skipped


def zip_folder(workspace: str | None, raw: str | None) -> tuple[str, str, int]:
    """Write a folder of the workspace (the root when `raw` is empty) to a
    temporary zip file. Returns (download name, path of the zip, entries left
    out for unsafe names); the caller deletes the file. Secret files and
    links out of the workspace are left out, as in a listing; links to
    folders are not followed."""
    root, target, _ = resolve(workspace, raw)
    if not target.is_dir():
        raise FileAccessError(400, "not_a_folder", "That is a file, not a folder.")
    members, skipped = _zip_members(root, target)
    handle = tempfile.NamedTemporaryFile(prefix="workspace-", suffix=".zip", delete=False)
    written = 0
    try:
        with handle, zipfile.ZipFile(handle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for arcname, path in members:
                try:
                    fd, opened, _ = _open_confined(root, path)
                except FileAccessError as error:
                    if error.code == "not_found":
                        continue  # removed since the walk
                    raise
                try:
                    stamp = time.localtime(opened.st_mtime)[:6]
                    info = zipfile.ZipInfo(arcname, max(stamp, (1980, 1, 1, 0, 0, 0)))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    info.external_attr = 0o644 << 16
                    with archive.open(info, "w", force_zip64=True) as out:
                        while chunk := os.read(fd, 1024 * 1024):
                            written += len(chunk)
                            if written > ZIP_MAX_BYTES:
                                raise _too_big_folder()  # a file grew after the walk
                            out.write(chunk)
                finally:
                    os.close(fd)
    except BaseException:
        os.unlink(handle.name)
        raise
    return f"{target.name or 'workspace'}.zip", handle.name, skipped
