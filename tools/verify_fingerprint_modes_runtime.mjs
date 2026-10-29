#!/usr/bin/env node
// Bounded, local candidate acceptance. No Playwright or automatic emulation.
import fs from "node:fs";
import fsp from "node:fs/promises";
import path from "node:path";
import http from "node:http";
import crypto from "node:crypto";
import zlib from "node:zlib";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { audioAcceptanceProbe, validateAudioAcceptance } from "./fingerprint_audio_acceptance.mjs";
import { fontAcceptanceProbe, validateFontAcceptance, FONT_ACCEPTANCE_ORACLE } from "./fingerprint_font_acceptance.mjs";
import { referenceAcceptanceProbe, validateReferenceAcceptance } from "./fingerprint_reference_acceptance.mjs";
import { renderbufferAcceptanceProbe, validateRenderbufferAcceptance } from "./fingerprint_renderbuffer_acceptance.mjs";
import { pixelSemanticProbe, validatePixelSemantics, validateHeadlessScreens, pngDimensions, PIXEL_SCENARIOS, HEADLESS_SCENARIOS } from "./pixel_semantic_acceptance.mjs";

const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function deadline(promise, ms, message) {
  let timer;
  try { return await Promise.race([promise, new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), ms);
  })]); } finally { clearTimeout(timer); }
}
async function sha256(filename) {
  const hash = crypto.createHash("sha256");
  for await (const chunk of fs.createReadStream(filename)) hash.update(chunk);
  return hash.digest("hex");
}
const hashBytes = bytes => crypto.createHash("sha256").update(bytes).digest("hex");
const errorText = error => String(error?.message || error).slice(0, 1500);
const equal = (a, b) => Buffer.isBuffer(a) && Buffer.isBuffer(b) && a.length > 0 && a.equals(b);
const bytes = text => typeof text === "string" && text.length ? Buffer.from(text, "base64") : null;

export function decodePNG(input) {
  if (!Buffer.isBuffer(input) || input.length > 2 * 1024 * 1024 ||
      !input.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])))
    throw new Error("Invalid or oversized owned PNG");
  let width, height, channels, end = false;
  const blocks = [];
  for (let at = 8; at + 12 <= input.length;) {
    const length = input.readUInt32BE(at), type = input.toString("ascii", at + 4, at + 8);
    if (length > input.length - at - 12) throw new Error("Truncated PNG chunk");
    const data = input.subarray(at + 8, at + 8 + length);
    if (type === "IHDR") {
      if (width || length !== 13) throw new Error("Invalid PNG IHDR");
      width = data.readUInt32BE(0); height = data.readUInt32BE(4);
      if (!width || !height || width > 256 || height > 256 ||
          data[8] !== 8 || ![2, 6].includes(data[9]) || data[10] || data[11] || data[12])
        throw new Error("Probe requires noninterlaced 8-bit RGB/RGBA PNG");
      channels = data[9] === 6 ? 4 : 3;
    } else if (type === "IDAT") blocks.push(data);
    else if (type === "IEND") { end = true; break; }
    at += length + 12;
  }
  if (!width || !end || !blocks.length) throw new Error("Incomplete PNG");
  const stride = width * channels, expected = (stride + 1) * height;
  const packed = zlib.inflateSync(Buffer.concat(blocks), { maxOutputLength: expected });
  if (packed.length !== expected) throw new Error("Unexpected PNG inflated length");
  const raw = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y++) {
    const filter = packed[y * (stride + 1)];
    if (filter > 4) throw new Error("Unknown PNG filter");
    for (let x = 0; x < stride; x++) {
      const left = x >= channels ? raw[y * stride + x - channels] : 0;
      const up = y ? raw[(y - 1) * stride + x] : 0;
      const diagonal = y && x >= channels ? raw[(y - 1) * stride + x - channels] : 0;
      const p = left + up - diagonal, pa = Math.abs(p - left), pb = Math.abs(p - up), pc = Math.abs(p - diagonal);
      const predict = [0, left, up, Math.floor((left + up) / 2),
        pa <= pb && pa <= pc ? left : pb <= pc ? up : diagonal][filter];
      raw[y * stride + x] = packed[y * (stride + 1) + x + 1] + predict;
    }
  }
  const rgba = Buffer.alloc(width * height * 4);
  for (let p = 0; p < width * height; p++) {
    raw.copy(rgba, p * 4, p * channels, p * channels + 3);
    rgba[p * 4 + 3] = channels === 4 ? raw[p * 4 + 3] : 255;
  }
  return { width, height, rgba };
}
function cropped(raw, width, x, y, w, h) {
  const output = Buffer.alloc(w * h * 4);
  for (let row = 0; row < h; row++)
    raw.copy(output, row * w * 4, ((y + row) * width + x) * 4, ((y + row) * width + x + w) * 4);
  return output;
}
function flipped(raw, width, height) {
  const output = Buffer.alloc(raw.length);
  for (let y = 0; y < height; y++) raw.copy(output, y * width * 4, (height - 1 - y) * width * 4, (height - y) * width * 4);
  return output;
}
function difference(a, b) {
  if (!a || !b || !a.length || a.length !== b.length) return null;
  let changed = 0, alphaChanges = 0, maxDelta = 0;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) {
    changed++; if (i % 4 === 3) alphaChanges++;
    maxDelta = Math.max(maxDelta, Math.abs(a[i] - b[i]));
  }
  return { changed, alphaChanges, maxDelta, bytes: a.length };
}

