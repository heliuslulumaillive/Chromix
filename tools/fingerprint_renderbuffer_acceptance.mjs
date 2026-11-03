// Bounded actual allocations, framebuffer readback, failed-storage preservation,
// and context restoration. Serializable over CDP; no captured native device data.
export async function renderbufferAcceptanceProbe(input = {}) {
  const errorValue = error => String(error?.message || error);
  const deadline = async (promise, ms, message) => {
    let timer;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => reject(new Error(message)), ms);
      })]);
    } finally { clearTimeout(timer); }
  };
  const run = async (scope, api) => {
    const canvas = scope === "window" ? document.createElement("canvas") : new OffscreenCanvas(16, 16);
    canvas.width = canvas.height = 16;
    if (scope === "window") { canvas.hidden = true; document.body.append(canvas); }
    const gl = canvas.getContext(api, { alpha: false, antialias: false });
    if (!gl) { canvas.remove?.(); return { supported: false, reason: "Context creation unavailable" }; }
    const webgl2 = api === "webgl2", format = webgl2 ? gl.RGBA8 : gl.RGBA4;
    const result = { supported: true, stages: [], unavailableMethods: [] };
    const drain = () => {
      const errors = [];
      for (let i = 0; i < 8; i++) {
        const error = gl.getError();
        if (!error) break;
        errors.push(error);
      }
      return errors;
    };
    const modes = ["storage"];
    if (webgl2) {
      modes.push("multisample0");
      const supported = [...gl.getInternalformatParameter(gl.RENDERBUFFER, format, gl.SAMPLES)];
      if (supported.includes(4)) modes.push("multisample4");
      else result.unavailableMethods.push({ method: "multisample4", nativeSamples: supported });
    }
    const invoke = (method, width, height, target = gl.RENDERBUFFER, selectedFormat = format, samplesOverride) => {
      if (method === "storage") gl.renderbufferStorage(target, selectedFormat, width, height);
      else gl.renderbufferStorageMultisample(target, samplesOverride ?? (method === "multisample4" ? 4 : 0), selectedFormat, width, height);
    };
    const state = () => ({
      width: gl.getRenderbufferParameter(gl.RENDERBUFFER, gl.RENDERBUFFER_WIDTH),
      height: gl.getRenderbufferParameter(gl.RENDERBUFFER, gl.RENDERBUFFER_HEIGHT),
      format: gl.getRenderbufferParameter(gl.RENDERBUFFER, gl.RENDERBUFFER_INTERNAL_FORMAT),
      samples: webgl2 ? gl.getRenderbufferParameter(gl.RENDERBUFFER, gl.RENDERBUFFER_SAMPLES) : 0,
      errors: drain(),
    });
    const read = (rb, width, height, method, paint) => {
      const framebuffer = gl.createFramebuffer();
      gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
      gl.framebufferRenderbuffer(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, rb);
      const value = { framebufferStatus: gl.checkFramebufferStatus(gl.FRAMEBUFFER) };
      if (paint) { gl.clearColor(1, 0, 0, 1); gl.clear(gl.COLOR_BUFFER_BIT); }
      let resolve, resolveBuffer;
      if (method === "multisample4") {
        resolveBuffer = gl.createRenderbuffer(); gl.bindRenderbuffer(gl.RENDERBUFFER, resolveBuffer);
        gl.renderbufferStorage(gl.RENDERBUFFER, format, width, height);
        resolve = gl.createFramebuffer(); gl.bindFramebuffer(gl.DRAW_FRAMEBUFFER, resolve);
        gl.framebufferRenderbuffer(gl.DRAW_FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.RENDERBUFFER, resolveBuffer);
        gl.bindFramebuffer(gl.READ_FRAMEBUFFER, framebuffer);
        gl.blitFramebuffer(0, 0, width, height, 0, 0, width, height, gl.COLOR_BUFFER_BIT, gl.NEAREST);
        gl.bindFramebuffer(gl.FRAMEBUFFER, resolve);
      }
      const pixel = new Uint8Array(4);
      gl.readPixels(width - 1, height - 1, 1, 1, gl.RGBA, gl.UNSIGNED_BYTE, pixel);
      value.pixel = [...pixel]; value.errors = drain();
      gl.bindFramebuffer(gl.FRAMEBUFFER, null);
      if (resolve) gl.deleteFramebuffer(resolve);
      if (resolveBuffer) gl.deleteRenderbuffer(resolveBuffer);
      gl.deleteFramebuffer(framebuffer); gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
      return value;
    };
    const capture = phase => {
      const stage = { phase, limit: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE), rows: [] };
      drain();
      for (const method of modes) {
        const cases = [
          ["below", 63, 1], ["at-width", 64, 1], ["at-height", 1, 64],
          ["over-width", 65, 1], ["over-height", 1, 65],
          ["zero-width", 0, 1], ["zero-height", 1, 0], ["zero-over", 0, 65],
          ["negative-width", -1, 1], ["negative-height", 1, -1],
          ["invalid-target", 65, 1], ["unbound", 65, 1], ["invalid-format", 4, 4],
        ];
        if (method !== "storage") cases.push(["negative-samples", 65, 1]);
        for (const [name, width, height] of cases) {
          const rb = gl.createRenderbuffer(); gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
          invoke(method, 4, 4); const setupErrors = drain();
          const before = state(), beforePixel = read(rb, 4, 4, method, true);
          if (name === "unbound") gl.bindRenderbuffer(gl.RENDERBUFFER, null);
          invoke(method, width, height, name === "invalid-target" ? 0 : gl.RENDERBUFFER,
            name === "invalid-format" ? 0 : format, name === "negative-samples" ? -1 : undefined);
          const errors = drain();
          gl.bindRenderbuffer(gl.RENDERBUFFER, rb);
          const after = state();
          const row = { method, name, width, height, setupErrors, before, beforePixel, errors, after };
          if (after.width > 0 && after.height > 0) {
            // Failed allocations must retain the previous rendered pixels.
            row.afterPixel = read(rb, after.width, after.height, method, errors.length === 0);
          }
          stage.rows.push(row); gl.deleteRenderbuffer(rb);
        }
      }
      return stage;
    };
    let lose = gl.getExtension("WEBGL_lose_context");
    try {
      result.stages.push(capture("initial"));
      if (scope === "window" && lose) {
        const lost = new Promise(resolve => canvas.addEventListener("webglcontextlost", event => {
          event.preventDefault(); resolve();
        }, { once: true }));
        lose.loseContext(); await deadline(lost, 4000, "context loss event missing");
        // Restore from a later task after the cancellable loss event completes.
        await new Promise(resolve => setTimeout(resolve, 100));
        const restored = new Promise(resolve => canvas.addEventListener("webglcontextrestored", resolve, { once: true }));
        lose.restoreContext(); await deadline(restored, 5000, "context restore event missing");
        result.stages.push(capture("restored"));
        result.restored = true; lose = gl.getExtension("WEBGL_lose_context");
      } else if (scope === "window") result.restoreUnavailable = "WEBGL_lose_context unavailable";
    } catch (error) { result.error = errorValue(error); }
    finally { try { lose?.loseContext(); } catch {} canvas.remove?.(); }
    return result;
  };
  const scopes = {};
  const names = input.workerOnly ? ["worker"] : ["window", "offscreen"];
  for (const scope of names) {
    scopes[scope] = {};
    for (const api of ["webgl", "webgl2"]) {
      try {
        scopes[scope][api] = await run(scope, api);
      } catch (error) { scopes[scope][api] = { error: errorValue(error) }; }
    }
  }
  if (!input.workerOnly) {
    let worker, url;
    try {
      const source = `(${renderbufferAcceptanceProbe.toString()})({workerOnly:true}).then(value => postMessage(value), error => postMessage({error:String(error)}));`;
      url = URL.createObjectURL(new Blob([source], { type: "text/javascript" })); worker = new Worker(url);
      const result = await deadline(new Promise((resolve, reject) => {
        worker.onmessage = event => resolve(event.data); worker.onerror = event => reject(new Error(event.message));
      }), 12000, "worker allocation probe timed out");
      if (result.error) scopes.worker = { error: result.error };
      else scopes.worker = result.scopes.worker;
    } catch (error) { scopes.worker = { error: errorValue(error) }; }
    finally { worker?.terminate(); if (url) URL.revokeObjectURL(url); }
  }
  return { version: 1, scopes };
}

