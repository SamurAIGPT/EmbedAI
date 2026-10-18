const { sameOrigin } = require("./origin");

function trustedPickerRequest(event, window, origin, kind) {
  if (!window || !origin || !["file", "folder"].includes(kind)) return false;
  if (event.sender !== window.webContents || event.senderFrame !== window.webContents.mainFrame) return false;
  const url = event.senderFrame?.url;
  if (!sameOrigin(url, origin)) return false;
  return ["/settings", "/settings/"].includes(new URL(url).pathname);
}

function registerPathPicker({ ipcMain, dialog, getWindow, getOrigin }) {
  let choosing = false;
  ipcMain.handle("open-dots:choose-path", async (event, kind) => {
    const window = getWindow();
    if (!trustedPickerRequest(event, window, getOrigin(), kind)) throw new Error("Path selection is only available in Settings.");
    if (choosing) return null;
    choosing = true;
    try {
      const result = await dialog.showOpenDialog(window, {
        title: kind === "file" ? "Choose the Claude executable" : "Choose a workspace root",
        properties: [kind === "file" ? "openFile" : "openDirectory"],
      });
      return result.canceled ? null : result.filePaths[0] ?? null;
    } finally {
      choosing = false;
    }
  });
}

module.exports = { trustedPickerRequest, registerPathPicker };
