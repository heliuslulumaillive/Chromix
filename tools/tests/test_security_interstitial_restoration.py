"""Native reporting interface/dispatch and preference-write closure regression.

The compiled probe uses exact declarations and method bodies from the effective
prepared source, with small PrefService/metrics transport models. It is not a
full Chromium translation-unit build or a live Safe Browsing service test.
"""
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
with mock.patch.object(sys, 'path', [str(ROOT / 'tools'), *sys.path]):
    import verify_google_services_source as verifier
    from build_lite_retirement_patch import matching
from tools.tests import test_security_restoration as restoration_tests

DERIVED_H = 'components/security_interstitials/content/security_interstitial_controller_client.h'
DERIVED_CC = 'components/security_interstitials/content/security_interstitial_controller_client.cc'
BASE_H = 'components/security_interstitials/core/controller_client.h'
BASE_CC = 'components/security_interstitials/core/controller_client.cc'
PREFS_CC = 'components/safe_browsing/core/common/safe_browsing_prefs.cc'
PATCH_NAME = 'core/ungoogled-chromium/remove-unused-preferences-fields.patch'
INTERFACE = 'GetExtendedReportingPrefName'

def declaration(text, pattern):
    match = re.search(pattern, text)
    if not match:
        raise AssertionError('Effective source lacks native declaration: ' + pattern)
    return match[0]

def definition(text, pattern):
    match = re.search(pattern, text)
    if not match:
        raise AssertionError('Effective source lacks native definition: ' + pattern)
    brace = text.index('{', match.start())
    return text[match.start():matching(text, brace, '{', '}') + 1]

def register_pref(text, name):
    for match in re.finditer(r'registry->RegisterBooleanPref\s*\(', text):
        opening = text.index('(', match.start())
        end = matching(text, opening) + 1
        statement = text[match.start():end + 1]
        if re.search(r'\b' + re.escape(name) + r'\b', statement):
            return statement
    raise AssertionError('Effective source lacks native preference registration: ' + name)

def compiler_environment(root):
    environment = dict(os.environ)
    if os.name == 'nt':
        vcvars = Path('C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/VC/Auxiliary/Build/vcvars64.bat')
        if not vcvars.is_file():
            raise RuntimeError('The local Visual Studio environment is required')
        setup = root / 'compiler-environment.cmd'
        setup.write_text('@echo off\ncall "' + str(vcvars) + '" >nul\nif errorlevel 1 exit /b 1\nset\n')
        run = subprocess.run(['cmd.exe', '/d', '/c', str(setup)], capture_output=True, text=True, check=True)
        for line in run.stdout.splitlines():
            if '=' in line:
                name, value = line.split('=', 1)
                environment[name] = value
    return environment

