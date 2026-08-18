// Builds src/main.ts -> dist/. Run `npm run build` from this directory
// whenever src/ changes; dist/ is committed to the repo since the Termux
// box this ships to never runs a Node build step -- only the Go gateway
// binary, which serves dist/ as static files.
//
// outdir + splitting (not a single outfile) so views that dynamically
// import() a heavy, occasionally-used dependency -- Three.js for #/wallets'
// AR mode, a WebGPU shader module for #/live -- ship as separate chunks
// nobody downloads until they actually open that view. A single bundle
// would put every byte of Three.js in front of someone who only ever opens
// #/findings.
import * as esbuild from "esbuild";

await esbuild.build({
  entryPoints: ["src/main.ts"],
  bundle: true,
  outdir: "dist",
  format: "esm",
  splitting: true,
  target: "es2022",
  minify: process.argv.includes("--minify"),
  sourcemap: true,
  logLevel: "info",
  loader: { ".wgsl": "text" }, // shader source imported as a plain string
});
