// Shown in place of the dashboard shell whenever the gateway reports 401
// (MYCELIUM_GATEWAY_AUTH=1 set, no valid session) -- see api.ts's
// getJSON/postJSON, which flip store.locked instead of throwing into every
// individual view. A full page reload after a successful register/login is
// the simplest way back into a fully-wired app (SSE connection, initial
// snapshot fetch) without re-implementing bootstrap() a second time here.
import { MyceliumElement } from "./base.js";
import { register, login, webAuthnSupported } from "../auth.js";

export class LockScreen extends MyceliumElement {
  protected render() {
    if (!webAuthnSupported()) {
      this.innerHTML = `
        <div class="lock-screen">
          <h2>Mycelium is locked</h2>
          <p class="empty-state">This browser doesn't support WebAuthn (navigator.credentials).
          Open the dashboard in a modern browser to pair a device or sign in.</p>
        </div>
      `;
      return;
    }
    this.innerHTML = `
      <div class="lock-screen">
        <h2>Mycelium is locked</h2>
        <p>Sign in with a previously-paired device, or pair this one for the first time.</p>
        <div class="lock-screen__actions">
          <button data-act="login">Sign in</button>
          <button data-act="register" class="secondary">Pair this device</button>
        </div>
        <p class="lock-screen__status" data-el="status"></p>
      </div>
    `;
    this.querySelector('[data-act="login"]')!.addEventListener("click", () => this.doLogin());
    this.querySelector('[data-act="register"]')!.addEventListener("click", () => this.doRegister());
  }

  private setStatus(msg: string) {
    const el = this.querySelector('[data-el="status"]');
    if (el) el.textContent = msg;
  }

  private async doLogin() {
    this.setStatus("Waiting for your device…");
    try {
      await login();
      location.reload();
    } catch (err) {
      this.setStatus(`Sign-in failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  private async doRegister() {
    this.setStatus("Pairing this device…");
    try {
      await register();
      this.setStatus("Device paired. Signing you in…");
      await login();
      location.reload();
    } catch (err) {
      this.setStatus(`Pairing failed: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
}
