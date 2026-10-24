// Sandboxed preload: one narrow native dialog capability, no Node or token.
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("openDots", {
  choosePath: (kind) => ipcRenderer.invoke("open-dots:choose-path", kind),
  nativeControl: (request) => ipcRenderer.invoke("open-dots:native-control", request),
  updates: {
    run: (action) => ipcRenderer.invoke("open-dots:update", action),
    onState: (listener) => {
      const handler = (_event, state) => listener(state);
      ipcRenderer.on("open-dots:update-state", handler);
      return () => ipcRenderer.removeListener("open-dots:update-state", handler);
    },
  },
});
