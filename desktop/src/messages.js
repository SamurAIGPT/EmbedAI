// What the person reads when the backend does not start or stops. The exit codes
// are the backend's contract with the launcher (backend/app/desktop/entry.py).

const fs = require("node:fs");

const EXIT = { HANDSHAKE: 2, KEY_MISMATCH: 3, DATABASE: 4, UI_MISSING: 5 };

// The backend explains itself on stderr in lines that start with "[desktop] ".
// They never hold a secret, so the last one can be shown as the reason.
function lastReason(logFile, fsImpl = fs) {
  let text;
  try {
    const size = fsImpl.statSync(logFile).size;
    const length = Math.min(size, 16 * 1024);
    const descriptor = fsImpl.openSync(logFile, "r");
    try {
      const buffer = Buffer.alloc(length);
      fsImpl.readSync(descriptor, buffer, 0, length, size - length);
      text = buffer.toString("utf8");
    } finally {
      fsImpl.closeSync(descriptor);
    }
  } catch {
    return null;
  }
  const lines = text.split(/\r?\n/).filter((line) => line.startsWith("[desktop] "));
  return lines.length ? lines[lines.length - 1].slice("[desktop] ".length) : null;
}

function describeFailure({ code, logFile, reason }) {
  const where = `Details are in ${logFile}.`;
  const because = reason ? `\n\n${reason}` : "";
  switch (code) {
    case EXIT.KEY_MISMATCH:
      return {
        title: "Open Duet cannot read its saved credentials",
        body:
          "The encryption key does not open what is stored, so Open Duet stopped without changing anything. " +
          `If you replaced or restored files in its data folder, restore the matching key too.${because}\n\n${where}`,
      };
    case EXIT.DATABASE:
      return {
        title: "Open Duet could not open its database",
        body: `The database was left as it was.${because}\n\n${where}`,
      };
    case EXIT.UI_MISSING:
      return {
        title: "Open Duet has no page to show",
        body: `Build it with "npm run build" in the desktop folder, then start again.${because}\n\n${where}`,
      };
    case EXIT.HANDSHAKE:
      return {
        title: "Open Duet could not start its backend",
        body: `The backend refused the launcher's start message.${because}\n\n${where}`,
      };
    default:
      return {
        title: "Open Duet could not start its backend",
        body: `The backend stopped${code === null || code === undefined ? "" : ` (exit ${code})`} before it was ready.${because}\n\n${where}`,
      };
  }
}

function describeUnexpectedExit({ code, logFile, reason }) {
  return {
    title: "Open Duet' backend stopped",
    body:
      `The backend stopped unexpectedly${code === null || code === undefined ? "" : ` (exit ${code})`}. ` +
      `Anything Claude was answering was interrupted and will not be repeated.` +
      `${reason ? `\n\n${reason}` : ""}\n\nDetails are in ${logFile}. Start Open Duet again to continue.`,
  };
}

module.exports = { EXIT, lastReason, describeFailure, describeUnexpectedExit };
