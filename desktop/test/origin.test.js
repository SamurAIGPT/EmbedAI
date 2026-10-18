const assert = require("node:assert/strict");
const { EventEmitter } = require("node:events");
const { test } = require("node:test");

const { HEADER, sameOrigin, withToken, attachTokenTo, lockDown } = require("../src/origin");

const ORIGIN = "http://127.0.0.1:51234";
const TOKEN = "t".repeat(43);

test("the token goes to the backend's own origin", () => {
  for (const url of [`${ORIGIN}/`, `${ORIGIN}/api/v1/threads`, `${ORIGIN}/chat/?thread=3`, `${ORIGIN}/_next/static/x.js`]) {
    const headers = withToken({ url, requestHeaders: { Accept: "*/*" } }, ORIGIN, TOKEN);
    assert.equal(headers[HEADER], TOKEN, url);
    assert.equal(headers.Accept, "*/*");
  }
});

test("the token goes nowhere else, however the address is dressed up", () => {
  const elsewhere = [
    "http://127.0.0.1:51235/api/v1/threads", // another port on the same host
    "http://127.0.0.1/api/v1/threads", // no port
    "https://127.0.0.1:51234/api/v1/threads", // another scheme
    "http://localhost:51234/api/v1/threads", // another name for the same machine
    "http://127.0.0.1:51234.evil.example/", // a host that starts like ours
    "http://127.0.0.1:51234@evil.example/", // ours as a user name
    "http://evil.example/http://127.0.0.1:51234/", // ours inside the path
    "http://evil.example/?u=http://127.0.0.1:51234",
    "file:///C:/x.html",
    "data:text/html,hello",
    "not a url",
    "",
  ];
  for (const url of elsewhere) {
    const headers = withToken({ url, requestHeaders: {} }, ORIGIN, TOKEN);
    assert.equal(HEADER in headers, false, url);
  }
});

test("withToken does not change the request it was given", () => {
  const details = { url: `${ORIGIN}/`, requestHeaders: { A: "1" } };
  withToken(details, ORIGIN, TOKEN);
  assert.deepEqual(details.requestHeaders, { A: "1" });
});

test("sameOrigin compares origins, not strings", () => {
  assert.equal(sameOrigin(`${ORIGIN}/x`, ORIGIN), true);
  assert.equal(sameOrigin("http://127.0.0.1:51234", ORIGIN), true);
  assert.equal(sameOrigin("HTTP://127.0.0.1:51234/x", ORIGIN), true);
  assert.equal(sameOrigin("http://127.0.0.1:51234.evil.example", ORIGIN), false);
});

test("the session is told to add the header only to requests for the backend's address", () => {
  let filter;
  let handler;
  attachTokenTo(
    { webRequest: { onBeforeSendHeaders: (f, h) => ((filter = f), (handler = h)) } },
    ORIGIN,
    TOKEN,
  );
  assert.deepEqual(filter, { urls: [`${ORIGIN}/*`] });
  let sent;
  handler({ url: `${ORIGIN}/api/v1/threads`, requestHeaders: {} }, (result) => (sent = result));
  assert.equal(sent.requestHeaders[HEADER], TOKEN);
  // Even if the filter let something else through, the handler would not add it.
  handler({ url: "http://evil.example/", requestHeaders: {} }, (result) => (sent = result));
  assert.equal(HEADER in sent.requestHeaders, false);
});

function fakeWebContents() {
  const contents = new EventEmitter();
  contents.setWindowOpenHandler = (handler) => (contents.openHandler = handler);
  return contents;
}

function navigate(contents, event, url) {
  let prevented = false;
  contents.emit(event, { preventDefault: () => (prevented = true) }, url);
  return prevented;
}

test("the window may stay on the backend's origin and nowhere else", () => {
  const contents = fakeWebContents();
  lockDown(contents, ORIGIN);
  for (const event of ["will-navigate", "will-redirect"]) {
    assert.equal(navigate(contents, event, `${ORIGIN}/chat/?thread=2`), false);
    assert.equal(navigate(contents, event, "https://example.com/"), true);
    assert.equal(navigate(contents, event, "file:///C:/Windows/win.ini"), true);
    assert.equal(navigate(contents, event, "http://127.0.0.1:51234.evil.example/"), true);
  }
});

test("no new windows, no embedded views, no permissions", () => {
  const contents = fakeWebContents();
  const permissions = {};
  lockDown(contents, ORIGIN, { setPermissionRequestHandler: (handler) => (permissions.handler = handler) });
  assert.deepEqual(contents.openHandler({ url: "https://example.com/" }), { action: "deny" });
  assert.equal(navigate(contents, "will-attach-webview", undefined), true);
  for (const permission of ["media", "geolocation", "notifications", "clipboard-read"]) {
    let granted = true;
    permissions.handler({}, permission, (value) => (granted = value));
    assert.equal(granted, false, permission);
  }
});
