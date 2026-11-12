import copy
import unittest
from tools import webrtc_document_loopback_acceptance as acceptance

H='a'*64
INPUTS={name:H for name in acceptance.HARNESS}

def evidence():
    runs=[]
    for i,name in enumerate(sorted(acceptance.RUNS),100):
        native=name=='native-control';off=name in ('feature-off','native-control')
        args=['--webrtc-ip-handling-policy='+('default' if native else 'disable_non_proxied_udp')]
        args+=['--no-proxy-server'] if native else ['--proxy-server=socks5://127.0.0.1:40060']
        if off:args+=['--disable-features=ShardXWebRtcDocumentLoopback']
        if name=='display-ip':args+=['--fingerprint-webrtc-ip=203.0.113.200']
        runs.append({'id':name,'native':native,'feature':not off,'args':args,'rootPID':i,
          'version':{'product':'Chrome/157.0.8103.38'},'cleanup':{'normalExit':True,'forced':False,
          'closeSent':True,'exitCode':0,'rootPID':i,'known_owned_processes_remaining':[],
          'owned_tree_before_close':[{'pid':i,'created':'2026-11-11T00:00:00Z'}],
          'process_tree_error':None}})
    cases={name:{'id':name,'run':'enabled','status':'observed','canary':[],
                'observed':{'connected':False,'ackBytes':0}} for name in acceptance.CASES}
    for name,size in [('internal-data',1048576),('pooled-data',65536),('relay-to-all',65536),('internal-media',262144),('with-stun',65536),
                      ('proxy-failure',65536),('display-ip-internal',65536),('internal-tcp-namespace',1024),('turn-relay',262144),('turn-all-cross-doc',262144),('navigation',65536)]:
        cases[name]['observed']={'connected':True,'ackBytes':size,'binaryErrors':0,'addErrors':[]}
    cases['navigation']['afterNavigation']=copy.deepcopy(cases['navigation']['observed'])
    cases['pooled-data']['spec']={'poolSize':1}
    cases['display-ip-internal']['observed']['peers']=[{'candidates':[{'type':'host','address':address}]} for address in ('127.0.0.1','::1')]
    cases['relay-to-all']['observed'].update(beforePolicyUpgrade={'candidateCounts':[0,0],'policies':['relay','relay']},afterPolicyUpgrade=['all','all'])
    cases['same-origin-iframe']['spec']={'crossDocument':True}
    for name in ('separate-tab','separate-profile'):
        peer={'candidates':[{'candidate':'owned'}],'open':False,'received':[],'errors':[]}
        cases[name].update(otherRun='enabled' if name=='separate-tab' else 'other-profile',observed={'a':copy.deepcopy(peer),'b':copy.deepcopy(peer)})
    for name in ('unknown-targets','unknown-mdns'):cases[name]['observed']={'attempts':[{'accepted':True}]}
    cases['internal-media']['observed'].update(videoFrames=30,videoStats=[{'framesDecoded':30}],
        restartConnected=True,ufragBefore='old',ufragAfter='new',states=['connected','connected'],
        pcmStages=[{'name':name,'rms':0 if hz is None else .035,'peakHz':0 if hz is None else hz,'stableWindows':3,'settleMs':200}
                   for name,hz in [('tone440',440),('tone880',880),('silence',None),('restore440',440)]])
    for name in ('turn-relay','turn-all-cross-doc'):
        stats=[{'id':'transport','type':'transport','selectedCandidatePairId':'pair'},
          {'id':'pair','type':'candidate-pair','state':'succeeded','localCandidateId':'local','remoteCandidateId':'remote'},
          {'id':'local','type':'local-candidate','candidateType':'relay','relayProtocol':'tcp'},
          {'id':'remote','type':'remote-candidate','candidateType':'relay'}]
        cases[name]['observed']['stats']=[copy.deepcopy(stats),copy.deepcopy(stats)]
        cases[name]['turnDelta']={'readBytes':550000,'authRequests':2}
    cases['wrong-turn'].update(observed={'candidates':[]},turnDelta={'authRequests':1})
    cases['native-canary']['canary']=[{'bytes':32}]
    cases['internal-tcp-namespace'].update(tcpEndpoint={'host':'127.0.0.1','port':49152},
        selectedPeerCandidates=[{'address':'127.0.0.1','candidate':'candidate:1 1 udp 1 127.0.0.1 49152 typ host'}],
        tcpConnectionsWhileBound=0,tcpConnectionsAfterClosed=0)
    cases['native-tcp-canary'].update(run='feature-off',tcpConnections=1)
    return {'schemaVersion':1,'kind':'document-webrtc-loopback-runtime','status':'passed',
      'exeSHA256':H,'dllSHA256':H,'exeUnchanged':True,'dllUnchanged':True,'harness':INPUTS,
      'turnHelperSHA256':H,'noJavaScriptOverrides':True,'realMediaCaptured':False,
      'checks':[{'id':'observed','pass':True}],'cases':list(cases.values()),'runs':runs,
      'localAddressExposure':[],'networkEvidence':[{'run':r,'peerMdnsQueries':0,'netlogSHA256':H}
                                                 for r in acceptance.RUNS-{'native-control'}]}

