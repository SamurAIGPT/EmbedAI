import assert from 'node:assert/strict';
import { test } from 'node:test';
import { LEGACY_IDENTITY_KEY, migrateLegacyIdentity, pairingCodeFromHash, safeNextPath } from './session.ts';

function storage(initial) {
  const map = new Map(Object.entries(initial));
  return { map, getItem: (k) => (map.has(k) ? map.get(k) : null), removeItem: (k) => { map.delete(k); } };
}

const owner = { kind: 'browser', scopes: ['admin', 'chat'], user_id: null };

test('an old browser identity moves to the server once and leaves the browser', async () => {
  const store = storage({ [LEGACY_IDENTITY_KEY]: 'web_abc' });
  const sent = [];
  const id = await migrateLegacyIdentity(owner, store, async (body) => { sent.push(body); return { user_id: body.legacy_user_id }; });
  assert.equal(id, 'web_abc');
  assert.deepEqual(sent, [{ legacy_user_id: 'web_abc' }]);
  assert.equal(store.map.has(LEGACY_IDENTITY_KEY), false);
});

test('the server identity wins and the local copy is dropped without a request', async () => {
  const store = storage({ [LEGACY_IDENTITY_KEY]: 'web_old' });
  let called = false;
  const id = await migrateLegacyIdentity({ ...owner, user_id: '42' }, store, async () => { called = true; return { user_id: 'x' }; });
  assert.equal(id, '42');
  assert.equal(called, false);
  assert.equal(store.map.has(LEGACY_IDENTITY_KEY), false);
});

test('a chat-only device does not migrate; a rejected id is dropped; a network error keeps it', async () => {
  const kept = storage({ [LEGACY_IDENTITY_KEY]: 'web_x' });
  await migrateLegacyIdentity({ ...owner, scopes: ['chat'] }, kept, async () => assert.fail('no request'));
  assert.equal(kept.map.has(LEGACY_IDENTITY_KEY), true);
  const rejected = storage({ [LEGACY_IDENTITY_KEY]: 'bad id' });
  await migrateLegacyIdentity(owner, rejected, async () => { throw Object.assign(new Error('x'), { status: 422 }); });
  assert.equal(rejected.map.has(LEGACY_IDENTITY_KEY), false);
  const offline = storage({ [LEGACY_IDENTITY_KEY]: 'web_y' });
  await migrateLegacyIdentity(owner, offline, async () => { throw new TypeError('fetch failed'); });
  assert.equal(offline.map.has(LEGACY_IDENTITY_KEY), true);
});

test('after sign-in only same-site paths are followed', () => {
  assert.equal(safeNextPath('/settings'), '/settings');
  assert.equal(safeNextPath(null), '/chat');
  for (const bad of ['https://evil.example', '//evil.example', '/\\evil.example', 'javascript:alert(1)', '/login', '/pair']) {
    assert.equal(safeNextPath(bad), '/chat', bad);
  }
});

test('the pairing code is read from the URL fragment', () => {
  assert.equal(pairingCodeFromHash('#code=ABCD-EFGH'), 'ABCD-EFGH');
  assert.equal(pairingCodeFromHash('#x=1&code=AB%20CD'), 'AB CD');
  assert.equal(pairingCodeFromHash(''), '');
});
