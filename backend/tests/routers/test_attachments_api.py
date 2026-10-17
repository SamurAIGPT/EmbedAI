"""Attachments and images in chat (docs/EVERYDAY.md): upload limits and safe
storage, the transcript record, what reaches the engine, and the refusal for
an engine that cannot read images."""

import base64
import json
import os
import struct
import zlib

import httpx
import pytest
from fastapi import FastAPI

from app.core.config import settings
from app.routers.attachments import router as attachments_router
from app.routers.threads import get_runtime
from app.routers.threads import router as threads_router
from app.services import attachments, local_claude_runner
from app.services import thread_store as store
from app.services.provider_adapter import Capabilities
from tests.services.thread_harness import (  # noqa: F401
    ReadyClaude,
    cleanup,
    cli,
    finished,
    new_thread,
    owner,
    rt,
    sessions,
    tools_on,
)


def png_bytes() -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    pixels = zlib.compress(b"\x00\xff\x00\x00")
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


class TextOnly(ReadyClaude):
    label = "Text Engine"
    capabilities = Capabilities(tools=True, approvals=True, images=False)


@pytest.fixture(autouse=True)
def local_enabled(monkeypatch):
    monkeypatch.setattr(settings, "LOCAL_EXECUTION_ENABLED", True)
    monkeypatch.setattr(settings, "LOCAL_ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]")


@pytest.fixture
async def api(rt):
    rt.adapters["textonly"] = TextOnly()
    app = FastAPI()
    app.include_router(threads_router, prefix="/api/v1")
    app.include_router(attachments_router, prefix="/api/v1")
    app.dependency_overrides[get_runtime] = lambda: rt
    transport = httpx.ASGITransport(app, client=("127.0.0.1", 50000))
    async with httpx.AsyncClient(transport=transport, base_url="http://localhost") as client:
        yield client


async def upload(api, thread_id, name, data, mime="application/octet-stream"):
    return await api.post(f"/api/v1/threads/{thread_id}/attachments", files={"file": (name, data, mime)})


# --- storage rules -------------------------------------------------------------------------------


def test_display_names_are_last_part_only_and_printable():
    assert attachments.display_name("../../etc/passwd") == "passwd"
    assert attachments.display_name("C:\\Users\\me\\notes.txt") == "notes.txt"
    assert attachments.display_name("evil\u202etxt.exe") == "evil_txt.exe"
    assert attachments.display_name(".hidden") == "hidden"
    assert attachments.display_name("") == "attachment"
    assert len(attachments.display_name("a" * 500 + ".png")) <= attachments.NAME_MAX_CHARS


def test_images_must_match_their_signature_and_text_must_be_utf8():
    assert attachments.classify("a.png", "image/png", png_bytes())[:2] == ("image", "image/png")
    # A real PNG named .txt is still an image; a text file named .png is refused.
    assert attachments.classify("a.txt", "text/plain", png_bytes())[0] == "image"
    with pytest.raises(attachments.AttachmentError) as fake:
        attachments.classify("a.png", "image/png", b"not an image")
    assert fake.value.status == 415
    with pytest.raises(attachments.AttachmentError) as binary:
        attachments.classify("a.txt", "text/plain", b"abc\x00def")
    assert binary.value.status == 415
    with pytest.raises(attachments.AttachmentError) as latin:
        attachments.classify("a.csv", "text/csv", "café".encode("latin-1"))
    assert latin.value.status == 415
    with pytest.raises(attachments.AttachmentError) as exe:
        attachments.classify("tool.exe", "application/octet-stream", b"MZ\x90\x00")
    assert exe.value.status == 415 and "Supported" in exe.value.message
    with pytest.raises(attachments.AttachmentError) as empty:
        attachments.classify("a.txt", "text/plain", b"")
    assert empty.value.status == 400
    with pytest.raises(attachments.AttachmentError) as big:
        attachments.classify("a.md", "text/markdown", b"x" * (attachments.TEXT_MAX_BYTES + 1))
    assert big.value.status == 413


