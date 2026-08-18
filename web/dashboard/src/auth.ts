// WebAuthn client: register ("pair this device") and login ("prove
// possession") against the gateway's optional auth gate (gateway/auth.go).
// Only relevant when MYCELIUM_GATEWAY_AUTH=1 is set server-side -- if it
// isn't, /api/auth/* doesn't exist and every other endpoint works exactly
// as before this module is ever touched.
//
// Talks to the gateway directly via fetch (not api.ts's getJSON/postJSON):
// those wrap 401s into a store-wide lock, which is the wrong behavior
// specifically for the login/register calls themselves -- a failed login
// attempt should surface as "wrong device" here, not re-trigger the lock
// screen it's already showing.

function b64uToBuf(s: string): ArrayBuffer {
  s = s.replace(/-/g, "+").replace(/_/g, "/");
  while (s.length % 4) s += "=";
  const bin = atob(s);
  const buf = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) buf[i] = bin.charCodeAt(i);
  return buf.buffer;
}

function bufToB64u(buf: ArrayBuffer): string {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function webAuthnSupported(): boolean {
  return typeof window !== "undefined" && !!window.PublicKeyCredential;
}

async function authPostJSON(path: string, body?: unknown): Promise<any> {
  const res = await fetch(path, {
    method: "POST",
    credentials: "include",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data?.error ?? `HTTP ${res.status}`) as Error & { status?: number };
    err.status = res.status;
    throw err;
  }
  return data;
}

/** Registers this browser/device as a new authenticator. There's no
 * separate "already paired" state to track client-side -- the first call
 * pairs the operator's first device, and every later call (another device,
 * a factory reset) just appends another credential on the gateway side
 * (gateway/auth.go's WebAuthnCredentials() returns all of them). */
export async function register(): Promise<void> {
  const begin = await authPostJSON("/api/auth/register/begin");
  const pk = begin.publicKey;
  pk.challenge = b64uToBuf(pk.challenge);
  pk.user.id = b64uToBuf(pk.user.id);
  if (pk.excludeCredentials) {
    for (const c of pk.excludeCredentials) c.id = b64uToBuf(c.id);
  }

  const cred = (await navigator.credentials.create({ publicKey: pk })) as PublicKeyCredential;
  const attestation = cred.response as AuthenticatorAttestationResponse;
  await authPostJSON("/api/auth/register/finish", {
    id: cred.id,
    rawId: bufToB64u(cred.rawId),
    type: cred.type,
    response: {
      clientDataJSON: bufToB64u(attestation.clientDataJSON),
      attestationObject: bufToB64u(attestation.attestationObject),
    },
  });
}

/** Proves possession of a previously-registered device; on success the
 * gateway sets the session cookie and every gated /api/* route opens up. */
export async function login(): Promise<void> {
  const begin = await authPostJSON("/api/auth/login/begin");
  const pk = begin.publicKey;
  pk.challenge = b64uToBuf(pk.challenge);
  if (pk.allowCredentials) {
    for (const c of pk.allowCredentials) c.id = b64uToBuf(c.id);
  }

  const assertion = (await navigator.credentials.get({ publicKey: pk })) as PublicKeyCredential;
  const ar = assertion.response as AuthenticatorAssertionResponse;
  await authPostJSON("/api/auth/login/finish", {
    id: assertion.id,
    rawId: bufToB64u(assertion.rawId),
    type: assertion.type,
    response: {
      clientDataJSON: bufToB64u(ar.clientDataJSON),
      authenticatorData: bufToB64u(ar.authenticatorData),
      signature: bufToB64u(ar.signature),
      userHandle: ar.userHandle ? bufToB64u(ar.userHandle) : null,
    },
  });
}

export async function logout(): Promise<void> {
  await authPostJSON("/api/auth/logout");
}
