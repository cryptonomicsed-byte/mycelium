// Builds src/main.ts -> dist/main.js. Run `npm run build` from this
// directory whenever src/ changes; dist/ is committed to the repo since the
// Termux box this ships to never runs a Node build step -- only the Go
// gateway binary, which serves dist/ as static files.
import * as esbuild from "esbuild";

await esbuild.build({
  entryPoints: ["src/main.ts"],
  bundle: true,
  outfile: "dist/main.js",
  format: "esm",
  target: "es2022",
  minify: process.argv.includes("--minify"),
  sourcemap: true,
  logLevel: "info",
});
