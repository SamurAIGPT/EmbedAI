# Structured browser tools

Claude agents with an assigned Docker **desktop** computer get a private,
visible Chromium browser when **Browser tools** is enabled in the Computer
panel (off by default). Build the [desktop image](../desktop-image/README.md),
then recreate older computers. Stop active turns before changing the setting;
it applies to the next turn. Turning it off removes the tools; it does not
erase the profile. No browser tool attaches to a host browser.
Shell and native desktop computers expose no `browser_*` tools.

With Browser tools off, a desktop starts empty (no browser is open) and the
agent is told so. It may launch Chromium or Firefox ESR in the background
through `exec` for a quick visit, or ask the person to enable Browser tools.

| Tool | Purpose |
|---|---|
| `browser_open` | Navigate the active tab to an HTTP(S) URL or `about:blank`; return a snapshot. |
| `browser_snapshot` | Read accessibility trees and fresh element refs, including iframe elements. |
| `browser_read` | Read up to 20,000 characters of visible main-frame text; `offset` pages through it. |
| `browser_click` | Click an element by ref or a selector matching exactly one main-frame element. |
| `browser_fill`, `browser_type`, `browser_press` | Replace field text, insert text, or press a key on the selected element. |
| `browser_tabs` | List tabs and pending dialogs/downloads, create a blank tab, switch or close by tab ID. |
| `browser_wait` | Wait up to ten seconds for visible text. |
| `browser_dialog` | Accept or dismiss an alert/confirm/prompt in the active tab. |
| `browser_screenshot` | Return a PNG of the visible page. |
| `browser_close`, `browser_restart` | Close Chromium or restart on a blank page while retaining sign-ins. |

Every call consumes the approval gate's exact, one-use permit. Reads are
automatically approved and logged; navigation, tab operations, dialog answers
and input require the same per-turn permission as screen input. The approval
card explicitly grants screen, keyboard and browser control for the turn.
Plan mode refuses browser inputs, even with an existing turn grant. Saved
command rules cannot grant browser control. All browser tools stop while the
person holds the computer. Another conversation's active screen lease also
blocks browser calls. Browser input shares the screen's action budget.

Take a fresh snapshot after actions. Refs are invalidated by navigation,
tab changes, another turn, pixel input, and takeover (even if no browser call
was attempted during takeover). They are random, bound to captured element
handles, and refuse detached elements. Selectors must match exactly one element.

Page text and accessibility labels are untrusted observations. They cannot
grant permissions or override the person's request. The agent is instructed
to stop at login, CAPTCHA or verification and request takeover. Password and
one-time-code inputs are refused by semantic tools; other challenge detection
depends on the page and the agent, so it is not a comprehensive detector.
Downloads are cancelled and reported for manual handling; uploads, arbitrary
JavaScript execution and automatic challenge solving are not exposed.

Each computer keeps its profile outside `/workspace`, under
`/home/agent/browser-profile` with mode 0700. Cookies survive normal browser
close/restart and computer stop/start. Recreate/removal discards them. Do not
expect session-only cookies to survive browser restart. Profiles are isolated
between containers; a host administrator with Docker access can inspect them.
The CDP endpoint listens only on container loopback, with no published port.
Networking follows the computer's no-network or Internet setting.

After a timeout, inspect the current state before retrying a consequential
action: it might already have happened. `browser_restart` closes all tabs and
discards form input while preserving saved sign-ins. The broker closes the
browser after thirty minutes without browser-tool calls. Computer idle-stop
and removal also stop its processes. This initial tool set uses the existing
desktop viewer; a separate browser-only streaming panel is not included.

## Verification

Run the approval/isolation boundary tests with:

```sh
cd backend
DATABASE_BACKEND=sqlite uv run pytest tests/computers/test_browser.py -q
```

Build the desktop image locally, then opt in to real Chromium/CDP tests:

```sh
OPEN_DUET_BROWSER_IMAGE=open-dots-desktop:local DATABASE_BACKEND=sqlite uv run pytest tests/computers/test_browser_real.py -q
```

These tests create disposable, non-root, no-network computers, serve local
fixtures inside them, and remove the computers during teardown. They exercise
semantic actions, iframes, dialogs/popups/tabs, navigation and stale refs,
screenshots, authentication refusals and persistent profile lifecycle.
