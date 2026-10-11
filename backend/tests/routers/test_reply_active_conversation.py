import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select

from app.core.config import settings
from app.db.models.task import Tasks
from app.db.session import get_db
from app.routers.task import router
from app.services import user_credential_service


@pytest.fixture
def conversation_boat_api():
    state = {'commands': [], 'completed': set()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, payload):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = urlsplit(self.path).path
            if '/commands/' in path:
                process_id = int(path.rsplit('/', 1)[1])
                completed = process_id in state['completed']
                self.respond(200, {
                    'ok': True, 'type': 'command.status', 'success': True,
                    'processId': process_id, 'status': 'exited' if completed else 'running',
                    'running': not completed, 'exitCode': 0 if completed else None,
                    'stdout': '{"is_error":false,"result":"done"}' if completed else '',
                    'stderr': '', 'stdoutTruncated': False,
                })
            elif path.endswith('/files'):
                self.respond(404, {
                    'ok': False, 'type': 'error', 'status': 404, 'code': 'owned',
                    'message': 'owned fixture', 'requestId': 'owned',
                    'error': {'code': 'owned', 'message': 'owned fixture'},
                })
            else:
                assert path == '/sandboxes/bx_abcdefgh'
                self.respond(200, {
                    'ok': True, 'type': 'sandbox.info', 'sandbox': {
                        'id': 'bx_abcdefgh', 'name': 'owned', 'state': 'ready',
                        'type': 'small', 'desktopAvailable': False, 'snapshotAvailable': False,
                    },
                })

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            if self.path.endswith('/files'):
                self.respond(200, {
                    'ok': True, 'type': 'file.written', 'success': True,
                    'path': data['path'], 'encoding': 'utf8', 'size': len(data['content']),
                })
            else:
                assert self.path.endswith('/commands')
                assert data['detached'] is True
                state['commands'].append(data['command'])
                process_id = len(state['commands'])
                self.respond(200, {
                    'ok': True, 'type': 'command.started', 'success': True,
                    'processId': process_id, 'pid': process_id, 'command': data['command'],
                    'startedAt': '2026-10-10T00:00:00Z',
                })

        do_PUT = do_POST

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', state
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


@pytest.mark.parametrize('scenario', ['active-descendant', 'finished-descendant', 'unrelated-active'])
async def test_reply_to_finished_ancestor_observes_conversation(
    db_session, user_id, monkeypatch, conversation_boat_api, scenario
):
    """Actual REST writes/reads, installed SDK over TCP and disposable Postgres."""
    await user_credential_service.save_token(
        db_session, user_id=user_id, provider='claude',
        file_path='.claude/.credentials.json',
        plaintext=json.dumps({'claudeAiOauth': {'expiresAt': 1, 'accessToken': 'owned-fixture'}}),
    )
    base_url, state = conversation_boat_api
    monkeypatch.setattr(settings, 'BOAT_BASE_URL', base_url)
    app = FastAPI()
    app.include_router(router, prefix='/api/v1')
    app.dependency_overrides[get_db] = lambda: db_session
    payload = {'user_id': user_id, 'provider': 'claude', 'box_id': 'bx_abcdefgh', 'prompt_text': 'initial'}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://owned') as client:
        if scenario == 'unrelated-active':
            unrelated = await client.post('/api/v1/tasks', json={**payload, 'prompt_text': 'unrelated'})
            assert unrelated.status_code == 200
        initial = await client.post('/api/v1/tasks', json=payload)
        assert initial.status_code == 200
        ancestor = initial.json()
        state['completed'].add(int(ancestor['prompt_id']))
        completed = await client.get(f"/api/v1/tasks/{ancestor['id']}")
        assert completed.json()['status'] == 'succeeded'
        sibling = await client.post('/api/v1/tasks', json={**payload, 'parent_task_id': ancestor['id'], 'prompt_text': 'first reply'})
        assert sibling.status_code == 200
        descendant = sibling.json()
        assert descendant['status'] == 'running'
        assert descendant['session_id'] == ancestor['session_id']
        assert state['commands'][-1].endswith(f"--resume {ancestor['session_id']}")
        if scenario != 'active-descendant':
            state['completed'].add(int(descendant['prompt_id']))
            completed = await client.get(f"/api/v1/tasks/{descendant['id']}")
            assert completed.json()['status'] == 'succeeded'
        launches_before = len(state['commands'])
        rows_before = (await db_session.execute(select(Tasks).where(Tasks.user_id == user_id))).scalars().all()
        second = await client.post('/api/v1/tasks', json={**payload, 'parent_task_id': ancestor['id'], 'prompt_text': 'second branch reply'})
    active = scenario == 'active-descendant'
    if not active:
        assert second.json()['session_id'] == ancestor['session_id']
        assert state['commands'][-1].endswith(f"--resume {ancestor['session_id']}")
    rows_after = (await db_session.execute(select(Tasks).where(Tasks.user_id == user_id))).scalars().all()
    observed = (second.status_code, len(state['commands']) - launches_before, len(rows_after) - len(rows_before))
    expected = (409, 0, 0) if active else (200, 1, 1)
    assert observed == expected, {'observed': observed, 'expected': expected, 'ancestor': ancestor, 'descendant': descendant, 'second': second.json(), 'commands': state['commands']}