def case(data,name):return next(c for c in data['cases'] if c['id']==name)

class DocumentAcceptanceTests(unittest.TestCase):
    def validate(self,data):return acceptance.validate(data,exe_sha256=H,dll_sha256=H,harness=INPUTS)
    def test_complete_observations_are_accepted(self):
        self.assertTrue(self.validate(evidence())['pcm_content_verified'])
    def test_incomplete_or_contradictory_proofs_are_rejected(self):
        mutations={
          'different_dll':lambda d:d.update(dllSHA256='b'*64),
          'missing_case':lambda d:d['cases'].pop(),
          'bad_cleanup':lambda d:d['runs'][0]['cleanup'].update(forced=True),
          'empty_process_proof':lambda d:d['runs'][0]['cleanup'].update(owned_tree_before_close=[]),
          'unrestricted_proxy':lambda d:d['runs'][0]['args'].append('--webrtc-ip-handling-policy=default'),
          'no_positive_canary':lambda d:case(d,'native-canary').update(canary=[]),
          'UDP_leak':lambda d:case(d,'unknown-targets').update(canary=[{'bytes':32}]),
          'TCP_namespace_escape':lambda d:case(d,'internal-tcp-namespace').update(tcpConnectionsAfterClosed=1),
          'bad_candidate_fixture':lambda d:case(d,'unknown-targets')['observed']['attempts'][0].update(accepted=False),
          'cross_document_delivery':lambda d:case(d,'separate-tab')['observed']['b'].update(received=['payload']),
          'cross_profile_wrong_boundary':lambda d:case(d,'separate-profile').update(otherRun='enabled'),
          'scope_without_candidates':lambda d:case(d,'separate-tab')['observed']['a'].update(candidates=[]),
          'missing_PCM':lambda d:case(d,'internal-media')['observed'].pop('pcmStages'),
          'silent_audio':lambda d:case(d,'internal-media')['observed']['pcmStages'][0].update(rms=0),
          'wrong_frequency':lambda d:case(d,'internal-media')['observed']['pcmStages'][1].update(peakHz=440),
          'unsettled_PCM':lambda d:case(d,'internal-media')['observed']['pcmStages'][0].update(stableWindows=1),
          'silence_not_reproduced':lambda d:case(d,'internal-media')['observed']['pcmStages'][2].update(rms=.035),
          'no_video':lambda d:case(d,'internal-media')['observed'].update(videoFrames=0),
          'no_restart':lambda d:case(d,'internal-media')['observed'].update(ufragAfter='old'),
          'TURN_did_not_transport':lambda d:case(d,'turn-relay')['turnDelta'].update(readBytes=0),
          'TURN_used_host_path':lambda d:case(d,'turn-relay')['observed']['stats'][0][2].update(candidateType='host'),
          'DNS_leak':lambda d:d['networkEvidence'][0].update(peerMdnsQueries=1),
          'missing_DNS_evidence':lambda d:d.update(networkEvidence=[]),
        }
        for name,mutate in mutations.items():
            with self.subTest(name=name):
                value=evidence();mutate(value)
                with self.assertRaises(ValueError):self.validate(value)

if __name__=='__main__':unittest.main()
