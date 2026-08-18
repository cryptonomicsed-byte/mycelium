// esbuild's `.wgsl -> text` loader (esbuild.config.mjs) turns a WGSL
// import into its raw source string at bundle time.
declare module "*.wgsl" {
  const source: string;
  export default source;
}
