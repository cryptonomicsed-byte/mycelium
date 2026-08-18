// Feature-detection only, deliberately split out of wallet-ar.ts: that
// module's top-level `import * as THREE from "three"` means any static
// import of it -- even just to reach one small function -- pulls Three.js
// into whatever bundle does the importing, defeating the whole point of
// dynamically importing wallet-ar.ts only on an "Enter AR" click. This
// file has no heavy dependencies, so views/wallets.ts can import it
// statically for the up-front check without downloading a byte of Three.js
// until (if ever) the operator actually clicks the button.
export async function arSupported(): Promise<boolean> {
  if (typeof navigator === "undefined" || !navigator.xr) return false;
  try {
    return await navigator.xr.isSessionSupported("immersive-ar");
  } catch {
    return false;
  }
}