// Executed only in the controller's loopback page. All media uses fake devices.
async function pageProbe(input) {
  const result = { level: input.basicOnly ? "basic" : "full", secureContext: isSecureContext, origin: location.origin, errors: [] };
  const safe = async (name, task) => {
    try { return await task(); } catch (error) {
      const info = { name, error: String(error?.message || error) };
      result.errors.push(info); return { error: info.error };
    }
  };
  const b64 = array => {
    const data = new Uint8Array(array.buffer ?? array, array.byteOffset ?? 0, array.byteLength ?? array.length);
    let text = ""; for (let i = 0; i < data.length; i += 4096) text += String.fromCharCode(...data.subarray(i, i + 4096));
    return btoa(text);
  };
  const timed = (promise, ms) => new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error("Probe deadline")), ms);
    promise.then(value => { clearTimeout(timer); resolve(value); }, error => { clearTimeout(timer); reject(error); });
  });
  result.navigator = { cpu: navigator.hardwareConcurrency, memory: navigator.deviceMemory,
    platform: navigator.platform, ua: navigator.userAgent,
    screen: { width: screen.width, height: screen.height, availWidth: screen.availWidth,
      availHeight: screen.availHeight, dpr: devicePixelRatio } };
  result.worker = await safe("worker", () => timed(new Promise((resolve, reject) => {
    const worker = new Worker(input.workerURL);
    worker.onmessage = event => { worker.terminate(); resolve(event.data); };
    worker.onerror = () => { worker.terminate(); reject(new Error("Owned worker failed")); };
  }), 5000));
  result.headers = await safe("client_hints", async () => {
    await fetch(input.echoURL + "?prime=1", { cache: "no-store" });
    return (await fetch(input.echoURL + "?measure=1", { cache: "no-store" })).json();
  });
  if (input.basicOnly) return result;
  result.canvas = await safe("canvas", async () => {
    const canvas = document.createElement("canvas"); canvas.width = canvas.height = 64;
    const ctx = canvas.getContext("2d", { willReadFrequently: true });
    const image = ctx.createImageData(64, 64);
    for (let p = 0; p < 4096; p++) image.data.set([(p * 17 + 31) & 255, (p * 37 + 66) & 255, (p * 13 + 123) & 255, 255], p * 4);
    const expected = b64(image.data); ctx.putImageData(image, 0, 0);
    const full = b64(ctx.getImageData(0, 0, 64, 64).data), repeat = b64(ctx.getImageData(0, 0, 64, 64).data);
    const crop = b64(ctx.getImageData(11, 13, 18, 16).data);
    const png = canvas.toDataURL("image/png").split(",")[1];
    const blob = await timed(new Promise(resolve => canvas.toBlob(resolve, "image/png")), 5000);
    if (!blob) throw new Error("Canvas toBlob returned null");
    const offscreen = new OffscreenCanvas(64, 64), offctx = offscreen.getContext("2d");
    offctx.putImageData(image, 0, 0);
    const offRaw = b64(offctx.getImageData(0, 0, 64, 64).data);
    const offBlob = await offscreen.convertToBlob({ type: "image/png" });
    const transparent = document.createElement("canvas"); transparent.width = transparent.height = 16;
    const transctx = transparent.getContext("2d");
    const transparentRaw = b64(transctx.getImageData(0, 0, 16, 16).data);
    return { width: 64, height: 64, expected, full, repeat, crop, png,
      blob: b64(await blob.arrayBuffer()), offscreen: { raw: offRaw, png: b64(await offBlob.arrayBuffer()) },
      transparentRaw };
  });
  result.layout = await safe("layout", async () => {
    const element = document.createElement("div");
    element.style.cssText = "position:absolute;left:10px;top:20px;width:123.25px;height:45.5px;padding:0;border:0;transform:none";
    element.textContent = "kernel modes control 7Qm_20261020";
    document.body.append(element);
    const rect = value => ({ x: value.x, y: value.y, width: value.width, height: value.height,
      left: value.left, top: value.top, right: value.right, bottom: value.bottom });
    const first = rect(element.getBoundingClientRect()), repeat = rect(element.getBoundingClientRect());
    const client = rect(element.getClientRects()[0]);
    const canvas = document.createElement("canvas"), context = canvas.getContext("2d");
    context.font = "16px Arial";
    const text = context.measureText(element.textContent).width, textRepeat = context.measureText(element.textContent).width;
    element.remove(); return { first, repeat, client, text, textRepeat };
  });
  result.audio = await safe("audio", async () => {
    const samples = Float32Array.from({ length: 256 }, (_, i) => 0.2 + i * 0.001);
    const buffer = new AudioBuffer({ length: samples.length, numberOfChannels: 1, sampleRate: 48000 });
    const expected = b64(samples); buffer.copyToChannel(samples, 0);
    const full = b64(buffer.getChannelData(0)), repeat = b64(buffer.getChannelData(0));
    const copied = new Float32Array(256); buffer.copyFromChannel(copied, 0);
    const overlap = new AudioBuffer({ length: 8, numberOfChannels: 1, sampleRate: 44100 });
    const backing = overlap.getChannelData(0);
    backing.set([0.11, 0.22, 0.33, 0.44, 0.55, 0.66, 0.77, 0.88]);
    const overlapBefore = b64(backing);
    overlap.copyFromChannel(backing, 0, 4);
    const overlapAfter = b64(backing);
    const context = new OfflineAudioContext(1, 256, 48000), analyser = context.createAnalyser();
    analyser.fftSize = 32;
    const tails = {};
    for (const [method, Type, count, sentinel] of [
      ["getFloatFrequencyData", Float32Array, analyser.frequencyBinCount, 42.25],
      ["getByteFrequencyData", Uint8Array, analyser.frequencyBinCount, 173],
      ["getFloatTimeDomainData", Float32Array, analyser.fftSize, 3.25],
      ["getByteTimeDomainData", Uint8Array, analyser.fftSize, 173],
    ]) {
      const target = new Type(count + 7); target.fill(sentinel);
      analyser[method](target);
      tails[method] = { writtenBound: count, tail: [...target.slice(count)],
        unchanged: [...target.slice(count)].every(value => Object.is(value, sentinel)) };
    }
    return { expected, full, repeat, copied: b64(copied), sampleRate: buffer.sampleRate,
      overlapBefore, overlapAfter, analyserTails: tails };
  });
  result.sensor = input.sensorOverride ? await safe("sensor", () => timed(new Promise((resolve, reject) => {
    if (typeof Accelerometer !== "function") { resolve({ supported: false, reason: "Accelerometer unavailable" }); return; }
    const sensor = new Accelerometer({ frequency: 10 });
    const timer = setTimeout(() => { sensor.stop(); reject(new Error("Synthetic accelerometer did not report a reading")); }, 3500);
    sensor.onreading = () => {
      const reading = { supported: true, x: sensor.x, y: sensor.y, z: sensor.z };
      clearTimeout(timer); sensor.stop(); resolve(reading);
    };
    sensor.onerror = event => { clearTimeout(timer); sensor.stop(); reject(event.error); };
    sensor.start();
  }), 4000)) : { supported: false, reason: "CDP sensor override unavailable" };
  result.media = await safe("synthetic_media", async () => {
    if (!input.fakeFlagsConfirmed || !isSecureContext || location.hostname !== "127.0.0.1")
      throw new Error("Synthetic device authorization was not confirmed");
    if (!navigator.mediaDevices?.getUserMedia) return { supported: false, reason: "mediaDevices unavailable" };
    const devices = async () => (await navigator.mediaDevices.enumerateDevices()).map(d =>
      d.kind === "audiooutput" ? { kind: d.kind, metadata: "output-device identity not collected" } :
        { kind: d.kind, label: d.label, deviceId: d.deviceId, groupId: d.groupId });
    const before = await devices();
    let stream, expired = false;
    try {
      stream = await timed(navigator.mediaDevices.getUserMedia({
        audio: true, video: { width: { ideal: 64 }, height: { ideal: 64 }, frameRate: { ideal: 1 } },
      }).then(value => { if (expired) value.getTracks().forEach(t => t.stop()); return value; }), 8000);
      const tracks = stream.getTracks().map(t => ({ kind: t.kind, label: t.label,
        settings: t.getSettings(), readyState: t.readyState }));
      const after = await devices(), repeat = await devices();
      return { supported: true, before, after, repeat, tracks, syntheticOnly: true };
    } finally { expired = true; stream?.getTracks().forEach(t => t.stop()); }
  });
  result.webgl = {};
  for (const kind of ["webgl", "webgl2"]) result.webgl[kind] = await safe(kind, async () => {
    const canvas = document.createElement("canvas"); canvas.width = canvas.height = 64;
    const gl = canvas.getContext(kind, { alpha: false, antialias: false, preserveDrawingBuffer: true });
    if (!gl) return { supported: false, reason: "Context creation unavailable" };
    const limits = { texture: gl.getParameter(gl.MAX_TEXTURE_SIZE), cube: gl.getParameter(gl.MAX_CUBE_MAP_TEXTURE_SIZE),
      renderbuffer: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE) };
    if (kind === "webgl2") Object.assign(limits, { texture3d: gl.getParameter(gl.MAX_3D_TEXTURE_SIZE),
      arrayLayers: gl.getParameter(gl.MAX_ARRAY_TEXTURE_LAYERS) });
    const extensionNames = gl.getSupportedExtensions() ?? [];
    const debug = gl.getExtension("WEBGL_debug_renderer_info");
    const identity = debug ? { vendor: gl.getParameter(debug.UNMASKED_VENDOR_WEBGL),
      renderer: gl.getParameter(debug.UNMASKED_RENDERER_WEBGL) } : null;
    const maskedIdentity = { vendor: gl.getParameter(gl.VENDOR), renderer: gl.getParameter(gl.RENDERER) };
    // Positive evidence uses rendered variation. Uniform clear is separately
    // required to remain exact by pixelSemanticProbe.
    const shader = (type, source) => {
      const value = gl.createShader(type); gl.shaderSource(value, source); gl.compileShader(value);
      if (!gl.getShaderParameter(value, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(value));
      return value;
    };
    const vertexShader = shader(gl.VERTEX_SHADER, kind === "webgl2" ?
      "#version 300 es\nin vec2 p;void main(){gl_Position=vec4(p,0.,1.);}" :
      "attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}");
    const fragmentBody = "vec2 p=gl_FragCoord.xy;vec4 c=vec4(fract(p.x*.017+p.y*.011),fract(p.x*.007+p.y*.023),fract((p.x+p.y)*.013),1.);";
    const fragmentShader = shader(gl.FRAGMENT_SHADER, kind === "webgl2" ?
      "#version 300 es\nprecision highp float;out vec4 color;void main(){" + fragmentBody + "color=c;}" :
      "precision highp float;void main(){" + fragmentBody + "gl_FragColor=c;}");
    const program = gl.createProgram(); gl.attachShader(program, vertexShader); gl.attachShader(program, fragmentShader); gl.linkProgram(program);
    if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error(gl.getProgramInfoLog(program));
    gl.useProgram(program); const vertexBuffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, vertexBuffer);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,3,-1,-1,3]), gl.STATIC_DRAW);
    const position = gl.getAttribLocation(program, "p"); gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position,2,gl.FLOAT,false,0,0);
    gl.disable(gl.DITHER); gl.viewport(0,0,64,64); gl.drawArrays(gl.TRIANGLES,0,3);
    const full = new Uint8Array(64 * 64 * 4), repeat = new Uint8Array(full.length), crop = new Uint8Array(18 * 16 * 4);
    gl.readPixels(0, 0, 64, 64, gl.RGBA, gl.UNSIGNED_BYTE, full);
    gl.readPixels(0, 0, 64, 64, gl.RGBA, gl.UNSIGNED_BYTE, repeat);
    gl.readPixels(11, 13, 18, 16, gl.RGBA, gl.UNSIGNED_BYTE, crop);
    const readError = gl.getError();
    const png = canvas.toDataURL("image/png").split(",")[1];
    const negative = {};
    if (input.gpuCustom) {
      const texture = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D, texture);
      // Fixed small allocations remain bounded even on an old binary that
      // ignores the new 64/16/4 custom constraints.
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, 65, 1, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      negative.texture = gl.getError();
      const cube = gl.createTexture(); gl.bindTexture(gl.TEXTURE_CUBE_MAP, cube);
      gl.texImage2D(gl.TEXTURE_CUBE_MAP_POSITIVE_X, 0, gl.RGBA, 65, 65, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
      negative.cube = gl.getError();
      const renderbuffer = gl.createRenderbuffer(); gl.bindRenderbuffer(gl.RENDERBUFFER, renderbuffer);
      gl.renderbufferStorage(gl.RENDERBUFFER, gl.RGBA4, 65, 1);
      negative.renderbuffer = gl.getError();
      if (kind === "webgl2") {
        const volume = gl.createTexture(); gl.bindTexture(gl.TEXTURE_3D, volume);
        gl.texImage3D(gl.TEXTURE_3D, 0, gl.RGBA8, 1, 1, 17, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        negative.texture3d = gl.getError();
        const layers = gl.createTexture(); gl.bindTexture(gl.TEXTURE_2D_ARRAY, layers);
        gl.texImage3D(gl.TEXTURE_2D_ARRAY, 0, gl.RGBA8, 1, 1, 5, 0, gl.RGBA, gl.UNSIGNED_BYTE, null);
        negative.arrayLayers = gl.getError(); gl.deleteTexture(volume); gl.deleteTexture(layers);
      }
      gl.deleteTexture(texture); gl.deleteTexture(cube); gl.deleteRenderbuffer(renderbuffer);
    }
    if (kind === "webgl") { gl.getParameter(0x8073); negative.webgl2Enum = gl.getError(); }
    gl.deleteBuffer(vertexBuffer); gl.deleteProgram(program); gl.deleteShader(vertexShader); gl.deleteShader(fragmentShader);
    gl.getExtension("WEBGL_lose_context")?.loseContext();
    return { supported: true, identity, maskedIdentity, limits, extensionNames, readError, full: b64(full), repeat: b64(repeat),
      crop: b64(crop), png, negative, dimensions: [64, 64] };
  });
  result.webgpu = await safe("webgpu", async () => {
    if (!navigator.gpu) return { supported: false, reason: "navigator.gpu unavailable" };
    const options = { powerPreference: "low-power" }, adapter = await timed(navigator.gpu.requestAdapter(options), 6000);
    if (!adapter) return { supported: false, reason: "No adapter" };
    const info = adapter.info, metadata = Object.fromEntries(["vendor", "architecture", "device", "description",
      "subgroupMinSize", "subgroupMaxSize", "isFallbackAdapter"].map(k => [k, info?.[k] ?? null]));
    const limits = {};
    for (const key of Object.getOwnPropertyNames(Object.getPrototypeOf(adapter.limits)))
      if (key !== "constructor" && typeof adapter.limits[key] === "number") limits[key] = adapter.limits[key];
    const features = [...adapter.features].sort(), errors = [];
    const device = await timed(adapter.requestDevice(), 8000);
    device.addEventListener("uncapturederror", event => errors.push(String(event.error?.message || event.error)));
    const resources = [];
    let compute;
    try {
      const module = device.createShaderModule({ code: "@group(0) @binding(0) var<storage,read_write> data: array<u32>; @compute @workgroup_size(1) fn main(@builtin(global_invocation_id) gid: vec3u) { if (gid.x < 4u) { data[gid.x] = gid.x * 7u + 3u; } }" });
      const messages = await module.getCompilationInfo();
      if (messages.messages.some(m => m.type === "error")) throw new Error("Small compute shader compilation failed");
      const pipeline = await timed(device.createComputePipelineAsync({ layout: "auto", compute: { module, entryPoint: "main" } }), 8000);
      const output = device.createBuffer({ size: 16, usage: GPUBufferUsage.STORAGE | GPUBufferUsage.COPY_SRC });
      const readback = device.createBuffer({ size: 16, usage: GPUBufferUsage.COPY_DST | GPUBufferUsage.MAP_READ }); resources.push(output, readback);
      const group = device.createBindGroup({ layout: pipeline.getBindGroupLayout(0), entries: [{ binding: 0, resource: { buffer: output } }] });
      const encoder = device.createCommandEncoder(), pass = encoder.beginComputePass();
      pass.setPipeline(pipeline); pass.setBindGroup(0, group); pass.dispatchWorkgroups(4); pass.end();
      encoder.copyBufferToBuffer(output, 0, readback, 0, 16); device.queue.submit([encoder.finish()]);
      await timed(readback.mapAsync(GPUMapMode.READ), 8000); compute = [...new Uint32Array(readback.getMappedRange())];
      readback.unmap();
    } finally { resources.forEach(r => r.destroy()); device.destroy(); }
    const request = async descriptor => {
      const next = await timed(navigator.gpu.requestAdapter(options), 6000);
      if (!next) return { unavailable: true };
      let selected;
      try { selected = await timed(next.requestDevice(descriptor), 8000);
        return { accepted: true, actualFeatures: [...selected.features].sort(),
          actualLimit: selected.limits.maxComputeWorkgroupSizeX };
      } catch (error) { return { accepted: false, error: String(error.message) }; }
      finally { selected?.destroy(); }
    };
    const optional = features.find(f => f !== "core-features-and-limits");
    const absent = ["shader-f16", "subgroups", "texture-compression-astc", "timestamp-query"].find(f => !features.includes(f));
    return { supported: true, info: metadata, features, limits, compute, errors,
      supportedFeature: optional ? { name: optional, ...await request({ requiredFeatures: [optional] }) } : null,
      absentFeature: absent ? { name: absent, ...await request({ requiredFeatures: [absent] }) } : null,
      overLimit: await request({ requiredLimits: { maxComputeWorkgroupSizeX: limits.maxComputeWorkgroupSizeX + 1 } }) };
  });
  return result;
}

