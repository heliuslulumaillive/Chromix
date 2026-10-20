// Browser entrypoint must stay closure-free: the controller serializes it over
// raw CDP. Query results retain only the selected, ordinary test font families.
export async function fontAcceptanceProbe(input = {}) {
  const families = ["Arial", "Consolas", "Times New Roman", "Comic Sans MS",
    "Gabriola", "Ink Free", "Bahnschrift", "DefinitelyAbsentFontXYZ"];
  const errorValue = error => ({
    name: error?.name || "Error", message: String(error?.message || error),
  });
  const bounded = async (promise, milliseconds, label) => {
    let timer;
    try {
      return await Promise.race([promise, new Promise((_, reject) => {
        timer = setTimeout(() => {
          const error = new Error(`${label} exceeded ${milliseconds} ms`);
          error.name = "TimeoutError";
          reject(error);
        }, milliseconds);
      })]);
    } finally { clearTimeout(timer); }
  };
  const result = {
    version: 1, scenario: input.scenario || "unspecified",
    secureContext: globalThis.isSecureContext === true,
    families, cssNodes: [], cssPlatformFonts: {}, errors: [],
  };

  // Invoke before the first await so the controller's userGesture is available.
  // Permission/visibility/policy failures remain explicit; no synthetic records.
  let localFontsPromise;
  if (typeof globalThis.queryLocalFonts !== "function") {
    localFontsPromise = Promise.resolve({ supported: false,
      reason: "queryLocalFonts is unavailable" });
  } else {
    try {
      localFontsPromise = bounded(globalThis.queryLocalFonts(), 8000, "queryLocalFonts")
        .then(records => {
          const selected = new Set(families.map(name => name.toLowerCase()));
          return {
            supported: true,
            records: records.filter(record =>
              selected.has(String(record.family).toLowerCase())).map(record => ({
              family: record.family, postscriptName: record.postscriptName,
              fullName: record.fullName, style: record.style,
            })),
          };
        }).catch(error => ({ supported: true, error: errorValue(error) }));
    } catch (error) {
      localFontsPromise = Promise.resolve({ supported: true, error: errorValue(error) });
    }
  }

  // This function is independently closure-free and reused inside the Worker.
  async function collectLocalFonts(selectedFamilies) {
    if (typeof FontFace !== "function")
      return { supported: false, reason: "FontFace is unavailable", entries: [] };
    const fontSet = typeof document === "object" ? document.fonts : globalThis.fonts;
    const entries = await Promise.all(selectedFamilies.map(async (family, index) => {
      const entry = { family, loaded: false };
      let timer;
      try {
        const alias = `OwnedLocalAcceptance${index}`;
        const face = new FontFace(alias, `local(${JSON.stringify(family)})`);
        await Promise.race([face.load(), new Promise((_, reject) => {
          timer = setTimeout(() => {
            const error = new Error("FontFace local load exceeded 5000 ms");
            error.name = "TimeoutError";
            reject(error);
          }, 5000);
        })]);
        entry.loaded = true;
        entry.status = face.status;
        if (fontSet && typeof fontSet.add === "function") {
          fontSet.add(face);
          const canvas = typeof document === "object"
            ? document.createElement("canvas")
            : typeof OffscreenCanvas === "function" ? new OffscreenCanvas(16, 16) : null;
          const context = canvas?.getContext("2d");
          if (context) {
            context.font = `32px "${alias}"`;
            entry.width = context.measureText("WWmmmmiii0123").width;
          }
        }
      } catch (error) {
        entry.error = { name: error?.name || "Error",
          message: String(error?.message || error) };
      } finally { clearTimeout(timer); }
      return entry;
    }));
    return { supported: true, entries };
  }

  const windowPromise = collectLocalFonts(families);
  let worker, workerURL;
  let workerPromise;
  if (typeof Worker !== "function" || typeof Blob !== "function") {
    workerPromise = Promise.resolve({ supported: false,
      reason: "Worker or Blob is unavailable", entries: [] });
  } else {
    try {
      const source = `self.onmessage = async event => {
        try {
          const collect = (${collectLocalFonts.toString()});
          self.postMessage(await collect(event.data));
        } catch (error) {
          self.postMessage({supported:true,error:{name:error.name,
            message:String(error.message || error)},entries:[]});
        }
      };`;
      workerURL = URL.createObjectURL(new Blob([source], { type: "application/javascript" }));
      worker = new Worker(workerURL);
      workerPromise = bounded(new Promise((resolve, reject) => {
        worker.onmessage = event => resolve(event.data);
        worker.onerror = event => reject(new Error(event.message || "Worker startup failed"));
        worker.postMessage(families);
      }), 8000, "font Worker").catch(error => ({
        supported: true, error: errorValue(error), entries: [],
      }));
    } catch (error) {
      workerPromise = Promise.resolve({ supported: true, error: errorValue(error), entries: [] });
    }
  }

  let root;
  try {
    document.getElementById("owned-font-acceptance-root")?.remove();
    root = document.createElement("div");
    root.id = "owned-font-acceptance-root";
    root.style.cssText = "position:absolute;left:0;top:0;white-space:nowrap;pointer-events:none;";
    document.body.append(root);
    for (const [index, family] of families.entries()) {
      const node = document.createElement("span");
      node.id = `owned-font-acceptance-${index}`;
      node.style.cssText = `display:inline-block;font:32px ${JSON.stringify(family)},monospace;white-space:nowrap;`;
      node.textContent = "WWmmmmiii0123";
      root.append(node, document.createElement("br"));
      const rect = node.getBoundingClientRect();
      result.cssNodes.push({ nodeId: node.id, family, text: node.textContent,
        width: rect.width, height: rect.height });
    }
  } catch (error) { result.errors.push({ stage: "cssNodes", ...errorValue(error) }); }

  result.ownedFont = { loaded: false, expectedWidth: 16 };
  if (!input.ownedFontURL) {
    result.ownedFont.unsupported = true;
    result.ownedFont.reason = "No original test-font URL supplied";
  } else if (typeof FontFace !== "function") {
    result.ownedFont.unsupported = true;
    result.ownedFont.reason = "FontFace is unavailable";
  } else {
    try {
      const face = new FontFace("OwnedDownloadedAcceptance",
        `url(${JSON.stringify(input.ownedFontURL)})`, { style: "normal", weight: "400" });
      await bounded(face.load(), 5000, "original downloaded font");
      document.fonts.add(face);
      result.ownedFont.loaded = true;
      result.ownedFont.status = face.status;
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      if (!context) throw new Error("2D context unavailable for original font");
      context.font = '20px "OwnedDownloadedAcceptance"';
      result.ownedFont.width = context.measureText("A").width;
      const node = document.createElement("span");
      node.id = "owned-font-acceptance-url";
      node.style.cssText = 'display:inline-block;font:20px "OwnedDownloadedAcceptance";white-space:nowrap;';
      node.textContent = "A";
      root?.append(node);
      const rect = node.getBoundingClientRect();
      result.ownedFont.cssWidth = rect.width;
      result.ownedFont.nodeId = node.id;
      result.cssNodes.push({ nodeId: node.id, family: "ChromixOwnedProbe",
        downloaded: true, text: "A", width: rect.width, height: rect.height });
    } catch (error) { result.ownedFont.error = errorValue(error); }
  }
  try {
    [result.windowLocal, result.workerLocal, result.localFonts] =
      await Promise.all([windowPromise, workerPromise, localFontsPromise]);
  } finally {
    worker?.terminate();
    if (workerURL) URL.revokeObjectURL(workerURL);
  }
  // CSS nodes remain alive for the controller's CSS.getPlatformFontsForNode.
  return result;
}

