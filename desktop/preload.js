// Sandboxed preload: one narrow native dialog capability, no Node or token.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("openDots", {
  choosePath: (kind) => ipcRenderer.invoke("open-dots:choose-path", kind),
});