async function connectCDP(endpoint, trace) {
  const socket = new WebSocket(endpoint), pending = new Map(); let next = 1;
  await deadline(new Promise((resolve, reject) => {
    socket.addEventListener("open", resolve, { once: true });
    socket.addEventListener("error", () => reject(new Error("Owned CDP connection failed")), { once: true });
  }), 10000, "CDP connection deadline");
  socket.addEventListener("message", event => {
    const packet = JSON.parse(String(event.data));
    if (pending.has(packet.id)) {
      const task = pending.get(packet.id); pending.delete(packet.id);
      packet.error ? task.reject(new Error(packet.error.message)) : task.resolve(packet.result);
    }
  });
  socket.addEventListener("close", () => { for (const task of pending.values()) task.reject(new Error("CDP closed")); pending.clear(); });
  return {
    async send(method, params = {}, sessionId) {
      const id = next++; trace.push({ method, parameterKeys: Object.keys(params), attached: !!sessionId });
      try { return await deadline(new Promise((resolve, reject) => {
        pending.set(id, { resolve, reject }); socket.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
      }), method === "Runtime.evaluate" ? 75000 : 10000, `CDP deadline: ${method}`);
      } finally { pending.delete(id); }
    },
    close() { socket.close(); },
  };
}
async function evaluate(cdp, session, expression, userGesture = false) {
  const reply = await cdp.send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true, userGesture }, session);
  if (reply.exceptionDetails) throw new Error(reply.exceptionDetails.text || "Owned page evaluation failed");
  return reply.result?.value;
}
async function collectPlatformFonts(cdp, session, probe) {
  probe.cssPlatformFonts = {};
  if (!probe.cssNodes?.length) return;
  try {
    await cdp.send("DOM.enable", {}, session);
    await cdp.send("CSS.enable", {}, session);
    const { root } = await cdp.send("DOM.getDocument", { depth: 1 }, session);
    for (const row of probe.cssNodes) {
      if (!/^[a-z0-9_-]+$/i.test(row.nodeId)) throw new Error("Unexpected owned font node ID");
      try {
        const { nodeId } = await cdp.send("DOM.querySelector", { nodeId: root.nodeId, selector: `#${row.nodeId}` }, session);
        if (!nodeId) throw new Error("Owned font node missing");
        probe.cssPlatformFonts[row.nodeId] = await cdp.send("CSS.getPlatformFontsForNode", { nodeId }, session);
      } catch (error) { probe.cssPlatformFonts[row.nodeId] = { error: errorText(error) }; }
    }
  } catch (error) {
    for (const row of probe.cssNodes) probe.cssPlatformFonts[row.nodeId] ||= { error: errorText(error) };
  }
}
export function browserEnvironment(source) {
  return Object.fromEntries(Object.entries(source).filter(([key]) =>
    !/^(GH_|GITHUB_|GOOGLE_|CLOAK|OPENAI_|ANTHROPIC_|CODEX_|AWS_|AZURE_)/i.test(key) &&
    !/(TOKEN|SECRET|PASSWORD|API_KEY|CREDENTIAL)/i.test(key)));
}
export async function ownedWindowsProcesses(rootPID, known = null) {
  if (process.platform !== "win32") throw new Error("Windows process verification required");
  if (!Number.isSafeInteger(rootPID) || rootPID <= 0) throw new Error("Invalid owned root PID");
  const payload = Buffer.from(JSON.stringify({ rootPID, known }), "utf8").toString("base64");
  const script = `
$ErrorActionPreference='Stop'
$request=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('${payload}')) | ConvertFrom-Json
$all=@(Get-CimInstance Win32_Process -Property ProcessId,ParentProcessId,CreationDate | ForEach-Object {
  if ($null -ne $_.CreationDate) {
    [PSCustomObject]@{pid=[int]$_.ProcessId;parent=[int]$_.ParentProcessId;created=$_.CreationDate.ToUniversalTime().ToString('o')}
  }
})
$owned=@()
if ($null -ne $request.known) {
  foreach ($item in @($request.known)) {
    $owned+=@($all | Where-Object { $_.pid -eq $item.pid -and $_.created -ceq $item.created })
  }
} else {
  $owned=@($all | Where-Object { $_.pid -eq $request.rootPID })
  for ($i=0; $i -lt $owned.Count; $i++) {
    $parent=$owned[$i]
    $owned+=@($all | Where-Object {
      $_.parent -eq $parent.pid -and $_.pid -notin @($owned.pid) -and $_.created -cge $parent.created
    })
  }
}
ConvertTo-Json -InputObject @($owned) -Compress
`;
  const executable = path.join(process.env.SystemRoot || "C:\\Windows", "System32/WindowsPowerShell/v1.0/powershell.exe");
  const child = spawn(executable, ["-NoProfile", "-NonInteractive", "-EncodedCommand", Buffer.from(script, "utf16le").toString("base64")],
    { windowsHide: true, env: browserEnvironment(process.env), stdio: ["ignore", "pipe", "pipe"] });
  let output = "", error = "";
  child.stdout.on("data", data => { output += data; });
  child.stderr.on("data", data => { error += data; });
  try {
    const code = await deadline(new Promise((resolve, reject) => {
      child.once("error", reject); child.once("close", resolve);
    }), 10000, "Owned process inventory deadline");
    if (code !== 0) throw new Error("Owned process inventory unavailable: " + error.slice(-1000));
    const records = JSON.parse(output.replace(/^\uFEFF/, "").trim());
    if (!Array.isArray(records) || records.some(r => !Number.isSafeInteger(r.pid) || typeof r.created !== "string"))
      throw new Error("Invalid owned process inventory");
    return records;
  } finally { if (child.exitCode === null) child.kill(); } // Only the inventory helper itself.
}
async function stopOwned(child, cdp) {
  let closeSent = false, forced = false, before = [], remaining = [], treeError = null;
  if (child.pid && child.exitCode === null) {
    try { before = await ownedWindowsProcesses(child.pid); }
    catch (error) { treeError = errorText(error); }
  }
  if (cdp) { closeSent = true; await cdp.send("Browser.close").catch(() => {}); }
  for (let i = 0; i < 100 && child.exitCode === null; i++) await delay(100);
  if (child.exitCode === null && child.pid) {
    forced = true;
    // An error cleanup is limited to this exact spawn's PID tree. Never by name.
    const killer = spawn("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"],
      { windowsHide: true, stdio: "ignore", env: browserEnvironment(process.env) });
    await deadline(new Promise(resolve => { killer.once("exit", resolve); killer.once("error", resolve); }), 10000, "Owned cleanup deadline").catch(() => {});
    for (let i = 0; i < 30 && child.exitCode === null; i++) await delay(100);
  }
  if (before.length) {
    try {
      remaining = await ownedWindowsProcesses(child.pid, before);
      if (remaining.length) {
        await delay(500);
        remaining = await ownedWindowsProcesses(child.pid, before);
      }
      for (const ownedEntry of remaining) {
        forced = true;
        // These PIDs were descendants of this spawn and their creation time
        // still matches. A reused PID is never treated as an owned process.
        const fresh = await ownedWindowsProcesses(child.pid, [ownedEntry]);
        if (!fresh.length) continue;
        const killer = spawn("taskkill.exe", ["/PID", String(ownedEntry.pid), "/T", "/F"],
          { windowsHide: true, stdio: "ignore", env: browserEnvironment(process.env) });
        await deadline(new Promise(resolve => { killer.once("close", resolve); killer.once("error", resolve); }),
          10000, "Owned descendant cleanup deadline");
      }
      if (remaining.length) remaining = await ownedWindowsProcesses(child.pid, before);
    } catch (error) { treeError = errorText(error); }
  }
  cdp?.close();
  return { rootPID: child.pid, closeSent, forced, exitCode: child.exitCode,
    owned_tree_before_close: before, known_owned_processes_remaining: remaining, process_tree_error: treeError,
    normalExit: closeSent && !forced && child.exitCode === 0 && before.length > 0 && !treeError && remaining.length === 0 };
}