def test_saved_files_get_a_uuid_name_in_a_private_folder(attachments_dir):
    saved = attachments.save(5, "../holiday photo.png", "image/png", png_bytes())
    folder = attachments_dir / "5"
    assert sorted(p.name for p in folder.iterdir()) == sorted([f"{saved.id}.png", f"{saved.id}.json"])
    assert saved.name == "holiday photo.png"
    assert oct(os.stat(folder).st_mode & 0o777) == "0o700"
    assert oct(os.stat(folder / f"{saved.id}.png").st_mode & 0o777) == "0o600"
    with pytest.raises(attachments.AttachmentError):
        attachments.load(5, "../" + saved.id)
    with pytest.raises(attachments.AttachmentError):
        attachments.load(6, saved.id)  # another thread's folder


def test_text_files_are_fenced_into_the_prompt(attachments_dir):
    saved = attachments.save(1, "notes.md", "text/markdown", b"line one\n```\ncode\n```\n")
    prompt = attachments.prompt_with_text_files("Summarise this", [attachments.load(1, saved.id)])
    assert prompt.startswith("Summarise this\n\nAttached file notes.md:\n````\n")
    assert prompt.rstrip().endswith("````")


def test_an_image_message_is_one_stream_json_line_with_an_image_block(tmp_path):
    image = tmp_path / "x.png"
    image.write_bytes(png_bytes())
    line = json.loads(local_claude_runner.user_message_line("What is this?", [{"path": str(image), "mime": "image/png"}]))
    text, block = line["message"]["content"]
    assert text == {"type": "text", "text": "What is this?"}
    assert block["type"] == "image" and block["source"]["media_type"] == "image/png"
    assert base64.b64decode(block["source"]["data"]) == png_bytes()
    plain = json.loads(local_claude_runner.user_message_line("hi"))
    assert plain["message"]["content"] == "hi"


def test_a_chat_only_run_with_images_switches_to_stream_json_input(monkeypatch, tmp_path):
    image = tmp_path / "x.png"
    image.write_bytes(png_bytes())
    seen = {}
    monkeypatch.setattr(local_claude_runner, "resolve_claude_executable", lambda: tmp_path / "claude")
    monkeypatch.setattr(local_claude_runner, "run_blocking", lambda args, **kw: seen.update(args=args, **kw))
    local_claude_runner._run_blocking("look", "sid", None, None, tools=False, workspace=tmp_path,
                                      images=[{"path": str(image), "mime": "image/png"}])
    assert seen["args"][-2:] == ["--input-format", "stream-json"]
    assert json.loads(seen["stdin_text"])["message"]["content"][1]["type"] == "image"
    local_claude_runner._run_blocking("look", "sid", None, None, tools=False, workspace=tmp_path)
    assert "--input-format" not in seen["args"] and seen["stdin_text"] == "look"


# --- the API -------------------------------------------------------------------------------------


async def test_upload_read_and_send_an_image_to_claude(api, cli, tools_on, sessions, owner, tmp_path):
    thread = await new_thread(sessions, owner, workspace_path=str(tmp_path))
    response = await upload(api, thread.id, "cat.png", png_bytes(), "image/png")
    assert response.status_code == 201, response.text
    meta = response.json()
    assert (meta["name"], meta["mime"], meta["kind"], meta["bytes"]) == ("cat.png", "image/png", "image", len(png_bytes()))

    got = await api.get(f"/api/v1/threads/{thread.id}/attachments/{meta['id']}")
    assert got.status_code == 200 and got.content == png_bytes()
    assert got.headers["content-type"] == "image/png"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["content-security-policy"] == "sandbox"

    sent = await api.post(
        f"/api/v1/threads/{thread.id}/turns", json={"prompt_text": "What is this?", "attachment_ids": [meta["id"]]}
    )
    assert sent.status_code == 202, sent.text
    await finished(sessions, thread.id)
    prompt = cli.log("prompt")[0]["prompt"]
    assert prompt[0] == {"type": "text", "text": "What is this?"}
    assert prompt[1]["type"] == "image" and base64.b64decode(prompt[1]["source"]["data"]) == png_bytes()

    async with sessions() as db:
        events = await store.list_all_events(db, thread.id)
        turn = (await store.list_turns(db, thread.id))[0]
    started = next(e for e in events if e.type == "turn.started")
    assert started.data == {
        "prompt_text": "What is this?",
        "attachments": [{k: meta[k] for k in ("id", "name", "mime", "bytes", "kind")}],
    }
    assert turn.prompt_text == "What is this?"  # what the person typed, nothing added


