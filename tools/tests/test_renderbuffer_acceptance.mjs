import test from "node:test";
import assert from "node:assert/strict";
import { validateRenderbufferAcceptance } from "../fingerprint_renderbuffer_acceptance.mjs";

// Synthetic validator fixtures are not GPU evidence. Actual driver observations
// are collected separately by the serializable acceptance probe.
function fixture() {
  const records = {};
  const pixel = () => ({ framebufferStatus: 36053, pixel: [255, 0, 0, 255], errors: [] });
  for (const id of ["all_native", "gpu_custom"]) {
    const probe = { version: 1, scopes: {} };
    records[id] = { extra: { renderbuffer: probe } };
    for (const scope of ["window", "offscreen", "worker"]) {
      probe.scopes[scope] = {};
      for (const api of ["webgl", "webgl2"]) {
        const value = { supported: true, stages: [], unavailableMethods: [], restored: scope === "window" };
        probe.scopes[scope][api] = value;
        const methods = api === "webgl" ? ["storage"] : ["storage", "multisample0", "multisample4"];
        for (const phase of scope === "window" ? ["initial", "restored"] : ["initial"]) {
          const stage = { phase, limit: id === "gpu_custom" ? 64 : 16384, rows: [] };
          value.stages.push(stage);
          for (const method of methods) {
            const cases = [
              ["below", 63, 1, 0], ["at-width", 64, 1, 0], ["at-height", 1, 64, 0],
              ["over-width", 65, 1, id === "gpu_custom" ? 1281 : 0],
              ["over-height", 1, 65, id === "gpu_custom" ? 1281 : 0],
              ["zero-width", 0, 1, 0], ["zero-height", 1, 0, 0],
              ["zero-over", 0, 65, id === "gpu_custom" ? 1281 : 0],
              ["negative-width", -1, 1, 1281], ["negative-height", 1, -1, 1281],
              ["invalid-target", 65, 1, 1280], ["unbound", 65, 1, 1282], ["invalid-format", 4, 4, 1280],
            ];
            if (method !== "storage") cases.push(["negative-samples", 65, 1, 1281]);
            for (const [name, width, height, error] of cases) {
              const before = { width: 4, height: 4, format: api === "webgl" ? 32854 : 32856,
                samples: method === "multisample4" ? 4 : 0, errors: [] };
              stage.rows.push({ name, method, width, height, setupErrors: [], before, beforePixel: pixel(),
                errors: error ? [error] : [], after: error ? { ...before } : { ...before, width, height },
                ...((error || width > 0 && height > 0) ? { afterPixel: pixel() } : {}) });
            }
          }
        }
      }
    }
  }
  return records;
}
const row = (record, name = "over-width") => record.gpu_custom.extra.renderbuffer.scopes.worker.webgl2.stages[0].rows
  .find(value => value.method === "multisample4" && value.name === name);
const failures = record => validateRenderbufferAcceptance(record).checks.filter(value => !value.passed);

test("complete coherent allocation evidence passes both native and constrained scopes", () => {
  const result = validateRenderbufferAcceptance(fixture());
  assert.equal(result.checks.length, 1482);
  assert.equal(result.not_tested.length, 0);
  assert.equal(result.checks.filter(value => !value.passed).length, 0);
});

test("the observed getter-only bug is rejected even if previous pixels look correct", () => {
  const record = fixture();
  row(record).errors = [];
  row(record).after.width = 65;
  assert.equal(failures(record).length, 2);
});

test("an error code alone cannot conceal replaced storage or discarded pixels", () => {
  for (const mutation of [value => { value.after.width = 65; }, value => { value.afterPixel.pixel[0] = 0; },
    value => { value.after.samples = 0; }, value => { value.afterPixel.errors = [1282]; }]) {
    const record = fixture(); mutation(row(record));
    assert.ok(failures(record).some(value => value.name.endsWith("_old_storage_preserved")));
  }
});

test("failure to render at the advertised boundary is a real failure", () => {
  const record = fixture();
  row(record, "at-width").afterPixel.framebufferStatus = 36054;
  assert.ok(failures(record).some(value => value.name.endsWith("_actual_render")));
});

test("omitted cases, Worker evidence, and context restoration cannot silently pass", () => {
  for (const mutation of [record => row(record).name = "duplicate",
    record => { delete record.gpu_custom.extra.renderbuffer.scopes.worker; },
    record => record.gpu_custom.extra.renderbuffer.scopes.window.webgl.stages.pop(),
    record => { delete record.gpu_custom.extra.renderbuffer; }]) {
    const record = fixture(); mutation(record);
    assert.ok(failures(record).length > 0);
  }
});

test("zero-sized storage is valid but does not waive a too-large other dimension", () => {
  const record = fixture();
  row(record, "zero-width").errors = [1281];
  row(record, "zero-over").errors = [];
  assert.ok(failures(record).some(value => value.name.includes("zero-width_error")));
  assert.ok(failures(record).some(value => value.name.includes("zero-over_error")));
});

test("native capability absence is recorded as not tested, never fabricated success", () => {
  const record = fixture();
  for (const id of ["all_native", "gpu_custom"]) record[id].extra.renderbuffer.scopes.worker.webgl2 = { supported: false };
  const result = validateRenderbufferAcceptance(record);
  assert.equal(result.not_tested.length, 2);
  assert.ok(result.not_tested.every(value => value.acceptance_group === "webgl_resource_limits"));
});
