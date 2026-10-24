const { sameOrigin } = require("./origin");

// Only the app's own page (main frame, backend origin) may drive updates.
function trustedUpdateRequest(event, window, origin) {
  if (!window || !origin) return false;
  if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) return false;
  return sameOrigin(event.senderFrame?.url, origin);
}

const ACTIONS = ["state", "check", "download", "install", "release-page"];

function registerUpdates({ ipcMain, openExternal, updater, getWindow, getOrigin }) {
  ipcMain.handle("open-dots:update", async (event, action) => {
    if (!trustedUpdateRequest(event, getWindow(), getOrigin())) throw new Error("Updates are only available in the app's window.");
    if (!ACTIONS.includes(action)) throw new Error("Unknown update action.");
    if (action === "check") {
      updater.reset();
      return updater.check({ manual: true });
    }
    if (action === "download") return updater.download();
    if (action === "install") return updater.install();
    if (action === "release-page") {
      const url = updater.getState().notesUrl;
      if (url) await openExternal(url);
    }
    return updater.getState();
  });
}

module.exports = { trustedUpdateRequest, registerUpdates };
