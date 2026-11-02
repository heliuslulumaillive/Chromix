"""Verify effective native environment controls, not runtime acceptance.

The fixed source contract checks both permission query and actual capture, the
preference layer, and the common child-process switch forwarding site. It does
not claim that a complete Chromium binary has been built or exercised.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

try:
    from .build_lite_retirement_patch import TOKENS, mask, matching
except ImportError:
    from build_lite_retirement_patch import TOKENS, mask, matching

OVERLAY = 'patches/native-browser/0002-environment-controls.patch'
TARGETS = (
    'net/base/shardx_environment_controls.h',
    'net/base/port_util.cc',
    'chrome/browser/chrome_content_browser_client.cc',
    'chrome/browser/media/webrtc/media_stream_device_permission_context.cc',
    'chrome/browser/media/webrtc/media_capture_devices_dispatcher.cc',
    'chrome/browser/prefs/chrome_command_line_pref_store.cc',
)


def code(text):
    return TOKENS.sub(lambda match: '' if match[0].startswith(('//', '/*')) else match[0], text)


def compact(text):
    return re.sub(r'\s+', '', code(text))


def function(text, name, parameter=None):
    """Return an actual definition, excluding calls, comments, and other overloads."""
    masked = mask(text)
    definitions = []
    for match in re.finditer(r'(?m)^[ \t]*[^\n;{}]*\b' + re.escape(name) + r'\s*\(', masked):
        prefix = match[0][:match[0].index(name)].strip()
        if not prefix and '::' not in name:
            continue
        if any(char in prefix for char in '()=') or prefix.startswith(('return ', 'if ', 'else ')):
            continue
        opening = masked.index('(', match.start() + match[0].index(name))
        end_parameters = matching(text, opening)
        if parameter is not None and compact(parameter) not in compact(text[opening:end_parameters]):
            continue
        start = masked.find('{', end_parameters + 1)
        if start < 0 or ';' in masked[end_parameters + 1:start]:
            continue
        end = matching(text, start, '{', '}')
        definitions.append(text[match.start():end + 1])
    if len(definitions) != 1:
        raise ValueError(f'Native environment controls require one definition of {name}, found {len(definitions)}')
    return definitions[0]


def body(text, name, parameter=None):
    definition = function(text, name, parameter)
    opening = mask(definition).index('{')
    return definition[opening + 1:-1]


def require(text, expected, where):
    normalized = compact(text)
    for token in expected:
        if compact(token) not in normalized:
            raise ValueError(where + ': native environment controls are missing ' + token)


def before(text, first, second, where):
    normalized = compact(text)
    first, second = compact(first), compact(second)
    if first not in normalized or second not in normalized or normalized.index(first) >= normalized.index(second):
        raise ValueError(where + ': native environment control precedence is incorrect')


def verify(src: Path) -> list[str]:
    src = Path(src)
    sources = {}
    for name in TARGETS:
        path = src / name
        if not path.is_file():
            raise ValueError('Missing native environment control source: ' + name)
        sources[name] = path.read_text(encoding='utf-8')

    name = TARGETS[0]
    header = sources[name]
    require(header, ['#include <algorithm>', '#include <cstddef>', '#include <string_view>',
                     '#include <vector>', 'bool valid = true;'], name)
    require(body(header, 'Contains'), [
        'return !valid || std::binary_search(ports.begin(), ports.end(), port);'], name)
    parser = body(header, 'ParseBlockedPorts')
    require(parser, ['if (value.empty()) return result;',
        'if (value.size() > 1535) { result.valid = false; return result; }',
        'if (result.ports.size() >= 256) { result.valid = false; return result; }',
        "value.find(',', start)", 'if (part.empty()) { result.valid = false; return result; }',
        "for (const char digit : part)",
        "if (digit < '0' || digit > '9') { result.valid = false; return result; }",
        "const int digit_value = digit - '0';",
        'if (port > (65535 - digit_value) / 10) { result.valid = false; return result; }',
        'port = port * 10 + digit_value;', 'if (port < 1) { result.valid = false; return result; }',
        'result.valid = false;', 'result.ports.push_back(port);',
        'std::sort(result.ports.begin(), result.ports.end());',
        'std::unique(result.ports.begin(), result.ports.end())'], name)
    before(parser, 'if (port > (65535 - digit_value) / 10)',
           'port = port * 10 + digit_value;', name)
    if ('part.data()' in compact(parser) or 'from_chars' in code(header)
            or 'UNSAFE_BUFFERS' in code(header) or 'UNSAFE_TODO' in code(header)
            or re.search(r'#\s*pragma\s+clang\s+diagnostic', code(header))):
        raise ValueError(name + ': unchecked pointer parsing or warning suppression is forbidden')

    name = TARGETS[1]
    source = sources[name]
    require(source, ['#include "base/command_line.h"', '#include "base/no_destructor.h"',
                     '#include "net/base/shardx_environment_controls.h"'], name)
    port = body(source, 'IsPortAllowedForScheme')
    require(port, ['if (!IsPortValid(port)) return false;',
        'static const base::NoDestructor<net::shardx::BlockedPorts> managed_ports([] {',
        'const auto* command_line = base::CommandLine::ForCurrentProcess();',
        'return command_line->HasSwitch("shardx-managed-environment") ? net::shardx::ParseBlockedPorts(command_line->GetSwitchValueASCII("shardx-blocked-ports")) : net::shardx::BlockedPorts();',
        'if (managed_ports->Contains(port)) return false;'], name)
    before(port, 'if (managed_ports->Contains(port)) return false;', 'GetExplicitlyAllowedPorts()', name)

    name = TARGETS[2]
    forwarding = body(sources[name], 'ChromeContentBrowserClient::AppendExtraCommandLineSwitches')
    common = re.search(r'kCommonSwitchNames\[\]\s*=\s*\{([^{}]*)\};', code(forwarding))
    if not common:
        raise ValueError(name + ': common child-process switch table is missing')
    require(common[1], ['"shardx-managed-environment"', '"shardx-blocked-ports"'], name)
    copy = 'command_line->CopySwitchesFrom(browser_command_line, kCommonSwitchNames);'
    require(forwarding, [copy], name)
    before(forwarding, copy, 'if (process_type == switches::kRendererProcess)', name)
    # The switch table and copy must be at the function's top level, never
    # hidden inside a renderer-only branch that omits the network utility.
    for token in ('kCommonSwitchNames[]', 'command_line->CopySwitchesFrom(browser_command_line, kCommonSwitchNames)'):
        normalized = compact(forwarding)
        offset = normalized.index(compact(token))
        prefix = mask(normalized[:offset])
        if prefix.count('{') != prefix.count('}'):
            raise ValueError(name + ': port policy forwarding is not common to child processes')

    name = TARGETS[3]
    context = body(sources[name], 'MediaStreamDevicePermissionContext::GetContentSettingStatusInternal')
    guard = '''if (command_line->HasSwitch("shardx-managed-environment") &&
        command_line->HasSwitch(content_settings_type_ == ContentSettingsType::MEDIASTREAM_MIC
            ? "shardx-block-microphone" : "shardx-block-camera")) {
      return CONTENT_SETTING_BLOCK;
    }'''
    require(context, [guard], name)
    before(context, guard, 'switches::kUseFakeUIForMediaStream', name)
    before(context, guard, 'GetDevicePolicy(', name)

    name = TARGETS[4]
    source = sources[name]
    capture = body(source, 'IsShardXCaptureBlocked')
    require(capture, ['if (!command_line->HasSwitch("shardx-managed-environment")) return false;',
        'return (type == blink::mojom::MediaStreamType::DEVICE_AUDIO_CAPTURE && command_line->HasSwitch("shardx-block-microphone")) || (type == blink::mojom::MediaStreamType::DEVICE_VIDEO_CAPTURE && command_line->HasSwitch("shardx-block-camera"));'], name)
    types = set(re.findall(r'MediaStreamType::(\w+)', code(capture)))
    if types != {'DEVICE_AUDIO_CAPTURE', 'DEVICE_VIDEO_CAPTURE'}:
        raise ValueError(name + ': capture policy must preserve screen and tab capture')
    access = body(source, 'MediaCaptureDevicesDispatcher::ProcessMediaAccessRequest')
    actual_deny = '''if (IsShardXCaptureBlocked(request.audio_type) ||
        IsShardXCaptureBlocked(request.video_type)) {
      std::move(callback).Run(blink::mojom::StreamDevicesSet(),
          blink::mojom::MediaStreamRequestResult::PERMISSION_DENIED, nullptr);
      return;
    }'''
    require(access, [actual_deny], name)
    before(access, actual_deny, 'for (const auto& handler : media_access_handlers_)', name)
    permission = body(source, 'MediaCaptureDevicesDispatcher::CheckMediaAccessPermission',
                      'const extensions::Extension* extension')
    deny = 'if (IsShardXCaptureBlocked(type)) return false;'
    require(permission, [deny], name)
    before(permission, deny, 'for (const auto& handler : media_access_handlers_)', name)

    name = TARGETS[5]
    preference = body(sources[name], 'ChromeCommandLinePrefStore::ChromeCommandLinePrefStore')
    require(preference, ['''if (command_line->HasSwitch("shardx-managed-environment")) {
      const std::string dnt = command_line->GetSwitchValueASCII("shardx-do-not-track");
      if (dnt == "true" || dnt == "false") {
        SetValue(prefs::kEnableDoNotTrack, base::Value(dnt == "true"),
                 WriteablePrefStore::DEFAULT_PREF_WRITE_FLAGS);
      }
    }'''], name)
    build = (src / 'net/BUILD.gn').read_text(encoding='utf-8')
    require(build, ['"base/shardx_environment_controls.h"'], 'net/BUILD.gn')
    return [*TARGETS, 'net/BUILD.gn']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--src', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'status': 'source-contract-passed', 'checked': verify(args.src),
                      'runtime_acceptance': 'not_tested'}))
