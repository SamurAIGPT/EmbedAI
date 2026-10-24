// Assisted app updates. The DMG is not signed, so the app cannot replace itself;
// it finds a newer GitHub release, downloads the installer for this platform,
// checks it and opens it when the person asks. Everything that needs no Electron
// lives here (the network, disk and "open" calls are passed in) so it is testable.

const crypto = require("node:crypto");
const fs = require("node:fs");
const path = require("node:path");
const { Readable } = require("node:stream");
const { pipeline } = require("node:stream/promises");

const RELEASES_URL = "https://api.github.com/repos/Anil-matcha/open-duet/releases?per_page=30";
const ASSET_NAME = /^[A-Za-z0-9._-]{1,120}$/;

function parseVersion(text) {
  const match = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/.exec(String(text ?? "").trim());
  if (!match) return null;
  return { core: [Number(match[1]), Number(match[2]), Number(match[3])], pre: match[4] ? match[4].split(".") : [] };
}

// Semantic-version order: a pre-release sorts below its release.
function compareVersions(a, b) {
  const left = parseVersion(a);
  const right = parseVersion(b);
  if (!left || !right) throw new Error(`Not a version: ${!left ? a : b}`);
  for (let i = 0; i < 3; i += 1) if (left.core[i] !== right.core[i]) return left.core[i] < right.core[i] ? -1 : 1;
  if (!left.pre.length || !right.pre.length) return left.pre.length === right.pre.length ? 0 : left.pre.length ? -1 : 1;
  for (let i = 0; i < Math.max(left.pre.length, right.pre.length); i += 1) {
    const x = left.pre[i];
    const y = right.pre[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    if (x === y) continue;
    const numeric = /^\d+$/.test(x) && /^\d+$/.test(y);
    if (numeric) return Number(x) < Number(y) ? -1 : 1;
    if (/^\d+$/.test(x) !== /^\d+$/.test(y)) return /^\d+$/.test(x) ? -1 : 1;
    return x < y ? -1 : 1;
  }
  return 0;
}

// The highest published release. GitHub marks these as pre-releases, so /latest is no use.
function newestRelease(releases) {
  let best = null;
  for (const release of Array.isArray(releases) ? releases : []) {
    if (!release || release.draft || !parseVersion(release.tag_name)) continue;
    if (!best || compareVersions(release.tag_name, best.tag_name) > 0) best = release;
  }
  return best;
}

// The installer built for this platform by `npm run package` (see package.json artifactName).
function pickAsset(release, platform, arch) {
  const wanted = platform === "darwin" && arch === "arm64" ? /^Open-Duet-.+-arm64\.dmg$/ : platform === "win32" && arch === "x64" ? /^Open-Duet-Setup-.+\.exe$/ : null;
  if (!wanted) return null;
  return (release.assets ?? []).find((asset) => ASSET_NAME.test(asset?.name ?? "") && wanted.test(asset.name) && typeof asset.browser_download_url === "string") ?? null;
}

function githubHost(url) {
  try {
    const parsed = new URL(url);
    if (parsed.protocol !== "https:") return false;
    return parsed.hostname === "github.com" || parsed.hostname.endsWith(".github.com") || parsed.hostname.endsWith(".githubusercontent.com");
  } catch {
    return false;
  }
}

function describeError(error) {
  const detail = `${error?.code ?? ""} ${error?.cause?.code ?? ""} ${error?.message ?? error}`;
  if (error?.kind === "integrity") return "The download failed verification and was deleted. Download a fresh installer from the release page.";
  if (/ENOSPC/.test(detail)) return "Not enough disk space for the download. Free some space and try again.";
  if (/EACCES|EPERM/.test(detail)) return "The installer could not be saved to your Downloads folder. Check its permissions.";
  if (/ENOTFOUND|ECONNREFUSED|ECONNRESET|ETIMEDOUT|EAI_AGAIN|fetch failed|network/i.test(detail)) return "Could not reach GitHub. Check your connection and try again.";
  if (/CERT_|SSL|TLS/i.test(detail)) return "The connection to GitHub could not be verified. Check your clock, VPN or proxy.";
  if (/\b403\b|rate limit/i.test(detail)) return "GitHub is limiting requests right now. Try again later.";
  return error?.message ? String(error.message).slice(0, 300) : "The update failed.";
}

function createUpdater({ currentVersion, platform, arch, fetchImpl = fetch, cacheDir, downloadsDir, openFile, onState = () => {}, fsImpl = fs }) {
  let state = { status: "idle", current: currentVersion };
  let busy = false;
  let release = null;
  let asset = null;

  const set = (patch) => {
    state = { ...state, ...patch };
    onState(state);
  };

  async function check({ manual = false } = {}) {
    if (busy || ["downloading", "downloaded", "installing"].includes(state.status)) return state;
    busy = true;
    // A quiet background check keeps what the window shows until it has an answer.
    if (manual) set({ status: "checking", message: undefined });
    try {
      const response = await fetchImpl(RELEASES_URL, { headers: { Accept: "application/vnd.github+json", "User-Agent": "open-duet-desktop" } });
      if (!response.ok) throw new Error(`GitHub answered ${response.status}`);
      const newest = newestRelease(await response.json());
      if (!newest || compareVersions(newest.tag_name, currentVersion) <= 0) {
        release = null;
        asset = null;
        set({ status: "idle", version: undefined, notesUrl: undefined, message: manual ? "You have the latest version." : undefined, checkedAt: Date.now() });
        return state;
      }
      release = newest;
      asset = pickAsset(newest, platform, arch);
      set({
        status: "available",
        version: newest.tag_name.replace(/^v/, ""),
        notesUrl: githubHost(newest.html_url) ? newest.html_url : undefined,
        canDownload: Boolean(asset),
        message: asset ? undefined : "No installer for this computer is attached to the release. Use the release page.",
        checkedAt: Date.now(),
      });
    } catch (error) {
      // Background checks stay quiet (offline is normal); a click gets the reason.
      if (manual) set({ status: "error", message: describeError(error) });
      else if (state.status === "checking") set({ status: "idle" });
    } finally {
      busy = false;
    }
    return state;
  }

  async function download() {
    if (busy || !asset || !(state.status === "available" || (state.status === "error" && state.canDownload))) return state;
    busy = true;
    const name = asset.name;
    const temporary = path.join(cacheDir, `${name}.part`);
    try {
      if (!githubHost(asset.browser_download_url)) throw new Error("The installer is not hosted on GitHub.");
      set({ status: "downloading", percent: 0, message: undefined });
      fsImpl.mkdirSync(cacheDir, { recursive: true, mode: 0o700 });
      const response = await fetchImpl(asset.browser_download_url, { headers: { "User-Agent": "open-duet-desktop" } });
      if (!response.ok || !response.body) throw new Error(`GitHub answered ${response.status}`);
      if (response.url && !githubHost(response.url)) throw new Error("The download was redirected away from GitHub.");
      const hash = crypto.createHash("sha256");
      let received = 0;
      let lastPercent = 0;
      const total = Number(asset.size) || Number(response.headers.get("content-length")) || 0;
      const source = Readable.fromWeb(response.body);
      source.on("data", (chunk) => {
        hash.update(chunk);
        received += chunk.length;
        const percent = total ? Math.min(99, Math.floor((received / total) * 100)) : 0;
        if (percent !== lastPercent) {
          lastPercent = percent;
          set({ percent });
        }
      });
      await pipeline(source, fsImpl.createWriteStream(temporary, { mode: 0o600 }));
      if (Number(asset.size) && received !== Number(asset.size)) {
        throw Object.assign(new Error("size mismatch"), { kind: "integrity" });
      }
      const digest = /^sha256:([0-9a-f]{64})$/i.exec(asset.digest ?? "")?.[1];
      if (digest && digest.toLowerCase() !== hash.digest("hex")) throw Object.assign(new Error("digest mismatch"), { kind: "integrity" });
      fsImpl.mkdirSync(downloadsDir, { recursive: true });
      const target = path.join(downloadsDir, name);
      fsImpl.rmSync(target, { force: true });
      fsImpl.renameSync(temporary, target);
      set({ status: "downloaded", percent: 100, file: target, verified: Boolean(digest) });
    } catch (error) {
      fsImpl.rmSync(temporary, { force: true });
      set({ status: "error", percent: undefined, message: describeError(error), canDownload: true });
    } finally {
      busy = false;
    }
    return state;
  }

  async function install() {
    if (busy || state.status !== "downloaded" || !state.file) return state;
    busy = true;
    try {
      const failure = await openFile(state.file);
      if (failure) throw new Error(failure);
      set({ status: "installing", message: platform === "darwin" ? "The installer opened. Drag Open Duet to Applications, then reopen the app." : "The installer opened. Follow its steps; Open Duet will need to be closed." });
    } catch (error) {
      set({ status: "error", message: describeError(error) });
    } finally {
      busy = false;
    }
    return state;
  }

  // After an error the person can go again from a clean check.
  function reset() {
    if (state.status === "error" || state.status === "installing") set({ status: "idle", message: undefined, percent: undefined });
    return state;
  }

  return { check, download, install, reset, getState: () => state, release: () => release };
}

module.exports = { RELEASES_URL, parseVersion, compareVersions, newestRelease, pickAsset, githubHost, describeError, createUpdater };
