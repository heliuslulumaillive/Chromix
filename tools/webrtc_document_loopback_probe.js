const rtcDelay = ms => new Promise(r => setTimeout(r, ms));
async function rtcIdle(spec = {}) {
  await rtcDelay(spec.observeMs ?? 8000);
  return {noPeerConnectionCreated: true};
}
async function rtcStats(pc) {
  const stats = await pc.getStats();
  return [...stats.values()].filter(
      x => ['local-candidate', 'remote-candidate', 'candidate-pair', 'transport', 'data-channel',
            'inbound-rtp', 'outbound-rtp']
               .includes(x.type));
}
function rtcWatch(pc, start) {
  const r =
      {candidates: [], errors: [], states: [], firstCandidateMs: null, gatherCompleteMs: null};
  const time = () => Math.round(performance.now() - start);
  pc.addEventListener('icecandidate', e => {
    if (e.candidate) {
      r.firstCandidateMs ??= time();
      r.candidates.push({
        time: time(),
        ...e.candidate.toJSON(),
        address: e.candidate.address,
        relatedAddress: e.candidate.relatedAddress,
        type: e.candidate.type,
        protocol: e.candidate.protocol
      });
    }
  });
  pc.addEventListener('icecandidateerror', e => r.errors.push({
    time: time(),
    address: e.address,
    port: e.port,
    url: e.url,
    code: e.errorCode,
    text: e.errorText
  }));
  pc.addEventListener(
      'connectionstatechange', () => r.states.push({time: time(), state: pc.connectionState}));
  pc.addEventListener('icegatheringstatechange', () => {
    if (pc.iceGatheringState === 'complete')
      r.gatherCompleteMs = time()
  });
  return r;
}
async function rtcPair(spec = {}) {
  let scopedFrame = null;
  if (spec.crossDocument) {
    scopedFrame = document.createElement('iframe');
    scopedFrame.srcdoc = '<!doctype html><title>Owned second document</title>';
    const loaded = new Promise(r => scopedFrame.onload = r);
    document.body.append(scopedFrame);
    await loaded;
  }
  const config = {
    iceServers: spec.iceServers ?? [],
    iceTransportPolicy: (spec.relayOnly || spec.promoteRelay) ? 'relay' : 'all',
    iceCandidatePoolSize: spec.poolSize ?? 0
  },
        start = performance.now(),
        peers =
            [
              new RTCPeerConnection(config),
              new (scopedFrame?.contentWindow ?? window).RTCPeerConnection(config)
            ],
        reports = peers.map(p => rtcWatch(p, start)), queues = [[], []], addErrors = [];
  const now = () => Math.round(performance.now() - start), result = {
    connected: false,
    connectionMs: null,
    ackBytes: 0,
    expectedBytes: spec.bytes ?? 262144,
    binaryErrors: 0,
    restartConnected: null,
    videoFrames: 0,
    config,
    peers: reports,
    addErrors
  };
  let remote, context, receiverContext, analyser, timer, canvasStream, remoteAudio, tone,
      gainControl;
  const video = document.createElement('video');
  video.muted = true;
  video.playsInline = true;
  document.body.append(video);
  const channels = [];
  peers.forEach((p, i) => p.onicecandidate = e => {
    if (!e.candidate)
      return;
    const other = peers[1 - i];
    if (other.remoteDescription)
      other.addIceCandidate(e.candidate.toJSON()).catch(e => addErrors.push(e.message));
    else
      queues[1 - i].push(e.candidate.toJSON());
  });
  const negotiate = async restart => {
    await peers[0].setLocalDescription(
        await peers[0].createOffer(restart ? {iceRestart: true} : {}));
    await peers[1].setRemoteDescription(peers[0].localDescription);
    for (const c of queues[1].splice(0))
      await peers[1].addIceCandidate(c).catch(e => addErrors.push(e.message));
    await peers[1].setLocalDescription(await peers[1].createAnswer());
    await peers[0].setRemoteDescription(peers[1].localDescription);
    for (const c of queues[0].splice(0))
      await peers[0].addIceCandidate(c).catch(e => addErrors.push(e.message));
  };
  try {
    if (spec.media) {
      const canvas = document.createElement('canvas');
      canvas.width = 160;
      canvas.height = 90;
      const x = canvas.getContext('2d');
      let frame = 0;
      const draw = () => {
        x.fillStyle = frame++ % 2 ? '#1166cc' : '#cc6611';
        x.fillRect(0, 0, 160, 90);
        x.fillStyle = '#ffffff';
        x.fillRect(10, 10, 30, 30);
      };
      draw();
      canvasStream = canvas.captureStream(15);
      timer = setInterval(draw, 67);
      for (const t of canvasStream.getTracks())
        peers[0].addTrack(t, canvasStream);
      context = new AudioContext({sampleRate: 48000, sinkId: {type: 'none'}});
      if (context.sinkId?.type !== 'none')
        throw Error('Silent audio sink unavailable');
      await context.resume();
      const osc = context.createOscillator(), gain = context.createGain(),
            destination = context.createMediaStreamDestination();
      tone = osc;
      gainControl = gain;
      osc.frequency.value = 440;
      gain.gain.value = .05;
      osc.connect(gain);
      gain.connect(destination);
      gain.connect(context.destination);
      osc.start();
      for (const t of destination.stream.getTracks())
        peers[0].addTrack(t, destination.stream);
      peers[1].ontrack = e => {
        if (e.track.kind === 'video') {
          video.srcObject = new MediaStream([e.track]);
          video.play().catch(e => result.playError = String(e));
          const tick = () => {
            result.videoFrames++;
            if (!video.paused)
              video.requestVideoFrameCallback(tick)
          };
          video.requestVideoFrameCallback(tick);
        } else if (e.track.kind === 'audio') {
          if (spec.attachPlayout !== false) {
            remoteAudio = document.createElement('audio');
            remoteAudio.volume = 0;
            remoteAudio.srcObject = new MediaStream([e.track]);
            document.body.append(remoteAudio);
            remoteAudio.play()
                .then(() => result.audioElementPlaying = true)
                .catch(error => result.audioPlayError = String(error));
          }
          receiverContext = new AudioContext({sampleRate: 48000, sinkId: {type: 'none'}});
          if (receiverContext.sinkId?.type !== 'none') {
            result.audioResumeError = 'Silent audio sink unavailable';
            return;
          }
          analyser = receiverContext.createAnalyser();
          analyser.fftSize = 2048;
          analyser.smoothingTimeConstant = 0;
          const input = receiverContext.createMediaStreamSource(new MediaStream([e.track])),
                mute = receiverContext.createGain();
          mute.gain.value = 0;
          input.connect(analyser);
          analyser.connect(receiverContext.destination);
          receiverContext.resume().catch(err => result.audioResumeError = String(err));
        }
      };
    }
    peers[1].ondatachannel = e => {
      remote = e.channel;
      channels.push(remote);
      remote.binaryType = 'arraybuffer';
      remote.onmessage = e => remote.send(e.data);
    };
    const channel = peers[0].createDataChannel('owned-test');
    channels.push(channel);
    channel.binaryType = 'arraybuffer';
    channel.onopen = () => {
      result.connected = true;
      result.connectionMs = now();
    };
    await negotiate(false);
    if (spec.promoteRelay) {
      await rtcDelay(300);
      result.beforePolicyUpgrade = {
        candidateCounts: reports.map(r => r.candidates.length),
        policies: peers.map(p => p.getConfiguration().iceTransportPolicy)
      };
      for (const p of peers)
        p.setConfiguration({...p.getConfiguration(), iceTransportPolicy: 'all'});
      await negotiate(true);
      result.afterPolicyUpgrade = peers.map(p => p.getConfiguration().iceTransportPolicy);
    }
    const waitUntil = performance.now() + (spec.timeoutMs ?? 6500);
    while (!result.connected && performance.now() < waitUntil)
      await rtcDelay(25);
    if (result.connected) {
      const chunkSize = 16384;
      let ackResolve;
      channel.onmessage = e => {
        if (typeof e.data === 'string') {
          if (e.data === 'restart-probe')
            result.restartConnected = true;
          return;
        }
        const b = new Uint8Array(e.data), seq = new DataView(b.buffer).getUint32(0, true);
        for (let j = 4; j < b.length; j++)
          if (b[j] !== ((j * 31 + seq * 17) & 255)) {
            result.binaryErrors++;
            break;
          }
        result.ackBytes += b.length;
        ackResolve?.();
      };
      for (let seq = 0; seq < Math.ceil(result.expectedBytes / chunkSize); seq++) {
        const size = Math.min(chunkSize, result.expectedBytes - result.ackBytes),
              b = new Uint8Array(size);
        for (let j = 4; j < size; j++)
          b[j] = (j * 31 + seq * 17) & 255;
        new DataView(b.buffer).setUint32(0, seq, true);
        let timeout;
        const ack = new Promise(r => {
          ackResolve = r;
          timeout = setTimeout(r, 1200)
        });
        channel.send(b);
        await ack;
        clearTimeout(timeout);
        if (result.ackBytes < (seq + 1) * chunkSize && result.ackBytes < result.expectedBytes)
          break;
      }
      if (spec.restart) {
        result.ufragBefore = peers[0].localDescription.sdp.match(/a=ice-ufrag:(.+)/)?.[1];
        await negotiate(true);
        result.ufragAfter = peers[0].localDescription.sdp.match(/a=ice-ufrag:(.+)/)?.[1];
        channel.send('restart-probe');
        const until = performance.now() + 4000;
        while (!result.restartConnected && performance.now() < until)
          await rtcDelay(25);
      }
    }
    await rtcDelay(spec.observeMs ?? 2500);
    if (spec.pcmStages && analyser) {
      result.pcmStages = [];
      for (const [name, hz, level] of [
               ['tone440', 440, .05], ['tone880', 880, .05], ['silence', 880, 0],
               ['restore440', 440, .05]]) {
        tone.frequency.value = hz;
        gainControl.gain.value = level;
        const began = performance.now();
        let sample, stable = 0;
        while (performance.now() - began < 4000) {
          await rtcDelay(60);
          const a = new Float32Array(analyser.fftSize),
                f = new Float32Array(analyser.frequencyBinCount);
          analyser.getFloatTimeDomainData(a);
          analyser.getFloatFrequencyData(f);
          let bin = 0;
          for (let i = 1; i < f.length; i++)
            if (f[i] > f[bin])
              bin = i;
          sample = {
            name,
            expectedHz: hz,
            expectedLevel: level,
            rms: Math.sqrt(a.reduce((s, x) => s + x * x, 0) / a.length),
            peakHz: bin * receiverContext.sampleRate / analyser.fftSize,
            binHz: receiverContext.sampleRate / analyser.fftSize,
            settleMs: Math.round(performance.now() - began)
          };
          const match = level === 0 ?
              sample.rms < .001 :
              sample.rms > .005 && sample.rms < .1 && Math.abs(sample.peakHz - hz) <= 36;
          stable = match ? stable + 1 : 0;
          if (stable >= 3)
            break;
        }
        result.pcmStages.push({...sample, stableWindows: stable});
      }
    }
    if (analyser) {
      const samples = new Float32Array(analyser.fftSize),
            spectrum = new Float32Array(analyser.frequencyBinCount);
      analyser.getFloatTimeDomainData(samples);
      analyser.getFloatFrequencyData(spectrum);
      let peak = 0;
      for (let i = 1; i < spectrum.length; i++)
        if (spectrum[i] > spectrum[peak])
          peak = i;
      result.audioMeasured = {
        contextState: receiverContext.state,
        rms: Math.sqrt(samples.reduce((s, x) => s + x * x, 0) / samples.length),
        peakHz: peak * receiverContext.sampleRate / analyser.fftSize,
        peakDb: spectrum[peak]
      };
    }
    result.stats = await Promise.all(peers.map(rtcStats));
    result.sdp = peers.map(p => ({
                             local: p.localDescription?.sdp,
                             remote: p.remoteDescription?.sdp,
                             pendingLocal: p.pendingLocalDescription?.sdp,
                             currentLocal: p.currentLocalDescription?.sdp
                           }));
    result.audioEnergy = result.stats[1]
                             .filter(x => x.type === 'inbound-rtp' && x.kind === 'audio')
                             .map(x => ({
                                    bytesReceived: x.bytesReceived,
                                    totalAudioEnergy: x.totalAudioEnergy,
                                    totalSamplesReceived: x.totalSamplesReceived
                                  }));
    result.videoStats = result.stats[1]
                            .filter(x => x.type === 'inbound-rtp' && x.kind === 'video')
                            .map(x => ({
                                   framesDecoded: x.framesDecoded,
                                   bytesReceived: x.bytesReceived,
                                   frameWidth: x.frameWidth,
                                   frameHeight: x.frameHeight
                                 }));
    result.sourceAudioState = context ?
        {state: context.state, time: context.currentTime, sampleRate: context.sampleRate} :
        null;
    result.elapsedMs = now();
    result.states = peers.map(p => p.connectionState);
    return result;
  } finally {
    if (spec.hold) {
      globalThis.__heldPair = {
        peers,
        channels,
        context,
        receiverContext,
        timer,
        canvasStream,
        remoteAudio,
        video,
        scopedFrame
      };
    } else {
      for (const c of channels)
        c.close();
      for (const p of peers)
        p.close();
      clearInterval(timer);
      canvasStream?.getTracks().forEach(t => t.stop());
      await context?.close();
      await receiverContext?.close();
      if (remoteAudio) {
        remoteAudio.pause();
        remoteAudio.srcObject = null;
        remoteAudio.remove();
      }
      video.pause();
      video.srcObject = null;
      video.remove();
      scopedFrame?.remove();
    }
  }
}
async function rtcGather(spec = {}) {
  const p = new RTCPeerConnection({
    iceServers: spec.iceServers ?? [],
    iceTransportPolicy: spec.relayOnly ? 'relay' : 'all'
  }),
        start = performance.now(), r = rtcWatch(p, start);
  try {
    p.createDataChannel('owned-gather');
    await p.setLocalDescription(await p.createOffer());
    await rtcDelay(spec.observeMs ?? 10000);
    return {
      ...r,
      stats: await rtcStats(p),
      sdp: p.localDescription?.sdp,
      state: p.iceGatheringState
    };
  } finally {
    p.close();
  }
}
async function rtcAdversarial(spec) {
  const a = new RTCPeerConnection(), b = new RTCPeerConnection(), start = performance.now(),
        r = rtcWatch(a, start);
  const attempts = [];
  try {
    a.createDataChannel('owned-candidate');
    await a.setLocalDescription(await a.createOffer());
    await b.setRemoteDescription(a.localDescription);
    const answer = await b.createAnswer();
    const ufrag = answer.sdp.match(/a=ice-ufrag:(.+)/)[1].trim();
    const remote = {
      type: answer.type,
      sdp: answer.sdp.split('\r\n').filter(l => !l.startsWith('a=candidate:')).join('\r\n')
    };
    b.close();
    await a.setRemoteDescription(remote);
    for (const target of spec.targets) {
      const candidate = `candidate:owned${attempts.length} 1 udp 2122260223 ${target.host} ${
          target.port} typ host generation 0 ufrag ${ufrag}`;
      try {
        await a.addIceCandidate({candidate, sdpMid: '0', sdpMLineIndex: 0});
        attempts.push({...target, accepted: true});
      } catch (e) {
        attempts.push({...target, error: String(e)});
      }
    }
    await rtcDelay(spec.observeMs ?? 9000);
    return {
      ...r,
      attempts,
      stats: await rtcStats(a),
      sdp: a.localDescription?.sdp,
      state: a.connectionState
    };
  } finally {
    a.close();
    b.close();
  }
}