export function scenarios() {
  const surfaces = ["canvas", "webgl", "audio", "fonts", "rects", "text", "offset", "sensors", "media", "voices"];
  const make = (id, overrides = {}, extra = []) => {
    const flags = ["--fingerprint=12345"];
    for (const surface of surfaces) flags.push(`--fingerprint-${surface}-mode=${overrides[surface] || "native"}`);
    for (const field of ["identity", "cpu", "memory", "screen"]) if (overrides[field] !== "omit")
      flags.push(`--fingerprint-${field}-mode=${overrides[field] || "native"}`);
    for (const field of ["gpu", "webgl-capabilities", "webgpu-features", "webgpu-limits"])
      if (overrides[field] !== "omit") flags.push(`--fingerprint-${field}-mode=${overrides[field] || "native"}`);
    return { id, flags: [...flags, ...extra] };
  };
  const display = scale => ["--fingerprint-screen-width=640", "--fingerprint-screen-height=480", "--fingerprint-taskbar-height=40", `--uxr-device-pixel-ratio=${scale}`, "--window-size=640,480"];
  return [
    { id: "native_off_control", flags: ["--fingerprint=off"] },
    make("all_native"),
    { id: "legacy_seeded", flags: ["--fingerprint=12345", "--fingerprint-pixel-noise=seeded"] },
    make("canvas_shardx", { canvas: "shardx" }, ["--fingerprint-canvas-mode-seed=314159"]),
    make("webgl_cloak", { webgl: "cloak" }),
    make("shard_surfaces", { audio: "shardx", rects: "shardx", text: "shardx", sensors: "shardx", media: "shardx" },
      ["--fingerprint-audio-mode-seed=314159", "--fingerprint-rects-mode-seed=314159", "--fingerprint-text-mode-seed=314159",
        "--fingerprint-sensors-mode-seed=314159", "--fingerprint-media-audio-inputs=0", "--fingerprint-media-audio-outputs=0", "--fingerprint-media-video-inputs=0"]),
    make("memory_custom", { cpu: "custom", memory: "custom" }, ["--fingerprint-hardware-concurrency=4", "--fingerprint-device-memory=2"]),
    make("hardware_cloak", { canvas: "cloak", audio: "cloak", text: "cloak", cpu: "omit", memory: "omit", gpu: "omit", "webgpu-features": "cloak", "webgpu-limits": "cloak" },
      ["--fingerprint-hardware-profile=cloak", "--fingerprint-hardware-vendor=nvidia"]),
    make("gpu_custom", { "webgl-capabilities": "custom", "webgpu-features": "custom" },
      ["--uxr-webgl-max-texture-size=64", "--uxr-webgl-max-cube-map-texture-size=64", "--uxr-webgl-max-renderbuffer-size=64",
        "--uxr-webgl-max-3d-texture-size=16", "--uxr-webgl-max-array-texture-layers=4", "--uxr-webgpu-features=timestamp-query"]),
    make("webgl_shardx", { webgl: "shardx" }, ["--fingerprint-webgl-mode-seed=314159"]),
    make("fonts_shard42", { fonts: "shardx" }, ["--fingerprint-fonts-mode-seed=42"]),
    make("fonts_shard43", { fonts: "shardx" }, ["--fingerprint-fonts-mode-seed=43"]),
    make("fonts_cloak10026", { fonts: "cloak" }, ["--fingerprint-fonts-mode-seed=10026", "--fingerprint-platform=windows"]),
    make("fonts_allowlists", {}, ["--fingerprint-fonts-allowlist=Arial,Consolas", "--fingerprint-fonts-local-allowlist=Arial"]),
    make("fonts_empty", {}, ["--fingerprint-fonts-allowlist="]),
    make("cloak_voices", { voices: "cloak" }, ["--fingerprint-platform=windows", "--fingerprint-locale=en-US", "--fingerprint-sapi-voices=true"]),
    make("cloak_voices_no_local", { voices: "cloak" }, ["--fingerprint-platform=windows", "--fingerprint-locale=en-US", "--fingerprint-sapi-voices=false"]),
    make("cloak_offset", { offset: "cloak" }),
    make("cloak_text_positive", { text: "cloak" }, ["--fingerprint-platform=windows"]),
    make("cloak_audio_positive", { audio: "cloak" }, ["--fingerprint-platform=macos"]),
    make("audio_defaults_44100", {}, ["--fingerprint-audio-default-sample-rate=44100", "--fingerprint-audio-channel-limit=1"]),
    make("audio_defaults_32000", {}, ["--fingerprint-audio-default-sample-rate=32000", "--fingerprint-audio-channel-limit=6"]),
    make("audio_default_mono", {}, ["--fingerprint-audio-default-channel-count=1"]),
    make("audio_default_stereo", {}, ["--fingerprint-audio-default-channel-count=2"]),
    make("audio_default_32", {}, ["--fingerprint-audio-default-channel-count=32"]),
    make("gpu_masked_custom", {}, ["--fingerprint-webgl-identity-mode=custom",
      "--fingerprint-gpu-vendor=Test Graphics Vendor", "--fingerprint-gpu-renderer=Test Graphics Renderer",
      "--fingerprint-webgl-masked-vendor=Test Masked Vendor", "--fingerprint-webgl-masked-renderer=Test Masked Renderer"]),
    make("headless_custom_dpr2", {screen:"custom"}, display(2)),
    make("headless_custom_fractional", {screen:"custom"}, display(1.25)),
    make("headless_explicit_info", {screen:"custom"}, [...display(2), "--screen-info={960x720 devicePixelRatio=1.5}"]),
    make("headless_force_scale", {screen:"custom"}, [...display(2), "--force-device-scale-factor=1.5"]),
    make("headless_native_ignored", {}, display(2)),
    {id:"headless_off_ignored", flags:["--fingerprint=off", ...display(2)]},
  ];
}
export const POSITIVE_GROUPS = [
  "legacy_canvas", "shard_canvas", "cloak_canvas", "shard_webgl", "cloak_webgl",
  "shard_audio", "shard_rects", "shard_text", "shard_sensors", "shard_media",
  "memory_custom", "hardware_profile", "webgl_resource_limits", "webgpu_capabilities",
  "hardware_gpu_identity", "masked_webgl", "shard_fonts", "cloak_fonts", "font_allowlists",
  "cloak_voices", "cloak_offset", "cloak_text", "cloak_audio", "audio_defaults", "audio_channels", "headless_screen",
];
export const BASIC_SCENARIOS = new Set([
  "fonts_shard42", "fonts_shard43", "fonts_cloak10026", "fonts_allowlists", "fonts_empty",
  "cloak_voices", "cloak_voices_no_local", "cloak_offset", "cloak_text_positive",
  "cloak_audio_positive", "audio_defaults_44100", "audio_defaults_32000",
  "audio_default_mono", "audio_default_stereo", "audio_default_32",
  ...HEADLESS_SCENARIOS,
]);
export function summarizePositiveAcceptance(checks, notTested) {
  const positive_acceptance = {};
  for (const group of POSITIVE_GROUPS) {
    const executed = checks.filter(c => c.acceptance_group === group);
    const missing = notTested.filter(c => c.acceptance_group === group && c.optional_candidate !== true);
    const passed = executed.filter(c => c.passed === true).length;
    const status = executed.some(c => c.passed !== true) ? "failed"
      : !executed.length || missing.length ? "not_tested" : "observed_pass";
    positive_acceptance[group] = { status, eligible_count: executed.length, passed_count: passed,
      reason: `${passed}/${executed.length} eligible checks passed; ${missing.length} required conditions not tested`,
      evidence: executed.map(c => ({ scenario: c.scenario, name: c.name })),
      missing: missing.map(c => ({ scenario: c.scenario, name: c.name, reason: c.reason })) };
  }
  const full_strategy_acceptance = Object.values(positive_acceptance).every(c => c.status === "observed_pass");
  return { positive_acceptance, full_strategy_acceptance, partial: !full_strategy_acceptance };
}

