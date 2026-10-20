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
    'content/browser/webauth/webauth_request_security_checker_impl.cc': ['9oo91e.qjz9zk', '95tat1c.qjz9zk'],
    'content/browser/webauth/webauth_request_security_checker_impl.h': ['9oo91e.qjz9zk', '95tat1c.qjz9zk'],
    'components/signin/internal/identity_manager/primary_account_mutator_impl.cc': ['bool is_signin_allowed = false;'],
    'chrome/browser/ui/webui/settings/settings_ui.cc': ['AddBoolean("signinAllowed", false)'],
}


def verify(src: Path):
    errors = []
    for name, required in REQUIRED.items():
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
        source = re.sub(r'\s+', '', source)
        for text in required:
            if re.sub(r'\s+', '', text) not in source:
                errors.append(name + ': missing required Google-service implementation')
        for text in FORBIDDEN.get(name, []):
            if re.sub(r'\s+', '', text) in source:
                errors.append(name + ': Google-service implementation remains disabled or substituted')
    if errors:
        raise ValueError('\n'.join(errors))
    return list(REQUIRED)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('src', type=Path)
    args = parser.parse_args()
    checked = verify(args.src)
    print(f'Sign-in and persistence source checks passed ({len(checked)} files; cloud access not tested)')
