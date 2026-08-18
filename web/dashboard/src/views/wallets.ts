// Wallet / Money-Flow (#/wallets) -- dedicated renderer for the 3 wallet
// miners' payloads (mycelium/miners.py: wallet_activity, wallet_correlation,
// wallet_anomaly), otherwise indistinguishable JSON blobs in the generic
// Findings Board. This is the most invisible real capability in the
// substrate today -- the whole reason this view isn't deferrable.
import { MyceliumElement, esc } from "../components/base.js";
import { store } from "../store.js";
import type { Finding, WalletActivityPayload, WalletCorrelationPayload } from "../types.js";
import { forceSimulation, forceLink, forceManyBody, forceCenter, forceCollide } from "d3-force";
import type { SimulationNodeDatum } from "d3-force";
import { arSupported } from "../ar/xr-detect.js";

interface GraphNode extends SimulationNodeDatum {
  id: string;
}
interface GraphLink {
  source: string | GraphNode;
  target: string | GraphNode;
  shared: number;
}

export class WalletsView extends MyceliumElement {
  private sim: ReturnType<typeof forceSimulation<GraphNode, GraphLink>> | null = null;
  // null = not checked yet -- the button only ever appears once this
  // resolves true, never speculatively (arSupported() is async, so it
  // can't be part of the synchronous initial template the way the
  // WebGPU/WebTransport feature-detects elsewhere in this app are).
  private arAvailable: boolean | null = null;
  private lastCorrelations: Finding[] = [];

  protected render() {
    this.innerHTML = `
      <div class="view-header"><h2>Wallet / Money-Flow</h2></div>
      <div data-el="body"></div>
    `;
    this.renderBody();
  }

  protected mount() {
    this.onDisconnect(store.subscribe(() => this.renderBody()));
    this.onDisconnect(() => this.sim?.stop());
    arSupported().then((ok) => {
      this.arAvailable = ok;
      if (this.isConnected) this.renderBody();
    });
  }

  private renderBody() {
    const body = this.querySelector<HTMLElement>('[data-el="body"]');
    if (!body) return;
    const { findingsById } = store.get();
    const all = Array.from(findingsById.values());

    const activity = all
      .filter((f) => f.miner === "wallet_activity")
      .sort((a, b) => (a.created_ts < b.created_ts ? 1 : -1))[0];
    const correlations = all.filter((f) => f.miner === "wallet_correlation");
    const anomalies = all.filter((f) => f.miner === "wallet_anomaly");

    if (!activity && !correlations.length && !anomalies.length) {
      body.innerHTML = `<div class="empty-state">No wallet findings yet -- run the wallet_activity /
        wallet_correlation / wallet_anomaly miners over trace data with wallet_buy actions.</div>`;
      return;
    }

    body.innerHTML = `
      ${activity ? renderActivity(activity) : ""}
      ${
        correlations.length
          ? `
        <div class="view-header">
          <h3>Wallet Clusters (${correlations.length})</h3>
          ${this.arAvailable ? `<button class="secondary" data-act="enter-ar">Enter AR</button>` : ""}
        </div>
        <svg data-el="graph" width="100%" height="360" viewBox="0 0 800 360"
             style="background:var(--bg-panel);border:1px solid var(--border);border-radius:6px;"></svg>
        ${renderCorrelationTable(correlations)}
      `
          : ""
      }
      ${anomalies.length ? `<h3>Wallet Anomalies (${anomalies.length})</h3>${renderAnomalies(anomalies)}` : ""}
    `;

    this.wireShareButtons(body);
    if (correlations.length) {
      this.lastCorrelations = correlations;
      this.renderGraph(correlations);
      this.wireAR(body);
    }
  }

  private wireAR(root: HTMLElement) {
    const btn = root.querySelector<HTMLButtonElement>('[data-act="enter-ar"]');
    btn?.addEventListener("click", async () => {
      btn.disabled = true;
      btn.textContent = "Starting AR…";
      try {
        const { enterWalletAR } = await import("../ar/wallet-ar.js");
        const { nodes, links } = this.buildARGraph(this.lastCorrelations);
        await enterWalletAR(nodes, links, () => {
          btn.disabled = false;
          btn.textContent = "Enter AR";
        });
      } catch (err) {
        console.warn("mycelium: entering AR failed", err);
        btn.disabled = false;
        btn.textContent = "Enter AR";
      }
    });
  }

