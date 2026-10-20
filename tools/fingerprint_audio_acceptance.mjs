// Synthetic, silent audio acceptance. The browser entrypoint is closure-free
// so the harness can serialize audioAcceptanceProbe.toString() over raw CDP.
export async function audioAcceptanceProbe(input = {}) {
  const result = {
    version: 1,
    scenario: input.scenario || "unspecified",
    scope: "Zero-signal realtime clock and synthetic offline PCM; no capture or speech",
    contexts: [],
    referenceCases: [],
    errors: [],
  };
  const errorValue = error => ({
    name: error?.name || "Error",
    message: String(error?.message || error),
  });
  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
  const bounded = async (promise, ms, label) => {
    let timer;
    try {
      return await Promise.race([
        promise,
        new Promise((_, reject) => {
          timer = setTimeout(() => {
            const error = new Error(`${label} exceeded ${ms} ms`);
            error.name = "TimeoutError";
            reject(error);
          }, ms);
        }),
      ]);
    } finally {
      clearTimeout(timer);
    }
  };
  const digest = async array => {
    const bytes = new Uint8Array(array.buffer, array.byteOffset, array.byteLength);
    return Array.from(new Uint8Array(
      await crypto.subtle.digest("SHA-256", bytes)),
    byte => byte.toString(16).padStart(2, "0")).join("");
  };
  const explicitObjects = async () => {
    const out = {};
    try {
      const offline = new OfflineAudioContext(6, 256, 44100);
      const rendered = await bounded(offline.startRendering(), 5000, "offline explicit");
      out.offline = {
        sampleRate: rendered.sampleRate,
        numberOfChannels: rendered.numberOfChannels,
        length: rendered.length,
        state: offline.state,
      };
    } catch (error) { out.offline = { error: errorValue(error) }; }
    try {
      const buffer = new AudioBuffer({
        numberOfChannels: 6, length: 16, sampleRate: 32000,
      });
      out.buffer = {
        sampleRate: buffer.sampleRate,
        numberOfChannels: buffer.numberOfChannels,
        length: buffer.length,
      };
    } catch (error) { out.buffer = { error: errorValue(error) }; }
    return out;
  };
  if (input.afterGC) {
    result.afterGC = { explicit: await explicitObjects() };
    let worker, url;
    try {
      url = URL.createObjectURL(new Blob([
        "self.onmessage = event => self.postMessage({echo: event.data, alive: true});",
      ], { type: "application/javascript" }));
      worker = new Worker(url);
      const response = new Promise((resolve, reject) => {
        worker.onmessage = event => resolve(event.data);
        worker.onerror = event => reject(new Error(event.message || "worker error"));
      });
      worker.postMessage("audio-after-gc");
      result.afterGC.worker = await bounded(response, 3000, "after-GC worker");
    } catch (error) {
      result.afterGC.worker = { error: errorValue(error) };
    } finally {
      worker?.terminate();
      if (url) URL.revokeObjectURL(url);
    }
    return result;
  }

  // The failure fixture must not bypass the configured default with a fake
  // AudioContext wrapper or another constructor. Offline objects stay separate.
  if (input.scenario === "audio_default_32") {
    result.constructionFailures = [];
    for (let attempt = 0; attempt < 3; ++attempt) {
      let context;
      const row = { attempt };
      try {
        context = new AudioContext();
        Object.assign(row, {
          created: true, sampleRate: context.sampleRate,
          channelCount: context.destination.channelCount,
          maxChannelCount: context.destination.maxChannelCount,
        });
      } catch (error) {
        Object.assign(row, { created: false, error: errorValue(error) });
      } finally {
        if (context) {
          try {
            await bounded(context.close(), 3000, "close default32");
            row.closed = context.state === "closed";
          } catch (error) { row.closeError = errorValue(error); }
        }
      }
      result.constructionFailures.push(row);
      await delay(50);
    }
    result.explicit = await explicitObjects();
    result.needGC = true;
    return result;
  }

  const moduleSource = `
    class FingerprintAudioAcceptance extends AudioWorkletProcessor {
      constructor() { super(); this.sent = 0; }
      process(inputs, outputs) {
        for (const output of outputs) for (const channel of output) channel.fill(0);
        if (this.sent++ < 3) this.port.postMessage({
          sampleRate, currentFrame, currentTime,
          frames: outputs[0]?.[0]?.length || 0,
          inputChannels: inputs[0]?.length || 0,
          outputChannels: outputs[0]?.length || 0
        });
        return true;
      }
    }
    registerProcessor("fingerprint-audio-acceptance", FingerprintAudioAcceptance);
  `;
  for (const [id, options] of [["default", {}], ["explicit48000", { sampleRate: 48000 }]]) {
    const row = { id, options, setterResults: [] };
    let context, node, mute, objectURL;
    try {
      context = new AudioContext(options);
      Object.assign(row, {
        sampleRate: context.sampleRate,
        channelCount: context.destination.channelCount,
        maxChannelCount: context.destination.maxChannelCount,
        baseLatency: context.baseLatency,
      });
      row.worklet = { messages: [] };
      try {
        const sourceURL = input.workletURL || (objectURL = URL.createObjectURL(
          new Blob([moduleSource], { type: "application/javascript" })));
        await bounded(context.audioWorklet.addModule(sourceURL), 5000, "worklet module");
        node = new AudioWorkletNode(
          context, input.workletName || "fingerprint-audio-acceptance",
          { numberOfInputs: 0, numberOfOutputs: 1, outputChannelCount: [1] });
        const threeFrames = new Promise((resolve, reject) => {
          node.port.onmessage = event => {
            const value = event.data;
            if (value && Number.isFinite(value.sampleRate) &&
                Number.isFinite(value.currentFrame) && Number.isFinite(value.currentTime)) {
              row.worklet.messages.push(value);
              if (row.worklet.messages.length >= 3) resolve();
            }
          };
          node.onprocessorerror = () => reject(new Error("AudioWorklet processor error"));
        });
        mute = context.createGain();
        mute.gain.value = 0;
        node.connect(mute);
        mute.connect(context.destination);
        await bounded(Promise.all([context.resume(), threeFrames]), 6000, "worklet clock");
        row.worklet.actualContextRate = context.sampleRate;
        row.worklet.state = context.state;
      } catch (error) { row.worklet.error = errorValue(error); }

      // These are real destination setters. Test after collecting the initial
      // context/worklet values and while the sole signal is identically zero.
      if (id === "default") {
        const attempts = Array.from(new Set([
          1, Math.min(row.maxChannelCount, 2), row.maxChannelCount,
          row.maxChannelCount < 32 ? row.maxChannelCount + 1 : 33,
        ])).filter(value => Number.isInteger(value) && value > 0);
        for (const requested of attempts) {
          const previous = context.destination.channelCount;
          try {
            context.destination.channelCount = requested;
            row.setterResults.push({
              requested, previous, actual: context.destination.channelCount,
              maxChannelCount: context.destination.maxChannelCount,
            });
          } catch (error) {
            row.setterResults.push({
              requested, previous, actual: context.destination.channelCount,
              error: errorValue(error),
            });
          }
        }
      }
    } catch (error) { row.error = errorValue(error); }
    finally {
      try { node?.disconnect(); mute?.disconnect(); node?.port.close(); } catch {}
      if (context) {
        try {
          await bounded(context.close(), 3000, "close realtime context");
          row.closed = context.state === "closed";
        } catch (error) { row.closeError = errorValue(error); }
      }
      if (objectURL) URL.revokeObjectURL(objectURL);
    }
    result.contexts.push(row);
  }
  result.explicit = await explicitObjects();

  if (["all_native", "cloak_audio_positive"].includes(input.scenario)) {
    const common = {
      sampleRate: 44100, channels: 1, type: "triangle", frequency: 10000,
      threshold: -50, knee: 40, ratio: 12, attack: 0, release: 0.25,
      compressor: true, bypass: false, doubleDest: false,
    };
    const defaults = [
      { id: "match_5000", length: 5000 },
      { id: "match_1000", length: 1000, frequency: 9998.123, threshold: -52, attack: 0.0001 },
      { id: "match_44100", length: 44100 },
    ];
    const specs = (input.audioReferenceSpecs || defaults).map(spec => ({ ...common, ...spec }));
    for (const spec of specs) {
      const row = { id: spec.id || `match_${spec.length}`, spec };
      let oscillator, compressor, analyser;
      try {
        if (!Number.isInteger(spec.length) || spec.length < 1 || spec.length > 44100 ||
            spec.sampleRate !== 44100 || spec.channels !== 1 ||
            spec.compressor === false || spec.bypass || spec.doubleDest) {
          throw new Error("Reference acceptance only permits the known mono 44100 Hz fixtures");
        }
        const context = new OfflineAudioContext(spec.channels, spec.length, spec.sampleRate);
        oscillator = context.createOscillator();
        oscillator.type = spec.type;
        oscillator.frequency.value = spec.frequency;
        analyser = context.createAnalyser();
        analyser.fftSize = 2048;
        compressor = context.createDynamicsCompressor();
        for (const key of ["threshold", "knee", "ratio", "attack", "release"]) {
          compressor[key].value = spec[key];
        }
        oscillator.connect(compressor);
        compressor.connect(analyser);
        analyser.connect(context.destination);
        oscillator.start();
        const rendered = await bounded(context.startRendering(), 6000, "reference offline render");
        const samples = new Float32Array(rendered.getChannelData(0));
        const copy = new Float32Array(rendered.length);
        rendered.copyFromChannel(copy, 0);
        Object.assign(row, {
          sampleRate: rendered.sampleRate,
          numberOfChannels: rendered.numberOfChannels,
          length: rendered.length,
          byteLength: samples.byteLength,
          sha256: await digest(samples),
          copySha256: await digest(copy),
          actualFrequency: oscillator.frequency.value,
          actualCompressor: Object.fromEntries(
            ["threshold", "knee", "ratio", "attack", "release"]
              .map(key => [key, compressor[key].value])),
          state: context.state,
        });
      } catch (error) { row.error = errorValue(error); }
      finally {
        try { oscillator?.disconnect(); compressor?.disconnect(); analyser?.disconnect(); } catch {}
      }
      result.referenceCases.push(row);
    }
  }
  return result;
}

