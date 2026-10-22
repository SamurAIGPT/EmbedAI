"""Workspace file browser (F08, docs/EVERYDAY.md): listing, previews and
downloads strictly inside the conversation's workspace."""

import io
import os
import zipfile

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.routers.threads import get_runtime
from app.routers.workspace_files import router as files_router
from app.services import workspace_files
from tests.services.thread_harness import cleanup, new_thread, owner, rt, sessions  # noqa: F401

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture
def layout(tmp_path):
    """A workspace with ordinary files, secrets, and links that escape it."""
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    (root / "src").mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.txt").write_text("outside the workspace")
    (root / "README.md").write_text("# Hello\n")
    (root / "src" / "main.py").write_text("print('hi')\n")
    (root / "chart.png").write_bytes(PNG)
    (root / "blob.bin").write_bytes(b"\x00\x01\x02" * 10)
    (root / ".env").write_text("API_KEY=sk-live")
    (root / ".env.local").write_text("API_KEY=sk-live")
    (root / "server.pem").write_text("-----BEGIN PRIVATE KEY-----")
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]")
    os.symlink(outside / "secret.txt", root / "escape.txt")
    os.symlink(outside, root / "escape-dir")
    os.symlink(root / "README.md", root / "readme-link.md")
    os.symlink(root / ".env", root / "innocent.txt")
    return root


