"""Independent acceptance of the compiled document-loopback browser probe."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import re

HARNESS = ('webrtc_document_loopback_runtime.mjs', 'webrtc_document_loopback_probe.js',
           'webrtc_runtime_controller.mjs', 'webrtc_network_fixtures.mjs')
CASES = frozenset(('internal-data', 'pooled-data', 'internal-media', 'with-stun', 'relay-no-server', 'relay-to-all',
    'unknown-targets', 'unknown-mdns', 'same-origin-iframe', 'separate-tab',
    'separate-profile', 'navigation', 'proxy-failure', 'display-ip-internal', 'turn-relay',
    'turn-all-cross-doc', 'internal-tcp-namespace', 'native-tcp-canary', 'wrong-turn', 'feature-off', 'native-canary'))
RUNS = frozenset(('enabled', 'other-profile', 'failed-proxy', 'display-ip', 'turn-proxy',
                  'feature-off', 'native-control'))

def require(value, message):
    if not value:
        raise ValueError(message)

def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()

def source_hashes(repo):
    return {name:digest(Path(repo)/'tools'/name) for name in HARNESS}

def transfer(value, size):
    require(value.get('connected') is True and type(value.get('ackBytes')) is int
            and value['ackBytes'] == size and value.get('binaryErrors') == 0
            and value.get('addErrors') == [], 'Compiled data-channel transfer failed')

def unconnected(value):
    require(value.get('connected') is False and value.get('ackBytes') == 0,
            'A restricted document or policy connected unexpectedly')

def relay_selected(value):
    for rows in value.get('stats', []):
        by_id={row.get('id'):row for row in rows}
        transports=[r for r in rows if r.get('type')=='transport' and r.get('selectedCandidatePairId')]
        require(transports, 'TURN transport selection missing')
        for transport in transports:
            pair=by_id.get(transport['selectedCandidatePairId'], {})
            local=by_id.get(pair.get('localCandidateId'), {})
            remote=by_id.get(pair.get('remoteCandidateId'), {})
            require(pair.get('state')=='succeeded' and local.get('candidateType')=='relay'
                    and remote.get('candidateType')=='relay' and local.get('relayProtocol')=='tcp',
                    'The TURN proof used a host/UDP path instead of the proxy TCP relay')

def validate(data, *, exe_sha256, dll_sha256, harness):
    require(data.get('schemaVersion')==1 and data.get('kind')=='document-webrtc-loopback-runtime'
            and data.get('status')=='passed', 'Missing successful document-loopback runtime')
    require(data.get('exeSHA256')==exe_sha256 and data.get('dllSHA256')==dll_sha256
            and data.get('exeUnchanged') is True and data.get('dllUnchanged') is True,
            'Document-loopback evidence is for a different or changed binary')
    require(data.get('harness')==harness and set(harness)==set(HARNESS),
            'Document-loopback harness identity differs')
    require(data.get('noJavaScriptOverrides') is True and data.get('realMediaCaptured') is False,
            'Uncontrolled API or media input')
    require(re.fullmatch('[a-f0-9]{64}', data.get('turnHelperSHA256','')),
            'TURN helper identity missing')
    checks=data.get('checks',[])
    require(checks and all(r.get('pass') is True for r in checks)
            and len({r.get('id') for r in checks})==len(checks), 'Failed or duplicate runtime checks')
    cases={r['id']:r for r in data.get('cases',[])}
    require(set(cases)==CASES and len(data['cases'])==len(CASES), 'Runtime case coverage is incomplete')
    runs={r['id']:r for r in data.get('runs',[])}
    require(set(runs)==RUNS and len(data['runs'])==len(RUNS), 'Runtime browser coverage is incomplete')
    for name,r in runs.items():
        require(r.get('version',{}).get('product')=='Chrome/157.0.8103.38', 'Wrong runtime browser version')
        cleanup=r.get('cleanup',{})
        require(cleanup.get('normalExit') is True and cleanup.get('forced') is False
                and cleanup.get('known_owned_processes_remaining')==[]
                and cleanup.get('process_tree_error') is None and cleanup.get('closeSent') is True
                and cleanup.get('exitCode')==0 and type(r.get('rootPID')) is int
                and cleanup.get('rootPID')==r['rootPID']
                and any(p.get('pid')==r['rootPID'] and p.get('created') for p in cleanup.get('owned_tree_before_close',[])),
                'Browser cleanup was not verified')
        policies=[a for a in r.get('args',[]) if a.startswith('--webrtc-ip-handling-policy=')]
        expected='default' if name=='native-control' else 'disable_non_proxied_udp'
        require(policies==['--webrtc-ip-handling-policy='+expected], 'Browser network policy differs')
        disabled='--disable-features=ShardXWebRtcDocumentLoopback' in r['args']
        require(disabled==(name in ('feature-off','native-control')), 'Feature control differs')
        require(r.get('native')==(name=='native-control') and r.get('feature')==(not disabled),
                'Declared feature/policy control differs from arguments')
        if name!='native-control':
            require(sum(a.startswith('--proxy-server=socks5://') for a in r['args'])==1,
                    'A protected run has no SOCKS proxy')
    for name,c in cases.items():
        require(c.get('status')=='observed' and isinstance(c.get('observed'),dict)
                and not c['observed'].get('probeError'), 'An observed result is missing: '+name)
        require(isinstance(c.get('canary'),list), 'Canary accounting missing')
        if name!='native-canary':require(not c['canary'], 'A protected run reached a real UDP canary')
    for name,size in [('internal-data',1048576),('pooled-data',65536),('relay-to-all',65536),('internal-media',262144),('with-stun',65536),
                      ('proxy-failure',65536),('display-ip-internal',65536),('internal-tcp-namespace',1024),('turn-relay',262144),('turn-all-cross-doc',262144)]:
        transfer(cases[name]['observed'],size)
    for name in ('relay-no-server','same-origin-iframe','feature-off'):
        unconnected(cases[name]['observed'])
    transition=cases['relay-to-all']['observed']
    require(transition.get('beforePolicyUpgrade')=={'candidateCounts':[0,0],'policies':['relay','relay']}
            and transition.get('afterPolicyUpgrade')==['all','all'], 'Relay/all transition did not preserve policy')
    require(cases['pooled-data'].get('spec',{}).get('poolSize')==1, 'Pooled allocator was not exercised')
    require('--fingerprint-webrtc-ip=203.0.113.200' in runs['display-ip']['args'], 'IP presentation override was not exercised')
    require(all(p.get('candidates') and all(c.get('type')=='host' and c.get('address') in ('127.0.0.1','::1')
                for c in p['candidates']) for p in cases['display-ip-internal']['observed'].get('peers',[]))
            and len(cases['display-ip-internal']['observed'].get('peers',[]))==2,
            'Internal endpoints were masked as public/fabricated candidates')
    require(cases['same-origin-iframe'].get('spec',{}).get('crossDocument') is True,
            'The iframe isolation case did not use another document')
    for name in ('separate-tab','separate-profile'):
        c=cases[name]
        require((c['run']!=c['otherRun'])==(name=='separate-profile'), 'Scope test used the wrong browser boundary')
        for peer in ('a','b'):
            p=c['observed'][peer]
            require(p.get('candidates') and p.get('open') is False and p.get('received')==[] and p.get('errors')==[],
                    'Scope isolation lacked candidates or delivered data')
    transfer(cases['navigation']['observed'],65536)
    transfer(cases['navigation'].get('afterNavigation',{}),65536)
    for name in ('unknown-targets','unknown-mdns'):
        attempts=cases[name]['observed'].get('attempts',[])
        require(attempts and all(a.get('accepted') is True for a in attempts), 'Adversarial candidates were not exercised')
    media=cases['internal-media']['observed']
    require(media.get('videoFrames',0)>=10 and any(v.get('framesDecoded',0)>=10 for v in media.get('videoStats',[])),
            'Video was not decoded')
    require(media.get('restartConnected') is True and media.get('ufragBefore')
            and media['ufragBefore']!=media.get('ufragAfter')
            and media.get('states')==['connected','connected'], 'ICE restart did not retain transport')
    stages=media.get('pcmStages',[])
    require([s.get('name') for s in stages]==['tone440','tone880','silence','restore440'],
            'PCM content stages missing')
    for s,hz in zip(stages,(440,880,None,440)):
        rms=s.get('rms')
        require(type(rms) in (int,float) and rms>=0, 'PCM RMS missing')
        require(s.get('stableWindows',0)>=3 and type(s.get('settleMs')) is int
                and 0<s['settleMs']<=4500, 'PCM content did not settle within the bounded observation')
        if hz is None:require(rms<0.001, 'PCM silence was not reproduced')
        else:
            require(0.005<rms<0.1 and type(s.get('peakHz')) in (int,float)
                    and abs(s['peakHz']-hz)<=36, 'PCM content frequency or amplitude mismatch')
    for name in ('turn-relay','turn-all-cross-doc'):
        c=cases[name];relay_selected(c['observed'])
        require(c.get('turnDelta',{}).get('readBytes',0)>500000
                and c['turnDelta'].get('authRequests',0)>=2, 'TURN server did not relay the payload')
    wrong=cases['wrong-turn']
    require(wrong.get('turnDelta',{}).get('authRequests',0)>=1
            and not any(c.get('type')=='relay' for c in wrong['observed'].get('candidates',[])),
            'Invalid TURN authentication was not rejected')
    require(any(e.get('bytes',0)>=20 for e in cases['native-canary']['canary']), 'Canary positive control failed')
    require(data.get('localAddressExposure')==[], 'Real host address observed')
    namespace=cases['internal-tcp-namespace'];endpoint=namespace.get('tcpEndpoint',{})
    require(endpoint.get('host')=='127.0.0.1' and type(endpoint.get('port')) is int
            and 0<endpoint['port']<=65535
            and any(c.get('address')=='127.0.0.1' and c.get('candidate','').split()[5]==str(endpoint['port'])
                    for c in namespace.get('selectedPeerCandidates',[]) if len(c.get('candidate','').split())>=8)
            and namespace.get('tcpConnectionsWhileBound')==0 and namespace.get('tcpConnectionsAfterClosed')==0,
            'A virtual UDP endpoint was promoted to a real TCP destination')
    require(cases['native-tcp-canary'].get('run')=='feature-off'
            and cases['native-tcp-canary'].get('tcpConnections',0)>0, 'TCP namespace positive control failed')
    network=data.get('networkEvidence',[])
    require({v.get('run') for v in network}==RUNS-{'native-control'}
            and len(network)==len(RUNS)-1
            and all(v.get('peerMdnsQueries')==0 and re.fullmatch('[a-f0-9]{64}',v.get('netlogSHA256','')) for v in network),
            'Peer mDNS resolution evidence is missing or not blocked')
    return {'status':'passed','cases':len(cases),'checks':len(checks),
            'pcm_content_verified':True,'document_scope_verified':True}

def validate_file(report_path, browser, repo):
    path=Path(report_path)
    require(path.stat().st_size<4*1024*1024, 'Runtime evidence too large')
    data=json.loads(path.read_text(encoding='utf-8-sig'))
    return validate(data,exe_sha256=digest(browser),dll_sha256=digest(Path(browser).with_name('chrome.dll')),
                    harness=source_hashes(repo))
