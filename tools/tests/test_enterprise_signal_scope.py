"""Keep restored security reads without re-enabling enterprise ID collection."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
SOURCE = 'chrome/browser/enterprise/signals/context_info_fetcher.cc'
OVERLAY = 'patches/security/0002-preserve-disabled-enterprise-profile-id.patch'
with mock.patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
    import verify_google_services_source as source_gate
from tools.tests import test_security_restoration as restoration_tests
from tools.tests.test_security_interstitial_restoration import compiler_environment, definition

class EnterpriseScopeSourceTest(unittest.TestCase):
    def test_recipe_retains_security_and_uses_the_focused_exclusion(self):
        config = json.loads((ROOT / 'build/google-services-win155.json').read_text())
        markers = config['restore_hunks']['core/ungoogled-chromium/remove-unused-preferences-fields.patch'][SOURCE]
        self.assertIn('safe_browsing', markers)
        self.assertNotIn('GetEnterpriseProfileId', markers)
        self.assertEqual(config['restoration_overlays'].count(OVERLAY), 1)
        self.assertLess(config['restoration_overlays'].index(OVERLAY),
                        config['restoration_overlays'].index('patches/lite-win156/0001-retire-bridge-runtime-synthetic.patch'))
        patch = (ROOT / OVERLAY).read_text()
        self.assertEqual([line for line in patch.splitlines() if line.startswith('-') and not line.startswith('---')],
                         ['-  info.enterprise_profile_id = GetEnterpriseProfileId(profile);'])
        self.assertFalse(any(line.startswith('+') and not line.startswith('+++') for line in patch.splitlines()))

    def test_source_gate_requires_security_reads_and_rejects_id_side_effects(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp)
            restoration_tests.SecuritySourceGateTest().fixture(src)
            path = src / SOURCE
            original = path.read_text()
            source_gate.verify(src, require_security=True)
            for name in ('info.safe_browsing_protection_level', 'info.password_protection_warning_trigger'):
                path.write_text(original.replace(name, 'retired_field'))
                with self.subTest(missing=name), self.assertRaises(ValueError):
                    source_gate.verify(src, require_security=True)
            for addition in ('info.enterprise_profile_id = GetEnterpriseProfileId(profile);',
                             'enterprise::ProfileIdServiceFactory::GetForProfile(profile);'):
                path.write_text(original + '\n' + addition)
                with self.subTest(addition=addition), self.assertRaisesRegex(ValueError, 'identifier collection'):
                    source_gate.verify(src, require_security=True)

def fetch_probe(text, collector=False):
    fetch = definition(text, r'void ContextInfoFetcher::Fetch\(ContextInfoCallback callback\)\s*\{')
    asynchronous = definition(text, r'ContextInfo ContextInfoFetcher::FetchAsyncSignals\(ContextInfo info\)\s*\{')
    dns = definition(text, r'bool GetBuiltInDnsClientEnabled\(PrefService\* local_state\)\s*\{')
    prefix = r'''#include <cassert>
#include <cstdio>
#include <functional>
#include <initializer_list>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>
#define DCHECK(x) assert(x)
#define FROM_HERE 0
#define IS_WIN 1
#define BUILDFLAG(x) (x)
namespace prefs { inline constexpr char kBuiltInDnsClientEnabled[]="built_in_dns_client_enabled"; }
namespace content { struct BrowserContext { virtual ~BrowserContext()=default; }; }
namespace safe_browsing { enum class SafeBrowsingState { NONE, STANDARD, ENHANCED }; enum class PasswordProtectionTrigger { OFF, PHISHING_REUSE }; }
struct PrefService {
  safe_browsing::SafeBrowsingState protection=safe_browsing::SafeBrowsingState::STANDARD;
  std::optional<safe_browsing::PasswordProtectionTrigger> warning=safe_browsing::PasswordProtectionTrigger::PHISHING_REUSE;
  bool GetBoolean(const char*) { return true; }
};
struct Profile:content::BrowserContext { PrefService prefs; PrefService* GetPrefs(){return &prefs;} static Profile* FromBrowserContext(content::BrowserContext* context){return static_cast<Profile*>(context);} };
struct BrowserProcess { PrefService prefs; PrefService* local_state(){return &prefs;} } browser_process;
BrowserProcess* g_browser_process=&browser_process;
struct ChromePolicyBlocklistServiceFactory { static int GetForProfile(Profile*){return 0;} };
namespace version_info { std::string GetVersionNumber(){return "156.0.8078.79";} }
namespace device_signals {
enum class SettingValue { UNKNOWN, ENABLED };
bool GetSiteIsolationEnabled(){return true;}
bool GetChromeRemoteDesktopAppBlocked(int){return false;}
safe_browsing::SafeBrowsingState GetSafeBrowsingProtectionLevel(PrefService* p){return p->protection;}
std::optional<safe_browsing::PasswordProtectionTrigger> GetPasswordProtectionWarningTrigger(PrefService* p){return p->warning;}
}
namespace enterprise_connectors { enum AnalysisConnector { FILE_ATTACHED, FILE_DOWNLOADED, BULK_DATA_ENTRY, PRINT }; using EnterpriseRealTimeUrlCheckMode=int; }
namespace base {
struct MayBlock {};
enum class BlockingType { MAY_BLOCK };
struct ScopedBlockingCall { ScopedBlockingCall(int,BlockingType){} };
int scheduled=0;
struct TaskRunner { template<class Task,class Reply> void PostTaskAndReplyWithResult(int,Task task,Reply reply){++scheduled;reply(task());} };
struct ThreadPool { static std::shared_ptr<TaskRunner> CreateCOMSTATaskRunner(std::initializer_list<MayBlock>){return std::make_shared<TaskRunner>();} };
template<class T>T* Unretained(T* p){return p;}
template<class C,class R,class A>auto BindOnce(R(C::*method)(A),C* object,A argument){return [object,method,argument=std::move(argument)]()mutable{return (object->*method)(std::move(argument));};}
}
namespace enterprise_signals {
using SettingValue=device_signals::SettingValue;
int id_service_accesses=0;
struct ContextInfo {
  std::vector<std::string> browser_affiliation_ids,profile_affiliation_ids,on_file_attached_providers,on_file_downloaded_providers,on_bulk_data_entry_providers,on_print_providers,on_security_event_providers,system_dns_servers;
  int realtime_url_check_mode=0;
  std::string browser_version;
  safe_browsing::SafeBrowsingState safe_browsing_protection_level=safe_browsing::SafeBrowsingState::NONE;
  std::optional<safe_browsing::PasswordProtectionTrigger> password_protection_warning_trigger;
  std::optional<std::string> enterprise_profile_id;
  bool site_isolation_enabled=false,built_in_dns_client_enabled=false,chrome_remote_desktop_app_blocked=false;
  SettingValue os_firewall=SettingValue::UNKNOWN;
};
class ContextInfoFetcher {
 public:
  using ContextInfoCallback=std::function<void(ContextInfo)>;
  explicit ContextInfoFetcher(content::BrowserContext* context):browser_context_(context){}
  void Fetch(ContextInfoCallback callback);
 private:
  content::BrowserContext* browser_context_;
  ContextInfo FetchAsyncSignals(ContextInfo info);
  std::vector<std::string> GetBrowserAffiliationIDs(){return {"browser"};}
  std::vector<std::string> GetProfileAffiliationIDs(){return {"profile"};}
#if BUILDFLAG(ENTERPRISE_CLOUD_CONTENT_ANALYSIS)
  std::vector<std::string> GetAnalysisConnectorProviders(enterprise_connectors::AnalysisConnector){return {"existing-provider"};}
  int GetRealtimeUrlCheckMode(){return 2;}
  std::vector<std::string> GetOnSecurityEventProviders(){return {"existing-provider"};}
#endif
  std::vector<std::string> GetDnsServers(){return {"192.0.2.53"};}
  SettingValue GetOSFirewall(){return SettingValue::ENABLED;}
};
'''
    if collector:
        prefix += 'std::optional<std::string> GetEnterpriseProfileId(Profile*){++id_service_accesses;return "generated-id";}\n'
    return prefix + dns + '\n' + asynchronous + '\n' + fetch + r'''
}
int main(){
  Profile profile;
  enterprise_signals::ContextInfoFetcher fetcher(&profile);
  int callbacks=0;
  std::optional<enterprise_signals::ContextInfo> result;
  fetcher.Fetch([&](enterprise_signals::ContextInfo info){++callbacks;result.emplace(std::move(info));});
  if(callbacks!=1||base::scheduled!=1||!result)return 10;
  if(result->safe_browsing_protection_level!=profile.prefs.protection||result->password_protection_warning_trigger!=profile.prefs.warning)return 11;
  if(result->enterprise_profile_id||enterprise_signals::id_service_accesses!=0)return 20;
  if(!result->site_isolation_enabled||!result->built_in_dns_client_enabled||result->browser_version!="156.0.8078.79"||result->system_dns_servers!=std::vector<std::string>{"192.0.2.53"})return 30;
  std::puts("{\"security_reads\":true,\"callback_count\":1,\"enterprise_profile_id_empty\":true,\"id_service_accesses\":0}");
}
'''

@unittest.skipUnless(os.environ.get('CHROMIX_STAGE3_REPAIR_SOURCE'), 'Supply the effective prepared source for executed C++ coverage')
class EnterpriseScopeNativeTest(unittest.TestCase):
    def test_real_fetch_fragment_keeps_security_callback_without_identifier_service(self):
        source = Path(os.environ['CHROMIX_STAGE3_REPAIR_SOURCE']).resolve() / SOURCE
        text = source.read_text(encoding='utf-8')
        root = Path(tempfile.mkdtemp(prefix='enterprise-native-', dir=os.environ.get('CHROMIX_STAGE3_REPAIR_EVIDENCE')))
        compiler = shutil.which('clang++') or 'C:/Program Files/LLVM/bin/clang++.exe'
        self.assertTrue(Path(compiler).is_file())
        environment = compiler_environment(root)
        assignment = '  info.enterprise_profile_id = GetEnterpriseProfileId(profile);\n'
        original = text.replace('#if BUILDFLAG(IS_WIN)\n', assignment + '#if BUILDFLAG(IS_WIN)\n', 1)
        no_safe_read = text.replace('  info.safe_browsing_protection_level =\n      device_signals::GetSafeBrowsingProtectionLevel(profile->GetPrefs());\n', '')
        no_password_read = text.replace('  info.password_protection_warning_trigger =\n      device_signals::GetPasswordProtectionWarningTrigger(profile->GetPrefs());\n', '')
        self.assertNotEqual(text, no_safe_read)
        self.assertNotEqual(text, no_password_read)
        cases = [('scope-cloud0', fetch_probe(text), 0, 0),
                 ('scope-cloud1', fetch_probe(text), 1, 0),
                 ('old-unresolved-id', fetch_probe(original), 0, None),
                 ('unwanted-id-collection', fetch_probe(original, collector=True), 0, 20),
                 ('missing-safe-browsing', fetch_probe(no_safe_read), 0, 11),
                 ('missing-password-protection', fetch_probe(no_password_read), 0, 11)]
        records = []
        for name, code, cloud, expected in cases:
            cpp, binary = root / (name + '.cc'), root / (name + ('.exe' if os.name == 'nt' else ''))
            cpp.write_text(code, encoding='utf-8')
            compiled = subprocess.run([compiler, '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                       '-DENTERPRISE_CLOUD_CONTENT_ANALYSIS=' + str(cloud), str(cpp), '-o', str(binary)],
                                      env=environment, capture_output=True, text=True, timeout=90)
            (root / (name + '.compile.log')).write_text(compiled.stdout + compiled.stderr, encoding='utf-8')
            record = {'case': name, 'compile_exit': compiled.returncode}
            if expected is None:
                self.assertNotEqual(compiled.returncode, 0)
                self.assertIn('GetEnterpriseProfileId', compiled.stderr)
            else:
                self.assertEqual(compiled.returncode, 0, name + '\n' + compiled.stdout + compiled.stderr)
                run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=20)
                self.assertEqual(run.returncode, expected, name + '\n' + run.stdout + run.stderr)
                record.update(run_exit=run.returncode, stdout=run.stdout.strip())
            records.append(record)
        receipt = {'source_file': str(source), 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                   'cases': records, 'full_chromium_build': False, 'network_executed': False, 'browser_executed': False,
                   'scope': 'actual Fetch and FetchAsyncSignals fragments with local dependency/task-runner models'}
        (root / 'result.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        print('enterprise_native_evidence=' + str(root / 'result.json'))

if __name__ == '__main__':
    unittest.main()
