// WebXR/AR mode for #/wallets' correlation graph -- "walk into" the same
// wallet-cluster data the 2D d3-force graph (views/wallets.ts) already
// renders, positioned in space instead of an SVG plane. Dynamically
// imported only when the operator clicks "Enter AR" (esbuild's code
// splitting keeps Three.js out of every other view's download), and the
// button itself only ever appears when navigator.xr.isSessionSupported
// confirms 'immersive-ar' -- there is no AR-capable device in this
// project's dev sandbox (navigator.xr is entirely absent there, confirmed
// via headless Chromium), so this ships as spec-correct WebXR/Three.js
// code, verified by feature-detection and typecheck, not by a real AR
// session -- the same bar the already-shipped WebNN feature ships under.
//
// Three.js is a second justified dependency here (d3-force is the first,
// for the 2D graph): hand-rolling raw WebGL immersive-AR rendering --
// XRWebGLLayer, reference spaces, frame-loop pose math -- from scratch is
// high-risk for one view, the same reasoning that already justified
// d3-force for the 2D layout.
//
// arSupported() deliberately does NOT live in this file -- see
// xr-detect.ts's header comment: a static import of anything from this
// module (even just a feature-detect function) would pull the `import *
// as THREE` below into the importer's bundle, defeating the whole point of
// only loading Three.js on an actual "Enter AR" click.
import * as THREE from "three";

export interface ARGraphNode {
  id: string;
  x: number;
  y: number;
}
export interface ARGraphLink {
  sourceId: string;
  targetId: string;
  weight: number;
}

/** Starts an immersive-ar session rendering `nodes`/`links` as a small
 * glowing cluster ~1.2m in front of the user, mapped from the same
 * force-layout coordinates the 2D SVG graph uses (roughly 0..800 x
 * 0..360) scaled down into a ~1m AR-space footprint. Slowly auto-rotates
 * so the cluster reads as three-dimensional rather than a flat sprite.
 * `onExit` fires once, whether the session ends via the platform's own
 * "leave AR" affordance or an error during setup. */
export async function enterWalletAR(
  nodes: ARGraphNode[],
  links: ARGraphLink[],
  onExit: () => void,
): Promise<void> {
  if (!navigator.xr) throw new Error("navigator.xr unavailable");

  const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.xr.enabled = true;
  renderer.domElement.style.position = "fixed";
  renderer.domElement.style.inset = "0";
  renderer.domElement.style.zIndex = "1000";
  document.body.appendChild(renderer.domElement);

  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(70, window.innerWidth / window.innerHeight, 0.01, 20);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x333333, 1.2));

  const group = new THREE.Group();
  group.position.set(0, 0, -1.2);
  scene.add(group);

  // 2D force-layout space is roughly 800x360 (views/wallets.ts's SVG
  // viewBox) -- this scale + offset maps that onto a ~1m cluster centered
  // in front of the user.
  const scale = 0.0015;
  const nodeMeshes = new Map<string, THREE.Mesh>();
  const nodeGeom = new THREE.SphereGeometry(0.02, 16, 16);
  const nodeMat = new THREE.MeshStandardMaterial({ color: 0x7addcc, emissive: 0x1a4a44 });
  for (const n of nodes) {
    const mesh = new THREE.Mesh(nodeGeom, nodeMat);
    mesh.position.set((n.x - 400) * scale, -(n.y - 180) * scale, 0);
    group.add(mesh);
    nodeMeshes.set(n.id, mesh);
  }

  const lineMat = new THREE.LineBasicMaterial({ color: 0x557766, transparent: true, opacity: 0.6 });
  for (const l of links) {
    const a = nodeMeshes.get(l.sourceId);
    const b = nodeMeshes.get(l.targetId);
    if (!a || !b) continue;
    const geom = new THREE.BufferGeometry().setFromPoints([a.position, b.position]);
    group.add(new THREE.Line(geom, lineMat));
  }

  let active = true;
  function cleanup() {
    if (!active) return;
    active = false;
    renderer.setAnimationLoop(null);
    renderer.dispose();
    renderer.domElement.remove();
    onExit();
  }

  let session: XRSession;
  try {
    session = await navigator.xr.requestSession("immersive-ar", { optionalFeatures: ["local-floor"] });
  } catch (err) {
    cleanup();
    throw err;
  }
  session.addEventListener("end", cleanup);

  await renderer.xr.setSession(session);
  renderer.setAnimationLoop(() => {
    group.rotation.y += 0.002;
    renderer.render(scene, camera);
  });
}