async def test_a_text_file_reaches_the_engine_inside_the_prompt(api, cli, tools_on, sessions, owner, tmp_path):
    thread = await new_thread(sessions, owner, workspace_path=str(tmp_path))
    meta = (await upload(api, thread.id, "todo.txt", b"buy milk", "text/plain")).json()
    sent = await api.post(f"/api/v1/threads/{thread.id}/turns", json={"prompt_text": "Read it", "attachment_ids": [meta["id"]]})
    assert sent.status_code == 202, sent.text
    await finished(sessions, thread.id)
    assert cli.log("prompt")[0]["prompt"] == "Read it\n\nAttached file todo.txt:\n```\nbuy milk\n```"


async def test_an_engine_without_images_refuses_them_before_the_turn(api, sessions, owner, tmp_path):
    thread = await new_thread(sessions, owner, provider="textonly", workspace_path=str(tmp_path))
    meta = (await upload(api, thread.id, "cat.png", png_bytes(), "image/png")).json()
    sent = await api.post(f"/api/v1/threads/{thread.id}/turns", json={"prompt_text": "see", "attachment_ids": [meta["id"]]})
    assert sent.status_code == 422
    detail = sent.json()["detail"]
    assert detail["error_code"] == "images_unsupported" and "Text Engine can't read images" in detail["message"]
    async with sessions() as db:
        assert await store.list_turns(db, thread.id) == []


async def test_refusals_say_what_to_do(api, sessions, owner, tmp_path):
    thread = await new_thread(sessions, owner, workspace_path=str(tmp_path))
    other = await new_thread(sessions, owner, workspace_path=str(tmp_path))
    exe = await upload(api, thread.id, "setup.exe", b"MZ\x90\x00")
    assert exe.status_code == 415 and exe.json()["detail"]["error_code"] == "unsupported_type"
    huge = await upload(api, thread.id, "big.png", b"\x89PNG\r\n\x1a\n" + b"0" * attachments.IMAGE_MAX_BYTES, "image/png")
    assert huge.status_code == 413
    meta = (await upload(api, other.id, "a.txt", b"hello", "text/plain")).json()
    # An id from another conversation is not found here.
    foreign = await api.post(f"/api/v1/threads/{thread.id}/turns", json={"prompt_text": "x", "attachment_ids": [meta["id"]]})
    assert foreign.status_code == 404 and foreign.json()["detail"]["error_code"] == "attachment_not_found"
    assert (await api.get(f"/api/v1/threads/{thread.id}/attachments/{meta['id']}")).status_code == 404
    assert (await api.get(f"/api/v1/threads/{thread.id}/attachments/..%2F..%2Fetc")).status_code == 404
    many = await api.post(f"/api/v1/threads/{thread.id}/turns", json={"prompt_text": "x", "attachment_ids": ["a" * 32] * 9})
    assert many.status_code == 422


async def test_deleting_a_conversation_removes_its_attachments(api, sessions, owner, tmp_path, attachments_dir):
    thread = await new_thread(sessions, owner, workspace_path=str(tmp_path))
    await upload(api, thread.id, "a.txt", b"hello", "text/plain")
    assert (attachments_dir / str(thread.id)).is_dir()
    assert (await api.delete(f"/api/v1/threads/{thread.id}")).status_code == 204
    assert not (attachments_dir / str(thread.id)).exists()


async def test_another_owners_thread_takes_no_uploads(api, sessions, tmp_path):
    theirs = await new_thread(sessions, "someone-else-attach", workspace_path=str(tmp_path))
    try:
        assert (await upload(api, theirs.id, "a.txt", b"x", "text/plain")).status_code == 404
    finally:
        from sqlalchemy import delete

        from app.db.models import Thread

        async with sessions() as db:
            await db.execute(delete(Thread).where(Thread.id == theirs.id))
            await db.commit()
