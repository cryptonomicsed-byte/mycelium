// Generative background for #/live -- a slow, flowing field of branching
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
