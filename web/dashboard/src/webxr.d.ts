// Minimal ambient types for the WebXR Device API surface this app uses
// (the AR entry point in src/ar/wallet-ar.ts) -- TypeScript's bundled
// lib.dom.d.ts doesn't include WebXR types at all (unlike WebTransport,
// which it does), and a full @types/webxr pull-in is more than the single
// requestSession() call here warrants -- same reasoning as d3-force.d.ts.
interface XRSession extends EventTarget {
  end(): Promise<void>;
}

interface XRSystem extends EventTarget {
  isSessionSupported(mode: "immersive-ar" | "immersive-vr" | "inline"): Promise<boolean>;
  requestSession(
    mode: "immersive-ar" | "immersive-vr" | "inline",
    options?: { optionalFeatures?: string[]; requiredFeatures?: string[] },
  ): Promise<XRSession>;
}

interface Navigator {
  readonly xr?: XRSystem;
}