@pytest.fixture
async def api(rt, sessions, owner, layout):
    thread = await new_thread(sessions, owner, workspace_path=str(layout))
    app = FastAPI()
    app.include_router(files_router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: rt
    transport = httpx.ASGITransport(app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        client.base = f"/api/v1/threads/{thread.id}/files"
        yield client


def code(response) -> str:
    return response.json()["detail"]["error_code"]


@pytest.mark.parametrize(
    "raw",
    ["/etc/passwd", "../outside/secret.txt", "src/../../outside", "a\\b", "C:/Windows", "x\x00y", "src/..", ".."],
)
def test_only_plain_relative_paths_are_accepted(raw):
    with pytest.raises(workspace_files.FileAccessError) as refused:
        workspace_files.clean_relative(raw)
    assert refused.value.status == 400


def test_secret_names():
    secret = workspace_files.is_secret
    for parts in [(".env",), (".ENV.production",), ("a", ".git", "config"), (".ssh",), ("k", "id_rsa.pub"), ("x.PEM",)]:
        assert secret(parts), parts
    for parts in [("env.md",), ("src", "main.py"), ("keys.txt",), ("environment",)]:
        assert not secret(parts), parts


async def test_listing_shows_folders_first_hides_escapes_and_marks_secrets(api):
    response = await api.get(api.base)
    assert response.status_code == 200, response.text
    body = response.json()
    entries = {e["name"]: e for e in body["entries"]}
    assert body["path"] == "" and body["truncated"] is False
    assert body["entries"][0]["name"] == "src" and entries["src"]["type"] == "dir"
    assert entries["README.md"]["type"] == "file" and entries["README.md"]["bytes"] == 8
    assert entries["readme-link.md"]["type"] == "file"  # a link inside the workspace is fine
    for name in (".env", ".env.local", "server.pem", ".git", "innocent.txt"):
        assert entries[name]["type"] == "hidden" and entries[name]["bytes"] is None, name
    assert "escape.txt" not in entries and "escape-dir" not in entries

    sub = (await api.get(api.base, params={"path": "src"})).json()
    assert [e["path"] for e in sub["entries"]] == ["src/main.py"]


async def test_previews_text_and_images(api):
    text = (await api.get(f"{api.base}/content", params={"path": "src/main.py"})).json()
    assert (text["kind"], text["text"], text["truncated"]) == ("text", "print('hi')\n", False)
    image = (await api.get(f"{api.base}/content", params={"path": "chart.png"})).json()
    assert image["kind"] == "image" and image["text"] is None
    raw = await api.get(f"{api.base}/raw", params={"path": "chart.png"})
    assert raw.status_code == 200 and raw.content == PNG and raw.headers["content-type"] == "image/png"
    assert (await api.get(f"{api.base}/raw", params={"path": "README.md"})).status_code == 415
    binary = (await api.get(f"{api.base}/content", params={"path": "blob.bin"})).json()
    assert binary["kind"] == "binary" and binary["text"] is None


async def test_a_long_text_preview_is_cut_and_says_so(api, layout):
    (layout / "big.log").write_text("x" * (workspace_files.TEXT_PREVIEW_BYTES + 10))
    body = (await api.get(f"{api.base}/content", params={"path": "big.log"})).json()
    assert body["truncated"] is True and len(body["text"]) == workspace_files.TEXT_PREVIEW_BYTES


async def test_download_is_an_attachment_with_safe_headers(api):
    response = await api.get(f"{api.base}/download", params={"path": "README.md"})
    assert response.status_code == 200 and response.content == b"# Hello\n"
    assert response.headers["content-disposition"].startswith('attachment; filename="README.md"')
    assert response.headers["content-type"] == "application/octet-stream"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"


@pytest.mark.parametrize("path", ["../outside/secret.txt", "/etc/passwd", "src/../../outside/secret.txt", "..%2Foutside"])
async def test_traversal_is_refused(api, path):
    for route in ("", "/content", "/download"):
        response = await api.get(f"{api.base}{route}", params={"path": path})
        assert response.status_code in (400, 404), (route, path, response.text)
        assert b"outside the workspace" not in response.content


async def test_symlink_escapes_are_refused(api):
    for route in ("/content", "/download", "/raw"):
        response = await api.get(f"{api.base}{route}", params={"path": "escape.txt"})
        assert response.status_code == 403 and code(response) == "outside_workspace", route
        through_dir = await api.get(f"{api.base}{route}", params={"path": "escape-dir/secret.txt"})
        assert through_dir.status_code == 403 and code(through_dir) == "outside_workspace", route
    listing = await api.get(api.base, params={"path": "escape-dir"})
    assert listing.status_code == 403 and code(listing) == "outside_workspace"


async def test_secrets_are_never_served(api):
    for path in (".env", ".env.local", "server.pem", ".git/config", "innocent.txt"):
        for route in ("/content", "/download"):
            response = await api.get(f"{api.base}{route}", params={"path": path})
            assert response.status_code == 403 and code(response) == "secret_file", (route, path)
            assert b"sk-live" not in response.content


async def test_folders_missing_files_and_size_caps(api, layout, monkeypatch):
    assert code(await api.get(f"{api.base}/download", params={"path": "src"})) == "not_a_file"
    assert code(await api.get(api.base, params={"path": "README.md"})) == "not_a_folder"
    assert (await api.get(f"{api.base}/download", params={"path": "nope.txt"})).status_code == 404
    monkeypatch.setattr(workspace_files, "DOWNLOAD_MAX_BYTES", 4)
    big = await api.get(f"{api.base}/download", params={"path": "README.md"})
    assert big.status_code == 413 and code(big) == "too_large"


async def test_a_missing_workspace_says_so(api, layout):
    import shutil

    shutil.rmtree(layout)
    response = await api.get(api.base)
    assert response.status_code == 404 and code(response) == "no_workspace"


def test_download_names_are_safe():
    value = workspace_files.download_name('rep"ort\u202e.txt')
    assert value.startswith('attachment; filename="rep_ort_.txt"') and "filename*=UTF-8''" in value


def zip_names(response) -> set[str]:
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        return set(archive.namelist())


async def test_a_folder_downloads_as_a_zip_without_secrets_or_escapes(api, layout):
    (layout / "src" / "deep").mkdir()
    (layout / "src" / "deep" / "notes.txt").write_text("deep")
    (layout / "src" / ".env").write_text("API_KEY=sk-live")
    (layout / "src" / "id_rsa").write_text("PRIVATE")
    os.symlink(layout / "src", layout / "src-link")  # a folder link is not followed
    os.symlink(layout / "chart.png", layout / "src" / "chart-link.png")  # a file link inside is fine

    response = await api.get(f"{api.base}/zip")
    root = layout.name
    assert zip_names(response) == {
        f"{root}/README.md",
        f"{root}/chart.png",
        f"{root}/blob.bin",
        f"{root}/readme-link.md",
        f"{root}/src/main.py",
        f"{root}/src/chart-link.png",
        f"{root}/src/deep/notes.txt",
    }
    assert b"sk-live" not in response.content and b"outside the workspace" not in response.content
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith(f'attachment; filename="{root}.zip"')
    assert response.headers["x-content-type-options"] == "nosniff"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.read(f"{root}/src/deep/notes.txt") == b"deep"
        assert archive.read(f"{root}/src/chart-link.png") == PNG

    sub = await api.get(f"{api.base}/zip", params={"path": "src"})
    assert zip_names(sub) == {"src/main.py", "src/chart-link.png", "src/deep/notes.txt"}
    assert sub.headers["content-disposition"].startswith('attachment; filename="src.zip"')


async def test_a_zip_refuses_escapes_secrets_and_files(api):
    for path in ("../outside", "/etc", "src/../.."):
        assert (await api.get(f"{api.base}/zip", params={"path": path})).status_code == 400, path
    escaped = await api.get(f"{api.base}/zip", params={"path": "escape-dir"})
    assert escaped.status_code == 403 and code(escaped) == "outside_workspace"
    secret = await api.get(f"{api.base}/zip", params={"path": ".git"})
    assert secret.status_code == 403 and code(secret) == "secret_file"
    a_file = await api.get(f"{api.base}/zip", params={"path": "README.md"})
    assert a_file.status_code == 400 and code(a_file) == "not_a_folder"


async def test_a_zip_over_the_caps_is_refused(api, monkeypatch):
    monkeypatch.setattr(workspace_files, "ZIP_MAX_FILES", 1)
    many = await api.get(f"{api.base}/zip")
    assert many.status_code == 413 and code(many) == "too_large"
    assert (await api.get(f"{api.base}/zip", params={"path": "src"})).status_code == 200  # one file fits

    monkeypatch.setattr(workspace_files, "ZIP_MAX_FILES", 100)
    monkeypatch.setattr(workspace_files, "ZIP_MAX_BYTES", 10)
    big = await api.get(f"{api.base}/zip")
    assert big.status_code == 413 and code(big) == "too_large"


async def test_a_zip_leaves_no_temporary_file(api, monkeypatch, tmp_path):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(workspace_files.tempfile, "tempdir", str(scratch))
    assert (await api.get(f"{api.base}/zip", params={"path": "src"})).status_code == 200
    assert list(scratch.iterdir()) == []
    # A file that grows past the cap after the walk: the half-written zip is removed.
    monkeypatch.setattr(workspace_files, "ZIP_MAX_BYTES", 5)
    monkeypatch.setattr(workspace_files, "_zip_members", lambda root, folder: ([("src/main.py", folder / "main.py")], 0))
    grown = await api.get(f"{api.base}/zip", params={"path": "src"})
    assert grown.status_code == 413 and code(grown) == "too_large"
    assert list(scratch.iterdir()) == []


async def test_a_zip_leaves_out_names_that_are_unsafe_in_a_zip(api, layout):
    unsafe = ["..\\..\\evil.bat", "C:evil.txt", "bell\x07.txt", "rep\u202etxt.exe", "zero\u200bwidth.txt"]
    for name in unsafe:
        (layout / "src" / name).write_text("bad")
    (layout / "src" / "sub\\dir").mkdir()
    (layout / "src" / "sub\\dir" / "inner.txt").write_text("bad")
    (layout / "src" / "ok name é.txt").write_text("fine")

    response = await api.get(f"{api.base}/zip", params={"path": "src"})
    assert zip_names(response) == {"src/main.py", "src/ok name é.txt"}
    assert response.headers["x-skipped-unsafe-names"] == str(len(unsafe) + 1)
    assert b"bad" not in response.content


async def test_a_zip_leaves_out_names_that_are_not_utf8(api, layout):
    try:
        fd = os.open(os.fsencode(layout / "src") + b"/latin-\xe9.txt", os.O_WRONLY | os.O_CREAT, 0o644)
    except OSError:
        pytest.skip("this filesystem refuses names that are not UTF-8")
    os.write(fd, b"bad")
    os.close(fd)
    response = await api.get(f"{api.base}/zip", params={"path": "src"})
    assert zip_names(response) == {"src/main.py"}
    assert response.headers["x-skipped-unsafe-names"] == "1"


async def test_a_folder_swapped_for_a_link_to_a_secret_folder_is_refused(api, layout, monkeypatch):
    (layout / "cfg").mkdir()
    (layout / "cfg" / "config").write_text("[harmless]")
    walk = workspace_files._zip_members

    def walk_then_swap(root, folder):
        found = walk(root, folder)
        (layout / "cfg" / "config").unlink()
        (layout / "cfg").rmdir()
        os.symlink(layout / ".git", layout / "cfg")  # .git/config holds "[core]"
        return found

    monkeypatch.setattr(workspace_files, "_zip_members", walk_then_swap)
    response = await api.get(f"{api.base}/zip", params={"path": "cfg"})
    assert response.status_code == 403 and code(response) == "secret_file"
    assert b"[core]" not in response.content