// Fixed expected observations from retained original binaries, not generated by
// the candidate C++ predicate. Availability must still be proved by this host's
// all_native run. Cloak's list is a recovered candidate table, not installed data.
export const FONT_ACCEPTANCE_ORACLE = Object.freeze({
  schema_version: 1,
  selected_families: ["Arial", "Consolas", "Times New Roman", "Comic Sans MS",
    "Gabriola", "Ink Free", "Bahnschrift"],
  shard: {
    fonts_shard42: { Arial: true, Consolas: true, "Times New Roman": true,
      "Comic Sans MS": false, Gabriola: true, "Ink Free": true, Bahnschrift: true },
    fonts_shard43: { Arial: true, Consolas: true, "Times New Roman": true,
      "Comic Sans MS": true, Gabriola: true, "Ink Free": false, Bahnschrift: true },
  },
  cloak: {
    scenario: "fonts_cloak10026", seed: "10026", platform: "windows",
    names: ["arial", "arial black", "calibri", "cambria", "cambria math",
      "comic sans ms", "consolas", "courier", "courier new", "franklin gothic medium",
      "georgia", "helvetica", "impact", "lucida console", "lucida sans unicode",
      "marlett", "microsoft sans serif", "ms gothic", "ms pgothic", "ms ui gothic",
      "palatino linotype", "segoe ui", "segoe ui emoji", "segoe ui light", "tahoma",
      "times new roman", "trebuchet ms", "verdana", "webdings", "wingdings"],
  },
  sources: [
    { path: "shardx-canvas-rea/full-study/identity-geometry-fonts/runs-v1/shard-fonts-42/result.json",
      sha256: "84c17decf74cfd1eac031c1cf32b5c769303c50782f69e79c611b378c263cbea" },
    { path: "shardx-canvas-rea/full-study/identity-geometry-fonts/runs-v1/shard-fonts-43/result.json",
      sha256: "424ae2d23f34dcade1c5a1a40c3f482c3c2f03a49ca271e52c2933d50f1ea489" },
    { path: "cloakbrowser-rea-20261020/deep/fonts-population-vectors.json",
      sha256: "bfb6a8e925bc86a26677bb3de359e1aeb6f1ed8de9f71c6711e23ca5f60b8502",
      selector: "representatives.zero", table_va: "0x193912d80" },
  ],
});