export function verifyResults(records, baselineOnly = false, oracle = null) {
  const checks = [], notTested = [];
  const check = (scenario, name, passed, detail = null) => checks.push({ scenario, name, passed: passed === true, detail });
  const skip = (scenario, name, reason) => notTested.push({ scenario, name, reason });
  const byId = Object.fromEntries(records.map(r => [r.id, r]));
  const base = byId.all_native?.probe;
  for (const record of records) {
    const p = record.probe, id = record.id;
    check(id, "normal_owned_browser_shutdown", record.cleanup?.normalExit &&
      record.cleanup.closeSent === true && record.cleanup.forced === false && record.cleanup.exitCode === 0);
    check(id, "probe_completed", !!p && !record.error);
    if (!p) continue;
    check(id, "secure_loopback", p.secureContext && p.origin?.startsWith("http://127.0.0.1:"));
    check(id, "no_internal_probe_errors", Array.isArray(p.errors) && p.errors.length === 0, p.errors);
    check(id, "window_worker_cpu_memory", Number.isFinite(p.navigator?.cpu) && p.navigator.cpu > 0 &&
      Number.isFinite(p.navigator.memory) && p.navigator.memory > 0 &&
      p.worker?.cpu === p.navigator.cpu && p.worker?.memory === p.navigator.memory);
    const expectedLevel = BASIC_SCENARIOS.has(id) ? "basic" : "full";
    check(id, "probe_scope", record.probe_level === expectedLevel && p.level === expectedLevel);
    if (expectedLevel === "basic") continue;
    const canvas = p.canvas, full = bytes(canvas?.full);
    if (full?.length === 64 * 64 * 4) {
      check(id, "canvas_repeat", equal(full, bytes(canvas.repeat)));
      check(id, "canvas_crop", equal(cropped(full, 64, 11, 13, 18, 16), bytes(canvas.crop)));
      check(id, "opaque_alpha_preserved", full.every((v, i) => i % 4 !== 3 || v === 255));
      const transparent = bytes(canvas.transparentRaw);
      check(id, "transparent_pixels_unchanged", transparent?.length === 16 * 16 * 4 && transparent.every(v => v === 0));
      for (const [name, payload, raw] of [["png", canvas.png, full], ["blob", canvas.blob, full],
        ["offscreen", canvas.offscreen?.png, bytes(canvas.offscreen?.raw)]]) {
        try {
          const decoded = decodePNG(bytes(payload));
          check(id, `canvas_${name}_decoded`, decoded.width === 64 && decoded.height === 64);
          if (id !== "legacy_seeded") check(id, `canvas_${name}_matches_raw`, equal(decoded.rgba, raw));
        } catch (error) { check(id, `canvas_${name}_decoded`, false, errorText(error)); }
      }
      if (id !== "legacy_seeded") check(id, "offscreen_html_consistency", equal(full, bytes(canvas.offscreen?.raw)));
    } else check(id, "canvas_nonempty_readback", false);
    if (p.audio?.full) {
      check(id, "audio_repeated_and_copied", equal(bytes(p.audio.full), bytes(p.audio.repeat)) && equal(bytes(p.audio.full), bytes(p.audio.copied)));
      const overlapBefore = bytes(p.audio.overlapBefore), overlapAfter = bytes(p.audio.overlapAfter);
      check(id, "audio_alias_copy_preserves_unwritten_tail", overlapBefore?.length === 32 &&
        overlapAfter?.length === 32 && equal(overlapBefore.subarray(16), overlapAfter.subarray(16)));
      const tails = p.audio.analyserTails;
      check(id, "analyser_preserves_unwritten_tails", !!tails && Object.keys(tails).length === 4 &&
        Object.values(tails).every(t => t.writtenBound > 0 && t.tail?.length === 7 && t.unchanged === true));
    } else check(id, "audio_readback_present", false);
    const rect = p.layout?.first, repeated = p.layout?.repeat;
    check(id, "layout_finite_repeatable", !!rect && Object.values(rect).every(Number.isFinite) &&
      JSON.stringify(rect) === JSON.stringify(repeated) && Number.isFinite(p.layout.text) && p.layout.text === p.layout.textRepeat);
    check(id, "element_client_rect_consistency", !!rect && !!p.layout?.client &&
      ["x", "y", "width", "height"].every(key => p.layout.client[key] === rect[key]));
    if (p.sensor?.supported) check(id, "synthetic_sensor_finite", ["x", "y", "z"].every(k => Number.isFinite(p.sensor[k])));
    else skip(id, "sensor", p.sensor?.reason || "No supported synthetic reading");
    check(id, "synthetic_media_tracks", p.media?.supported && p.media.syntheticOnly &&
      p.media.tracks.some(t => t.kind === "audio" && t.readyState === "live") &&
      p.media.tracks.some(t => t.kind === "video" && t.readyState === "live"));
    for (const kind of ["webgl", "webgl2"]) {
      const gl = p.webgl?.[kind];
      if (!gl?.supported) { skip(id, kind, gl?.reason || gl?.error || "Unavailable"); continue; }
      const raw = bytes(gl.full);
      check(id, `${kind}_readback`, raw?.length === 16384 && gl.readError === 0);
      check(id, `${kind}_repeat_and_crop`, equal(raw, bytes(gl.repeat)) && equal(cropped(raw, 64, 11, 13, 18, 16), bytes(gl.crop)));
      try {
        const decoded = decodePNG(bytes(gl.png));
        check(id, `${kind}_png_decoded`, decoded.width === 64 && decoded.height === 64);
        if (id !== "legacy_seeded")
          check(id, `${kind}_png_matches_readback`, equal(decoded.rgba, flipped(raw, 64, 64)));
      } catch (error) { check(id, `${kind}_png_decoded`, false, errorText(error)); }
      if (kind === "webgl") check(id, "webgl1_invalid_webgl2_enum", gl.negative.webgl2Enum === 1280);
    }
    const gpu = p.webgpu;
    if (!gpu?.supported) skip(id, "webgpu", gpu?.reason || gpu?.error || "Unavailable");
    else {
      check(id, "webgpu_public_capabilities_present", Array.isArray(gpu.features) &&
        new Set(gpu.features).size === gpu.features.length &&
        ["maxTextureDimension2D", "maxBufferSize", "maxComputeWorkgroupSizeX"].every(name =>
          Number.isFinite(gpu.limits?.[name]) && gpu.limits[name] > 0));
      check(id, "webgpu_compute_readback", JSON.stringify(gpu.compute) === "[3,10,17,24]");
      check(id, "webgpu_no_uncaptured_errors", gpu.errors?.length === 0);
      check(id, "webgpu_over_limit_rejected", gpu.overLimit?.accepted === false);
      if (gpu.supportedFeature) check(id, "webgpu_required_supported_feature", gpu.supportedFeature.accepted &&
        gpu.supportedFeature.actualFeatures.includes(gpu.supportedFeature.name));
      else skip(id, "webgpu_optional_feature_request", "No optional feature was exposed");
      if (gpu.absentFeature) check(id, "webgpu_required_absent_feature_rejected", gpu.absentFeature.accepted === false);
      else skip(id, "webgpu_absent_feature_request", "All bounded negative-control feature names are present");
    }
  }
  if (!base) check("all_native", "baseline_present", false);
  else {
    check("all_native", "native_canvas_matches_input", equal(bytes(base.canvas?.full), bytes(base.canvas?.expected)));
    check("all_native", "native_audio_matches_input", equal(bytes(base.audio?.full), bytes(base.audio?.expected)));
    check("all_native", "native_layout", base.layout?.first?.width === 123.25 && base.layout?.first?.height === 45.5);
    if (base.sensor?.supported) check("all_native", "native_sensor_matches_injected", base.sensor.x === 1 && base.sensor.y === -2 && base.sensor.z === 10);
  }
  const nativeOff = byId.native_off_control?.probe;
  if (base && nativeOff) {
    for (const field of ["cpu", "memory", "platform", "ua"])
      check("all_native", `native_${field}_matches_off_control`, base.navigator?.[field] !== undefined &&
        base.navigator[field] === nativeOff.navigator?.[field]);
    check("all_native", "native_screen_matches_off_control", !!base.navigator?.screen &&
      JSON.stringify(base.navigator.screen) === JSON.stringify(nativeOff.navigator?.screen));
    check("all_native", "native_pixels_match_off_control", equal(bytes(base.canvas?.full), bytes(nativeOff.canvas?.full)));
    check("all_native", "native_audio_matches_off_control", equal(bytes(base.audio?.full), bytes(nativeOff.audio?.full)));
  } else if (!baselineOnly) check("all_native", "native_off_control_present", false);
  if (!baselineOnly && base) {
    const p = id => byId[id]?.probe;
    for (const id of ["legacy_seeded", "canvas_shardx", "hardware_cloak"]) {
      const diff = difference(bytes(p(id)?.canvas?.full), bytes(base.canvas?.full));
      check(id, "selected_canvas_noise_reaches_runtime", diff && diff.changed > 0 && diff.alphaChanges === 0 &&
        diff.maxDelta <= 1 && (id === "legacy_seeded" || diff.changed <= (id === "canvas_shardx" ? 32 : 30)), diff);
    }
    for (const id of ["webgl_cloak", "shard_surfaces", "memory_custom", "gpu_custom"])
      check(id, "canvas_native_independent", equal(bytes(p(id)?.canvas?.full), bytes(base.canvas?.full)));
    for (const kind of ["webgl", "webgl2"]) if (base.webgl?.[kind]?.supported) {
      for (const id of ["canvas_shardx", "shard_surfaces", "memory_custom", "hardware_cloak", "gpu_custom"])
        if (p(id)?.webgl?.[kind]?.supported) check(id, `${kind}_pixels_native_independent`, equal(bytes(p(id).webgl[kind].full), bytes(base.webgl[kind].full)));
      if (p("webgl_cloak")?.webgl?.[kind]?.supported) {
        const diff = difference(bytes(p("webgl_cloak").webgl[kind].full), bytes(base.webgl[kind].full));
        check("webgl_cloak", `${kind}_noise_reaches_runtime`, diff && diff.changed > 0 && diff.changed <= 30 && !diff.alphaChanges && diff.maxDelta <= 1, diff);
      }
    }
    const custom = p("memory_custom"), hardware = p("hardware_cloak");
    for (const [id, value, cpu, memory] of [["memory_custom", custom, 4, 2], ["hardware_cloak", hardware, 20, 16]]) {
      check(id, "expected_cpu_and_memory", value?.navigator?.cpu === cpu && value?.worker?.cpu === cpu &&
        value?.navigator?.memory === memory && value?.worker?.memory === memory);
      for (const header of ["device-memory", "sec-ch-device-memory"])
        check(id, `${header}_matches_js`, Number(value?.headers?.[header]) === memory && typeof value?.headers?.[header] === "string", value?.headers?.[header] ?? null);
    }
    const shard = p("shard_surfaces"), originalAudio = bytes(base.audio?.full), changedAudio = bytes(shard?.audio?.full);
    check("shard_surfaces", "audio_changed", originalAudio?.length === 1024 && changedAudio?.length === 1024 && !originalAudio.equals(changedAudio));
    if (originalAudio?.length === 1024 && changedAudio?.length === 1024) {
      let preserved = true; for (let i = 0; i < 1024; i += 4) preserved &&= (originalAudio.readUInt32LE(i) & 0xffffff00) === (changedAudio.readUInt32LE(i) & 0xffffff00);
      check("shard_surfaces", "audio_sign_exponent_high_mantissa_preserved", preserved);
    }
    const a = base.layout?.first, b = shard?.layout?.first;
    check("shard_surfaces", "rect_mode_effect_and_bound", !!a && !!b && a.width !== b.width &&
      Math.abs(a.width - b.width) <= 1.0001 && Math.abs(a.height - b.height) <= 1.0001 && a.x === b.x && a.y === b.y);
    check("shard_surfaces", "text_mode_effect_and_bound", Number.isFinite(shard?.layout?.text) &&
      shard.layout.text !== base.layout.text && Math.abs(shard.layout.text - base.layout.text) <= 1.0001);
    if (base.sensor?.supported && shard?.sensor?.supported)
      check("shard_surfaces", "sensor_mode_effect_and_bound", ["x", "y", "z"].some(k => base.sensor[k] !== shard.sensor[k]) &&
        ["x", "y", "z"].every(k => Math.abs(base.sensor[k] - shard.sensor[k]) <= 0.100001));
    check("shard_surfaces", "media_filter_observable_on_fake_devices", base.media?.after?.length > 0 &&
      Array.isArray(shard?.media?.after) && shard.media.after.length === 0 && Array.isArray(shard.media.repeat) && shard.media.repeat.length === 0);
    check("hardware_cloak", "unqualified_cloak_audio_remains_native", equal(bytes(hardware?.audio?.full), bytes(base.audio?.full)));
    check("hardware_cloak", "unmatched_cloak_text_remains_native", hardware?.layout?.text === base.layout?.text);
    for (const kind of ["webgl", "webgl2"]) {
      const reference = base.webgl?.[kind], constrained = p("gpu_custom")?.webgl?.[kind];
      if (!reference?.supported || !constrained?.supported) continue;
      const wanted = { texture: 64, cube: 64, renderbuffer: 64, ...(kind === "webgl2" ? { texture3d: 16, arrayLayers: 4 } : {}) };
      for (const [name, cap] of Object.entries(wanted)) {
        check("gpu_custom", `${kind}_${name}_context_limit`, constrained.limits[name] === Math.min(reference.limits[name], cap));
        check("gpu_custom", `${kind}_${name}_oversize_rejected`, constrained.negative[name] === 1281, constrained.negative[name]);
      }
      const identity = hardware?.webgl?.[kind]?.identity, original = reference.identity;
      if (original && identity) {
        const software = /swiftshader|llvmpipe|softpipe|lavapipe|software|basic render driver/i.test(original.renderer);
        check("hardware_cloak", `${kind}_software_identity_guard`, software
          ? JSON.stringify(identity) === JSON.stringify(original)
          : /NVIDIA/i.test(identity.vendor) && /NVIDIA/i.test(identity.renderer));
      } else skip("hardware_cloak", `${kind}_identity`, "WEBGL_debug_renderer_info identity unavailable");
    }
    if (base.webgpu?.supported) for (const id of ["hardware_cloak", "gpu_custom"]) {
      const gpu = p(id)?.webgpu; if (!gpu?.supported) continue;
      check(id, "webgpu_features_subset_native", gpu.features.every(f => base.webgpu.features.includes(f)));
      check(id, "webgpu_limit_fields_preserved", JSON.stringify(Object.keys(gpu.limits).sort()) ===
        JSON.stringify(Object.keys(base.webgpu.limits).sort()));
      if (id === "gpu_custom") check(id, "webgpu_allowlist_applied", gpu.features.every(f => ["core-features-and-limits", "timestamp-query"].includes(f)));
      else for (const [name, value] of Object.entries(gpu.limits))
        if (typeof base.webgpu.limits[name] === "number") check(id, `webgpu_${name}_bounded_by_native`,
          name.startsWith("min") ? value >= base.webgpu.limits[name] : value <= base.webgpu.limits[name]);
      check(id, "webgpu_real_subgroup_range", gpu.info.subgroupMinSize === base.webgpu.info.subgroupMinSize &&
        gpu.info.subgroupMaxSize === base.webgpu.info.subgroupMaxSize);
      if (base.webgpu.info.isFallbackAdapter) check(id, "webgpu_software_identity_guard",
        gpu.info.vendor === base.webgpu.info.vendor && gpu.info.architecture === base.webgpu.info.architecture);
      else if (id === "hardware_cloak" && base.webgpu.info.isFallbackAdapter === false)
        check(id, "webgpu_catalog_identity", gpu.info.vendor === "nvidia" && gpu.info.architecture === "blackwell");
      else if (base.webgpu.info.isFallbackAdapter === null)
        skip(id, "webgpu_fallback_identity_classification", "API did not expose fallback classification");
    }
  }
  if (!baselineOnly && oracle) {
    // Presence is mandatory even if a native capability legitimately makes an
    // individual positive condition ineligible. Missing probes are not skips.
    const extraScopes = {
      all_native: ["fonts", "reference", "audio", "renderbuffer", "pixel_semantics"],
      gpu_custom: ["renderbuffer"],
      canvas_shardx: ["pixel_semantics"], webgl_shardx: ["pixel_semantics"],
      ...Object.fromEntries(HEADLESS_SCENARIOS.map(id=>[id,["headless_screen"]])),
      ...Object.fromEntries(["fonts_shard42", "fonts_shard43", "fonts_cloak10026", "fonts_allowlists", "fonts_empty"].map(id => [id, ["fonts"]])),
      ...Object.fromEntries(["cloak_voices", "cloak_voices_no_local", "cloak_offset", "cloak_text_positive"].map(id => [id, ["reference"]])),
      ...Object.fromEntries(["cloak_audio_positive", "audio_defaults_44100", "audio_defaults_32000", "audio_default_mono", "audio_default_stereo", "audio_default_32"].map(id => [id, ["audio"]])),
    };
    for (const [id, scopes] of Object.entries(extraScopes)) for (const scope of scopes) {
      const value = byId[id]?.extra?.[scope];
      check(id, `${scope}_acceptance_probe_present`, !!value && value.version === 1 && typeof value === "object");
    }
    for (const validator of [validateAudioAcceptance, validateReferenceAcceptance, validateRenderbufferAcceptance]) {
      const result = validator(byId, oracle);
      checks.push(...result.checks); notTested.push(...result.not_tested);
    }
    const fontResult = validateFontAcceptance(byId, FONT_ACCEPTANCE_ORACLE);
    checks.push(...fontResult.checks); notTested.push(...fontResult.not_tested);
    const pixelResult = validatePixelSemantics(byId, decodePNG);
    checks.push(...pixelResult.checks.filter(c=>!c.name.endsWith("_acceptance_probe_present"))); notTested.push(...pixelResult.not_tested);
    const headlessResult = validateHeadlessScreens(byId);
    checks.push(...headlessResult.checks.filter(c=>!c.name.endsWith("_acceptance_probe_present"))); notTested.push(...headlessResult.not_tested);
    const tagged = (id, name, passed, group, detail = null) =>
      checks.push({ scenario: id, name, passed: passed === true, acceptance_group: group, detail });
    const skipped = (id, name, reason, group) =>
      notTested.push({ scenario: id, name, reason, acceptance_group: group });
    for (const kind of ["webgl", "webgl2"]) {
      const baseline = base?.webgl?.[kind], shard = byId.webgl_shardx?.probe?.webgl?.[kind];
      if (!baseline?.supported) skipped("webgl_shardx", `${kind}_positive`, "Native WebGL context unavailable", "shard_webgl");
      else {
        const original = bytes(baseline.full), target = bytes(shard?.full);
        if (original?.length !== 16384) tagged("webgl_shardx", `${kind}_v2_sparse_positive`, false, "shard_webgl", "Native readback missing");
        else {
          // The repaired ShardX-derived policy is coordinate-addressed and
          // idempotent. Historical process-order XOR bytes remain native
          // reference fixtures, not an oracle for the new production policy.
          const delta = difference(original, target);
          tagged("webgl_shardx", `${kind}_v2_sparse_positive`, !!delta &&
            delta.changed > 0 && delta.changed <= 256 && delta.maxDelta === 1 &&
            delta.alphaChanges === 0, "shard_webgl", delta);
          tagged("webgl_shardx", `${kind}_v2_repeat`, equal(target, bytes(shard?.repeat)), "shard_webgl");
          tagged("webgl_shardx", `${kind}_v2_absolute_crop`, target?.length === 16384 &&
            equal(cropped(target, 64, 11, 13, 18, 16), bytes(shard?.crop)), "shard_webgl");
          try {
            tagged("webgl_shardx", `${kind}_v2_export_matches_readback`, target?.length === 16384 &&
              equal(decodePNG(bytes(shard?.png)).rgba, flipped(target, 64, 64)), "shard_webgl");
          } catch (error) { tagged("webgl_shardx", `${kind}_v2_export_matches_readback`, false, "shard_webgl", errorText(error)); }
        }
      }
      const masked = byId.gpu_masked_custom?.probe?.webgl?.[kind];
      if (!baseline?.supported || !baseline.identity?.renderer) {
        skipped("gpu_masked_custom", `${kind}_hardware_masked_identity`, "No classified native WebGL adapter identity", "masked_webgl");
      } else if (/swiftshader|llvmpipe|softpipe|lavapipe|software|basic render driver/i.test(baseline.identity.renderer)) {
        check("gpu_masked_custom", `${kind}_software_masked_identity_guard`,
          !!masked?.maskedIdentity && JSON.stringify(masked.maskedIdentity) === JSON.stringify(baseline.maskedIdentity) &&
          JSON.stringify(masked.identity) === JSON.stringify(baseline.identity));
        skipped("gpu_masked_custom", `${kind}_hardware_masked_identity`, "Actual software/fallback adapter must retain native metadata; hardware path was not exercised", "masked_webgl");
      } else tagged("gpu_masked_custom", `${kind}_hardware_masked_identity`,
        masked?.maskedIdentity?.vendor === "Test Masked Vendor" && masked.maskedIdentity.renderer === "Test Masked Renderer" &&
        masked.identity?.vendor === "Test Graphics Vendor" && masked.identity.renderer === "Test Graphics Renderer", "masked_webgl");
      if (!baseline?.supported) skipped("webgl_cloak", `${kind}_positive`, "Native WebGL context unavailable", "cloak_webgl");
      if (!baseline?.supported) skipped("gpu_custom", `${kind}_resource_limits`, "Native WebGL context unavailable", "webgl_resource_limits");
      if (!baseline?.identity?.renderer || /swiftshader|llvmpipe|softpipe|lavapipe|software|basic render driver/i.test(baseline.identity.renderer))
        skipped("hardware_cloak", `${kind}_hardware_identity_positive`, "Actual hardware WebGL identity path not available", "hardware_gpu_identity");
      else {
        const actual = byId.hardware_cloak?.probe?.webgl?.[kind]?.identity;
        tagged("hardware_cloak", `${kind}_hardware_identity_positive`, !!actual &&
          /NVIDIA/i.test(actual.vendor) && /NVIDIA/i.test(actual.renderer), "hardware_gpu_identity");
      }
    }
    const simple = {
      legacy_seeded: { selected_canvas_noise_reaches_runtime: "legacy_canvas" },
      canvas_shardx: { selected_canvas_noise_reaches_runtime: "shard_canvas" },
      hardware_cloak: { selected_canvas_noise_reaches_runtime: "cloak_canvas", expected_cpu_and_memory: "hardware_profile" },
      memory_custom: { expected_cpu_and_memory: "memory_custom", "device-memory_matches_js": "memory_custom", "sec-ch-device-memory_matches_js": "memory_custom" },
      shard_surfaces: { audio_changed: "shard_audio", audio_sign_exponent_high_mantissa_preserved: "shard_audio",
        rect_mode_effect_and_bound: "shard_rects", text_mode_effect_and_bound: "shard_text",
        sensor_mode_effect_and_bound: "shard_sensors", media_filter_observable_on_fake_devices: "shard_media" },
    };
    for (const entry of checks) {
      const group = simple[entry.scenario]?.[entry.name];
      if (group) entry.acceptance_group = group;
      if (entry.scenario === "webgl_cloak" && /_noise_reaches_runtime$/.test(entry.name)) entry.acceptance_group = "cloak_webgl";
      if (entry.scenario === "gpu_custom" && /_(context_limit|oversize_rejected)$/.test(entry.name)) entry.acceptance_group = "webgl_resource_limits";
      if (["gpu_custom", "hardware_cloak"].includes(entry.scenario) && entry.name.startsWith("webgpu_"))
        entry.acceptance_group = "webgpu_capabilities";
    }
    if (!base?.sensor?.supported || !byId.shard_surfaces?.probe?.sensor?.supported)
      skipped("shard_surfaces", "sensor_positive", "Synthetic sensor baseline/target unavailable", "shard_sensors");
    if (!base?.webgpu?.supported || !byId.gpu_custom?.probe?.webgpu?.supported || !byId.hardware_cloak?.probe?.webgpu?.supported)
      skipped("gpu_custom", "webgpu_positive", "Native or constrained WebGPU adapter unavailable", "webgpu_capabilities");
  }
  const coverage = summarizePositiveAcceptance(checks, notTested);
  return { checks, not_tested: notTested, failed: checks.filter(c => !c.passed), ...coverage,
    qualification: baselineOnly ? "Harness baseline only; no new-mode acceptance" : "Required local routing and invariant checks only; see not_tested and coverage limits" };
}

