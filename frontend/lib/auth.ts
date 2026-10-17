// Owner sign-in, pairing and devices (docs/AUTH.md).
import { request, setCsrfToken } from "./api";
import type { AuthStatus, Device, PairingCode, SessionInfo } from "./session";

function remember(session: SessionInfo): SessionInfo {
  setCsrfToken(session.csrf_token);
  return session;
}

export const auth = {
  status: () => request<AuthStatus>("/auth/status"),
  session: async () => remember(await request<SessionInfo>("/auth/session")),
  setup: async (passphrase: string) =>
    remember(await request<SessionInfo>("/auth/setup", { method: "POST", body: JSON.stringify({ passphrase }) })),
  login: async (passphrase: string) =>
    remember(await request<SessionInfo>("/auth/login", { method: "POST", body: JSON.stringify({ passphrase }) })),
  logout: async () => {
    try {
      await request<void>("/auth/logout", { method: "POST" });
    } finally {
      setCsrfToken(null);
    }
  },
  pair: async (code: string, label: string) => {
    const result = await request<{ token: string | null; session: SessionInfo }>("/auth/pair", {
      method: "POST",
      body: JSON.stringify({ code, label, cookie: true }),
    });
    return remember(result.session);
  },
  createPairingCode: (scope: "admin" | "chat", label: string) =>
    request<PairingCode>("/auth/pairing-codes", { method: "POST", body: JSON.stringify({ scope, label }) }),
  devices: () => request<Device[]>("/auth/devices"),
  revokeDevice: (id: number) => request<void>(`/auth/devices/${id}`, { method: "DELETE" }),
  setIdentity: (body: { link_code: string } | { guest: true } | { legacy_user_id: string }) =>
    request<{ user_id: string | null }>("/auth/identity", { method: "PUT", body: JSON.stringify(body) }),
  clearIdentity: () => request<{ user_id: string | null }>("/auth/identity", { method: "DELETE" }),
};

