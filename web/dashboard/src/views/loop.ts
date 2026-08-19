// Self-improvement loop (#/loop) -- the reason mycelium exists, made
// visible: traces -> miners -> findings -> applied -> generated skills.
// Counts come from the store (traces/findings already live there); the
// skills list comes from GET /api/skills (gateway/ops.go scans
// generated-skills/). Applied skill-findings are joined to their SKILL.md
// by slug: apply.py names the directory from the finding payload's slug.
import { MyceliumElement, esc, relTime } from "../components/base.js";
import { api } from "../api.js";
import { store } from "../store.js";
import type { Finding, SkillEntry } from "../types.js";

export class LoopView extends MyceliumElement {
  private skills: SkillEntry[] = [];
  private skillsError = "";

  protected render() {
    this.innerHTML = `
      <div class="view-header">
        <h2>Self-improvement Loop</h2>
        <span class="sub">traces → mined → findings → applied → skills</span>
      </div>
      <div data-el="pipeline"></div>
      <h3>Applied findings → generated skills</h3>
      <div data-el="applied"></div>
      <h3>Generated skills on disk</h3>
      <div data-el="skills"></div>
      <p class="muted">Skills are hot-swappable: agents that load SKILL.md files pick up new ones
      without a restart — a finding applied here changes future agent behavior with no human in
      the loop.</p>
    `;
  }

  protected mount() {
    this.onDisconnect(store.subscribe(() => this.renderPipeline()));
    this.fetchSkills();
    const t = setInterval(() => this.fetchSkills(), 30_000);
    this.onDisconnect(() => clearInterval(t));
  }

  private async fetchSkills() {
    try {
      const res = await api.skills();
      this.skills = res.skills;
      this.skillsError = "";
    } catch (err) {
      this.skillsError = err instanceof Error ? err.message : String(err);
    }
    this.renderSkills();
    this.renderApplied();
  }

  private counts() {
    const s = store.get();
    const findings = Array.from(s.findingsById.values());
    return {
      traces: s.status?.traces ?? s.recentTraces.length,
      findings: findings.length,
      open: findings.filter((f) => f.state === "open").length,
      applied: findings.filter((f) => f.state === "applied").length,
      skills: this.skills.length,
    };
  }

  private renderPipeline() {
    const el = this.querySelector<HTMLElement>('[data-el="pipeline"]');
    if (!el) return;
    const c = this.counts();
    const stage = (n: number | string, lbl: string) =>
      `<div class="loop-stage"><div class="big">${n}</div><div class="lbl">${lbl}</div></div>`;
    el.innerHTML = `
      <div class="loop-pipeline">
        ${stage(c.traces, "traces")}
        <span class="loop-arrow">→</span>
        ${stage("7", "miners")}
        <span class="loop-arrow">→</span>
        ${stage(c.findings, "findings")}
        <span class="loop-arrow">→</span>
        ${stage(c.applied, "applied")}
        <span class="loop-arrow">→</span>
        ${stage(c.skills, "skills")}
      </div>`;
  }

  private appliedFindings(): Finding[] {
    return Array.from(store.get().findingsById.values())
      .filter((f) => f.state === "applied")
      .sort((a, b) => (a.created_ts < b.created_ts ? 1 : -1));
  }

  private renderApplied() {
    const el = this.querySelector<HTMLElement>('[data-el="applied"]');
    if (!el) return;
    const applied = this.appliedFindings();
    if (!applied.length) {
      el.innerHTML = `<div class="empty-state">No applied findings yet — apply one from the
        Findings board (or let the cron auto-apply at ≥0.9 confidence) and its artifact
        appears here.</div>`;
      return;
    }
    const skillNames = new Set(this.skills.map((s) => s.name));
    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Applied</th><th>Miner</th><th>Finding</th><th>Suggestion</th><th>Artifact</th></tr></thead>
        <tbody>
          ${applied
            .map((f) => {
              const slug = slugOf(f);
              const hasSkill = slug && skillNames.has(slug);
              return `<tr>
                <td class="muted" title="${esc(f.created_ts)}">${esc(relTime(f.created_ts))}</td>
                <td>${esc(f.miner)}</td>
                <td>${esc(f.title)}</td>
                <td><span class="badge badge--${esc(f.suggestion)}">${esc(f.suggestion)}</span></td>
                <td>${hasSkill ? `<code>generated-skills/${esc(slug!)}/SKILL.md</code>` : `<span class="muted">${f.suggestion === "skill" ? "skill not on disk (yet)" : f.suggestion === "alert" ? "generated-alerts/*.json" : "generated-fixes/*.patch"}</span>`}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
  }

  private renderSkills() {
    const el = this.querySelector<HTMLElement>('[data-el="skills"]');
    if (!el) return;
    if (this.skillsError) {
      el.innerHTML = `<div class="empty-state">Skills listing unavailable: ${esc(this.skillsError)}</div>`;
      return;
    }
    if (!this.skills.length) {
      el.innerHTML = `<div class="empty-state">generated-skills/ is empty — no skill-type finding
        has been applied yet.</div>`;
      return;
    }
    el.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Skill</th><th>Written</th><th>Size</th><th>Path</th></tr></thead>
        <tbody>
          ${this.skills
            .map(
              (s) => `<tr>
              <td><b>${esc(s.name)}</b></td>
              <td class="muted" title="${esc(s.mtime)}">${esc(relTime(s.mtime))}</td>
              <td>${s.size} B</td>
              <td class="muted"><code>${esc(s.path)}</code></td>
            </tr>`,
            )
            .join("")}
        </tbody>
      </table>`;
  }
}

function slugOf(f: Finding): string | null {
  try {
    const p = JSON.parse(f.payload) as { slug?: string };
    return p.slug ?? null;
  } catch {
    return null;
  }
}
