// Where the launch token may go (D-012 decision 5): to the backend this launcher
// started, and nowhere else. The page never sees it: the browser session adds the
// header to requests on their way out.

const HEADER = "X-Open-Dots-Token";

function sameOrigin(url, origin) {
  try {
    return new URL(url).origin === origin;
  } catch {
    return false;
  }
}

// Adds the token to a request's headers, but only if the request is for `origin`.
// Returns the headers to send.
function withToken(details, origin, token) {
  const headers = { ...details.requestHeaders };
  if (sameOrigin(details.url, origin)) headers[HEADER] = token;
  return headers;
}

// Wires the rule into an Electron session.
function attachTokenTo(session, origin, token) {
  session.webRequest.onBeforeSendHeaders({ urls: [`${origin}/*`] }, (details, callback) => {
    callback({ requestHeaders: withToken(details, origin, token) });
  });
}

// What a window may do beyond its own origin: nothing.
function lockDown(webContents, origin, session) {
  const stay = (event, url) => {
    if (!sameOrigin(url, origin)) event.preventDefault();
  };
  webContents.on("will-navigate", stay);
  webContents.on("will-redirect", stay);
  webContents.setWindowOpenHandler(() => ({ action: "deny" }));
  webContents.on("will-attach-webview", (event) => event.preventDefault());
  // Camera, location, notifications and the rest are not something this app asks for.
  session?.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
}

module.exports = { HEADER, sameOrigin, withToken, attachTokenTo, lockDown };
