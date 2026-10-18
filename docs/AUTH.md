# Authentication: owner, sessions and paired devices

Open Dots runs agents with access to your machine, so the server must know who
is asking. This document is the design and the operating guide for a
self-hosted, single-owner install. The desktop app keeps its own launch-token
model (below) and is not changed by any of this.

## Principals

Every request acts as exactly one of these, decided by the auth middleware
(`backend/app/auth/middleware.py`) before any route runs:

| Principal | How it is proven | Scopes |
|---|---|---|
| `desktop` | The desktop launcher's per-launch token (`X-Open-Dots-Token`), checked by `LaunchAuthMiddleware` | admin + chat |
| `browser` | The owner signed in with the passphrase, or a browser paired with a code; an `HttpOnly` session cookie | admin + chat, or chat |
| `device` | A paired device (phone, script, another computer); `Authorization: Bearer ods_…` | admin + chat, or chat |
| `loopback` | Only when `LOOPBACK_TRUST` allows it: a request from this machine directly to this server | admin + chat |

A browser session is accepted only as its cookie and a device token only as a
bearer header, so a token copied out of one cannot be replayed as the other.

### Scopes

- `admin`: everything, including settings, approval rules, computers, engines,
  devices and pairing.
- `chat`: conversations (create, send, stop, read events, answer approvals) and
  the reads the chat page needs. The list is in `backend/app/auth/policy.py`
  and is an allowlist: a route added later is admin-only until it is listed.

## The owner

A self-hosted install has one owner and one secret: a passphrase of at least
10 characters. It is stored as a scrypt hash (Python standard library; N=2^16,
r=8, p=1, 16-byte salt), never in clear and never in `.env`.

Setting it:

- **First run, on the server itself.** The web UI shows "Set the owner
  passphrase" when none is set and the browser is on the same machine
  (loopback socket, loopback `Host`, no proxy headers). This is refused from
  anywhere else, because whoever reached a fresh remote server first would
  otherwise own it.
- **From the server's shell**, at any time (this also replaces a forgotten
  passphrase and signs every browser out; paired devices keep working):

  ```sh
  cd backend
  uv run python -m app.auth.cli set-passphrase          # prompts twice
  echo 'my long passphrase' | uv run python -m app.auth.cli set-passphrase --stdin
  ```

The same CLI opens pairing codes (`pairing-code [--scope chat]`), lists devices
(`devices`) and revokes one (`revoke ID`). A shell on the server is the
authority, as it already is for `backend/.env`.

## Sessions

Signing in creates a session row (`auth_session`): kind, label (from the
User-Agent, or the name given when pairing), scopes, created, last seen,
expiry, revoked. Only the SHA-256 digest of the 256-bit token is stored.

- Lifetime 30 days, extended on use while less than 15 days remain, never
  beyond 180 days after it was created. "Last seen" is written at most once a
  minute.
- The cookie is `od_session`, `HttpOnly`, `SameSite=Lax`, `Path=/`, and
  `Secure` when the request is HTTPS (directly, or `X-Forwarded-Proto: https`
  from a proxy on this machine).
- Signing in again in the same browser revokes that browser's previous
  session. Signing out revokes the current one.

### CSRF

A cookie is sent automatically, so every cookie-authenticated request that is
not `GET`/`HEAD`/`OPTIONS` must carry `X-CSRF-Token`. Its value is derived
from the session token (`SHA-256("csrf" || token)`), so nothing extra is
stored; the page learns it only from `GET /api/v1/auth/session` (or the
sign-in response), which a page on another origin cannot read because of CORS.
The custom header also forces a CORS preflight, which only `FRONTEND_ORIGINS`
pass. Bearer requests need no token: nothing ambient carries a bearer header.

The routes that run before a session exists (setup, login, pairing exchange)
accept only `application/json`, which a cross-site HTML form cannot send
without a preflight. That closes login CSRF.

## Pairing a device

1. The owner, signed in with `admin`, opens **Settings → Devices** and chooses
   **Create pairing code** (or runs the CLI). The server returns an 8-character
   code from a 31-symbol alphabet without look-alikes (about 2^39.6), shown as
   `ABCD-EFGH`, with a QR code of `<web UI>/pair#code=ABCD-EFGH`. The code is
   carried in the URL fragment, so it never reaches a server log.
2. On the device, scan the QR or open `/pair` and type the code. A browser
   exchanges it for its own session cookie; any other client
   (`POST /api/v1/auth/pair` without `"cookie": true`) receives a bearer token
   **once**, to keep in its own secret storage.
3. The code is single use (consumed atomically: of two simultaneous
   redemptions only one wins), expires after 5 minutes, and is stored only as
   a SHA-256 digest. At most 5 codes are open at a time.

Guessing is bounded three ways: 10 wrong codes from one address lock that
address out for 10 minutes; 10 wrong codes in total, from anyone, while codes
are open kill every open code; and all auth endpoints are limited to 30
requests a minute per address.

### Revoking

**Settings → Devices** lists every signed-in browser and paired device with
its scope, created and last-seen times, and marks the current one. **Revoke**
takes effect on the next request: the token is refused with `401
session_invalid`, and a revoked credential never falls back to loopback trust.
Revoking the current browser signs it out.

## Host, Origin and DNS rebinding

- `TRUSTED_HOSTS` (default `localhost,127.0.0.1,[::1]`) lists the host names
  the server answers to. A request whose `Host` header is missing, repeated or
  not listed gets `421 untrusted_host`, before anything else, on every path.
  That defeats DNS rebinding: a hostile page can point its own name at
  127.0.0.1, but its requests then carry its own name. A `*.example.com` entry
  matches subdomains. Add your public name when self-hosting.
