// src/shaders/live-background.wgsl
var live_background_default = `// Generative background for #/live -- a slow, flowing field of branching
// threads evoking mycelial growth / pheromone trails, reinforcing the
// "stigmergic substrate" metaphor the trace rows already carry (colored by
// outcome, fading with age). Value-noise fbm, domain-warped over time,
// ridge-sharpened into thin glowing lines rather than a soft cloud.
//
// Uniform layout: two vec4f (32 bytes total, 16-byte aligned) rather than a
// mixed-field struct, specifically to sidestep std140-style alignment
// bugs -- a: (time, tiltX, tiltY, unused), b: (resWidth, resHeight,
// unused, unused). tiltX/tiltY come from the gyroscope-driven parallax
// (views/live.ts's DeviceOrientationEvent handler), zero on desktop.
struct Uniforms {
  a: vec4f,
  b: vec4f,
};

@group(0) @binding(0) var<uniform> u: Uniforms;

fn hash(p: vec2f) -> f32 {
  var p3 = fract(vec3f(p.x, p.y, p.x) * 0.1031);
  p3 = p3 + dot(p3, p3.yzx + vec3f(33.33));
  return fract((p3.x + p3.y) * p3.z);
}

fn valueNoise(p: vec2f) -> f32 {
  let i = floor(p);
  let f = fract(p);
  let a = hash(i);
  let b = hash(i + vec2f(1.0, 0.0));
  let c = hash(i + vec2f(0.0, 1.0));
  let d = hash(i + vec2f(1.0, 1.0));
  let s = f * f * (vec2f(3.0) - vec2f(2.0) * f);
  return mix(mix(a, b, s.x), mix(c, d, s.x), s.y);
}

fn fbm(p0: vec2f) -> f32 {
  var value = 0.0;
  var amplitude = 0.5;
  var freq = 1.0;
  var p = p0;
  for (var i: i32 = 0; i < 5; i = i + 1) {
    value = value + amplitude * valueNoise(p * freq);
    freq = freq * 2.0;
    amplitude = amplitude * 0.5;
  }
  return value;
}

@vertex
fn vs(@builtin(vertex_index) idx: u32) -> @builtin(position) vec4f {
  // Full-screen triangle (covers the viewport, clipped) -- avoids a vertex
  // buffer for a single static quad.
  var pos = array<vec2f, 3>(
    vec2f(-1.0, -1.0),
    vec2f(3.0, -1.0),
    vec2f(-1.0, 3.0),
  );
  return vec4f(pos[idx], 0.0, 1.0);
}

@fragment
fn fs(@builtin(position) fragCoord: vec4f) -> @location(0) vec4f {
  let time = u.a.x;
  let tilt = u.a.yz;
  let resolution = u.b.xy;

  let aspect = resolution.x / resolution.y;
  let uv = (fragCoord.xy / resolution) - vec2f(0.5);
  var p = vec2f(uv.x * aspect, uv.y) + tilt * 0.15;

  let t = time * 0.05;
  let warp = fbm(p * 2.0 + vec2f(t, -t));
  let threads = fbm(p * 3.0 + warp * 1.5 + vec2f(-t * 0.7, t * 0.3));

  // Ridge-sharpen the fbm field into thin glowing lines rather than a soft
  // cloud -- ridge noise via 1 - |2*frac(x)-1|, then a steep power curve.
  let ridge = 1.0 - abs(2.0 * fract(threads * 6.0) - 1.0);
  let glow = pow(ridge, 6.0);

  let bg = vec3f(0.055, 0.055, 0.055);      // matches --bg: #0e0e0e
  let colorOk = vec3f(0.486, 0.784, 0.333); // --ok: #7c5
  let colorAccent = vec3f(0.478, 0.667, 0.867); // --accent: #7ad
  let threadColor = mix(colorOk, colorAccent, warp);

  let out = bg + threadColor * glow * 0.35;
  return vec4f(out, 1.0);
}
`;

// src/shaders/live-background.ts
function webGPUSupported() {
  return typeof navigator !== "undefined" && "gpu" in navigator;
}
async function mountLiveBackground(canvas) {
  if (!webGPUSupported()) return null;
  let device;
  let context;
  try {
    const adapter = await navigator.gpu.requestAdapter();
    if (!adapter) return null;
    device = await adapter.requestDevice();
    const ctx = canvas.getContext("webgpu");
    if (!ctx) return null;
    context = ctx;
  } catch {
    return null;
  }
  const format = navigator.gpu.getPreferredCanvasFormat();
  context.configure({ device, format, alphaMode: "opaque" });
  const module = device.createShaderModule({ code: live_background_default });
  const pipeline = device.createRenderPipeline({
    layout: "auto",
    vertex: { module, entryPoint: "vs" },
    fragment: { module, entryPoint: "fs", targets: [{ format }] },
    primitive: { topology: "triangle-list" }
  });
  const uniformBuffer = device.createBuffer({
    size: 32,
    usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST
  });
  const bindGroup = device.createBindGroup({
    layout: pipeline.getBindGroupLayout(0),
    entries: [{ binding: 0, resource: { buffer: uniformBuffer } }]
  });
  const uniformData = new Float32Array(8);
  let tiltX = 0;
  let tiltY = 0;
  let stopped = false;
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  function resize() {
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const w = Math.max(1, Math.floor(canvas.clientWidth * dpr));
    const h = Math.max(1, Math.floor(canvas.clientHeight * dpr));
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
  }
  function frame(timeMs) {
    if (stopped) return;
    resize();
    uniformData[0] = reducedMotion ? 0 : timeMs / 1e3;
    uniformData[1] = tiltX;
    uniformData[2] = tiltY;
    uniformData[3] = 0;
    uniformData[4] = canvas.width;
    uniformData[5] = canvas.height;
    uniformData[6] = 0;
    uniformData[7] = 0;
    device.queue.writeBuffer(uniformBuffer, 0, uniformData);
    const encoder = device.createCommandEncoder();
    const pass = encoder.beginRenderPass({
      colorAttachments: [
        {
          view: context.getCurrentTexture().createView(),
          clearValue: { r: 0, g: 0, b: 0, a: 1 },
          loadOp: "clear",
          storeOp: "store"
        }
      ]
    });
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, bindGroup);
    pass.draw(3);
    pass.end();
    device.queue.submit([encoder.finish()]);
    if (!reducedMotion) requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
  return {
    setTilt(x, y) {
      tiltX = x;
      tiltY = y;
    },
    stop() {
      stopped = true;
      device.destroy();
    }
  };
}

export {
  webGPUSupported,
  mountLiveBackground
};
//# sourceMappingURL=chunk-EF7SS7UC.js.map
