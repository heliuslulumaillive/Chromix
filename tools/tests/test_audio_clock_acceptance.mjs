import test from "node:test";
import assert from "node:assert/strict";
import { validateAudioAcceptance } from "../fingerprint_audio_acceptance.mjs";

function records() {
  const context = (id, rate, capacity) => ({
    id, sampleRate: rate, maxChannelCount: capacity, channelCount: capacity, closed: true,
    setterResults: [],
    worklet: {
      actualContextRate: rate, protocolVersion: 2, expectedCallbacks: 384,
      initialization: "suspend-build-resume", suspendedBeforeGraph: true,
      messages: Array.from({ length: 384 }, (_, sequence) => ({
        sequence, sampleRate: rate, currentFrame: 1408 + sequence * 128,
        currentTime: (1408 + sequence * 128) / rate, frames: 128,
      })),
    },
  });
  return Object.fromEntries([
    ["all_native", 48000, 2], ["audio_defaults_44100", 44100, 1],
    ["audio_defaults_32000", 32000, 2],
  ].map(([id, rate, capacity]) => [id, { extra: { audio: {
    contexts: [context("default", rate, capacity), context("explicit48000", 48000, capacity)],
    explicit: {
      offline: { sampleRate: 44100, numberOfChannels: 6, length: 256, state: "closed" },
      buffer: { sampleRate: 32000, numberOfChannels: 6, length: 16 },
    },
  } } }]));
}
const clock = fixture => fixture.audio_defaults_44100.extra.audio.contexts[0].worklet;
const check = (fixture, name) => validateAudioAcceptance(fixture).checks.find(row =>
  row.scenario === "audio_defaults_44100" && row.name === name);

test("controlled complete sequence passes both independent clock criteria", () => {
  const fixture = records();
  for (const name of ["worklet_rate_time_consistent", "worklet_clock",
    "explicit_worklet_rate_time_consistent", "explicit_worklet_clock_kept"])
    assert.equal(check(fixture, name)?.passed, true, name);
});

test("startup skip remains a strict failure even when rate and time ratios are correct", () => {
  const fixture = records();
  for (const message of clock(fixture).messages.slice(1)) {
    message.currentFrame += 512;
    message.currentTime = message.currentFrame / message.sampleRate;
  }
  assert.equal(check(fixture, "worklet_rate_time_consistent")?.passed, true);
  assert.equal(check(fixture, "worklet_clock")?.passed, false);
});

for (const [name, mutate] of [
  ["wrong global rate", value => { value.messages[10].sampleRate = 48000; }],
  ["wrong time ratio", value => { value.messages[20].currentTime += 0.001; }],
  ["out of order callback", value => { value.messages[30].sequence = 20; }],
  ["only old three callback sample", value => { value.messages.length = 3; }],
  ["missing final callback", value => { value.messages.pop(); }],
  ["uncontrolled graph initialization", value => { value.suspendedBeforeGraph = false; }],
  ["zero output quantum", value => { value.messages[100].frames = 0; }],
  ["backward frame clock", value => {
    value.messages[250].currentFrame = value.messages[249].currentFrame - 128;
    value.messages[250].currentTime = value.messages[250].currentFrame / 44100;
  }],
]) test(`${name} cannot pass clock acceptance`, () => {
  const fixture = records(); mutate(clock(fixture));
  assert.equal(check(fixture, "worklet_rate_time_consistent")?.passed, false);
  assert.equal(check(fixture, "worklet_clock")?.passed, false);
});

test("a late skipped quantum is not hidden by an early stable prefix", () => {
  const fixture = records();
  for (const message of clock(fixture).messages.slice(380)) {
    message.currentFrame += 128;
    message.currentTime = message.currentFrame / message.sampleRate;
  }
  assert.equal(check(fixture, "worklet_rate_time_consistent")?.passed, true);
  assert.equal(check(fixture, "worklet_clock")?.passed, false);
});
