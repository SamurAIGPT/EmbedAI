const test = require("node:test");
const assert = require("node:assert/strict");
const { trustedPickerRequest, registerPathPicker } = require("../src/path-picker");

function fixture(url = "http://127.0.0.1:1234/settings/") {
  const mainFrame = { url };
  const window = { webContents: { mainFrame } };
  const event = { sender: window.webContents, senderFrame: mainFrame };
  return { window, event };
}

test("only the Settings main frame at this launch's origin may choose a file or folder", () => {
  const { window, event } = fixture();
  const origin = "http://127.0.0.1:1234";
  assert.ok(trustedPickerRequest(event, window, origin, "file"));
  assert.ok(trustedPickerRequest(event, window, origin, "folder"));
  assert.equal(trustedPickerRequest(event, window, origin, "anything"), false);
  assert.equal(trustedPickerRequest(event, window, "http://127.0.0.1:4321", "file"), false);
  assert.equal(trustedPickerRequest({ ...event, senderFrame: { url: event.senderFrame.url } }, window, origin, "file"), false);
  assert.equal(trustedPickerRequest({ ...event, sender: {} }, window, origin, "file"), false);
  const chat = fixture("http://127.0.0.1:1234/chat/");
  assert.equal(trustedPickerRequest(chat.event, chat.window, origin, "file"), false);
});

test("the dialog exposes only a selected path, cancellation returns null", async () => {
  const { window, event } = fixture();
  let handler, options;
  const dialog = { showOpenDialog: async (parent, given) => { assert.equal(parent, window); options = given; return { canceled: false, filePaths: ["/chosen"] }; } };
  registerPathPicker({ ipcMain: { handle: (_, fn) => { handler = fn; } }, dialog, getWindow: () => window, getOrigin: () => "http://127.0.0.1:1234" });
  assert.equal(await handler(event, "folder"), "/chosen");
  assert.deepEqual(options.properties, ["openDirectory"]);
  dialog.showOpenDialog = async () => ({ canceled: true, filePaths: [] });
  assert.equal(await handler(event, "file"), null);
  await assert.rejects(handler(event, "arbitrary"));
});