function rtcScopedStart(config = {}) {
  const pc = new RTCPeerConnection(config);
  globalThis.__scoped = {pc, candidates: [], received: [], errors: [], open: false};
  pc.onicecandidate = e => {
    if (e.candidate)
      __scoped.candidates.push(e.candidate.toJSON());
  };
  pc.ondatachannel = e => {
    const channel = e.channel;
    __scoped.remote = channel;
    channel.onmessage = e => {
      __scoped.received.push(e.data);
      channel.send('echo:' + e.data);
    };
  };
  return true;
}
async function rtcScopedOffer() {
  const channel = __scoped.pc.createDataChannel('scope');
  __scoped.channel = channel;
  channel.onopen = () => {
    __scoped.open = true;
    channel.send('owned-scope-proof');
  };
  channel.onmessage = e => __scoped.received.push(e.data);
  await __scoped.pc.setLocalDescription(await __scoped.pc.createOffer());
  return __scoped.pc.localDescription.toJSON();
}
async function rtcScopedAnswer(offer) {
  await __scoped.pc.setRemoteDescription(offer);
  await __scoped.pc.setLocalDescription(await __scoped.pc.createAnswer());
  return __scoped.pc.localDescription.toJSON();
}
async function rtcScopedRemote(answer) {
  await __scoped.pc.setRemoteDescription(answer);
  return true;
}
async function rtcScopedCandidates(candidates) {
  for (const c of candidates)
    try {
      await __scoped.pc.addIceCandidate(c);
    } catch (e) {
      __scoped.errors.push(String(e));
    }
  return true;
}
async function rtcScopedState() {
  return {
    open: __scoped.open,
    candidates: __scoped.candidates,
    received: __scoped.received,
    errors: __scoped.errors,
    state: __scoped.pc.connectionState,
    stats: await rtcStats(__scoped.pc)
  };
}
function rtcScopedClose() {
  __scoped.channel?.close();
  __scoped.remote?.close();
  __scoped.pc.close();
  return true;
}