export function validateFontAcceptance(recordsById, oracle = FONT_ACCEPTANCE_ORACLE) {
  // A caller can append provenance without replacing fixed expectations.
  const expectedOracle = {
    ...FONT_ACCEPTANCE_ORACLE, ...oracle,
    shard: oracle.shard || FONT_ACCEPTANCE_ORACLE.shard,
    cloak: oracle.cloak || FONT_ACCEPTANCE_ORACLE.cloak,
  };
  const checks = [];
  const not_tested = [];
  const coverage = new Map();
  const baseline = recordsById?.all_native?.extra?.fonts;
  const normalize = name => String(name || "").toLowerCase();
  const selected = expectedOracle.selected_families;
  const scenarios = [
    ["fonts_shard42", "shard_fonts"], ["fonts_shard43", "shard_fonts"],
    ["fonts_cloak10026", "cloak_fonts"],
    ["fonts_allowlists", "font_allowlists"], ["fonts_empty", "font_allowlists"],
  ];
  const check = (scenario, group, name, passed, detail) => {
    checks.push({ scenario, acceptance_group: group, name, passed: !!passed,
      ...(detail === undefined ? {} : { detail }) });
  };
  const skip = (scenario, group, name, reason) => {
    // Missing individual candidates do not invalidate other observed positive
    // candidates. Required coverage gaps are emitted separately below.
    not_tested.push({ scenario, acceptance_group: group, name, reason,
      optional_candidate: true });
  };
  const recordCoverage = (scenario, channel, retained) => {
    const key = `${scenario}:${channel}`;
    const value = coverage.get(key) || { retained: 0, blocked: 0 };
    ++value[retained ? "retained" : "blocked"];
    coverage.set(key, value);
  };
  const localEntry = (probe, channel, family) =>
    probe?.[channel]?.entries?.find(entry => entry.family === family);
  const localOutcome = entry => {
    if (entry?.loaded === true && entry.status === "loaded") return true;
    // Local native lookup failures use NetworkError. Timeout/API/security
    // failures must never masquerade as a successful blocked-font observation.
    if (entry?.loaded === false && entry.error?.name === "NetworkError") return false;
    return null;
  };
  const nativeQueryReady = probe => probe?.localFonts?.supported === true &&
    !probe.localFonts.error && Array.isArray(probe.localFonts.records);
  const familyRecords = (probe, family) => nativeQueryReady(probe)
    ? probe.localFonts.records.filter(row => normalize(row.family) === normalize(family)) : null;
  const validMetadata = row => ["family", "postscriptName", "fullName", "style"]
    .every(key => typeof row?.[key] === "string" && row[key].length > 0);
  const signatures = rows => JSON.stringify(rows.map(row =>
    [row.family, row.postscriptName, row.fullName, row.style]).sort(
    (a, b) => JSON.stringify(a).localeCompare(JSON.stringify(b))));
  const cssRow = (probe, family) => {
    const node = probe?.cssNodes?.find(entry => !entry.downloaded && entry.family === family);
    const platform = node && probe?.cssPlatformFonts?.[node.nodeId];
    if (!node || platform?.error || !Array.isArray(platform?.fonts)) return null;
    return { node, fonts: platform.fonts.filter(font =>
      typeof font.familyName === "string" && font.glyphCount > 0) };
  };
  const expected = (scenario, family, channel) => {
    if (expectedOracle.shard[scenario]) return expectedOracle.shard[scenario][family];
    if (scenario === "fonts_cloak10026")
      return expectedOracle.cloak.names.includes(normalize(family));
    if (scenario === "fonts_empty") return false;
    if (scenario === "fonts_allowlists")
      return channel === "css" ? ["Arial", "Consolas"].includes(family) : family === "Arial";
    return undefined;
  };

  for (const [scenario, group] of scenarios) {
    const target = recordsById?.[scenario]?.extra?.fonts;
    for (const family of selected) {
      for (const channel of ["windowLocal", "workerLocal"]) {
        const baseEntry = localEntry(baseline, channel, family);
        if (baseline?.[channel]?.supported !== true || localOutcome(baseEntry) !== true) {
          skip(scenario, group, `${channel}:${family}`, "Native baseline cannot successfully load this local font in this scope");
          continue;
        }
        const allowed = expected(scenario, family, channel);
        if (typeof allowed !== "boolean") {
          skip(scenario, group, `${channel}:${family}`, "No independent original oracle for this family");
          continue;
        }
        recordCoverage(scenario, channel, allowed);
        const entry = localEntry(target, channel, family);
        check(scenario, group, `${channel}:${family}:${allowed ? "retained" : "blocked"}`,
          localOutcome(entry) === allowed, { expected_load: allowed,
            actual: entry || null, baseline_load: true });
      }
      const baseMetadata = familyRecords(baseline, family);
      if (!baseMetadata?.length || !baseMetadata.every(validMetadata)) {
        skip(scenario, group, `localFonts:${family}`, "Native enumeration unavailable, denied, empty, or missing valid records for this family");
      } else {
        const allowed = expected(scenario, family, "query");
        recordCoverage(scenario, "query", allowed);
        const metadata = familyRecords(target, family);
        check(scenario, group, `localFonts:${family}:${allowed ? "retained" : "blocked"}`,
          Array.isArray(metadata) && (allowed
            ? metadata.length > 0 && metadata.every(validMetadata) &&
              signatures(metadata) === signatures(baseMetadata)
            : metadata.length === 0),
          { expected_retained: allowed, baseline_records: baseMetadata.length,
            target_records: metadata?.length ?? null,
            error: target?.localFonts?.error || null });
      }
      const baseCSS = cssRow(baseline, family);
      if (!baseCSS?.fonts.some(font => normalize(font.familyName) === normalize(family))) {
        skip(scenario, group, `css:${family}`, "Native CSS/CDP did not establish actual glyphs from this family");
      } else {
        const allowed = expected(scenario, family, "css");
        recordCoverage(scenario, "css", allowed);
        const css = cssRow(target, family);
        const usesFamily = css?.fonts.some(font =>
          normalize(font.familyName) === normalize(family)) ?? null;
        check(scenario, group, `css:${family}:${allowed ? "retained" : "blocked"}`,
          !!css && Number.isFinite(css.node.width) && usesFamily === allowed &&
            (scenario === "fonts_empty" || css.fonts.length > 0),
          { expected_retained: allowed, actual_families: css?.fonts || null });
      }
    }

    for (const channel of ["windowLocal", "workerLocal"]) {
      const baseMissing = localEntry(baseline, channel, "DefinitelyAbsentFontXYZ");
      if (localOutcome(baseMissing) !== false) {
        skip(scenario, group, `${channel}:missing_name_not_fabricated`,
          "Negative-control name was not proved absent by the native baseline");
      } else {
        check(scenario, group, `${channel}:missing_name_not_fabricated`,
          localOutcome(localEntry(target, channel, "DefinitelyAbsentFontXYZ")) === false);
      }
    }
    const baseOwned = baseline?.ownedFont;
    if (baseOwned?.loaded !== true || baseOwned.error ||
        !Number.isFinite(baseOwned.width) || Math.abs(baseOwned.width - 16) > 1e-6) {
      skip(scenario, group, "downloaded_font_preserved",
        "Native baseline did not load and measure the original URL fixture");
    } else {
      const owned = target?.ownedFont;
      recordCoverage(scenario, "downloaded", true);
      check(scenario, group, "downloaded_font_preserved",
        owned?.loaded === true && !owned.error &&
          Number.isFinite(owned.width) && Math.abs(owned.width - 16) <= 1e-6 &&
          Number.isFinite(owned.cssWidth) && Math.abs(owned.cssWidth - 16) <= 1e-6,
        { actual: owned || null, expected_width: 16 });
    }
  }

  const positive_acceptance = {};
  for (const group of ["shard_fonts", "cloak_fonts", "font_allowlists"]) {
    const groupChecks = checks.filter(item => item.acceptance_group === group);
    const gaps = [];
    for (const [scenario, assigned] of scenarios) {
      if (assigned !== group) continue;
      for (const channel of ["windowLocal", "workerLocal", "query", "css"]) {
        const value = coverage.get(`${scenario}:${channel}`);
        if (!value?.blocked || (scenario !== "fonts_empty" && !value.retained))
          gaps.push(`${scenario}:${channel}:needs_actual_blocked_and_retained_inputs`);
      }
      if (!coverage.get(`${scenario}:downloaded`)?.retained)
        gaps.push(`${scenario}:downloaded_fixture_unavailable`);
    }
    for (const gap of gaps) {
      const [scenario, ...detail] = gap.split(":");
      not_tested.push({
        scenario, acceptance_group: group,
        name: `positive_coverage:${detail.join(":")}`,
        reason: "Actual native-available blocked/retained inputs, or the original downloaded-font fixture, were not established for this required scope",
      });
    }
    const failures = groupChecks.filter(item => !item.passed);
    positive_acceptance[group] = {
      status: failures.length ? "failed" : gaps.length ? "not_tested" : "observed_pass",
      reason: failures.length ? `${failures.length} eligible checks failed`
        : gaps.length ? gaps.join("; ")
        : "Actual native-available fonts were retained and blocked in Window, Worker, CSS and Local Font Access; the URL font remained intact",
      eligible_count: groupChecks.length,
      passed_count: groupChecks.filter(item => item.passed).length,
    };
  }
  return { checks, not_tested, positive_acceptance };
}