def native_probe(src):
    texts = {name: (src / name).read_text(encoding='utf-8') for name in (BASE_H, BASE_CC, DERIVED_H, DERIVED_CC, PREFS_CC)}
    base_name = declaration(texts[BASE_H], r'virtual\s+const std::string GetExtendedReportingPrefName\(\) const = 0;')
    derived_name = declaration(texts[DERIVED_H], r'const std::string GetExtendedReportingPrefName\(\) const override;')
    base_service = declaration(texts[BASE_H], r'virtual PrefService\* GetPrefService\(\) = 0;')
    derived_service = declaration(texts[DERIVED_H], r'PrefService\* GetPrefService\(\) override;')
    setter_decl = declaration(texts[BASE_H], r'void SetReportingPreference\(bool report\);')
    setter = definition(texts[BASE_CC], r'void ControllerClient::SetReportingPreference\(bool report\)\s*\{')
    name_getter = definition(texts[DERIVED_CC], r'const std::string\s+SecurityInterstitialControllerClient::GetExtendedReportingPrefName\(\) const\s*\{')
    service_getter = definition(texts[DERIVED_CC], r'PrefService\* SecurityInterstitialControllerClient::GetPrefService\(\)\s*\{')
    registrations = [register_pref(texts[PREFS_CC], name) for name in (
        'kSafeBrowsingEnabled', 'kSafeBrowsingEnhanced', 'kSafeBrowsingScoutReportingEnabled')]
    code = r'''#include <cassert>
#include <cstdio>
#include <map>
#include <string>
#include <type_traits>
#include <vector>
#define DCHECK(condition) assert(condition)
namespace prefs {
inline constexpr char kSafeBrowsingEnabled[]="safebrowsing.enabled";
inline constexpr char kSafeBrowsingEnhanced[]="safebrowsing.enhanced";
inline constexpr char kSafeBrowsingScoutReportingEnabled[]="safebrowsing.scout_reporting_enabled";
}
namespace user_prefs { struct PrefRegistrySyncable { static constexpr int SYNCABLE_PREF=1; }; }
struct PrefRegistry {
  std::map<std::string,bool> values;
  void RegisterBooleanPref(const std::string& name,bool value,int=0) { values[name]=value; }
};
struct PrefService {
  std::map<std::string,bool> values;
  std::vector<std::pair<std::string,bool>> writes;
  void SetBoolean(const std::string& name,bool value) { values[name]=value; writes.emplace_back(name,value); }
};
namespace security_interstitials {
struct MetricsHelper {
  enum { SET_EXTENDED_REPORTING_ENABLED=1, SET_EXTENDED_REPORTING_DISABLED=2 };
  std::vector<int> events;
  void RecordUserInteraction(int event) { events.push_back(event); }
};
class ControllerClient {
 public:
  explicit ControllerClient(MetricsHelper* metrics):metrics_helper_(metrics) {}
  virtual ~ControllerClient()=default;
''' + base_service + '\n' + setter_decl + '\n protected:\n' + base_name + r'''
 private:
  MetricsHelper* metrics_helper_;
};
class SecurityInterstitialControllerClient:public ControllerClient {
 public:
  SecurityInterstitialControllerClient(PrefService* prefs,MetricsHelper* metrics):ControllerClient(metrics),prefs_(prefs) {}
''' + derived_service + '\n protected:\n' + derived_name + r'''
 private:
  PrefService* prefs_;
};
''' + setter + '\n' + name_getter + '\n' + service_getter + r'''
static_assert(std::is_abstract_v<ControllerClient>);
static_assert(!std::is_abstract_v<SecurityInterstitialControllerClient>);
}
int main() {
  PrefRegistry defaults;
  auto* registry=&defaults;
''' + '\n'.join(registrations) + r'''
  PrefService prefs{defaults.values,{}};
  const auto standard=prefs::kSafeBrowsingEnabled;
  const auto enhanced=prefs::kSafeBrowsingEnhanced;
  const auto reporting=prefs::kSafeBrowsingScoutReportingEnabled;
  if(!prefs.values[standard]||prefs.values[enhanced]||prefs.values[reporting])return 10;
  security_interstitials::MetricsHelper metrics;
  security_interstitials::SecurityInterstitialControllerClient controller(&prefs,&metrics);
  if(!prefs.writes.empty())return 11;
  security_interstitials::ControllerClient& native_interface=controller;
  native_interface.SetReportingPreference(true);
  if(!prefs.values[reporting]||!prefs.values[standard]||prefs.values[enhanced]||prefs.writes.size()!=1)return 20;
  if(prefs.writes[0]!=std::make_pair(std::string(reporting),true)||metrics.events!=std::vector<int>{1})return 21;
  native_interface.SetReportingPreference(false);
  if(prefs.values[reporting]||!prefs.values[standard]||prefs.values[enhanced]||prefs.writes.size()!=2)return 30;
  if(prefs.writes[1]!=std::make_pair(std::string(reporting),false)||metrics.events!=std::vector<int>({1,2}))return 31;
  std::puts("{\"native_interface\":true,\"virtual_dispatch\":true,\"preference_writes\":2,\"standard\":true,\"enhanced\":false,\"reporting\":false}");
}
'''
    return code, {'base_declaration': base_name, 'derived_declaration': derived_name,
                  'reporting_registration': registrations[2]}, texts