export function validateRenderbufferAcceptance(recordsById) {
  const checks = [], not_tested = [];
  const add = (scenario, name, passed, detail) => checks.push({ scenario, name, passed: Boolean(passed),
    acceptance_group: "webgl_resource_limits", ...(detail === undefined ? {} : { detail }) });
  const skip = (scenario, name, reason) => not_tested.push({ scenario, name, reason, acceptance_group: "webgl_resource_limits" });
  const goodPixel = value => value?.framebufferStatus === 36053 && value.errors?.length === 0 &&
    JSON.stringify(value.pixel) === "[255,0,0,255]";
  const sameState = (a, b) => a && b && ["width", "height", "format", "samples"].every(k => a[k] === b[k]) &&
    a.errors?.length === 0 && b.errors?.length === 0;
  const rowsFor = method => ["below", "at-width", "at-height", "over-width", "over-height",
    "zero-width", "zero-height", "zero-over", "negative-width", "negative-height",
    "invalid-target", "unbound", "invalid-format", ...(method === "storage" ? [] : ["negative-samples"])];
  const expectedSizes = [[63,1], [64,1], [1,64], [65,1], [1,65], [0,1], [1,0], [0,65], [-1,1], [1,-1], [65,1], [65,1], [4,4], [65,1]];
  for (const id of ["all_native", "gpu_custom"]) {
    const probe = recordsById?.[id]?.extra?.renderbuffer;
    add(id, "renderbuffer_probe_present", probe?.version === 1 && !!probe.scopes);
    for (const scope of ["window", "offscreen", "worker"]) for (const api of ["webgl", "webgl2"]) {
      const prefix = `${scope}_${api}_renderbuffer`, value = probe?.scopes?.[scope]?.[api];
      const native = recordsById?.all_native?.extra?.renderbuffer?.scopes?.[scope]?.[api];
      if (native?.supported === false && value?.supported === false) {
        skip(id, prefix, "Native context unavailable"); continue;
      }
      add(id, `${prefix}_context`, value?.supported === true && !value.error, value?.error);
      if (!value?.supported || value.error) continue;
      const phases = scope === "window" ? ["initial", "restored"] : ["initial"];
      if (scope === "window" && native?.restoreUnavailable && value.restoreUnavailable) {
        skip(id, `${prefix}_restoration`, native.restoreUnavailable); phases.pop();
      }
      const methods = api === "webgl" ? ["storage"] : ["storage", "multisample0", "multisample4"];
      if (api === "webgl2" && native?.unavailableMethods?.some(row => row.method === "multisample4")) {
        skip(id, `${prefix}_multisample4`, "Native RGBA8 sample count 4 unavailable"); methods.pop();
      }
      add(id, `${prefix}_stage_inventory`, Array.isArray(value.stages) && value.stages.length === phases.length &&
        phases.every(phase => value.stages.filter(stage => stage.phase === phase).length === 1));
      for (const phase of phases) {
        const stage = value.stages?.find(row => row.phase === phase);
        const nativeStage = native?.stages?.find(row => row.phase === phase);
        const title = `${prefix}_${phase}`;
        add(id, `${title}_bound`, Number.isInteger(stage?.limit) && stage.limit ===
          (id === "gpu_custom" ? Math.min(64, nativeStage?.limit) : nativeStage?.limit) && nativeStage?.limit >= 65);
        const expectedNames = methods.flatMap(method => rowsFor(method).map(name => `${method}:${name}`));
        const actualNames = stage?.rows?.map(row => `${row.method}:${row.name}`) ?? [];
        add(id, `${title}_case_inventory`, actualNames.length === expectedNames.length &&
          expectedNames.every(name => actualNames.filter(actual => actual === name).length === 1));
        for (const method of methods) for (const [index, name] of rowsFor(method).entries()) {
          const row = stage?.rows?.find(item => item.method === method && item.name === name);
          const [width, height] = expectedSizes[index];
          let expectedError = 0;
          if (name === "invalid-target" || name === "invalid-format") expectedError = 1280;
          else if (name === "unbound") expectedError = 1282;
          else if (name.startsWith("negative-") || (id === "gpu_custom" && (width > 64 || height > 64))) expectedError = 1281;
          const label = `${title}_${method}_${name}`;
          const errorsOK = JSON.stringify(row?.errors) === JSON.stringify(expectedError ? [expectedError] : []);
          add(id, `${label}_error`, row?.width === width && row?.height === height && errorsOK, row?.errors);
          add(id, `${label}_setup`, row?.setupErrors?.length === 0 && row.before?.width === 4 && row.before?.height === 4 &&
            row.before?.errors?.length === 0 && goodPixel(row.beforePixel));
          if (expectedError) {
            add(id, `${label}_old_storage_preserved`, sameState(row?.before, row?.after) && goodPixel(row?.afterPixel));
          } else {
            add(id, `${label}_dimensions`, row?.after?.width === width && row.after.height === height && row.after.errors?.length === 0);
            if (width > 0 && height > 0) add(id, `${label}_actual_render`, goodPixel(row?.afterPixel));
          }
        }
      }
    }
  }
  return { checks, not_tested };
}
