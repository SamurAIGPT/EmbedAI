// Session helpers with no runtime imports, so node tests can load them (docs/AUTH.md).

export type AuthStatus = {
  owner_configured: boolean;
  setup_allowed: boolean;
  desktop: boolean;
  signed_in: boolean;
};

export type SessionInfo = {
  kind: "desktop" | "loopback" | "browser" | "device";
  scopes: string[];
  label: string | null;
  session_id: number | null;
  user_id: string | null;
  csrf_token: string | null;
  owner_configured: boolean;
};

export type PairingCode = {
  id: number;
  code: string;
  display_code: string;
  scope: "admin" | "chat";
  label: string;
  expires_at: string;
};

export type Device = {
  id: number;
  kind: "browser" | "device";
  label: string;
  scopes: string[];
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  current: boolean;
};

// The key older versions of the web UI kept the identity under.
export const LEGACY_IDENTITY_KEY = "vadoo_user_id";

// Moves an identity an older web UI left in this browser to the server, once.
// The server keeps its own if it already has one; the local copy goes either way.
export async function migrateLegacyIdentity(
  session: SessionInfo,
  storage: Pick<Storage, "getItem" | "removeItem">,
  setIdentity: (body: { legacy_user_id: string }) => Promise<{ user_id: string | null }>,
): Promise<string | null> {
  let legacy: string | null = null;
  try {
    legacy = storage.getItem(LEGACY_IDENTITY_KEY);
  } catch {
    return session.user_id;
  }
  if (!legacy || !session.scopes.includes("admin")) return session.user_id;
  try {
    const { user_id } = session.user_id ? { user_id: session.user_id } : await setIdentity({ legacy_user_id: legacy });
    storage.removeItem(LEGACY_IDENTITY_KEY);
    return user_id;
  } catch (err) {
    // An id the server does not accept is not worth keeping either.
    if ((err as { status?: number } | null)?.status === 422) storage.removeItem(LEGACY_IDENTITY_KEY);
    return session.user_id;
  }
}

// Only a same-site path: never send someone to another site after sign-in.
export function safeNextPath(raw: string | null): string {
  if (!raw || !raw.startsWith("/") || raw.startsWith("//") || raw.startsWith("/\\")) return "/chat";
  if (raw.startsWith("/login") || raw.startsWith("/pair")) return "/chat";
  return raw;
}

export function pairingCodeFromHash(hash: string): string {
  const match = /(?:^#|&)code=([^&]*)/.exec(hash);
  return match ? decodeURIComponent(match[1]).slice(0, 32) : "";
}