export function validateAudioAcceptance(recordsById, oracle = {}) {
  const checks = [], not_tested = [];
  const groups = Object.fromEntries(["audio_defaults", "audio_channels", "cloak_audio"]
    .map(name => [name, { eligible_count: 0, passed_count: 0, failed: 0, skipped: 0 }]));
  const audio = id => recordsById?.[id]?.extra?.audio;
  const context = (id, which = "default") => audio(id)?.contexts?.find(row => row.id === which);
  const baseline = context("all_native");
  const capacity = baseline?.maxChannelCount;
  const add = (scenario, name, passed, detail, group) => {
    checks.push({ scenario, name, passed: Boolean(passed), ...(detail ? { detail } : {}),
      ...(group ? { acceptance_group: group } : {}) });
    if (group) {
      groups[group].eligible_count++;
      if (passed) groups[group].passed_count++;
      else groups[group].failed++;
    }
  };
  const skip = (scenario, name, reason, group) => {
    not_tested.push({ scenario, name, reason,
      ...(group ? { acceptance_group: group } : {}) });
    if (group) groups[group].skipped++;
  };
  const clockOK = (row, rate) => {
    const messages = row?.worklet?.messages;
    if (!row || row.error || row.worklet?.error || !Array.isArray(messages) ||
        messages.length < 3 || row.sampleRate !== rate ||
        row.worklet.actualContextRate !== rate) return false;
    return messages.every((m, index) => {
      if (m.sampleRate !== rate || !Number.isInteger(m.currentFrame) ||
          !Number.isFinite(m.currentTime) || !Number.isInteger(m.frames) || m.frames <= 0 ||
          Math.abs(m.currentTime - m.currentFrame / rate) > 1e-9) return false;
      if (index === 0) return true;
      const previous = messages[index - 1];
      const frames = m.currentFrame - previous.currentFrame;
      const seconds = m.currentTime - previous.currentTime;
      return frames === previous.frames && seconds > 0 &&
        Math.abs(seconds - frames / rate) <= 1e-9;
    });
  };
  const explicitOK = row =>
    row?.offline?.sampleRate === 44100 && row.offline.numberOfChannels === 6 &&
    row.offline.length === 256 && row.offline.state === "closed" &&
    row?.buffer?.sampleRate === 32000 && row.buffer.numberOfChannels === 6 &&
    row.buffer.length === 16 && !row.offline.error && !row.buffer.error;
  const baselineClock = clockOK(baseline, baseline?.sampleRate);
  const baselineBackend = baseline && !baseline.error &&
    Number.isFinite(baseline.sampleRate) && baseline.sampleRate >= 3000 &&
    baseline.sampleRate <= 768000;
  for (const [scenario, rate] of [
    ["audio_defaults_44100", 44100], ["audio_defaults_32000", 32000],
  ]) {
    if (!baselineBackend) {
      skip(scenario, "default_sample_rate_and_worklet",
          "Native baseline did not establish an available realtime audio backend", "audio_defaults");
      if (audio(scenario)) {
        add(scenario, "offline_and_buffer_explicit_values_kept",
            explicitOK(audio(scenario).explicit),
            "Explicit offline objects remain testable without a realtime backend", "audio_defaults");
      } else {
        skip(scenario, "offline_and_buffer_explicit_values_kept",
            "Scenario result missing", "audio_defaults");
      }
      continue;
    }
    const actual = context(scenario);
    if (!actual) {
      skip(scenario, "default_sample_rate_and_worklet", "Scenario or context result missing", "audio_defaults");
      continue;
    }
    add(scenario, "default_sample_rate", actual.sampleRate === rate && !actual.error,
        `expected=${rate}; actual=${actual.sampleRate}; error=${actual.error?.name || "none"}`,
        "audio_defaults");
    if (!baselineClock) {
      skip(scenario, "worklet_clock", "Native baseline did not establish a working realtime Worklet clock",
          "audio_defaults");
    } else {
      add(scenario, "worklet_clock", clockOK(actual, rate),
          "Requires context/global rate agreement and positive frame/time progression", "audio_defaults");
    }
    const explicit = context(scenario, "explicit48000");
    add(scenario, "explicit_context_rate_kept", explicit?.sampleRate === 48000 && !explicit?.error,
        "AudioContext({sampleRate:48000}) must keep its explicit value", "audio_defaults");
    if (baselineClock) {
      add(scenario, "explicit_worklet_clock_kept", clockOK(explicit, 48000),
          "Explicit graph rate must propagate to Worklet", "audio_defaults");
    }
    add(scenario, "offline_and_buffer_explicit_values_kept", explicitOK(audio(scenario)?.explicit),
        "Offline44100 and AudioBuffer32000 both retain six explicitly requested channels", "audio_defaults");
    add(scenario, "realtime_contexts_closed", actual.closed === true && explicit?.closed === true,
        "Both realtime contexts must close normally", "audio_defaults");
  }

  for (const [scenario, limit, requestedDefault] of [
    ["audio_defaults_44100", 1, null],
    ["audio_defaults_32000", 6, null],
    ["audio_default_mono", null, 1],
    ["audio_default_stereo", null, 2],
  ]) {
    if (!Number.isInteger(capacity) || capacity < 1 || baseline?.error) {
      skip(scenario, "channel_policy", "No valid native backend capacity baseline", "audio_channels");
      continue;
    }
    if (requestedDefault > capacity) {
      skip(scenario, "channel_policy", "Native backend cannot support this positive default-count case",
          "audio_channels");
      continue;
    }
    const row = context(scenario);
    if (!row) {
      skip(scenario, "channel_policy", "Scenario result missing", "audio_channels");
      continue;
    }
    const maximum = limit === null ? capacity : Math.min(capacity, limit);
    const initial = requestedDefault === null
      ? Math.min(baseline.channelCount, maximum) : requestedDefault;
    add(scenario, "backend_bounded_capacity_and_initial_count",
        !row.error && row.maxChannelCount === maximum && row.channelCount === initial,
        `nativeCapacity=${capacity}; expectedMaximum=${maximum}; expectedInitial=${initial}`,
        "audio_channels");
    const setters = row.setterResults;
    add(scenario, "allowed_setter_applies",
        Array.isArray(setters) && setters.some(s => s.requested === 1 && !s.error && s.actual === 1),
        "A real allowed destination setter must succeed", "audio_channels");
    add(scenario, "above_capacity_setter_rejects",
        Array.isArray(setters) && setters.some(s => s.requested > maximum &&
          ["IndexSizeError", "NotSupportedError"].includes(s.error?.name) && s.actual === s.previous),
        "Rejected setter must preserve the previous actual channelCount", "audio_channels");
    add(scenario, "context_closed", row.closed === true, "Realtime context closed", "audio_channels");
  }
  const large = audio("audio_default_32");
  if (!(Number.isInteger(capacity) && capacity > 0 && capacity < 32)) {
    skip("audio_default_32", "unsupported_default_count", "Native capacity is absent or does not make 32 unsupported",
        "audio_channels");
  } else if (!large) {
    skip("audio_default_32", "unsupported_default_count", "Scenario result missing", "audio_channels");
  } else {
    const failures = large.constructionFailures;
    add("audio_default_32", "unsupported_default_count_repeatedly_rejects",
        Array.isArray(failures) && failures.length === 3 &&
          failures.every(row => row.created === false && row.error?.name === "NotSupportedError"),
        "Three unmodified AudioContext constructors must reject the unsupported environment default",
        "audio_channels");
    add("audio_default_32", "independent_explicit_objects_survive",
        explicitOK(large.explicit), "Offline and AudioBuffer remain independent", "audio_channels");
    const afterRecord = recordsById?.audio_default_32?.extra?.audioAfterGC;
    const after = afterRecord?.afterGC || afterRecord || large.afterGC;
    if (!after) {
      skip("audio_default_32", "post_gc_liveness", "Harness has not supplied the post-CDP-GC follow-up",
          "audio_channels");
    } else {
      add("audio_default_32", "post_gc_liveness",
          after.worker?.alive === true && after.worker.echo === "audio-after-gc" &&
            explicitOK(after.explicit), "Worker echo and explicit offline objects after GC/event-loop",
          "audio_channels");
    }
  }

  const pairs = oracle.audioReferencePairs || oracle.pairs || [];
  const sourceCases = audio("all_native")?.referenceCases || [];
  const targetCases = audio("cloak_audio_positive")?.referenceCases || [];
  if (!Array.isArray(pairs) || pairs.length === 0) {
    skip("cloak_audio_positive", "reference_bank", "Pinned native/reference hash pairs were not provided", "cloak_audio");
  } else {
    for (const pair of pairs) {
      const find = rows => rows.find(row =>
        (row.spec?.length ?? row.length) === pair.length);
      const source = find(sourceCases), target = find(targetCases);
      const name = `reference_${pair.length}_full_pcm`;
      const sourceComplete = source && !source.error && source.sampleRate === 44100 &&
        source.numberOfChannels === 1 && source.length === pair.length &&
        source.byteLength === pair.length * 4 &&
        /^[0-9a-f]{64}$/i.test(source.sha256 || "") &&
        source.copySha256 === source.sha256;
      if (!source || source.error || source.sampleRate !== 44100 || source.numberOfChannels !== 1 ||
          source.length !== pair.length || source.sha256 !== pair.native_sha256 ||
          source.copySha256 !== pair.native_sha256 || source.byteLength !== pair.length * 4) {
        skip("cloak_audio_positive", name,
            "Complete native PCM did not match the pinned source waveform; strict production guard must not be weakened",
            "cloak_audio");
        if (sourceComplete) {
          add("cloak_audio_positive", `${name}_native_fallback`,
              Boolean(target && !target.error && target.length === source.length &&
                target.sampleRate === source.sampleRate &&
                target.numberOfChannels === source.numberOfChannels &&
                target.byteLength === source.byteLength &&
                target.sha256 === source.sha256 && target.copySha256 === source.copySha256),
              "Valid nonmatching native PCM must remain unchanged; this is not positive reference evidence");
        }
        continue;
      }
      if (!target) {
        add("cloak_audio_positive", name, false,
            "Eligible native fixture exists but target result is missing", "cloak_audio");
        continue;
      }
      add("cloak_audio_positive", name,
          !target.error && target.sampleRate === 44100 && target.numberOfChannels === 1 &&
            target.length === pair.length && target.sha256 === pair.reference_sha256 &&
            target.copySha256 === pair.reference_sha256 && target.byteLength === pair.length * 4,
          `eligibleNative=${pair.native_sha256}; expectedReference=${pair.reference_sha256}`,
          "cloak_audio");
    }
  }
  const positive_acceptance = Object.fromEntries(Object.entries(groups).map(([name, value]) => {
    const status = value.failed ? "failed"
      : (value.skipped || value.eligible_count === 0) ? "not_tested" : "observed_pass";
    return [name, {
      status,
      reason: `${value.passed_count}/${value.eligible_count} eligible checks passed; ` +
        `${value.failed} failed; ${value.skipped} required checks not tested`,
      eligible_count: value.eligible_count,
      passed_count: value.passed_count,
    }];
  }));
  return { checks, not_tested, positive_acceptance };
}