class InterstitialSourceClosureTest(unittest.TestCase):
    def test_fixed_recipe_restores_all_three_interface_hunks(self):
        config = json.loads((ROOT / 'build/google-services-win155.json').read_text())
        self.assertEqual(config['chromium_version'], '156.0.8078.79')
        for name in (DERIVED_H, BASE_H, BASE_CC):
            self.assertEqual(config['restore_hunks'][PATCH_NAME][name], [INTERFACE])

    def test_missing_declarations_or_setter_write_fail_source_admission(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp)
            restoration_tests.SecuritySourceGateTest().fixture(src)
            verifier.verify(src, require_security=True)
            cases = {DERIVED_H: 'const std::string GetExtendedReportingPrefName() const override;',
                     BASE_H: 'virtual const std::string GetExtendedReportingPrefName() const = 0;',
                     BASE_CC: 'GetPrefService()->SetBoolean(GetExtendedReportingPrefName(), report);'}
            for name, fragment in cases.items():
                path = src / name
                original = path.read_text()
                path.write_text(original.replace(fragment, '// ' + fragment), encoding='utf-8')
                with self.subTest(path=name), self.assertRaisesRegex(ValueError, 'security_interstitials'):
                    verifier.verify(src, require_security=True)
                path.write_text(original, encoding='utf-8')

    def test_decoy_write_after_return_or_in_false_branch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            src = Path(temp)
            restoration_tests.SecuritySourceGateTest().fixture(src)
            path = src / BASE_CC
            original = path.read_text()
            for body in ('return; ', 'if (false) '):
                path.write_text(original.replace('SetReportingPreference(bool report) {',
                                                'SetReportingPreference(bool report) {' + body))
                with self.subTest(body=body), self.assertRaisesRegex(ValueError, 'write is bypassed or guarded'):
                    verifier.verify(src, require_security=True)

@unittest.skipUnless(os.environ.get('CHROMIX_SECURITY_REPAIR_SOURCE'),
                     'Supply the effective replay tree for executed C++ interface coverage')
class InterstitialNativeProbeTest(unittest.TestCase):
    def test_actual_declarations_dispatch_defaults_and_mutation_controls(self):
        src = Path(os.environ['CHROMIX_SECURITY_REPAIR_SOURCE']).resolve()
        evidence = os.environ.get('CHROMIX_SECURITY_REPAIR_EVIDENCE')
        root = Path(tempfile.mkdtemp(prefix='interstitial-native-', dir=evidence))
        compiler = shutil.which('clang++') or 'C:/Program Files/LLVM/bin/clang++.exe'
        self.assertTrue(Path(compiler).is_file(), 'Executed coverage requires a C++ compiler')
        environment = compiler_environment(root)
        code, fragments, sources = native_probe(src)
        records = []
        mutations = [
            ('native', code, 0, 0),
            ('missing-derived-declaration', code.replace(fragments['derived_declaration'], ''), 1, None),
            ('missing-base-virtual', code.replace(fragments['base_declaration'], ''), 1, None),
            ('missing-preference-write', code.replace('GetPrefService()->SetBoolean(GetExtendedReportingPrefName(), report);', ''), 0, 20),
            ('wrong-preference-target', code.replace('return prefs::kSafeBrowsingScoutReportingEnabled;', 'return prefs::kSafeBrowsingEnhanced;'), 0, 20),
            ('reporting-default-enabled', code.replace(fragments['reporting_registration'], fragments['reporting_registration'].replace('false', 'true')), 0, 10),
        ]
        for name, content, expected_compile, expected_run in mutations:
            cpp = root / (name + '.cc')
            executable = root / (name + ('.exe' if os.name == 'nt' else ''))
            cpp.write_text(content, encoding='utf-8')
            built = subprocess.run([compiler, '-std=c++20', '-Wall', '-Wextra', '-Werror',
                                    str(cpp), '-o', str(executable)], env=environment,
                                   capture_output=True, text=True, timeout=90)
            (root / (name + '.compile.log')).write_text(built.stdout + built.stderr, encoding='utf-8')
            record = {'case': name, 'compile_exit': built.returncode}
            if expected_compile:
                self.assertNotEqual(built.returncode, 0, name)
                self.assertIn(INTERFACE, built.stderr, name)
            else:
                self.assertEqual(built.returncode, 0, name + '\n' + built.stdout + built.stderr)
                run = subprocess.run([str(executable)], capture_output=True, text=True, timeout=20)
                record.update(run_exit=run.returncode, stdout=run.stdout.strip())
                self.assertEqual(run.returncode, expected_run, name + '\n' + run.stdout + run.stderr)
            records.append(record)
        result = {'source_tree': str(src), 'compiler': compiler,
                  'source_sha256': {name: hashlib.sha256((src / name).read_bytes()).hexdigest() for name in sources},
                  'cases': records, 'full_chromium_compilation': False, 'browser_run': False}
        (root / 'result.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
        print('interstitial_native_evidence=' + str(root / 'result.json'))

if __name__ == '__main__':
    unittest.main()
