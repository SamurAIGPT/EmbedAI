// The encryption key (D-012 decision 6): made once, kept with the operating
// system's secret storage, never replaced.
//
// The key is a Fernet key (32 random bytes, URL-safe base64 with padding), the
// format the backend's TOKEN_ENCRYPTION_KEYS has always used. Electron's
// safeStorage encrypts it (DPAPI on Windows, Keychain on macOS, libsecret or
// KWallet on Linux) and the encrypted bytes live in one file. That protects the
// key from other accounts and from a copied disk; it does not protect it from
// software running as the same user, and the docs say so.
//
// Every failure here is an error the person is told about. There is no quiet
// fallback to a plain file, and an existing key that cannot be read is never a
// reason to make a new one: stored credentials would be lost behind it.

const fs = require("node:fs");
const nodeCrypto = require("node:crypto");
const path = require("node:path");

const FILE_NAME = "encryption-key.v1";

class KeyStoreError extends Error {
  // kind: "unavailable" | "weak" | "unreadable" | "corrupt" | "unwritable"
  constructor(kind, message, options) {
    super(message, options);
    this.name = "KeyStoreError";
    this.kind = kind;
  }
}

function newFernetKey(randomBytes = nodeCrypto.randomBytes) {
  return randomBytes(32).toString("base64").replace(/\+/g, "-").replace(/\//g, "_");
}

function looksLikeFernetKey(text) {
  return typeof text === "string" && /^[A-Za-z0-9_-]{43}=$/.test(text);
}

// What the person is told, per failure. Written for the dialog, not for a log.
const ADVICE = {
  unavailable:
    "This computer's secret storage is not available, so Open Dots cannot keep its encryption key safely. " +
    "Nothing was stored.",
  weak:
    "This session's secret storage is only obfuscating what it keeps (no keyring is running), so Open Dots " +
    "will not use it for its encryption key. Start a keyring such as GNOME Keyring or KWallet, then try again.",
};

function createKeyStore({
  dir,
  safeStorage,
  platform = process.platform,
  fsImpl = fs,
  randomBytes = nodeCrypto.randomBytes,
}) {
  const file = path.join(dir, FILE_NAME);

  function assertSecureStorage() {
    if (!safeStorage.isEncryptionAvailable()) {
      throw new KeyStoreError("unavailable", ADVICE.unavailable);
    }
    if (platform === "linux") {
      const backend = safeStorage.getSelectedStorageBackend?.();
      // "unknown" is as unproven as "basic_text": only a named keyring is trusted.
      if (backend === "basic_text" || backend === "unknown" || backend === undefined) {
        throw new KeyStoreError("weak", ADVICE.weak);
      }
    }
  }

  function readExisting() {
    let encrypted;
    try {
      encrypted = fsImpl.readFileSync(file);
    } catch (error) {
      if (error.code === "ENOENT") return null;
      throw new KeyStoreError("unreadable", `The stored encryption key could not be read (${error.code ?? error.message}).`, {
        cause: error,
      });
    }
    let key;
    try {
      key = safeStorage.decryptString(encrypted);
    } catch (error) {
      throw new KeyStoreError(
        "unreadable",
        "The stored encryption key could not be decrypted. It was probably made under a different " +
          "user account or computer. Open Dots will not make a new one over it: that would lose " +
          "everything encrypted with the old key.",
        { cause: error },
      );
    }
    if (!looksLikeFernetKey(key)) {
      throw new KeyStoreError("corrupt", "The stored encryption key is damaged. Open Dots will not replace it.");
    }
    return key;
  }

  // Writes the encrypted key so that it either exists complete or not at all, and
  // never replaces a key that is already there (a hard link fails if the target
  // exists; a plain rename is the fallback on a file system without links).
  function writeNew(key) {
    const encrypted = safeStorage.encryptString(key);
    const temporary = path.join(dir, `${FILE_NAME}.${process.pid}.${nodeCrypto.randomBytes(4).toString("hex")}.tmp`);
    let descriptor;
    try {
      fsImpl.mkdirSync(dir, { recursive: true, mode: 0o700 });
      descriptor = fsImpl.openSync(temporary, "wx", 0o600);
      fsImpl.writeSync(descriptor, encrypted);
      fsImpl.fsyncSync(descriptor);
      fsImpl.closeSync(descriptor);
      descriptor = undefined;
      try {
        fsImpl.linkSync(temporary, file);
      } catch (error) {
        if (error.code === "EEXIST") return false; // someone else made it first
        if (fsImpl.existsSync(file)) return false;
        fsImpl.renameSync(temporary, file);
        return true;
      }
      return true;
    } catch (error) {
      throw new KeyStoreError("unwritable", `The encryption key could not be stored (${error.code ?? error.message}).`, {
        cause: error,
      });
    } finally {
      if (descriptor !== undefined) {
        try {
          fsImpl.closeSync(descriptor);
        } catch {
          /* already closed */
        }
      }
      try {
        fsImpl.unlinkSync(temporary);
      } catch {
        /* renamed away or never made */
      }
    }
  }

  // The key to give the backend, and whether this call made it.
  function ensure() {
    assertSecureStorage();
    const existing = readExisting();
    if (existing !== null) return { key: existing, created: false };

    const key = newFernetKey(randomBytes);
    if (writeNew(key)) return { key, created: true };
    // Lost the race: use what the winner wrote.
    const winner = readExisting();
    if (winner === null) throw new KeyStoreError("unwritable", "The encryption key could not be stored.");
    return { key: winner, created: false };
  }

  return { ensure, file };
}

module.exports = { createKeyStore, KeyStoreError, newFernetKey, looksLikeFernetKey, FILE_NAME };
