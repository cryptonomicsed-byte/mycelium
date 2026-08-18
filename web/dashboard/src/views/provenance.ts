// Provenance Chain (#/provenance) -- the literal hash chain, rendered link
// by link, with a break highlighted exactly where it diverges. GET
// /api/provenance itself returns 409 on divergence (gateway/main.go
// handleProvenance calls reconcile() and refuses to return a tampered
// chain), so on divergence this falls back to the last chain fetched
// successfully and labels it "last known-good" -- reconcile()'s reason
// string is forensic info worth keeping on screen, not just a red boolean.
import { MyceliumElement, esc, relTime } from "../components/base.js";
import { api } from "../api.js";
import { store } from "../store.js";
import { traceViewedTamperedProvenance } from "../trace.js";
import type { ProvenanceChain, ProvenanceEnvelope, ProvenanceStatus } from "../types.js";

export class ProvenanceView extends MyceliumElement {
  private lastGoodChain: ProvenanceChain | null = null;
  private currentChain: ProvenanceChain | null = null;
  private currentError: string | null = null;
  private status: ProvenanceStatus | null = null;
  private loading = true;

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Provenance Chain</h2>
        <button data-act="verify">Verify now</button>
      </div>
      <div data-el="body"></div>
    `;
    this.querySelector('[data-act="verify"]')!.addEventListener("click", () => this.refresh());
    this.refresh();
  }

  protected mount() {
    this.onDisconnect(
      store.subscribe((s) => {
        this.status = s.provenance;
        this.renderBody();
      }),
    );
  }

  private async refresh() {
    this.loading = true;
    this.renderBody();
    try {
      this.status = await api.provenanceVerify();
      store.setProvenance(this.status);
    } catch (err) {
      console.warn("mycelium: /api/provenance/verify failed", err);
    }
    try {
      this.currentChain = await api.provenance();
      this.currentError = null;
      this.lastGoodChain = this.currentChain;
    } catch (err) {
      this.currentChain = null;
      this.currentError = err instanceof Error ? err.message : String(err);
    }
    this.loading = false;
    if (this.status && !this.status.valid) traceViewedTamperedProvenance();
    this.renderBody();
  }

  private renderBody() {
    const body = this.querySelector<HTMLElement>('[data-el="body"]');
    if (!body) return;
    if (this.loading && !this.currentChain && !this.lastGoodChain) {
      body.innerHTML = `<div class="empty-state">Loading…</div>`;
      return;
    }

    const parts: string[] = [];
    if (this.status) {
      parts.push(`
        <div class="panel">
          <div class="stat-row">
            <div class="stat"><span class="stat__label">Status</span><span class="stat__value">${this.status.valid ? "OK" : "TAMPERED"}</span></div>
            <div class="stat"><span class="stat__label">Anchored</span><span class="stat__value">${this.status.anchored}</span></div>
            <div class="stat"><span class="stat__label">Reason</span><span class="stat__value">${esc(this.status.reason)}</span></div>
          </div>
        </div>
      `);
    }

    if (this.currentChain) {
      parts.push(
        `<h3>Chain (${this.currentChain.count} envelopes, pubkey ${esc(this.currentChain.pubkey.slice(0, 16))}…)</h3>`,
      );
      parts.push(renderChain(this.currentChain.chain));
    } else if (this.currentError) {
      parts.push(`<div class="empty-state">Current chain unavailable: ${esc(this.currentError)}</div>`);
      if (this.lastGoodChain) {
        parts.push(
          `<h3>Last known-good chain (${this.lastGoodChain.count} envelopes, captured before this divergence)</h3>`,
        );
        parts.push(renderChain(this.lastGoodChain.chain));
      }
    }

    body.innerHTML = parts.join("");
  }
}

function renderChain(chain: ProvenanceEnvelope[]): string {
  if (!chain.length) return `<div class="empty-state">Chain is empty.</div>`;
  const rows: string[] = [];
  let prevHash = "";
  for (const e of chain) {
    const broken = e.prev_hash !== prevHash;
    rows.push(`
      <div class="chain-link ${broken ? "chain-link--broken" : ""}">${broken ? "✕ BROKEN LINK" : "↓"}</div>
      <div class="chain-envelope ${broken ? "chain-envelope--broken" : ""}">
        <span>#${e.index}</span>
        <span class="chain-hash" title="${esc(e.trace_id)}">${esc(e.action)} → ${esc(e.target)} (${esc(e.outcome)})</span>
        <span class="chain-hash" title="prev_hash: ${esc(e.prev_hash)}&#10;hash: ${esc(e.hash)}&#10;sig: ${esc(e.sig)}">${esc(e.hash.slice(0, 12))}…</span>
        <span title="${esc(e.ts)}">${esc(relTime(e.ts))}</span>
      </div>
    `);
    prevHash = e.hash;
  }
  return `<div class="chain-list">${rows.join("")}</div>`;
}
