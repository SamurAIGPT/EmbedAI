// Test entry only: exercise the real preload, IPC validation and settings page
// with a deterministic native dialog result. Production main.js has no test hook.
const { dialog } = require("electron");
dialog.showOpenDialog = async () => ({ canceled: false, filePaths: [process.env.OPEN_DOTS_E2E_PICKER_PATH] });
require("../main.js");