- An `Origin` header, when present, must be one of `FRONTEND_ORIGINS` or the
  server's own origin, and its host must also be trusted; otherwise `403
  untrusted_origin`.

## Loopback trust

`LOOPBACK_TRUST` decides whether a request made on this machine needs a sign-in:

| Value | A request with no credential from this machine |
|---|---|
| `off` (default) | must sign in, like any other |
| `programs` | is the owner if it is not from a browser (no `Sec-Fetch-Site`, `Sec-Fetch-Dest` or `Origin`); browsers still sign in |
| `all` | is the owner, browsers included (development only) |

"From this machine" means all of: the socket peer is a loopback address, the
`Host` is a loopback name, the request has none of `Forwarded`,
`X-Forwarded-For`, `X-Forwarded-Host`, `X-Forwarded-Proto` or `X-Real-IP`, and
any `Origin` is a loopback origin. A reverse proxy or tunnel on the same
machine connects from loopback, so its forwarding headers are exactly what
cancels the trust; a proxy that strips them must not be used with
`programs`/`all`.

The tradeoff: with `programs`, `curl` and scripts on the machine need no
token, but so does every agent running there with shell tools, which could
then change settings or answer its own approvals as the owner. With `all`, any
web page you open could do the same through your browser, limited only by the
Host/Origin checks. `off` costs one sign-in per browser and a paired token for
scripts, and is the default.

## Desktop app

Unchanged. The desktop profile is switched on only by the launcher, which
attaches its per-launch token to every request. `LaunchAuthMiddleware` refuses
anything without it; the auth middleware then marks the request `desktop` and
adds no checks. The web UI detects the desktop (`GET /api/v1/auth/status`
says `desktop: true`) and never shows the sign-in screen or the Devices
section there.

## Web identity (Telegram and guest ids)

The web UI used to keep its identity (a Telegram user id, or a `web_…` guest
id) in the browser's `localStorage` and send it with each request; the server
believed it. Now the identity is stored on the owner row and returned by
`GET /api/v1/auth/session` as `user_id`:

- **Telegram:** the link-code flow is unchanged (the bot claims the code), but
  the page then calls `PUT /api/v1/auth/identity {"link_code": …}` and the
  server copies the user id from the link record it saw claimed (within an
  hour). The browser no longer supplies the id.
- **Continue without Telegram:** `PUT /api/v1/auth/identity {"guest": true}`;
  the server makes the `web_…` id.
- **Start over:** `DELETE /api/v1/auth/identity`.

### Migrating an existing browser

After the first sign-in, if the browser still has the old `vadoo_user_id` key
and the server has no identity yet, the page sends it once as
`{"legacy_user_id": …}` (accepted only in the old formats, and only while
unset) and deletes the key. Tasks, schedules and sandboxes made under that id
are therefore still yours. If the server already has an identity (another
browser migrated first), the server's wins and the local key is simply
removed. Only the signed-in owner can do this; in a single-owner install every
id on the server is the owner's anyway.

## Endpoints

All under `/api/v1`.

| Method and path | Who | Purpose |
|---|---|---|
| `GET /health` | public | liveness |
| `GET /auth/status` | public | owner configured, setup allowed here, desktop, signed in |
| `POST /auth/setup` | public, this machine only, first run | set the passphrase, sign in |
| `POST /auth/login` | public | passphrase → session cookie |
| `POST /auth/logout` | public | revoke the presented cookie or bearer |
| `GET /auth/session` | chat | who am I, scopes, `user_id`, `csrf_token` |
| `POST /auth/pairing-codes` | admin | open a one-time code |
| `POST /auth/pair` | public | code → cookie session or bearer token |
| `GET /auth/devices` | admin | list sessions and devices |
| `DELETE /auth/devices/{id}` | admin | revoke |
| `PUT`/`DELETE /auth/identity` | admin | set or clear the web identity |

Also public, with their own credential: `POST /permissions/check/…` (Boat's
`HOOK_TOKEN`) and `POST /hooks/{id}` (a routine's webhook secret). Everything
else, including `/docs`, needs a session.

Errors are `{"detail": {"error_code": …, "message": …}}`. The codes a client
acts on: `auth_required`, `session_invalid` (sign in or pair again),
`csrf_failed` (reload), `scope_denied`, `untrusted_host`, `untrusted_origin`,
`locked_out` and `rate_limited` (with `Retry-After`).

## Self-hosting checklist

1. Put a TLS proxy in front (Caddy, nginx, a tunnel) so the cookie is
   `Secure`; over plain HTTP on a LAN it travels in clear.
2. `TRUSTED_HOSTS=localhost,127.0.0.1,[::1],dots.example.com`
3. `FRONTEND_ORIGINS=https://dots.example.com` (where the web UI is served),
   and build the web UI with `NEXT_PUBLIC_API_BASE_URL` pointing at the API.
   Serve both from the same site (same host, or subdomains of one domain) so
   the `SameSite=Lax` cookie is sent.
4. Set the passphrase with the CLI on the server, sign in, then pair your
   phone from **Settings → Devices**.
5. Leave `LOOPBACK_TRUST=off` unless you understand the tradeoff above.

## Limits

- One owner. No multiple accounts, no per-user data separation; every
  principal acts as the owner (within its scope). The `user_id` that older
  routes take as a parameter is not cross-checked against the session.
- Lockout and rate-limit counters are in memory, per process: a restart
  forgets them, and several worker processes each count separately. Run one
  backend process, or put a rate limit in the proxy too.
- No email sign-in, no passkeys, no second factor.
- A paired `admin` device is as powerful as the owner. Pair phones with `chat`
  unless they need settings, and revoke a lost device at once.
