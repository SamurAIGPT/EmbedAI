"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { auth } from "@/lib/auth";
import { encodeQr, qrSvgPath } from "@/lib/qr";
import type { Device, PairingCode } from "@/lib/session";
import { useSession } from "./AuthGate";

const inputClass = "block w-full rounded-md border border-line bg-transparent px-3 py-2 text-sm";
const buttonClass = "cursor-pointer rounded-md border border-line px-3 py-2 text-sm hover:bg-hover disabled:cursor-not-allowed disabled:opacity-50";

function when(iso: string): string {
  return new Date(iso).toLocaleString();
}

function QrCode({ text }: { text: string }) {
  const qr = useMemo(() => {
    const matrix = encodeQr(text);
    return matrix ? qrSvgPath(matrix) : null;
  }, [text]);
  if (!qr) return null;
  return (
    <svg role="img" aria-label="Pairing QR code" viewBox={`0 0 ${qr.size} ${qr.size}`} width={192} height={192}
      shapeRendering="crispEdges" className="rounded bg-white" data-testid="pairing-qr">
      <rect width={qr.size} height={qr.size} fill="#fff" />
      <path d={qr.path} fill="#000" />
    </svg>
  );
}

function OpenCode({ code, onDone }: { code: PairingCode; onDone: () => void }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const left = Math.max(0, Math.round((new Date(code.expires_at).getTime() - now) / 1000));
  const link = `${window.location.origin}/pair#code=${encodeURIComponent(code.display_code)}`;
  return (
    <div className="space-y-3 rounded-md border border-line p-4" aria-live="polite">
      {left > 0 ? (
        <>
          <p className="text-sm">On the other device, scan the code or open <span className="break-all font-mono text-xs">{link}</span></p>
          <div className="flex flex-wrap items-center gap-4">
            <QrCode text={link} />
            <div>
              <p className="font-mono text-2xl font-semibold tracking-widest" data-testid="pairing-code">{code.display_code}</p>
              <p className="text-xs text-muted">{code.scope === "admin" ? "Full access" : "Chat only"} · works once · {Math.floor(left / 60)}:{String(left % 60).padStart(2, "0")} left</p>
            </div>
          </div>
        </>
      ) : (
        <p className="text-sm text-muted">That code has expired.</p>
      )}
      <button type="button" className={buttonClass} onClick={onDone}>Done</button>
    </div>
  );
}

// Signed-in browsers and paired devices: pair a new one, see and revoke them (docs/AUTH.md).
export function DevicesSection() {
  const { session } = useSession();
  const router = useRouter();
  const [devices, setDevices] = useState<Device[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [scope, setScope] = useState<"admin" | "chat">("chat");
  const [label, setLabel] = useState("");
  const [code, setCode] = useState<PairingCode | null>(null);
  const [busy, setBusy] = useState(false);
  const [confirming, setConfirming] = useState<number | null>(null);

  const admin = session !== null && session.kind !== "desktop" && session.scopes.includes("admin");

  const load = useCallback(async () => {
    try {
      setDevices(await auth.devices());
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not load devices.");
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (admin) void load();
  }, [admin, load]);

  if (!session || session.kind === "desktop") return null;

  const create = async () => {
    setBusy(true);
    setError(null);
    try {
      setCode(await auth.createPairingCode(scope, label));
      setLabel("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not create a pairing code.");
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (device: Device) => {
    if (confirming !== device.id) {
      setConfirming(device.id);
      return;
    }
    setConfirming(null);
    setBusy(true);
    try {
      await auth.revokeDevice(device.id);
      if (device.current) {
        router.replace("/login/");
        return;
      }
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not revoke that device.");
    } finally {
      setBusy(false);
    }
  };

  const signOut = async () => {
    try { await auth.logout(); } finally { router.replace("/login/"); }
  };

  return (
    <section className="space-y-4 rounded-lg border border-line p-5" aria-labelledby="devices-heading">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 id="devices-heading" className="font-semibold">Devices</h3>
        {session.kind === "browser" && <button type="button" className={buttonClass} onClick={signOut}>Sign out</button>}
      </div>
      <p className="text-xs text-muted">
        {session.kind === "loopback"
          ? "This request is trusted because it comes from this machine (LOOPBACK_TRUST)."
          : `Signed in as ${session.label ?? "this browser"} · ${session.scopes.includes("admin") ? "full access" : "chat only"}.`}
      </p>
      {error && <p role="alert" className="text-sm text-danger">{error}</p>}
      {admin && (
        <>
          {code ? (
            <OpenCode code={code} onDone={() => { setCode(null); void load(); }} />
          ) : (
            <div className="space-y-3">
              <p className="text-sm text-muted">Pair a phone, tablet or another browser with a one-time code.</p>
              <div className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1">
                  <label htmlFor="pair-scope" className="block text-sm font-medium">Access</label>
                  <select id="pair-scope" className={inputClass} value={scope} onChange={(e) => setScope(e.target.value as "admin" | "chat")}>
                    <option value="chat">Chat only</option>
                    <option value="admin">Full access (settings, devices)</option>
                  </select>
                </div>
                <div className="space-y-1">
                  <label htmlFor="pair-label" className="block text-sm font-medium">Name (optional)</label>
                  <input id="pair-label" className={inputClass} value={label} onChange={(e) => setLabel(e.target.value)} placeholder="My phone" maxLength={80} />
                </div>
              </div>
              <button type="button" className={buttonClass} onClick={create} disabled={busy}>Create pairing code</button>
            </div>
          )}
          <div>
            <h4 className="mb-2 text-sm font-medium">Signed in</h4>
            {devices === null ? <p className="text-sm text-muted">Loading…</p> : devices.length === 0 ? (
              <p className="text-sm text-muted">No signed-in browsers or paired devices.</p>
            ) : (
              <ul className="divide-y divide-line">
                {devices.map((device) => (
                  <li key={device.id} className="flex flex-wrap items-center justify-between gap-2 py-2" data-testid="device-row">
                    <div className="min-w-0">
                      <p className="truncate text-sm font-medium">{device.label}{device.current && <span className="ml-2 rounded bg-inset px-1.5 py-0.5 text-xs font-normal">this browser</span>}</p>
                      <p className="text-xs text-muted">{device.kind === "browser" ? "Browser" : "Device token"} · {device.scopes.includes("admin") ? "full access" : "chat only"} · last seen {when(device.last_seen_at)} · since {when(device.created_at)}</p>
                    </div>
                    <button type="button" className={buttonClass} disabled={busy} onClick={() => revoke(device)} aria-label={`Revoke ${device.label}`}>
                      {confirming === device.id ? "Confirm revoke" : "Revoke"}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </>
      )}
    </section>
  );
}
