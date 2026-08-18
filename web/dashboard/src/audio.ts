// Spatial audio tamper alert -- a short synthesized tone (no audio asset
// files, matching the app's no-external-assets ethos) panned left-to-right
// via a StereoPannerNode, played when the provenance chain flips to
// tampered. Gated behind an explicit one-time "enable sound alerts" click:
// browsers block audio playback before any user gesture unlocks the
// AudioContext, and even where they wouldn't, an unannounced sound
// starting on its own is bad behavior for a dashboard to default to.
let ctx: AudioContext | null = null;

export function audioAlertsEnabled(): boolean {
  return ctx !== null;
}

/** Must be called from within a user gesture handler (click, tap) -- that
 * is what actually unlocks AudioContext playback under every major
 * browser's autoplay policy. */
export function enableAudioAlerts(): void {
  if (ctx) return;
  const Ctor =
    window.AudioContext ||
    (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) return;
  ctx = new Ctor();
}

/** A short descending tone swept left-to-right over ~600ms -- deliberately
 * distinct from routine UI sounds, reserved for provenance tamper
 * detection only. No-ops silently if enableAudioAlerts() hasn't run yet
 * (unsupported browser, or the operator never opted in). */
export function playTamperAlert(): void {
  if (!ctx) return;
  const now = ctx.currentTime;

  const panner = new StereoPannerNode(ctx, { pan: -1 });
  panner.connect(ctx.destination);
  panner.pan.setValueAtTime(-1, now);
  panner.pan.linearRampToValueAtTime(1, now + 0.6);

  const gain = ctx.createGain();
  gain.gain.setValueAtTime(0, now);
  gain.gain.linearRampToValueAtTime(0.15, now + 0.02);
  gain.gain.exponentialRampToValueAtTime(0.001, now + 0.6);
  gain.connect(panner);

  const osc = ctx.createOscillator();
  osc.type = "sawtooth";
  osc.frequency.setValueAtTime(440, now);
  osc.frequency.exponentialRampToValueAtTime(220, now + 0.6);
  osc.connect(gain);

  osc.start(now);
  osc.stop(now + 0.6);
}