  /** Reuses the same 2D force-layout positions the SVG graph already
   * computed (renderGraph populates GraphNode.x/y via d3-force) so the AR
   * cluster's layout matches what's on screen, rather than re-running the
   * simulation a second time for a second renderer. */
  private buildARGraph(correlations: Finding[]) {
    const nodes: { id: string; x: number; y: number }[] = [];
    const links: { sourceId: string; targetId: string; weight: number }[] = [];
    const seen = new Set<string>();
    for (const f of correlations) {
      const p = safeParse<WalletCorrelationPayload>(f.payload);
      if (!p) continue;
      links.push({ sourceId: p.wallet_a, targetId: p.wallet_b, weight: p.shared.length });
      for (const id of [p.wallet_a, p.wallet_b]) {
        if (seen.has(id)) continue;
        seen.add(id);
        nodes.push({ id, x: 400, y: 180 }); // overwritten below once the sim has real positions
      }
    }
    return { nodes: this.withSimPositions(nodes), links };
  }

  private withSimPositions(nodes: { id: string; x: number; y: number }[]) {
    const simNodes: GraphNode[] = this.sim?.nodes() ?? [];
    const byId = new Map<string, GraphNode>(simNodes.map((n) => [n.id, n]));
    return nodes.map((n) => {
      const simNode = byId.get(n.id);
      return { id: n.id, x: simNode?.x ?? n.x, y: simNode?.y ?? n.y };
    });
  }

  private wireShareButtons(root: HTMLElement) {
    root.querySelectorAll<HTMLButtonElement>("[data-share]").forEach((btn) => {
      btn.addEventListener("click", async () => {
        const text = btn.dataset.share!;
        try {
          if (navigator.share) {
            await navigator.share({ title: "Mycelium wallet finding", text });
          } else {
            await navigator.clipboard.writeText(text);
            const orig = btn.textContent;
            btn.textContent = "Copied";
            setTimeout(() => {
              btn.textContent = orig;
            }, 1500);
          }
        } catch {
          // user cancelled the share sheet, or clipboard permission denied --
          // neither is worth surfacing as an error
        }
      });
    });
  }

  private renderGraph(correlations: Finding[]) {
    const svg = this.querySelector<SVGSVGElement>('[data-el="graph"]');
    if (!svg) return;
    this.sim?.stop();

    const nodeIds = new Set<string>();
    const links: GraphLink[] = [];
    for (const f of correlations) {
      const p = safeParse<WalletCorrelationPayload>(f.payload);
      if (!p) continue;
      nodeIds.add(p.wallet_a);
      nodeIds.add(p.wallet_b);
      links.push({ source: p.wallet_a, target: p.wallet_b, shared: p.shared.length });
    }
    const nodes: GraphNode[] = Array.from(nodeIds).map((id) => ({ id }));

    const W = 800;
    const H = 360;
    svg.innerHTML = `<g data-el="links"></g><g data-el="nodes"></g>`;
    const linkG = svg.querySelector('[data-el="links"]')!;
    const nodeG = svg.querySelector('[data-el="nodes"]')!;
    const svgNS = "http://www.w3.org/2000/svg";

    const linkEls = links.map((l) => {
      const line = document.createElementNS(svgNS, "line");
      line.setAttribute("stroke", "var(--border)");
      line.setAttribute("stroke-width", String(Math.min(6, 1 + l.shared)));
      linkG.appendChild(line);
      return line;
    });
    const nodeEls = nodes.map((n) => {
      const g = document.createElementNS(svgNS, "g");
      const circle = document.createElementNS(svgNS, "circle");
      circle.setAttribute("r", "10");
      circle.setAttribute("fill", "var(--accent)");
      const label = document.createElementNS(svgNS, "text");
      label.setAttribute("font-size", "9");
      label.setAttribute("fill", "var(--fg-dim)");
      label.setAttribute("dy", "-14");
      label.setAttribute("text-anchor", "middle");
      label.textContent = `${n.id.slice(0, 6)}…`;
      g.append(circle, label);
      nodeG.appendChild(g);
      return g;
    });

    this.sim = forceSimulation<GraphNode, GraphLink>(nodes)
      .force(
        "link",
        forceLink<GraphNode, GraphLink>(links)
          .id((d) => d.id)
          .distance(80),
      )
      .force("charge", forceManyBody<GraphNode>().strength(-120))
      .force("center", forceCenter(W / 2, H / 2))
      .force("collide", forceCollide<GraphNode>(20))
      .on("tick", () => {
        links.forEach((l, i) => {
          const s = l.source as GraphNode;
          const t = l.target as GraphNode;
          const el = linkEls[i]!;
          el.setAttribute("x1", String(s.x ?? 0));
          el.setAttribute("y1", String(s.y ?? 0));
          el.setAttribute("x2", String(t.x ?? 0));
          el.setAttribute("y2", String(t.y ?? 0));
        });
        nodes.forEach((n, i) => {
          nodeEls[i]!.setAttribute("transform", `translate(${n.x ?? 0},${n.y ?? 0})`);
        });
      });
  }
}

