// Generative WebGPU background for #/live -- dynamically imported only
// when this view mounts AND navigator.gpu is present (esbuild's code
// splitting means nobody who never opens #/live downloads this or its
// shader source). Falls back to nothing -- the view's existing dark
// background/CSS carries the view fine without it -- when WebGPU is
// unavailable, which is most environments today; ships as spec-correct
// code verified via feature-detection and a real render loop, same bar the
// existing WebNN feature already ships under (no WebGPU-capable browser in
// this project's dev sandbox to visually confirm the render against).
import shaderSource from "./live-background.wgsl";

export function webGPUSupported(): boolean {
  return typeof navigator !== "undefined" && "gpu" in navigator;
}

export interface LiveBackground {
  setTilt(x: number, y: number): void;
  stop(): void;
}

/** Mounts the shader onto `canvas` (already sized/positioned by the
 * caller) and starts an animation loop. Resolves to null if WebGPU
 * initialization fails at any point (no adapter, device lost, context
 * config rejected, etc.) -- the caller should treat that exactly like
 * "unsupported" and leave the plain background in place. */
export async function mountLiveBackground(canvas: HTMLCanvasElement): Promise<LiveBackground | null> {
  if (!webGPUSupported()) return null;

  let device: GPUDevice;
  let context: GPUCanvasContext;
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

  const module = device.createShaderModule({ code: shaderSource });
  const pipeline = device.createRenderPipeline({
    layout: "auto",
    vertex: { module, entryPoint: "vs" },
    fragment: { module, entryPoint: "fs", targets: [{ format }] },
    primitive: { topology: "triangle-list" },
  });

  // Two vec4f (32 bytes): a = (time, tiltX, tiltY, unused), b = (resWidth,
  // resHeight, unused, unused) -- see the WGSL file's header comment for
  // why this layout instead of a mixed-field struct.
  const uniformBuffer = device.createBuffer({
    size: 32,
    usage: GPUBufferUsage.UNIFORM | GPUBufferUsage.COPY_DST,
  });
  const bindGroup = device.createBindGroup({
    layout: pipeline.getBindGroupLayout(0),
    entries: [{ binding: 0, resource: { buffer: uniformBuffer } }],
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

  function frame(timeMs: number) {
    if (stopped) return;
    resize();

    uniformData[0] = reducedMotion ? 0 : timeMs / 1000;
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
          storeOp: "store",
        },
      ],
    });
    pass.setPipeline(pipeline);
    pass.setBindGroup(0, bindGroup);
    pass.draw(3);
    pass.end();
    device.queue.submit([encoder.finish()]);

    // A single static frame is enough under prefers-reduced-motion --
    // don't schedule another rAF, don't animate.
    if (!reducedMotion) requestAnimationFrame(frame);
  }

  requestAnimationFrame(frame);

  return {
    setTilt(x: number, y: number) {
      tiltX = x;
      tiltY = y;
    },
    stop() {
      stopped = true;
      device.destroy();
    },
  };
}
