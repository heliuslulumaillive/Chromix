// Original captures are supplied as data. This module does not reproduce a
// production hash, PRNG or reference-selection algorithm to make its oracle.
export async function referenceAcceptanceProbe(input = {}) {
  const out = { version: 1, errors: [], cssNodes: [], text: [] };
  if (input.offset) {
    out.offset = [];
    for (const [width, height] of [[120, 80], [50, 50]]) {
      const node = document.createElement("div");
      node.style.cssText = `position:absolute;left:0;top:0;box-sizing:content-box;width:${width}px;height:${height}px;padding:0;border:0;font:16px Arial;transform:none`;
      document.body.append(node);
      const sample = () => {
        const rect = node.getBoundingClientRect();
        return { offsetWidth: node.offsetWidth, offsetHeight: node.offsetHeight,
          clientWidth: node.clientWidth, clientHeight: node.clientHeight,
          rectWidth: rect.width, rectHeight: rect.height };
      };
      out.offset.push({ width, height, first: sample(), repeat: sample() });
      node.remove();
    }
  }
  if (input.textRows) {
    const canvas = document.createElement("canvas"), context = canvas.getContext("2d");
    for (const row of input.textRows) {
      try {
        await document.fonts.load(row.font, row.text);
        const node = document.createElement("span"), nodeId = `reference-text-${row.index}`;
        node.id = nodeId; node.textContent = row.text;
        node.style.cssText = `position:absolute;white-space:pre;font:${row.font};`;
        document.body.append(node); out.cssNodes.push({ nodeId, family: row.family });
        context.font = row.font;
        const measure = () => {
          const metrics = context.measureText(row.text);
          return Object.fromEntries(Object.keys(row.expected).filter(key => Number.isFinite(metrics[key])).map(key => [key, metrics[key]]));
        };
        out.text.push({ index: row.index, nodeId, font: context.font, metrics: measure(), repeat: measure() });
      } catch (error) {
        out.text.push({ index: row.index, error: { name: error.name, message: String(error.message) } });
      }
    }
  }
  if (input.voices) {
    if (!("speechSynthesis" in globalThis)) out.voices = { supported: false, reason: "SpeechSynthesis unavailable" };
    else {
      const pack = () => speechSynthesis.getVoices().map(v => ({
        name: v.name, lang: v.lang, voiceURI: v.voiceURI, localService: v.localService, default: v.default,
      }));
      const start = performance.now(); let last = "", unchangedSince = start, events = 0, values = [];
      const changed = () => { events++; };
      speechSynthesis.addEventListener("voiceschanged", changed);
      try {
        do {
          values = pack();
          const next = JSON.stringify(values);
          if (next !== last) { last = next; unchangedSince = performance.now(); }
          // An empty expected filtered inventory must still wait for the full
          // native initialization window; an initial empty list proves nothing.
          if (values.length && performance.now() - unchangedSince >= 650) break;
          await new Promise(resolve => setTimeout(resolve, 100));
        } while (performance.now() - start < 3500);
        out.voices = { supported: true, values, events, elapsedMs: performance.now() - start,
          settled: performance.now() - unchangedSince >= 650, emptyWaitCompleted: !values.length && performance.now() - start >= 3500 };
      } finally { speechSynthesis.removeEventListener("voiceschanged", changed); }
    }
  }
  return out;
}