function safeParse<T>(raw: string): T | null {
  try {
    return JSON.parse(raw) as T;
  } catch {
    return null;
  }
}

function renderActivity(f: Finding): string {
  const p = safeParse<WalletActivityPayload>(f.payload);
  if (!p) return "";
  return `
    <h3>Money Flow</h3>
    <p class="finding-card__title">${esc(f.title)}</p>
    <table class="data-table">
      <thead><tr><th>Token</th><th>Wallets</th><th>Buys</th><th>Volume USD</th><th>Smart</th></tr></thead>
      <tbody>
        ${p.tokens
          .map(
            (t) => `
          <tr>
            <td>${esc(t.symbol)}</td>
            <td>${t.distinct_wallets}</td>
            <td>${t.buys}</td>
            <td>$${t.volume_usd.toLocaleString()}</td>
            <td>${t.smart_wallets}</td>
          </tr>
        `,
          )
          .join("")}
      </tbody>
    </table>
    <table class="data-table">
      <thead><tr><th>Wallet</th><th>Tokens</th><th>Buys</th><th>Volume USD</th><th>Tags</th></tr></thead>
      <tbody>
        ${p.wallets
          .map(
            (w) => `
          <tr>
            <td title="${esc(w.wallet)}">${esc(w.wallet.slice(0, 10))}…</td>
            <td>${w.distinct_tokens}</td>
            <td>${w.buys}</td>
            <td>$${w.volume_usd.toLocaleString()}</td>
            <td>${w.tags.map((t) => `<span class="badge badge--skill">${esc(t)}</span>`).join(" ")}</td>
          </tr>
        `,
          )
          .join("")}
      </tbody>
    </table>
  `;
}

function renderCorrelationTable(correlations: Finding[]): string {
  return `
    <table class="data-table">
      <thead><tr><th>Wallet A</th><th>Wallet B</th><th>Shared tokens</th><th></th></tr></thead>
      <tbody>
        ${correlations
          .map((f) => {
            const p = safeParse<WalletCorrelationPayload>(f.payload);
            if (!p) return "";
            const shareText = `Wallet cluster: ${p.wallet_a} + ${p.wallet_b} co-bought ${p.shared.join(", ")}`;
            return `
            <tr>
              <td title="${esc(p.wallet_a)}">${esc(p.wallet_a.slice(0, 10))}…</td>
              <td title="${esc(p.wallet_b)}">${esc(p.wallet_b.slice(0, 10))}…</td>
              <td>${p.shared.length}</td>
              <td><button class="secondary" data-share="${esc(shareText)}">Share</button></td>
            </tr>
          `;
          })
          .join("")}
      </tbody>
    </table>
  `;
}

function renderAnomalies(anomalies: Finding[]): string {
  return anomalies
    .map((f) => {
      const raw = safeParse<Record<string, unknown>>(f.payload);
      const isBurst = !!raw && "tokens" in raw && "window_s" in raw;
      const shareText = `${f.title} -- ${f.evidence}`;
      return `
      <div class="finding-card finding-card--${esc(f.state)}">
        <div class="finding-card__head">
          <span class="badge badge--alert">${isBurst ? "burst" : "everything-buyer"}</span>
          <span class="finding-card__conf">${Math.round(f.confidence * 100)}%</span>
        </div>
        <div class="finding-card__title">${esc(f.title)}</div>
        <p>${esc(f.evidence)}</p>
        <div class="finding-card__actions">
          <button class="secondary" data-share="${esc(shareText)}">Share</button>
        </div>
      </div>
    `;
    })
    .join("");
}
