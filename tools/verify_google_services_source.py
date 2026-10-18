#!/usr/bin/env python3
"""Cheap source checks for the sign-in/persistence regressions found in review.

This is not an account/network test. It prevents known disabled implementations
from reaching a long native compile and complements the later interactive probe.
"""
from __future__ import annotations
import argparse
from pathlib import Path
import re

REQUIRED = {
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
    'components/sync/service/sync_prefs.cc': ['::prefs::kExplicitBrowserSignin', '::prefs::kPrefsThemesSearchEnginesAccountStorageEnabled'],
    'chrome/browser/ui/webui/settings/settings_ui.cc': ['GetBoolean(prefs::kSigninAllowed)'],
    'chrome/browser/ui/toolbar/app_menu_model.cc': ['info.should_build_signin_section = true;', 'AddTitle(info.title);'],
}
FORBIDDEN = {
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
        source = re.sub(r'\s+', '', path.read_text(encoding='utf-8'))
        for text in required:
            if re.sub(r'\s+', '', text) not in source:
                errors.append(name + ': missing required sign-in/persistence implementation')
        for text in FORBIDDEN.get(name, []):
            if re.sub(r'\s+', '', text) in source:
                errors.append(name + ': sign-in remains hard-disabled')
    if errors:
        raise ValueError('\n'.join(errors))
    return list(REQUIRED)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('src', type=Path)
    args = parser.parse_args()
    checked = verify(args.src)
    print(f'Sign-in and persistence source checks passed ({len(checked)} files; cloud access not tested)')
