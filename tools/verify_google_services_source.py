#!/usr/bin/env python3
"""Cheap source checks for the sign-in/persistence regressions found in review.

This is not an account/network test. It prevents known disabled implementations
from reaching a long native compile and complements the later interactive probe.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re

CPP_TOKENS = re.compile(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|(?P<comment>//[^\r\n]*|/\*[\s\S]*?\*/)')

REQUIRED = {
    'base/BUILD.gn': ['"uxr_voice_compat.h"', '"uxr_voice_compat_tests.cc"'],
    'base/uxr_surface_modes_test_main.cc': [
        'const int voices = RunVoiceCompatibilityTests();',
        'Check(voices == 0, "Windows voice compatibility routing checks");',
    ],
    'content/browser/speech/tts_win.cc': [
        '#include "base/uxr_voice_compat.h"',
        'SetVoiceFromName(base::uxr_voice_compat::NativeVoiceName(voice));',
        'base::uxr_voice_compat::AppendWindowsCompatibilityVoices(out_voices);',
    ],
    'BUILD.gn': ['"//chrome/test:native_passkey_unittests"'],
    'chrome/test/BUILD.gn': [
        'test("native_passkey_unittests")',
        '"../browser/webauthn/authenticator_request_dialog_model_unittest.cc"',
        '"../../device/fido/win/authenticator_unittest.cc"',
        'assert_no_deps = [',
        '"//chrome/test/base:chrome_render_view_host_test_harness"',
        '"//chrome/test/base:chrome_unit_test_suite"',
        '"//chrome/test/data/webui:*"',
    ],
    'chrome/test/base/BUILD.gn': [
        '"//chrome/browser/extensions:test_extension_system"',
    ],
    'chrome/browser/extensions/BUILD.gn': [
        'source_set("test_extension_system")',
        'public_deps = [ ":test_extension_system" ]',
    ],
    'chrome/test/base/testing_browser_process.h': [
        '#if BUILDFLAG(SAFE_BROWSING_AVAILABLE)\n'
        '  safe_browsing::SafeBrowsingService* safe_browsing_service() override;\n'
        '#endif',
        '#if BUILDFLAG(SAFE_BROWSING_AVAILABLE)\n'
        '  void SetSafeBrowsingService(safe_browsing::SafeBrowsingService* sb_service);\n'
        '#endif',
        '#if BUILDFLAG(SAFE_BROWSING_AVAILABLE)\n'
        '  scoped_refptr<safe_browsing::SafeBrowsingService> sb_service_;\n'
        '#endif',
    ],
    'chrome/test/base/testing_browser_process.cc': [
        '#if BUILDFLAG(SAFE_BROWSING_AVAILABLE)\n'
        'safe_browsing::SafeBrowsingService*\n'
        'TestingBrowserProcess::safe_browsing_service()',
        '#if BUILDFLAG(SAFE_BROWSING_AVAILABLE)\n'
        'void TestingBrowserProcess::SetSafeBrowsingService(',
    ],
    'chrome/browser/webauthn/authenticator_request_dialog_controller.cc': [
        'std::get_if<Mechanism::Credential>(&model_->mechanisms[0].type)',
        'transport_availability_.has_win_native_api_authenticator && credential &&',
        'credential->value().source == AuthenticatorType::kWinNative',
    ],
    'chrome/browser/webauthn/authenticator_request_dialog_model_unittest.cc': [
        'WinSingleCachedCredentialPreservesOriginalAllowList',
        'Automatic Windows dispatch must preserve the RP allow-list',
    ],
    'device/fido/win/authenticator.cc': [
        'api->Version() >= WEBAUTHN_API_VERSION_9',
        'credentials.empty() && supports_native_passkey_providers',
        'FilterFoundCredentials(&credentials, allow_list)',
        'FidoRequestHandlerBase::RecognizedCredential::kUnknown',
    ],
    'device/fido/win/authenticator_unittest.cc': [
        'GetCredentialInformationForRequest_NativeProvidersEmptyCache',
        'GetCredentialInformationForRequest_NativeProvidersFilteredCache',
        'GetCredentialInformationForRequest_NativeProvidersPreservesMatch',
        'GetCredentialInformationForRequest_NativeProvidersRespectsTransports',
        'GetCredentialInformationForRequest_NativeProvidersUnavailableBeforeV9',
    ],
    'chrome/browser/BUILD.gn': ['"//chrome/browser/security_events"'],
    'chrome/browser/enterprise/BUILD.gn': ['"signin/managed_profile_creation_controller.cc"'],
    'chrome/browser/enterprise/signin/managed_profile_creation_controller.cc': [
        'ManagedProfileCreationController::CreateManagedProfile(',
        'ManagedProfileCreationController::CreateManagedProfileForTesting(',
    ],
    'chrome/browser/security_events/BUILD.gn': ['"security_event_recorder_factory.cc"'],
    'chrome/browser/security_events/security_event_recorder_factory.cc': [
        'SecurityEventRecorderFactory::GetInstance()',
        'SecurityEventRecorderFactory::GetForProfile(',
    ],
    'components/browser_sync/BUILD.gn': ['"sync_to_signin_migration.cc"', '"sync_to_signin_migration.h"'],
    'components/browser_sync/sync_to_signin_migration.cc': [
        'void MaybeMigrateSyncingUserToSignedIn(',
        'void MaybeMigrateSyncingUserToSignedInAsync(',
    ],
    'content/browser/webauth/webauth_request_security_checker_impl.cc': [
        'caller_origin.DomainIs("google.com")',
        '!appid_url.has_ref()',
        'appid_url.EqualsIgnoringRef(gstatic_appid)',
        'appid_url.EqualsIgnoringRef(gstatic_corp_appid)',
    ],
    'content/browser/webauth/webauth_request_security_checker_impl.h': [
        'https://www.gstatic.com/securitykey/origins.json',
        'https://www.gstatic.com/securitykey/a/google.com/origins.json',
    ],
    'components/signin/internal/identity_manager/primary_account_mutator_impl.cc': [
        'pref_service_->GetBoolean(prefs::kSigninAllowed)',
    ],
    'chrome/browser/signin/account_consistency_mode_manager.cc': [
        'return AccountConsistencyMethod::kDice;',
        'registry->RegisterBooleanPref(prefs::kSigninAllowedOnNextStartup, true)',
    ],
    'components/signin/internal/identity_manager/primary_account_manager.cc': [
        'prefs->GetString(prefs::kGoogleServicesAccountId)',
        'prefs->GetBoolean(prefs::kGoogleServicesConsentedToSync)',
        'scoped_pref_commit.SetString(prefs::kGoogleServicesAccountId, account_id)',
        'scoped_pref_commit.SetBoolean(prefs::kGoogleServicesConsentedToSync,',
    ],
    'components/signin/internal/identity_manager/account_tracker_service.cc': [
        'pref_service_->GetList(prefs::kAccountInfo)',
        'ScopedListPrefUpdate update(pref_service_, prefs::kAccountInfo)',
        'dict->Merge(signin::SerializeAccountInfo(account_info))',
    ],
    'components/signin/internal/identity_manager/profile_oauth2_token_service.cc': [
        'registry->RegisterStringPref(prefs::kGoogleServicesSigninScopedDeviceId,',
    ],
    'components/signin/public/base/device_id_helper.cc': [
        'prefs->GetString(prefs::kGoogleServicesSigninScopedDeviceId)',
        'prefs->SetString(prefs::kGoogleServicesSigninScopedDeviceId,',
    ],
    'components/signin/public/base/BUILD.gn': ['"signin_pref_names.cc"', '"signin_pref_names.h"'],
    'components/signin/public/base/signin_pref_names.h': ['kSigninAllowed', 'kAccountInfo', 'kGoogleServicesAccountId'],
    'components/signin/public/base/signin_pref_names.cc': [
        'const char kAccountInfo[] = "account_info";',
        'const char kGoogleServicesAccountId[] = "google.services.account_id";',
    ],
    'components/sync/service/sync_prefs.cc': ['::prefs::kExplicitBrowserSignin', '::prefs::kPrefsThemesSearchEnginesAccountStorageEnabled'],
    'chrome/browser/ui/webui/settings/settings_ui.cc': ['GetBoolean(prefs::kSigninAllowed)'],
    'chrome/browser/ui/toolbar/app_menu_model.cc': ['info.should_build_signin_section = true;', 'AddTitle(info.title);'],
    'chrome/browser/sync/sync_service_factory.cc': [
        'builder.SetSecurityEventRecorder(SecurityEventRecorderFactory::GetForProfile(profile))',
        'DependsOn(SecurityEventRecorderFactory::GetInstance())',
        'std::make_unique<syncer::SyncServiceImpl>(std::move(init_params))',
        'sync_service->Initialize(CreateControllers(profile, sync_service.get()))',
        'account_password_store->OnSyncServiceInitialized(sync_service.get())',
        'pref_service->OnSyncServiceInitialized(sync_service.get())',
        'return sync_service;',
    ],
}
FORBIDDEN = {
    'chrome/test/base/testing_browser_process.h': [
        'speech_recognition_small_expert_model_installer',
        'SpeechRecognitionSmallExpertModelInstaller',
    ],
    'chrome/test/base/testing_browser_process.cc': [
        'speech_recognition_small_expert_model_installer',
        'SpeechRecognitionSmallExpertModelInstaller',
    ],
    'content/browser/webauth/webauth_request_security_checker_impl.cc': ['9oo91e.qjz9zk', '95tat1c.qjz9zk'],
    'content/browser/webauth/webauth_request_security_checker_impl.h': ['9oo91e.qjz9zk', '95tat1c.qjz9zk'],
    'components/signin/internal/identity_manager/primary_account_mutator_impl.cc': ['bool is_signin_allowed = false;'],
    'chrome/browser/ui/webui/settings/settings_ui.cc': ['AddBoolean("signinAllowed", false)'],
}

SECURITY_REQUIRED = {
    'chrome/browser/BUILD.gn': ['"//chrome/browser/safe_browsing"'],
    'chrome/browser/safe_browsing/BUILD.gn': [
        'static_library("safe_browsing")', '"safe_browsing_service.cc"',
    ],
    'components/safe_browsing/core/common/BUILD.gn': [
        'static_library("safe_browsing_prefs")', '"safe_browsing_prefs.cc"',
    ],
    'components/safe_browsing/core/common/safe_browsing_prefs.cc': [
        'prefs::kSafeBrowsingEnabled, true,',
        'prefs::kSafeBrowsingEnhanced, false',
        'prefs::kSafeBrowsingScoutReportingEnabled, false',
    ],
    'chrome/browser/prefs/browser_prefs.cc': [
        'safe_browsing::RegisterLocalStatePrefs(registry)',
    ],
    'chrome/browser/browser_process_impl.cc': [
        'safe_browsing::SafeBrowsingServiceInterface::CreateSafeBrowsingService()',
        'safe_browsing_service_->Initialize()',
    ],
    'chrome/browser/safe_browsing/safe_browsing_service.cc': [
        'services_delegate_->Initialize()',
        'base::BindRepeating(&SafeBrowsingServiceImpl::RefreshState,',
    ],
    'chrome/browser/safe_browsing/services_delegate_desktop.cc': [
        'database_manager_ = CreateDatabaseManager()',
        'services_creator_->CreateDownloadProtectionService()',
        'database_manager_->StartOnUIThread(browser_url_loader_factory, config)',
    ],
    'components/safe_browsing/core/browser/client_side_detection_service_base.cc': [
        'network::SimpleURLLoader::Create(std::move(resource_request),',
        'loader->AttachStringForUpload(request_data, "application/octet-stream")',
    ],
    'components/resources/safe_browsing_resources.grdp': [
        'IDR_DOWNLOAD_FILE_TYPES_PB', 'download_file_types.pb',
    ],
    'components/safe_browsing/content/resources/BUILD.gn': [
        'action("make_file_types_protobuf")',
        'input_filename = "download_file_types.asciipb"',
    ],
    'components/safe_browsing/content/resources/download_file_types.asciipb': [
        'extension: "exe"',
    ],
    'chrome/browser/component_updater/registration.cc': [
        'RegisterFileTypePoliciesComponent(cus)',
    ],
    'chrome/browser/component_updater/file_type_policies_component_installer.cc': [
        'safe_browsing::FileTypePolicies::GetInstance()->PopulateFromDynamicUpdate(',
        'installer->Register(cus, base::OnceClosure())',
    ],
    'components/update_client/update_checker.cc': [
        'update_check_callback_ = std::move(update_check_callback)',
        '&UpdateCheckerImpl::CheckForUpdatesHelper', 'config_->UpdateUrl()',
    ],
    'components/download/internal/common/download_item_impl.cc': [
        'danger_type_ = danger_type;',
    ],
    'components/password_manager/core/browser/leak_detection/leak_detection_check_impl.cc': [
        'prefs.GetBoolean(prefs::kPasswordLeakDetectionEnabled)',
        'return is_leak_protection_on && !LeakDetectionCheck::IsURLBlockedByPolicy(',
    ],
    'components/password_manager/core/browser/leak_detection_delegate.cc': [
        'LeakDetectionCheck::CanStartLeakCheck(',
        'leak_factory_->TryCreateLeakCheck(', 'leak_check_->Start(',
    ],
    'chrome/browser/extensions/api/settings_private/prefs_util.cc': [
        '(*s_allowlist)[::prefs::kSafeBrowsingEnabled]',
        '(*s_allowlist)[::prefs::kSafeBrowsingEnhanced]',
        '(*s_allowlist)[password_manager::prefs::kPasswordLeakDetectionEnabled]',
    ],
    'chrome/browser/resources/settings/privacy_page/security/security_page.html': [
        'id="safeBrowsingSection"', 'passwordsLeakDetectionLabel',
    ],
    'chrome/browser/resources/settings/privacy_page/privacy_page_index.ts': [
        'this.showPage_(this.pageVisibility_.safetyHub)',
        "defaultViews.push('safetyHubEntryPoint')",
    ],
}


def function_body(source: str, name: str) -> str:
    match = re.search(re.escape(name) + r'\s*\([^;{}]*\)\s*(?:const\s*)?\{', source)
    if not match:
        raise ValueError('Security implementation is missing: ' + name)
    depth = 1
    for end in range(match.end(), len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if depth == 0:
            return source[match.end():end]
    raise ValueError('Incomplete security implementation: ' + name)


def verify(src: Path, *, require_security=False, require_lite_retirement=False):
    errors = []
    requirements = {name: list(required) for name, required in REQUIRED.items()}
    if require_security:
        for name, required in SECURITY_REQUIRED.items():
            requirements.setdefault(name, []).extend(required)
    for name, required in requirements.items():
        path = src / name
        if not path.is_file():
            errors.append(name + ': missing source')
            continue
        # Domain restoration intentionally leaves comment-only substitutions
        # untouched to preserve incremental builds. Check executable code only.
        text = path.read_text(encoding='utf-8')
        if path.suffix == '.gn':
            text = re.sub(r'(?m)^\s*#.*$', '', text)
        source = CPP_TOKENS.sub(lambda token: '' if token.group('comment') else token[0], text)
        if require_security:
            if name in ('chrome/browser/safe_browsing/BUILD.gn',
                        'components/safe_browsing/core/common/BUILD.gn') and re.search(r'\bif\s*\(\s*false\s*\)', source):
                errors.append(name + ': security build target is disabled')
            if name.endswith('/leak_detection_check_impl.cc'):
                body = function_body(source, 'LeakDetectionCheck::CanStartLeakCheck')
                if re.search(r'\breturn\s+(?:false|true)\s*;', body):
                    errors.append(name + ': password leak checking or its policy gate is bypassed')
            if name.endswith('/download_item_impl.cc'):
                body = function_body(source, 'DownloadItemImpl::SetDangerType')
                if re.search(r'danger_type_\s*=\s*(?:download::)?DOWNLOAD_DANGER_TYPE_NOT_DANGEROUS', body):
                    errors.append(name + ': download danger classification is discarded')
            if name == 'components/update_client/update_checker.cc':
                body = function_body(source, 'UpdateCheckerImpl::CheckForUpdates')
                if re.match(r'\s*(?:if\s*\(\s*\(?\s*true\s*\)?\s*\)\s*)?return\s*;', body):
                    errors.append(name + ': security component update requests are disabled')
        source = re.sub(r'\s+', '', source)
        for text in required:
            if re.sub(r'\s+', '', text) not in source:
                errors.append(name + ': missing required Google-service implementation')
        for text in FORBIDDEN.get(name, []):
            if re.sub(r'\s+', '', text) in source:
                errors.append(name + ': Google-service implementation remains disabled or substituted')
    if require_lite_retirement:
        try:
            from .lite_retirement import verify_retirement
        except ImportError:
            from lite_retirement import verify_retirement
        try:
            verify_retirement(src)
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError('\n'.join(errors))
    return list(requirements)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('src', type=Path)
    parser.add_argument('--security', action='store_true')
    parser.add_argument('--lite-retirement', action='store_true')
    args = parser.parse_args()
    checked = verify(args.src, require_security=args.security,
                     require_lite_retirement=args.lite_retirement)
    print(f'Sign-in and persistence source checks passed ({len(checked)} files; cloud access not tested)')
