"""Source gate for the Windows native-browser contract, not runtime acceptance."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

CONTRACT = 'shardx-native-browser-v1'
CAPABILITIES = 'build/native-browser-capabilities.json'
OVERLAY = 'patches/native-browser/0001-native-windows-shell.patch'
TOKENS = re.compile(r'(?P<string>"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\')|(?P<comment>//[^\r\n]*|/\*[\s\S]*?\*/)')


def code(text):
    return TOKENS.sub(lambda match: '' if match.group('comment') else match[0], text)


def compact(text):
    return re.sub(r'\s+', '', code(text))


def body(text, name):
    source = code(text)
    match = re.search(re.escape(name) + r'\s*\([^;{}]*\)\s*(?:const\s*)?\{', source)
    if not match:
        raise ValueError('Native browser implementation is absent: ' + name)
    depth = 1
    # String literals in the checked functions contain no braces.
    for end in range(match.end(), len(source)):
        depth += (source[end] == '{') - (source[end] == '}')
        if depth == 0:
            return source[match.end():end]
    raise ValueError('Incomplete native browser implementation: ' + name)


def read(src, name):
    path = src / name
    if not path.is_file():
        raise ValueError('Missing native browser source: ' + name)
    return path.read_text(encoding='utf-8')


def require(source, tokens, location):
    text = compact(source)
    for token in tokens:
        if compact(token) not in text:
            raise ValueError(location + ': native browser contract is missing ' + token)


def verify_shell(src: Path):
    checked = []
    name = 'chrome/browser/shell_integration_win.cc'
    source = read(src, name)
    require(body(source, 'GetShardXManagedEnvironmentId'), [
        'base::Uuid::ParseCaseInsensitive', '"shardx-managed-environment"',
        'uuid.is_valid()', 'uuid.AsLowercaseString()'], name)
    identity = body(source, 'GetShardXManagedAppUserModelId')
    require(identity, ['GetShardXManagedEnvironmentId()', 'profile_path.empty()',
        'profile_path.BaseName().AsUTF8Unsafe()', 'base::ToLowerASCII',
        'base::WideToUTF8(app_name)', 'base::SHA1HashString(identity)',
        'ShellUtil::BuildAppUserModelId', 'L"ShardX"'], name)
    if 'DirName' in identity:
        raise ValueError(name + ': managed identity depends on the data-root location')
    for entry in ('GetAppUserModelIdForBrowser', 'GetAppUserModelIdForApp'):
        require(body(source, entry), ['GetShardXManagedAppUserModelId', 'GetAppUserModelIdImpl'], name)
    if 'ShardX' in body(source, 'GetExpectedAppId') or 'ShardX' in body(source, 'GetProfileIdFromPath'):
        raise ValueError(name + ': managed identity contaminates native shortcut migration')
    checked.append(name)

    name = 'chrome/browser/ui/views/frame/browser_window_property_manager_win.cc'
    source = read(src, name)
    require(source, ['#include <shellapi.h>'], name)
    clear = body(source, 'ClearManagedRelaunchDetails')
    require(clear, ['PKEY_AppUserModel_RelaunchCommand',
        'PKEY_AppUserModel_RelaunchDisplayNameResource',
        'PKEY_AppUserModel_RelaunchIconResource',
        'const PROPVARIANT empty = {}', 'properties->SetValue(key, empty)',
        'properties->Commit()'], name)
    update = compact(body(source, 'BrowserWindowPropertyManager::UpdateWindowProperties'))
    managed = ('if(shell_integration::win::IsShardXManagedEnvironment()){'
               'ClearManagedRelaunchDetails(hwnd_);'
               'ui::win::PreventWindowFromPinning(hwnd_);'
               'ui::win::SetAppIdForWindow(app_id,hwnd_);return;}')
    if managed not in update or update.index(managed) > update.index('web_app::UpdateRelaunchDetailsForApp'):
        raise ValueError(name + ': managed windows can reach raw browser relaunch details')
    checked.append(name)

    name = 'chrome/browser/profiles/profile_shortcut_manager_win.cc'
    feature = compact(body(read(src, name), 'ProfileShortcutManager::IsFeatureEnabled'))
    guard = 'if(shell_integration::win::IsShardXManagedEnvironment())returnfalse;'
    if guard not in feature or feature.index(guard) > feature.index('kEnableProfileShortcutManager'):
        raise ValueError(name + ': native shortcut feature gate does not protect managed environments')
    checked.append(name)

    name = 'chrome/browser/taskbar/taskbar_decorator_win.cc'
    avatar = body(read(src, name), 'UpdateTaskbarDecoration')
    require(avatar, ['shell_integration::win::IsShardXManagedEnvironment() && profile->IsRegularProfile()',
        'profile->IsGuestSession()', '.GetNumberOfProfiles() <= 1 && !managed_avatar',
        'AvatarMenu::GetImageForMenuButton', 'taskbar::DrawTaskbarDecoration(window, &decoration)'], name)
    checked.append(name)

    name = 'chrome/browser/ui/views/frame/browser_desktop_window_tree_host_win.cc'
    source = read(src, name)
    for event in ('HandleCreate', 'OnProfileAvatarChanged', 'OnProfileWasRemoved'):
        require(body(source, 'BrowserDesktopWindowTreeHostWin::' + event), [
            'shell_integration::win::IsShardXManagedEnvironment()',
            'IsRegularProfile()', 'SetWindowIcon'], name + '/' + event)
    require(body(source, 'GetBadgedIconBitmapForProfile'), [
        'GetProfileAttributesStorage()', 'profiles::GetWin2xAvatarImage(entry)',
        'profiles::GetBadgedWinIconBitmapForAvatar'], name)
    checked.append(name)

    name = 'chrome/test/BUILD.gn'
    source = read(src, name)
    target = re.search(r'test\("native_passkey_unittests"\)\s*\{(.*?)\n  \}', source, re.S)
    if not target or target[1].count('"../browser/profiles/shardx_native_browser_unittest.cc"') != 1:
        raise ValueError(name + ': native browser tests are not linked in the selected executable')
    checked.append(name)
    name = 'chrome/browser/profiles/shardx_native_browser_unittest.cc'
    source = read(src, name)
    require(source, ['TEST(ShardXNativeBrowserTest, ManagedEnvironmentOwnsProfileShellIdentity)',
        'TEST(ShardXNativeBrowserTest, InvalidManagedMarkerPreservesNativeIdentity)',
        'TEST(ShardXNativeBrowserTest, BlockedPortParserPreservesBoundsAndDenySemantics)'], name)
    checked.append(name)
    return checked


def verify(src: Path, repo: Path):
    config = json.loads((repo / 'build/google-services-win155.json').read_text(encoding='utf-8'))
    setting = config.get('native_browser', {})
    if (setting.get('version') != 1 or setting.get('capabilities') != CAPABILITIES
            or setting.get('source_gate_required') is not True
            or OVERLAY not in config.get('restoration_overlays', [])):
        raise ValueError('Native browser restoration is not explicitly configured')
    capabilities = json.loads((repo / CAPABILITIES).read_text(encoding='utf-8'))
    if (capabilities.get('schema_version') != 1 or capabilities.get('contract') != CONTRACT
            or capabilities.get('chromium_version') != '156.0.8078.79'
            or capabilities.get('managed_environment', {}).get('flag') != 'shardx-managed-environment'):
        raise ValueError('Native browser capability identity is invalid')
    checked = verify_shell(src)
    requirements = {
        'chrome/browser/profiles/profile.cc': ['prefs::kSearchSuggestEnabled, true'],
        'chrome/browser/ui/browser_ui_prefs.cc': ['translate::prefs::kOfferTranslateEnabled, true'],
        'chrome/browser/preloading/preloading_prefs.h': ['kDefault = kDisabled'],
        'chrome/browser/browser_features.cc': [
            'BASE_FEATURE(kClearDataOnExit, "ClearDataOnExit", base::FEATURE_DISABLED_BY_DEFAULT)'],
        'third_party/search_engines_data/resources/definitions/prepopulated_engines.json': [
            '"name": "Google"', '"keyword": "google.com"', '"search_url": "{google:baseURL}search?q='],
    }
    for name, tokens in requirements.items():
        source = read(src, name)
        require(source, tokens, name)
        if name.endswith('prepopulated_engines.json') and '"No Search"' in source:
            raise ValueError(name + ': No Search replacement remains active')
        checked.append(name)
    try:
        from .verify_native_environment_controls_source import verify as verify_controls
    except ImportError:
        from verify_native_environment_controls_source import verify as verify_controls
    checked.extend(verify_controls(src))
    try:
        from .verify_history_retention_source import verify as verify_history
    except ImportError:
        from verify_history_retention_source import verify as verify_history
    checked.extend(verify_history(src))
    return list(dict.fromkeys(checked))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--src', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'status': 'source-contract-passed',
                      'checked': verify(args.src, args.repo),
                      'runtime_acceptance': 'not_tested'}))