export function validateReferenceAcceptance(records, oracle) {
  const checks = [], not_tested = [];
  const groups = Object.fromEntries(["cloak_offset", "cloak_text", "cloak_voices"].map(name => [name, {}]));
  const add = (scenario, name, passed, group, detail) =>
    checks.push({ scenario, name, passed: passed === true, acceptance_group: group, ...(detail ? { detail } : {}) });
  const skip = (scenario, name, reason, group) => not_tested.push({ scenario, name, reason, acceptance_group: group });
  const reference = id => records[id]?.extra?.reference;
  const native = reference("all_native");
  const offset = reference("cloak_offset")?.offset;
  for (const [index, width, height] of [[0, 120, 80], [1, 50, 50]]) {
    const a = native?.offset?.[index], b = offset?.[index], original = a?.first, target = b?.first;
    add("cloak_offset", `offset_${width}_native_control`, !!original &&
      original.offsetWidth === width && original.offsetHeight === height &&
      original.clientWidth === width && original.clientHeight === height &&
      original.rectWidth === width && original.rectHeight === height, "cloak_offset");
    const expectedWidth = width + (width > oracle.cloakOffset.minimum_exclusive ? oracle.cloakOffset.width_delta : 0);
    const expectedHeight = height + (height > oracle.cloakOffset.minimum_exclusive ? oracle.cloakOffset.height_delta : 0);
    add("cloak_offset", `offset_${width}_captured_oracle`, !!target &&
      target.offsetWidth === expectedWidth && target.offsetHeight === expectedHeight &&
      target.clientWidth === width && target.clientHeight === height && target.rectWidth === width && target.rectHeight === height &&
      JSON.stringify(target) === JSON.stringify(b.repeat), "cloak_offset",
      { expectedWidth, expectedHeight, originalCapture: "seed12345 Arial offset deltas +40/-28; <=50 unchanged" });
  }

  const front = ["width", "actualBoundingBoxRight", "actualBoundingBoxAscent", "actualBoundingBoxDescent", "actualBoundingBoxLeft"];
  const textTarget = reference("cloak_text_positive");
  const familyOK = (probe, row) => {
    const fonts = probe?.cssPlatformFonts?.[`reference-text-${row.index}`]?.fonts;
    return Array.isArray(fonts) && fonts.some(f => f.glyphCount > 0) &&
      fonts.filter(f => f.glyphCount > 0).every(f => !f.isCustomFont && f.familyName?.toLowerCase() === row.family.toLowerCase());
  };
  let eligible = 0;
  for (const row of oracle.cloakTextWindows) {
    const source = native?.text?.find(value => value.index === row.index);
    const target = textTarget?.text?.find(value => value.index === row.index);
    const qualified = source && !source.error && source.font === row.font && familyOK(native, row) &&
      front.every(key => source.metrics?.[key] === row.expected[key]);
    const exposed = Object.keys(row.expected).filter(key => Number.isFinite(source?.metrics?.[key]));
    const observable = qualified && exposed.some(key => !front.includes(key) && source.metrics[key] !== row.expected[key]);
    if (!qualified || !observable) {
      skip("cloak_text_positive", `text_reference_${row.index}`, !qualified
        ? "Exact Windows font, glyph family or native first-five metrics did not match this captured row"
        : "Native metrics already equal this reference; this row cannot prove replacement occurred", "cloak_text");
      if (source?.metrics && target?.metrics && !qualified)
        add("cloak_text_positive", `text_unqualified_${row.index}_native_fallback`,
          exposed.length >= front.length && exposed.every(key => source.metrics[key] === target.metrics[key]), undefined);
      if (qualified && !observable)
        add("cloak_text_positive", `text_reference_${row.index}_indistinguishable_output_preserved`,
          !!target && !target.error && exposed.every(key => target.metrics?.[key] === source.metrics[key] &&
            target.repeat?.[key] === source.metrics[key]), undefined);
      continue;
    }
    eligible++;
    add("cloak_text_positive", `text_reference_${row.index}_eligible_replacement`,
      !!target && !target.error && target.font === row.font && familyOK(textTarget, row) &&
      exposed.every(key => target.metrics?.[key] === row.expected[key] &&
        target.repeat?.[key] === row.expected[key]), "cloak_text", { record_sha256: row.record_sha256, native: source.metrics, expected: row.expected });
  }
  // Alternative candidate rows are opportunities, not individually required:
  // a real distinguishable hit is sufficient for this strict-guard strategy.
  if (eligible > 0) for (const entry of not_tested)
    if (entry.acceptance_group === "cloak_text") entry.optional_candidate = true;

  const sourceVoices = native?.voices, targetVoices = reference("cloak_voices")?.voices;
  const withoutLocal = reference("cloak_voices_no_local")?.voices;
  if (!sourceVoices?.supported || !sourceVoices.settled || !sourceVoices.values?.length) {
    skip("cloak_voices", "native_voice_inventory", "No settled, nonempty native voice inventory; voice names cannot manufacture a backend", "cloak_voices");
  } else {
    const locale = records.cloak_voices?.voice_oracle_locale;
    const referenceVoices = oracle.cloakVoicesWindowsByLocale?.[locale];
    const matchingTargetFlags = typeof locale === "string" &&
      records.cloak_voices?.flags?.includes(`--fingerprint-locale=${locale}`) &&
      records.cloak_voices_no_local?.voice_oracle_locale === locale &&
      records.cloak_voices_no_local?.flags?.includes(`--fingerprint-locale=${locale}`);
    add("cloak_voices", "captured_voice_locale_bound", !!referenceVoices?.length && matchingTargetFlags,
      "cloak_voices", { locale, availableOriginalCaptures: Object.keys(oracle.cloakVoicesWindowsByLocale || {}) });
    const nativeValues = sourceVoices.values, selected = [], seen = new Set();
    for (const expected of referenceVoices || []) {
      const index = nativeValues.findIndex(v => v.name === expected.name && v.lang.toLowerCase() === expected.lang.toLowerCase());
      if (index >= 0 && !seen.has(index)) { seen.add(index); selected.push(nativeValues[index]); }
    }
    if (!selected.length) skip("cloak_voices", "eligible_reference_intersection",
      "No actual native voice matches the selected original Windows locale reference", "cloak_voices");
    else {
      add("cloak_voices", "native_voice_intersection_and_metadata",
        targetVoices?.supported && targetVoices.settled && JSON.stringify(targetVoices.values) === JSON.stringify(selected),
        "cloak_voices", { selectedCount: selected.length, nativeCount: nativeValues.length });
      const filtered = selected.filter(v => !v.localService);
      if (!selected.some(v => v.localService) && JSON.stringify(selected) === JSON.stringify(nativeValues))
        skip("cloak_voices_no_local", "observable_voice_filter", "Native inventory already equals reference and contains no local voice to filter", "cloak_voices");
      else add("cloak_voices_no_local", "observable_voice_filter",
        withoutLocal?.supported && withoutLocal.settled &&
        (filtered.length > 0 || (withoutLocal.emptyWaitCompleted && withoutLocal.events > 0)) &&
        JSON.stringify(withoutLocal.values) === JSON.stringify(filtered), "cloak_voices",
        { expectedCount: filtered.length, originalLocalVoices: selected.filter(v => v.localService).length });
    }
  }
  const positive_acceptance = Object.fromEntries(Object.keys(groups).map(group => {
    const actual = checks.filter(c => c.acceptance_group === group);
    const skipped = not_tested.filter(c => c.acceptance_group === group && !c.optional_candidate);
    const failed = actual.some(c => !c.passed), passed = actual.filter(c => c.passed).length;
    return [group, { status: failed ? "failed" : !actual.length || skipped.length ? "not_tested" : "observed_pass",
      reason: `${passed}/${actual.length} eligible checks passed; ${skipped.length} required conditions unavailable`,
      eligible_count: actual.length, passed_count: passed }];
  }));
  return { checks, not_tested, positive_acceptance };
}