async function main() {
  const args = {};
  for (let i = 2; i < process.argv.length; i++) {
    const key = process.argv[i];
    if (key === "--baseline-only" || key === "--self-test") args[key.slice(2)] = true;
    else { if (!key.startsWith("--") || !process.argv[i + 1]) throw new Error("Invalid arguments"); args[key.slice(2)] = process.argv[++i]; }
  }
  if (args["self-test"]) {
    const env = browserEnvironment({ PATH: "retained", GH_TOKEN: "drop", GOOGLE_API_KEY: "drop", CLOAKBROWSER_LICENSE_KEY: "drop", SOME_SECRET: "drop" });
    if (JSON.stringify(env) !== '{"PATH":"retained"}') throw new Error("Environment filter failed");
    const header = Buffer.alloc(13); header.writeUInt32BE(2, 0); header.writeUInt32BE(1, 4); header[8] = 8; header[9] = 6;
    const chunk = (name, data) => { const b = Buffer.alloc(data.length + 12); b.writeUInt32BE(data.length); b.write(name, 4); data.copy(b, 8); return b; };
    const png = Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk("IHDR", header),
      chunk("IDAT", zlib.deflateSync(Buffer.from([0, 1, 2, 3, 255, 4, 5, 6, 255]))), chunk("IEND", Buffer.alloc(0))]);
    if (!decodePNG(png).rgba.equals(Buffer.from([1, 2, 3, 255, 4, 5, 6, 255]))) throw new Error("PNG transport self-test failed");
    if (!verifyResults([]).failed.length) throw new Error("Empty result falsely passed");
    console.log(JSON.stringify({ status: "passed", self_tests: 3, browsers_started: 0 })); return 0;
  }
  if (Number(process.versions.node.split(".")[0]) < 24) throw new Error("Node 24 or newer is required");
  if (!args.browser || !args.report || !args.capabilities) throw new Error("--browser --report --capabilities are required");
  const browser = path.resolve(args.browser), reportPath = path.resolve(args.report);
  const dll = path.join(path.dirname(browser), "chrome.dll");
  const capabilities = JSON.parse(await fsp.readFile(path.resolve(args.capabilities), "utf8"));
  if (!capabilities || typeof capabilities !== "object" || Array.isArray(capabilities)) throw new Error("Invalid capabilities manifest");
  await fsp.mkdir(path.dirname(reportPath), { recursive: true });
  const runRoot = await fsp.mkdtemp(path.join(path.dirname(reportPath), "modes-runtime-evidence-"));
  const toolRoot = path.dirname(fileURLToPath(import.meta.url));
  const oraclePath = path.join(toolRoot, "tests/fixtures/fingerprint-runtime-v1/positive-oracles.json");
  const oracle = JSON.parse(await fsp.readFile(oraclePath, "utf8"));
  const ownedFont = await fsp.readFile(path.join(toolRoot, "tests/fixtures/font-allowlist-v1/ChromixOwnedProbe-Regular.ttf"));
  if (hashBytes(ownedFont) !== "1a4670b97d09fe70f1bcce5b90676728bdb065beb2f7ae89a2d2e6e450a60e4f")
    throw new Error("Owned font fixture identity changed");
  const token = crypto.randomBytes(16).toString("hex"), workerPath = `/${token}/worker.js`, echoPath = `/${token}/echo`;
  const fontPath = `/${token}/owned-font.ttf`, workletPath = `/${token}/audio-worklet.js`;
  const server = http.createServer((req, res) => {
    res.setHeader("Cache-Control", "no-store");
    res.setHeader("Accept-CH", "Device-Memory, Sec-CH-Device-Memory");
    res.setHeader("Permissions-Policy", "camera=(self), microphone=(self), accelerometer=(self), local-fonts=(self)");
    res.setHeader("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; connect-src 'self'; worker-src 'self' blob:; font-src 'self'; img-src 'self' data: blob:");
    const route = new URL(req.url, "http://127.0.0.1").pathname;
    if (route === workerPath) { res.setHeader("Content-Type", "text/javascript"); res.end("postMessage({cpu:navigator.hardwareConcurrency,memory:navigator.deviceMemory});"); }
    else if (route === fontPath) { res.setHeader("Content-Type", "font/ttf"); res.end(ownedFont); }
    else if (route === workletPath) { res.setHeader("Content-Type", "text/javascript"); res.end(
      "class P extends AudioWorkletProcessor {constructor(){super();this.records=[];this.sent=false;}process(inputs,outputs){for(const output of outputs)for(const channel of output)channel.fill(0);if(!this.sent){this.records.push({sequence:this.records.length,sampleRate,currentFrame,currentTime,frames:outputs[0]?.[0]?.length||0,inputChannels:inputs[0]?.length||0,outputChannels:outputs[0]?.length||0});if(this.records.length===384){this.sent=true;this.port.postMessage({kind:'clock-records',messages:this.records});}}return true;}}registerProcessor('fingerprint-audio-acceptance',P);"); }
    else if (route === echoPath) { res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(req.headers)); }
    else if (route === `/${token}/`) { res.setHeader("Content-Type", "text/html"); res.end("<!doctype html><meta charset=utf-8><title>Owned kernel acceptance</title><body>"); }
    else { res.statusCode = 404; res.end(); }
  });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${server.address().port}`, url = `${origin}/${token}/`;
  const report = { schema_version: 1, status: "failed", browser_sha256: await sha256(browser), chrome_dll_sha256: await sha256(dll),
    capabilities_sha256: await sha256(path.resolve(args.capabilities)), harness_sha256: await sha256(fileURLToPath(import.meta.url)),
    started_at: new Date().toISOString(), node: process.version, transport: "Raw CDP; sensor emulation only, no API replacement or automatic UA/font/media/viewport emulation",
    synthetic_media_only: true, credentials_environment_removed: true, external_pages: false, baseline_only: !!args["baseline-only"], scenarios: [],
    oracle_sha256: await sha256(oraclePath),
    collector_modules_sha256: Object.fromEntries(await Promise.all(["fingerprint_audio_acceptance.mjs", "fingerprint_font_acceptance.mjs", "fingerprint_reference_acceptance.mjs", "fingerprint_renderbuffer_acceptance.mjs", "pixel_semantic_acceptance.mjs"]
      .map(async name => [name, await sha256(path.join(toolRoot, name))]))),
    coverage_limits: ["Headless Windows acceptance; not all physical GPUs or driver versions.",
      "Cloak text/audio positive acceptance requires the pinned native input to meet the unchanged strict guard; ineligible rows remain not_tested.",
      "Pixel semantic checks cover opaque RGBA8 and a bounded WebGL2 PBO/PACK case; no partial-alpha/float16/FBO acceptance in this bounded run.",
      "Headless screenshots verify raster scale and caller precedence, not ordinary headed-window or all-monitor behavior.",
      "No real camera, microphone, account, external STUN, network detection site or recording. Realtime AudioContext opens only its native output backend with zero-valued Worklet output; no audible signal is produced.",
      "Native font/voice availability and actual hardware adapter identity are environmental prerequisites; see positive_acceptance and partial.",
      "WebGPU small integer compute and negotiated features/limits do not prove every feature workload or large allocation."] };
  try {
    const matrix = args["baseline-only"] ? scenarios().slice(0, 2) : scenarios();
    report.expected_scenarios = matrix.length;
    for (const scenario of matrix) {
      const folder = path.join(runRoot, scenario.id); await fsp.mkdir(folder);
      const profile = path.join(folder, "profile"); await fsp.mkdir(profile);
      const scenarioFlags = [...scenario.flags];
      let voiceLocale;
      if (["cloak_voices", "cloak_voices_no_local"].includes(scenario.id)) {
        const nativeVoices = report.scenarios.find(r => r.id === "all_native")?.extra?.reference?.voices?.values || [];
        const choices = Object.entries(oracle.cloakVoicesWindowsByLocale).map(([locale, rows]) => ({
          locale, score: rows.filter(row => nativeVoices.some(v => v.name === row.name &&
            v.lang.toLowerCase() === row.lang.toLowerCase())).length,
        })).sort((a, b) => b.score - a.score);
        voiceLocale = choices[0].locale;
        scenarioFlags[scenarioFlags.findIndex(flag => flag.startsWith("--fingerprint-locale="))] = `--fingerprint-locale=${voiceLocale}`;
      }
      const trace = [], record = { id: scenario.id, flags: scenarioFlags, cdp: trace,
        ...(voiceLocale ? { voice_oracle_locale: voiceLocale } : {}),
        probe_level: BASIC_SCENARIOS.has(scenario.id) ? "basic" : "full" };
      const flags = ["--headless=new", "--remote-debugging-address=127.0.0.1", "--remote-debugging-port=0", `--user-data-dir=${profile}`,
        "--no-first-run", "--no-default-browser-check", "--disable-background-networking", "--disable-component-update", "--disable-sync",
        "--disable-extensions", "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream",
        "--autoplay-policy=no-user-gesture-required", ...scenarioFlags, "about:blank"];
      const child = spawn(browser, flags, { windowsHide: true, env: browserEnvironment(process.env), stdio: ["ignore", "pipe", "pipe"] });
      const closed = new Promise(resolve => child.once("close", resolve));
      const stdout = fs.createWriteStream(path.join(folder, "browser.stdout.log")), stderr = fs.createWriteStream(path.join(folder, "browser.stderr.log"));
      for (const stream of [stdout, stderr]) stream.on("error", error => { record.evidenceError = errorText(error); });
      child.stdout.pipe(stdout); child.stderr.pipe(stderr);
      let cdp;
      child.on("error", error => { record.spawnError = errorText(error); });
      try {
        let endpoint;
        for (let i = 0; i < 150; i++) {
          if (record.spawnError || child.exitCode !== null) throw new Error("Owned browser exited before CDP");
          try { const [port, address] = (await fsp.readFile(path.join(profile, "DevToolsActivePort"), "utf8")).trim().split(/\r?\n/);
            if (/^[0-9]+$/.test(port) && address?.startsWith("/devtools/browser/")) { endpoint = `ws://127.0.0.1:${port}${address}`; break; }
          } catch {}
          await delay(100);
        }
        if (!endpoint) throw new Error("Owned browser CDP startup deadline");
        cdp = await connectCDP(endpoint, trace);
        record.version = await cdp.send("Browser.getVersion");
        const target = await cdp.send("Target.createTarget", { url: "about:blank" });
        const { sessionId } = await cdp.send("Target.attachToTarget", { targetId: target.targetId, flatten: true });
        await cdp.send("Page.enable", {}, sessionId);
        await cdp.send("Page.navigate", { url }, sessionId);
        let ready = false;
        for (let i = 0; i < 100; i++) {
          try { ready = await evaluate(cdp, sessionId, `location.href === ${JSON.stringify(url)} && document.readyState === "complete"`); } catch {}
          if (ready) break; await delay(50);
        }
        if (!ready) throw new Error("Owned loopback page did not finish navigation");
        let sensorOverride = false;
        try {
          await cdp.send("Emulation.setSensorOverrideEnabled", { enabled: true, type: "accelerometer",
            metadata: { available: true, minimumFrequency: 1, maximumFrequency: 60 } }, sessionId);
          // Integers survive Chromium's native sensor precision reduction.
          await cdp.send("Emulation.setSensorOverrideReadings", { type: "accelerometer", reading: { xyz: { x: 1, y: -2, z: 10 } } }, sessionId);
          sensorOverride = true;
        } catch (error) { record.sensor_unavailable = errorText(error); }
        record.probe = await evaluate(cdp, sessionId, `(${pageProbe.toString()})(${JSON.stringify({
          workerURL: origin + workerPath, echoURL: origin + echoPath, sensorOverride,
          fakeFlagsConfirmed: flags.includes("--use-fake-device-for-media-stream") && flags.includes("--use-fake-ui-for-media-stream"),
          gpuCustom: scenario.id === "gpu_custom",
          basicOnly: BASIC_SCENARIOS.has(scenario.id),
        })})`);
        record.extra = {};
        if (PIXEL_SCENARIOS.includes(scenario.id)) {
          record.extra.pixel_semantics = await evaluate(cdp, sessionId, `(${pixelSemanticProbe.toString()})()`, true);
        }
        if (HEADLESS_SCENARIOS.includes(scenario.id)) {
          const page = await evaluate(cdp, sessionId, `({screen:{width:screen.width,height:screen.height,availWidth:screen.availWidth,availHeight:screen.availHeight},dpr:devicePixelRatio,innerWidth,innerHeight})`);
          const layout = await cdp.send("Page.getLayoutMetrics", {}, sessionId);
          const capture = await cdp.send("Page.captureScreenshot", {format:"png",fromSurface:true,captureBeyondViewport:false}, sessionId);
          const dimensions = pngDimensions(capture.data);
          const system = await cdp.send("SystemInfo.getInfo");
          await fsp.writeFile(path.join(folder,"headless-viewport.png"),Buffer.from(capture.data,"base64"));
          record.extra.headless_screen = {version:1,page,layout,effectiveCommandLine:system.commandLine,screenshot:{...dimensions,png:capture.data}};
        }
        if (["all_native", "gpu_custom"].includes(scenario.id)) {
          record.extra.renderbuffer = await evaluate(cdp, sessionId, `(${renderbufferAcceptanceProbe.toString()})({})`, true);
        }
        if (scenario.id === "all_native" || scenario.id.startsWith("fonts_")) {
          try {
            await cdp.send("Browser.grantPermissions", { origin, permissions: ["localFonts"] });
            record.local_fonts_permission = "granted_for_owned_origin_only";
          } catch (error) { record.local_fonts_permission_error = errorText(error); }
          record.extra.fonts = await evaluate(cdp, sessionId, `(${fontAcceptanceProbe.toString()})(${JSON.stringify({
            scenario: scenario.id, ownedFontURL: origin + fontPath,
          })})`, true);
          await collectPlatformFonts(cdp, sessionId, record.extra.fonts);
        }
        if (["all_native", "cloak_offset", "cloak_text_positive", "cloak_voices", "cloak_voices_no_local"].includes(scenario.id)) {
          record.extra.reference = await evaluate(cdp, sessionId, `(${referenceAcceptanceProbe.toString()})(${JSON.stringify({
            offset: ["all_native", "cloak_offset"].includes(scenario.id),
            voices: ["all_native", "cloak_voices", "cloak_voices_no_local"].includes(scenario.id),
            textRows: ["all_native", "cloak_text_positive"].includes(scenario.id) ? oracle.cloakTextWindows : undefined,
          })})`, true);
          await collectPlatformFonts(cdp, sessionId, record.extra.reference);
        }
        if (scenario.id === "all_native" || scenario.id === "cloak_audio_positive" || scenario.id.startsWith("audio_default")) {
          const native = report.scenarios.find(r => r.id === "all_native")?.extra?.audio?.contexts?.find(r => r.id === "default");
          record.extra.audio = await evaluate(cdp, sessionId, `(${audioAcceptanceProbe.toString()})(${JSON.stringify({
            scenario: scenario.id, workletURL: origin + workletPath, baselineCapacity: native?.maxChannelCount,
            audioReferencePairs: oracle.audioReferencePairs,
          })})`, true);
          if (record.extra.audio.needGC) {
            await evaluate(cdp, sessionId, "new Promise(resolve => setTimeout(resolve, 50))");
            await cdp.send("HeapProfiler.collectGarbage", {}, sessionId);
            await evaluate(cdp, sessionId, "new Promise(resolve => setTimeout(resolve, 50))");
            record.extra.audioAfterGC = await evaluate(cdp, sessionId, `(${audioAcceptanceProbe.toString()})(${JSON.stringify({
              scenario: scenario.id, afterGC: true,
            })})`, true);
          }
        }
      } catch (error) { record.error = errorText(error); }
      finally {
        record.cleanup = await stopOwned(child, cdp);
        await deadline(closed, 5000, "Owned browser pipes did not close").catch(error => { record.evidenceError = errorText(error); });
        await Promise.all([stdout, stderr].map(stream => stream.writableFinished || stream.destroyed ? Promise.resolve() :
          deadline(new Promise(resolve => stream.once("finish", resolve)), 5000, "Owned browser log did not finish")
            .catch(error => { record.evidenceError = errorText(error); stream.destroy(); })));
        if (record.evidenceError) record.error ||= record.evidenceError;
        if (record.error) {
          const log = await fsp.readFile(path.join(folder, "browser.stderr.log"), "utf8").catch(() => "");
          record.browser_stderr_tail = log.slice(-10000).replace(/([?&](?:key|token|secret|api_key)=)[^&\s]+/gi, "$1[redacted]");
        }
        await fsp.writeFile(path.join(folder, "result.json"), JSON.stringify(record, null, 2) + "\n");
        report.scenarios.push(record);
      }
      console.log(JSON.stringify({ scenario: record.id, completed: !!record.probe && !record.error, normal_exit: record.cleanup.normalExit }));
      if (!record.cleanup.normalExit) break; // Never multiply an unclosed session.
    }
    const validation = verifyResults(report.scenarios, !!args["baseline-only"], oracle);
    Object.assign(report, validation);
    if (report.scenarios.length !== matrix.length) validation.failed.push({ name: "complete_scenario_matrix", passed: false });
    report.status = validation.failed.length ? "failed" : args["baseline-only"] ? "baseline_only" : "passed";
  } catch (error) { report.error = errorText(error); }
  finally {
    await new Promise(resolve => server.close(resolve));
    report.completed_at = new Date().toISOString();
    report.evidence_directory = runRoot;
    await fsp.writeFile(reportPath, JSON.stringify(report, null, 2) + "\n");
  }
  console.log(JSON.stringify({ status: report.status, required_checks: report.checks?.length ?? 0,
    failures: report.failed?.map(c => ({ scenario: c.scenario, name: c.name })) ?? [], not_tested: report.not_tested?.length ?? 0,
    full_strategy_acceptance: report.full_strategy_acceptance, partial: report.partial }));
  return ["passed", "baseline_only"].includes(report.status) ? 0 : 1;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url))
  process.exitCode = await main().catch(error => { console.error(errorText(error)); return 1; });
