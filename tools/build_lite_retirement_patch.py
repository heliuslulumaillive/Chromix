#!/usr/bin/env python3
"""Generate a final-source retirement overlay without changing donor patches.

The input is the exact prepared Windows 156 source. This deliberately removes
implementations, call sites and build edges, rather than disabling their flags.
Only a fresh output tree is writable; every removal requires its expected source.
"""
from __future__ import annotations
import argparse
import difflib
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import re
import shutil

TOKENS = re.compile(
    r'R"(?P<d>[^ ()\\\t\r\n]{0,16})\([\s\S]*?\)(?P=d)"'
    r'|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\''
    r'|//[^\r\n]*|/\*[\s\S]*?\*/')

@lru_cache(maxsize=8)
def mask(text):
    return TOKENS.sub(lambda m: ''.join('\n' if c == '\n' else ' ' for c in m[0]), text)

def matching(text, start, opening='(', closing=')'):
    code = mask(text)
    if code[start] != opening:
        raise ValueError('Expected opening delimiter')
    level = 1
    for i in range(start + 1, len(code)):
        level += (code[i] == opening) - (code[i] == closing)
        if level == 0:
            return i
    raise ValueError('Unbalanced source delimiters')

def replace(text, before, after, count=1):
    if text.count(before) != count:
        raise ValueError(f'Expected {count} occurrences, found {text.count(before)}: {before[:100]!r}')
    return text.replace(before, after)

def remove_function(text, name, count=1):
    found = 0
    while True:
        pattern = re.compile(r'(?m)^[^\n;{}]*\b' + re.escape(name) + r'\s*\(')
        match = next((m for m in pattern.finditer(text)
                      if (prefix := m[0][:m[0].index(name)].strip())
                      and re.search(r'[A-Za-z_]', prefix)
                      and not any(c in prefix for c in '()=')
                      and not prefix.startswith(('return ', 'if ', 'else '))), None)
        if not match:
            break
        opening = text.index('(', match.start() + match[0].index(name))
        end_args = matching(text, opening)
        body = mask(text).find('{', end_args + 1)
        if body < 0 or ';' in mask(text)[end_args + 1:body]:
            raise ValueError('Expected function definition: ' + name)
        end = matching(text, body, '{', '}') + 1
        if end < len(text) and text[end] == '\n':
            end += 1
        text = text[:match.start()] + text[end:]
        found += 1
    if found != count:
        raise ValueError(f'Expected {count} definitions of {name}, found {found}')
    return text

def if_ranges(text, marker):
    code = mask(text)
    result = []
    for m in re.finditer(r'\bif\s*\(', code):
        opening = code.index('(', m.start())
        cond_end = matching(text, opening)
        if marker not in text[opening:cond_end + 1]:
            continue
        start = text.rfind('\n', 0, m.start()) + 1
        if text[start:m.start()].strip():
            start = m.start()
        body = cond_end + 1
        while code[body].isspace():
            body += 1
        end = matching(text, body, '{', '}') + 1 if code[body] == '{' else code.index(';', body) + 1
        if re.match(r'\s*else\b', code[end:]):
            raise ValueError('Retired condition unexpectedly has else: ' + marker)
        if end < len(text) and text[end] == '\n':
            end += 1
        result.append((start, end))
    # Delete an outer block only once.
    return [r for r in result if not any(o[0] <= r[0] and o[1] >= r[1] and o != r for o in result)]

def remove_ifs(text, marker, minimum=1):
    spans = if_ranges(text, marker)
    if len(spans) < minimum:
        raise ValueError('Missing expected retired condition: ' + marker)
    for a, b in reversed(spans):
        text = text[:a] + text[b:]
    return text

def remove_calls(text, name):
    code = mask(text)
    spans = []
    for m in re.finditer(r'\b' + re.escape(name) + r'\s*\(', code):
        opening = code.index('(', m.start())
        end = matching(text, opening) + 1
        while code[end].isspace():
            end += 1
        if code[end] != ';':
            raise ValueError('Retired call is not a statement: ' + name)
        start = text.rfind('\n', 0, m.start()) + 1
        if text[start:m.start()].strip():
            raise ValueError('Retired statement has a prefix: ' + text[start:m.start()])
        end += 1
        if end < len(text) and text[end] == '\n':
            end += 1
        spans.append((start, end))
    for a, b in reversed(spans):
        text = text[:a] + text[b:]
    return text

def remove_declaration(text, name):
    pattern = re.compile(r'(?m)^\s*[^\n;{}]*\b' + re.escape(name) + r'\([^;{}]*\)(?:\s*(?:const|override))*\s*;\n')
    text, count = pattern.subn('', text)
    if count != 1:
        raise ValueError('Missing unique declaration: ' + name)
    return text

BRIDGE_ROOT = 'third_party/blink/renderer/platform/canvas_bridge/'
DELETE_FILES = [BRIDGE_ROOT + name for name in (
    'BUILD.gn', 'canvas_bridge_client.cc', 'canvas_bridge_client.h',
    'canvas_bridge_codec.cc', 'canvas_bridge_codec.h')] + ['base/uxr_audio_reference_data.h']

def transform(sources):
    data = dict(sources)
    def edit(name, callback):
        if name not in data:
            raise ValueError('Missing source: ' + name)
        data[name] = callback(data[name])
    def sub(name, before, after, count=1):
        edit(name, lambda text: replace(text, before, after, count))
    for name in DELETE_FILES:
        if name not in data:
            raise ValueError('Missing Bridge implementation: ' + name)
        del data[name]

    # The V8 feature is removed, but early renderer UA/locale forwarding stays.
    name = 'content/browser/renderer_host/render_process_host_impl.cc'
    edit(name, lambda t: remove_function(t, 'IsValidCanvasBridgeEndpoint'))
    edit(name, lambda t: remove_ifs(t, 'uxr-devtools-runtime-suppression'))
    edit(name, lambda t: remove_ifs(t, 'uxr-canvas-bridge-unsafe'))
    for comment in (
        "  // V8 cannot read the renderer's base::UxrConfig during flag initialization.\n",
        '  // The bridge uses a renderer-local raw socket. Keep the renderer sandboxed\n'
        '  // unless both the bridge endpoint and the separate unsafe opt-in are valid.\n'):
        sub(name, comment, '')
    sub('v8/src/flags/flag-definitions.h',
        'DEFINE_BOOL(uxr_devtools_runtime_suppression, false,\n'
        '            "suppress selected Runtime domain observables")\n', '')
    name = 'v8/src/inspector/v8-runtime-agent-impl.cc'
    sub(name, '#include "src/flags/flags.h"\n', '')
    sub(name, '  if (v8::internal::v8_flags.uxr_devtools_runtime_suppression) return;\n', '')
    sub(name, '  if (m_enabled && !v8::internal::v8_flags.uxr_devtools_runtime_suppression) {\n'
              '    reportMessage(message, true);\n  }', '  if (m_enabled) reportMessage(message, true);')
    edit(name, lambda t: remove_function(t, 'V8RuntimeAgentImpl::enabled'))
    sub('v8/src/inspector/v8-runtime-agent-impl.h',
        '  bool enabled() const;\n', '  bool enabled() const { return m_enabled; }\n')

    # Remove the endpoint/remote operation layer from every rendering call site.
    b2 = 'third_party/blink/renderer/modules/canvas/canvas2d/'
    wg = 'third_party/blink/renderer/modules/webgl/'
    definitions = {
        b2 + 'base_rendering_context_2d.cc': ['EnsureBridgeCanvas', 'BaseRenderingContext2D::RecordBridgeOp', 'BaseRenderingContext2D::ReadBridgePixels'],
        wg + 'webgl_rendering_context_base.cc': ['EnsureBridgeWebGL', 'RecordWebGLOp', 'RecordWebGLCreate', 'WebGLRenderingContextBase::ReadBridgePixels', 'WebGLRenderingContextBase::BridgeAllowedForThisContext'],
    }
    for name, functions in definitions.items():
        for function in functions:
            edit(name, lambda t, f=function: remove_function(t, f))
    for name in (b2 + 'base_rendering_context_2d.cc', wg + 'webgl_rendering_context_base.cc'):
        edit(name, lambda t: remove_ifs(t, 'auto* bridge = canvas_bridge::CanvasBridgeClient::Get()', minimum=1))
    name = wg + 'webgl_rendering_context_base.cc'
    # Preserve the native deletion side effect when removing an instrumentation
    # body; dropping the if statement would leak native WebGL objects.
    edit(name, lambda t: re.sub(r'(?m)^  if \((DeleteObject\([^\n]+\))\)\n(?=    RecordWebGLOp)', r'  \1;\n', t))
    # The remaining readback replacement block is a remote-only feature.
    edit(name, lambda t: remove_ifs(t, 'bridge_canvas_id_ != 0', minimum=0))
    edit(name, lambda t: remove_ifs(t, '!BridgeAllowedForThisContext()', minimum=1))
    for name in (b2 + 'base_rendering_context_2d.cc', b2 + 'canvas_2d_recorder_context.cc', b2 + 'canvas_path.cc', wg + 'webgl_rendering_context_base.cc'):
        for function in ('RecordBridgeOp', 'RecordWebGLOp', 'RecordWebGLCreate'):
            edit(name, lambda t, f=function: remove_calls(t, f))
    # uniformMatrix4fv staged doubles exclusively for the removed Bridge
    # message. Native GL already consumes the validated original float span.
    sub(wg + 'webgl_rendering_context_base.cc',
        '  std::vector<double> matrix;\n'
        '  matrix.reserve(static_cast<size_t>(length) * 16);\n'
        '  for (GLuint index = 0; index < length * 16; ++index)\n'
        '    matrix.push_back(data[index]);\n', '')
    name = 'third_party/blink/renderer/core/html/canvas/html_canvas_element.cc'
    edit(name, lambda t: remove_ifs(t, 'context->ReadBridgePixels('))
    sub(name, '    std::vector<uint8_t> bridge_pixels;\n', '')
    # Interfaces, object IDs, policy state and build dependencies are removed too.
    for name in (wg + 'webgl_object.h', wg + 'webgl_uniform_location.h'):
        edit(name, lambda t: remove_function(remove_function(t, 'BridgeId'), 'SetBridgeId'))
        sub(name, '  uint32_t bridge_id_ = 0;\n', '')
    name = 'third_party/blink/renderer/core/html/canvas/text_metrics.h'
    edit(name, lambda t: remove_function(t, 'OverrideFromBridge'))
    name = 'third_party/blink/renderer/core/html/canvas/canvas_rendering_context.h'
    edit(name, lambda t: remove_function(t, 'ReadBridgePixels'))
    name = b2 + 'canvas_path.h'
    edit(name, lambda t: remove_function(t, 'RecordBridgeOp'))
    for name in (b2 + 'base_rendering_context_2d.h', wg + 'webgl_rendering_context_base.h'):
        edit(name, lambda t: remove_declaration(t, 'ReadBridgePixels'))
    edit(b2 + 'base_rendering_context_2d.h', lambda t: remove_declaration(t, 'RecordBridgeOp'))
    edit(wg + 'webgl_rendering_context_base.h', lambda t: remove_declaration(t, 'BridgeAllowedForThisContext'))
    for name in (b2 + 'base_rendering_context_2d.h', wg + 'webgl_rendering_context_base.h', wg + 'webgl_rendering_context_base.cc'):
        edit(name, lambda t: re.sub(r'(?m)^.*\b(?:bridge_canvas_id_|bridge_webgl_obj_counter_|bridge_policy_checked_|bridge_policy_allowed_|bridge_webgl_unsupported_|bridge_tex_handled_|kBridgeDisabledCanvasId)\s*=.*\n', '', t))
    for name in list(data):
        if name.endswith(('.cc', '.h', '.gn')):
            data[name] = re.sub(r'(?m)^.*(?:#include[^\n]*canvas_bridge/|"//third_party/blink/renderer/platform/canvas_bridge)[^\n]*\n', '', data[name])

    # Restore the native closed-shadow binding, removing the generated feature.
    name = 'third_party/blink/renderer/platform/runtime_enabled_features.json5'
    sub(name, '    {\n      // Explicit debugging opt-in only; never expose UA-internal roots.\n'
              '      name: "FakeShadowRoot",\n      base_feature: "none",\n    },\n', '')
    sub('third_party/blink/renderer/core/dom/element.idl', 'ImplementedAs=ShadowRootForBindings', 'ImplementedAs=OpenShadowRoot')
    edit('third_party/blink/renderer/core/dom/element.h', lambda t: remove_declaration(t, 'ShadowRootForBindings'))
    edit('third_party/blink/renderer/core/dom/element.cc', lambda t: remove_function(t, 'Element::ShadowRootForBindings'))

    # Remove synthetic-only APIs and their callers, not their protective gates.
    name = 'base/uxr_config.cc'
    start = data[name].index('namespace {\n\nuint64_t SeedFromCanvasKey(')
    end = data[name].index('}  // namespace base', start)
    data[name] = data[name][:start] + data[name][end:]
    for function in ('GetSeededHwConcurrency', 'GetSeededDeviceMemory', 'GetSeededScreen', 'GetSeededTaskbarHeight', 'GetSeededNetRttMs'):
        edit('base/uxr_config.h', lambda t, f=function: remove_declaration(t, f))
    for name, key in (('third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc', 'GetSeededHwConcurrency'),
                      ('third_party/blink/renderer/core/frame/navigator_device_memory.cc', 'GetSeededDeviceMemory')):
        # A seeded fallback lived in its own scope before the native return.
        marker = data[name].index(key)
        start = data[name].rfind('\n  {', 0, marker) + 1
        assert start > 0
        brace = data[name].index('{', start)
        end = matching(data[name], brace, '{', '}') + 1
        data[name] = data[name][:start] + data[name][end:]
    name = 'third_party/blink/renderer/modules/speech/speech_synthesis.cc'
    edit(name, lambda t: remove_function(remove_function(t, 'UseWindowsSpeechVoiceTable'), 'WindowsSpeechVoiceTable'))
    sub(name, '  } else if (UseWindowsSpeechVoiceTable()) {\n    mojom_voices = WindowsSpeechVoiceTable();\n  }', '  }')
    name = 'third_party/blink/renderer/modules/credentialmanagement/public_key_credential.cc'
    marker = data[name].index('std::string p_uv = ')
    start = data[name].rfind('\n  {', 0, marker) + 1
    end = matching(data[name], data[name].index('{', start), '{', '}') + 1
    data[name] = data[name][:start] + data[name][end:]
    for name in ('third_party/blink/renderer/modules/plugins/dom_plugin_array.cc',
                 'third_party/blink/renderer/modules/keyboard/keyboard_layout.cc'):
        edit(name, lambda t: remove_ifs(t, 'uxr-synthetic-device-tests'))
    name = 'third_party/blink/renderer/modules/keyboard/keyboard_layout.cc'
    sub(name, '      const auto& config = base::UxrConfig::GetInstance();\n      const std::string layout = config.Get("uxr-keyboard-layout");\n', '')
    name = 'third_party/blink/renderer/platform/fonts/font_cache.cc'
    edit(name, lambda t: remove_function(remove_function(t, 'UxrSystemFontSubstitute'), 'UxrFontFamilyAllowed'))
    edit(name, lambda t: remove_ifs(t, 'UxrSystemFontSubstitute'))
    edit(name, lambda t: remove_ifs(t, '!UxrFontFamilyAllowed('))
    edit(name, lambda t: remove_ifs(t, 'uxr-synthetic-device-tests'))
    sub(name, '  const std::string persona = base::UxrConfig::GetInstance().Get("uxr-platform");\n'
              '  const std::string whitelist = base::UxrConfig::GetInstance().Get("uxr-font-whitelist");\n', '')
    sub(name, '  // Only CSS family lookup substitutes generics; src:local() stays exact.\n'
              '  if (creation_params.CreationType() == kCreateFontByFamily &&\n'
              '      (alternate_font_name == AlternateFontName::kAllowAlternate ||\n'
              '       alternate_font_name == AlternateFontName::kNoAlternate)) {\n  }\n\n'
              '  // Filter native families and src:local() probes. Downloaded faces and\n'
              '  // last-resort fallback retain normal Blink behavior.\n', '')
    name = 'components/ungoogled/persona_profile.cc'
    sub(name, '       !ConfigSwitchEnabled(config, "uxr-synthetic-device-tests") &&\n', '')
    sub(name, '  p.webgl_native_capabilities =\n      !ConfigSwitchEnabled(config, "uxr-synthetic-device-tests");', '  p.webgl_native_capabilities = true;')
    name = 'third_party/blink/renderer/modules/webgpu/gpu_adapter_info.cc'
    start = data[name].index('  if (config.Get("uxr-fingerprint-off") == "true" ||')
    end = data[name].index('\n}\n\nvoid GPUAdapterInfo::AppendMemoryHeapInfo', start)
    data[name] = data[name][:start] + data[name][end:]
    sub(name, '#include "components/ungoogled/persona_profile.h"\n', '')
    name = 'base/uxr_config.cc'
    sub(name, '  const auto synthetic = cfg.find("uxr-synthetic-device-tests");\n'
              '  const bool synthetic_tests = synthetic != cfg.end() && synthetic->second == "true";\n', '')
    sub(name, '  policy->native = !synthetic_tests;', '  policy->native = true;')
    sub(name, '  const bool allow_synthetic = !policy->native && synthetic_tests;\n', '')
    sub(name, '      (allow_synthetic || policy->seeded_pixel_noise) && !noise_disabled;', '      policy->seeded_pixel_noise && !noise_disabled;')
    sub(name, '  policy->canvas_bridge = allow_synthetic;\n', '')
    # The keyboard override was a synthetic fixture, never an input backend.
    edit(name, lambda t: remove_ifs(t, 'cfg.find("uxr-keyboard-layout")'))
    sub('base/uxr_config.h', '  bool canvas_bridge = false;\n', '')
    # Native graph/audio/real TTS remain. The old helper assertion loses only
    # the removed Bridge field, not its pixel/backend checks.
    sub('base/uxr_surface_modes_test_main.cc', 'gpu.canvas_pixel_noise && !gpu.canvas_text_noise && !gpu.canvas_bridge,', 'gpu.canvas_pixel_noise && !gpu.canvas_text_noise,')

    # Fonts, Web Audio and media now have exactly Native/ShardX implementations.
    # Other surfaces keep their existing modes, including text/offset/voices.
    name = 'base/uxr_config.cc'
    for surface, amount in (('audio', '0, 1'), ('fonts', '0, 0'), ('media', '0, 0')):
        old_modes = '{M::kNative, M::kChromix, M::kShardX}' if surface == 'media' else '{M::kNative, M::kChromix, M::kShardX, M::kCloak}'
        sub(name, f'read("{surface}", &modes->{surface}, {amount},\n              {old_modes})',
                  f'read("{surface}", &modes->{surface}, {amount},\n              {{M::kNative, M::kShardX}})')
    for surface in ('audio', 'fonts', 'media'):
        sub('base/uxr_surface_modes.h', f'  UxrSurfacePolicy {surface};',
            f'  UxrSurfacePolicy {surface}{{UxrSurfaceMode::kNative}};')
    # Remove real-time graph perturbation and its per-node polling entirely.
    edit(name, lambda t: remove_function(remove_function(t, 'ParseAudioIsolation'), 'UxrConfig::AudioIsolationSeed'))
    edit(name, lambda t: remove_ifs(t, 'surface_modes.audio.IsConfigured()'))
    sub(name, '  uint64_t audio_isolation_seed = 0;\n', '')
    sub(name, '      !ParseAudioIsolation(cfg, &audio_isolation_seed) ||\n', '')
    sub(name, '  audio_isolation_seed_.store(audio_isolation_seed, std::memory_order_relaxed);\n', '')
    edit('base/uxr_config.h', lambda t: remove_declaration(t, 'AudioIsolationSeed'))
    sub('base/uxr_config.h', '  std::atomic<uint64_t> audio_isolation_seed_{0};\n', '')
    name = 'third_party/blink/renderer/modules/webaudio/audio_handler.cc'
    edit(name, lambda t: remove_function(t, 'ApplyUxrAudioIsolation'))
    edit(name, lambda t: remove_ifs(t, 'isolation_seed'))
    sub(name, '      const uint64_t isolation_seed = base::UxrConfig::GetInstance().AudioIsolationSeed();\n', '')
    sub(name, '#include "base/uxr_config.h"\n', '')
    name = 'third_party/blink/renderer/modules/webaudio/offline_audio_context.cc'
    edit(name, lambda t: remove_ifs(t, 'modes.audio.mode == base::UxrSurfaceMode::kCloak'))
    sub(name, '    const auto modes = base::UxrConfig::GetInstance().SurfaceModes();\n', '')
    for include in ('base/uxr_audio_reference_data.h', 'base/uxr_surface_reference_data.h'):
        sub(name, '#include "' + include + '"\n', '')
    sub('base/BUILD.gn', '    "uxr_audio_reference_data.h",\n', '')

    # Preserve the current allowlists and their mandatory native fallback;
    # remove only the retired font-population/restricted-policy branches.
    name = 'third_party/blink/renderer/platform/fonts/font_cache.cc'
    edit(name, lambda t: remove_function(t, 'UxrFontFamilyIsGeneric'))
    sub(name, '      (font_policy.mode == base::UxrSurfaceMode::kShardX ||\n'
              '       font_policy.mode == base::UxrSurfaceMode::kCloak) &&',
              '      font_policy.mode == base::UxrSurfaceMode::kShardX &&')
    sub(name, '  const bool native_generic =\n'
              '      font_policy.mode == base::UxrSurfaceMode::kCloak &&\n'
              '      creation_params.CreationType() == kCreateFontByFamily &&\n'
              '      UxrFontFamilyIsGeneric(creation_params.Family());\n', '')
    sub(name, 'if (data && !mandatory_fallback && !native_generic &&', 'if (data && !mandatory_fallback &&')
    start = data[name].index('  if (surface.mode == base::UxrSurfaceMode::kCloak)')
    end = data[name].index('\n}\n\nbool FontCache::IsUxrLocalFontFamilyAllowed', start)
    data[name] = data[name][:start] + '  return true;' + data[name][end:]
    sub(name, '  const auto surface = config.SurfaceModes().fonts;\n'
              '  const bool explicit_pool = config.Has("uxr-fonts-allowlist");\n'
              '  if (!explicit_pool && surface.IsConfigured() &&\n'
              '      surface.mode != base::UxrSurfaceMode::kChromix)\n'
              '    return nullptr;\n'
              '  if (!explicit_pool && config.Get("uxr-font-policy") != "restricted")\n'
              '    return nullptr;\n'
              '  const auto pool = config.Get(explicit_pool ? "uxr-fonts-allowlist"\n'
              '                                            : "uxr-font-whitelist");',
              '  if (!config.Has("uxr-fonts-allowlist"))\n'
              '    return nullptr;\n'
              '  const auto pool = config.Get("uxr-fonts-allowlist");')
    edit(name, lambda t: remove_ifs(t, 'legacy_fonts'))
    sub(name, '  const bool legacy_fonts = surface.IsLegacy() ||\n'
              '      surface.mode == base::UxrSurfaceMode::kChromix;\n', '')
    # The Linux-only Windows metric rewrite was exclusively a retired font
    # policy. Native glyph metrics and the separate current text mode remain.
    font_root = 'third_party/blink/renderer/platform/fonts/'
    edit(font_root + 'skia/skia_text_metrics.cc', lambda t: remove_function(t, 'SkFontApplyWindowsMetrics'))
    edit(font_root + 'skia/skia_text_metrics.h', lambda t: remove_declaration(t, 'SkFontApplyWindowsMetrics'))
    edit(font_root + 'simple_font_data.cc', lambda t: remove_calls(t, 'SkFontApplyWindowsMetrics'))
    sub(font_root + 'font_metrics.cc', '  [[maybe_unused]] const bool windows_metrics =\n'
                                     '      SkFontApplyWindowsMetrics(font, &metrics);\n', '')
    sub(font_root + 'font_metrics.cc', '!windows_metrics && ', '', count=2)
    # Keep protected ShardX families, native voice helpers and current text
    # reference rows; only the Cloak font candidate pools are removed.
    name = 'base/uxr_surface_reference_data.h'
    start = data[name].index('// Candidate pools from the original DLL; not installed font assets.')
    end = data[name].index('struct TextReferenceRow', start)
    data[name] = data[name][:start] + data[name][end:]
    # Remove goldens for removed code, retaining every ShardX/text/voice golden.
    name = 'base/uxr_surface_golden_tests.cc'
    sub(name, '#include "base/uxr_audio_reference_data.h"\n', '')
    data[name] = re.sub(r'(?m)^.*check\(!?CloakFontCandidate\([^\n]*\n', '', data[name])
    spans = []
    for match in re.finditer(r'(?m)^  \{', data[name]):
        brace = data[name].index('{', match.start())
        end = matching(data[name], brace, '{', '}') + 1
        if 'TryCloakAudioReference' in data[name][brace:end]:
            spans.append((match.start(), end))
    if not spans:
        raise ValueError('Missing retired audio reference goldens')
    for a, b in reversed(spans):
        data[name] = data[name][:a] + data[name][b:]
    # The retired raw controls are rejected by the real configuration parser.
    name = 'base/uxr_config.cc'
    edit(name, lambda t: remove_function(t, 'ValidateFontPolicy'))
    sub(name, '      !ValidateFontPolicy(cfg) ||\n', '')
    guard = ('  for (const char* retired : {"uxr-font-policy", "uxr-font-whitelist",\n'
             '                               "uxr-audio-render", "uxr-windows-font-metrics"}) {\n'
             '    if (cfg.contains(retired)) {\n'
             '      validation_error_ = "retired Lite font/audio control";\n'
             '      return false;\n'
             '    }\n'
             '  }\n')
    sub(name, '  int timer_resolution_ms = 0;\n', guard + '  int timer_resolution_ms = 0;\n')
    # Update the compiled suite to the supported behavior and explicitly check
    # each rejected mode. Do not just lower a numeric execution threshold.
    name = 'base/uxr_surface_modes_test_main.cc'
    sub(name, '      {"uxr-pixel-noise", "seeded"}, {"uxr-audio-render", "isolated"},\n'
              '      {"uxr-audio-seed", "84"}});', '      {"uxr-pixel-noise", "seeded"}});')
    sub(name, 'modes.audio.IsLegacy() && modes.fonts.IsLegacy()', 'modes.audio.IsNative() && modes.fonts.IsNative()')
    sub(name, 'modes.media.IsLegacy() && modes.voices.IsLegacy()', 'modes.media.IsNative() && modes.voices.IsLegacy()')
    sub(name, '  Check(config.AudioIsolationSeed() == 84, "legacy graph audio seed");\n', '')
    sub(name, '      {"uxr-audio-render", "isolated"}, {"uxr-audio-seed", "84"},\n', '')
    sub(name, '{"uxr-fonts-mode", "cloak"}, {"uxr-rects-mode", "shardx"}', '{"uxr-fonts-mode", "shardx"}, {"uxr-rects-mode", "shardx"}')
    sub(name, '  Check(modes.audio.mode == Mode::kShardX && modes.audio.seed == 42 &&\n'
              '            config.AudioIsolationSeed() == 0,\n'
              '        "readback audio does not enable legacy graph isolation");',
              '  Check(modes.audio.mode == Mode::kShardX && modes.audio.seed == 42,\n'
              '        "ShardX audio keeps its independent seed without a graph rewrite");')
    sub(name, 'Check(modes.fonts.mode == Mode::kCloak && modes.text.mode == Mode::kCloak &&',
              'Check(modes.fonts.mode == Mode::kShardX && modes.text.mode == Mode::kCloak &&')
    sub(name, '      {"uxr-audio-mode", "chromix"},\n', '')
    sub(name, '  Check(modes.audio.seed == 84 && config.AudioIsolationSeed() == 84,\n'
              '        "existing graph per-surface seed precedence retained");',
              '  Check(modes.audio.IsNative() && modes.fonts.IsNative() && modes.media.IsNative(),\n'
              '        "omitted retired surfaces use their native implementations");')
    sub(name, '      Input({{"uxr-media-mode", "cloak"}}),\n',
        ''.join('      Input({{"uxr-' + surface + '-mode", "' + mode + '"}}),\n'
                for surface in ('fonts', 'audio', 'media') for mode in ('chromix', 'cloak')) +
        ''.join('      Input({{"' + key + '", "' + value + '"}}),\n'
                for key, value in (('uxr-audio-render', 'isolated'), ('uxr-font-policy', 'restricted'),
                                   ('uxr-font-whitelist', 'Arial'), ('uxr-windows-font-metrics', 'true'))))

    name = 'chrome/app/chrome_main.cc'
    sub(name, '      {"fingerprint-devtools-runtime-suppression",\n                                             "uxr-devtools-runtime-suppression"},\n'
              '      {"fingerprint-canvas-bridge",        "uxr-canvas-bridge"},\n'
              '      {"fingerprint-canvas-bridge-unsafe", "uxr-canvas-bridge-unsafe"},\n', '')
    # Keep all existing normal startup defaults, simply remove the synthetic
    # branch that skipped them for fake seeded hardware pools.
    marker = 'if (command_line->GetSwitchValueASCII("uxr-synthetic-device-tests") != "true")'
    at = data[name].index(marker)
    line_start = data[name].rfind('\n', 0, at) + 1
    brace = data[name].index('{', at)
    end = matching(data[name], brace, '{', '}')
    block = data[name][brace + 1:end]
    block = re.sub(r'(?m)^  ', '', block)
    data[name] = data[name][:line_start] + block.strip('\n').rstrip() + data[name][end + 1:]

    retired = ('fingerprint-canvas-bridge', 'uxr-canvas-bridge',
               'fingerprint-canvas-bridge-unsafe', 'uxr-canvas-bridge-unsafe',
               'fingerprint-devtools-runtime-suppression', 'uxr-devtools-runtime-suppression',
               'uxr-synthetic-device-tests', 'fingerprint-keyboard-layout',
               'uxr-keyboard-layout', 'uxr-webauthn-uvpaa', 'uxr-plugins',
               'fingerprint-audio-render', 'uxr-audio-render',
               'fingerprint-font-policy', 'uxr-font-policy',
               'fingerprint-font-whitelist', 'uxr-font-whitelist',
               'fingerprint-windows-font-metrics', 'uxr-windows-font-metrics')
    gate = ('    // These implementations are absent in the Lite kernel. Reject stale\n'
            '    // inputs instead of silently accepting switches that cannot work.\n'
            '    for (const char* retired : {\n' +
            ''.join('             "' + key + '",\n' for key in retired) +
            '         }) {\n'
            '      if (command_line->HasSwitch(retired)) {\n'
            '        std::cerr << "Retired Lite kernel switch: " << retired << std::endl;\n'
            '        return 1;\n'
            '      }\n'
            '    }\n\n')
    gate += ('    for (const char* surface : {"fonts", "audio", "media"}) {\n'
             '      for (const char* prefix : {"fingerprint-", "uxr-"}) {\n'
             '        const std::string key = std::string(prefix) + surface + "-mode";\n'
             '        if (!command_line->HasSwitch(key))\n'
             '          continue;\n'
             '        const std::string mode = command_line->GetSwitchValueASCII(key);\n'
             '        if (mode != "native" && mode != "shardx") {\n'
             '          std::cerr << "Unsupported Lite surface mode: " << key << "=" << mode << std::endl;\n'
             '          return 1;\n'
             '        }\n'
             '      }\n'
             '    }\n\n')
    sub(name, '    // Reject conflicting caller duplicates; repeated identical values are safe.\n', gate + '    // Reject conflicting caller duplicates; repeated identical values are safe.\n')
    # Remove the public alias for the synthetic-only keyboard override.
    sub(name, '      {"fingerprint-keyboard-layout",     "uxr-keyboard-layout"},\n', '')
    for public, internal in (('fingerprint-font-policy', 'uxr-font-policy'),
                             ('fingerprint-font-whitelist', 'uxr-font-whitelist'),
                             ('fingerprint-audio-render', 'uxr-audio-render'),
                             ('fingerprint-windows-font-metrics', 'uxr-windows-font-metrics')):
        data[name], count = re.subn(r'(?m)^\s*\{"' + public + r'",\s*"' + internal + r'"\},\n', '', data[name])
        if count != 1:
            raise ValueError('Missing retired public alias: ' + public)

    stale_comments = {
        'base/uxr_config.h': [
            '  // Synthetic hardware/display templates derived from "uxr-canvas-seed".\n',
        ],
        'chrome/app/chrome_main.cc': [
            '      // Public launch defaults are independent of noise seeds. Explicit\n'
            '      // synthetic tests retain their opt-in, seeded hardware/geometry pools.\n',
        ],
        'third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc': [
            '  // Independent randomized hardware pools remain explicit test fixtures.\n',
        ],
        'third_party/blink/renderer/core/frame/navigator_device_memory.cc': [
            '  // Synthetic memory buckets; an absent seed preserves the host value.\n',
        ],
    }
    for name, comments in stale_comments.items():
        for comment in comments:
            sub(name, comment, '')

    # Strip empty namespaces and orphaned Bridge-only include declarations.
    for name in list(data):
        if name.endswith(('.cc', '.h')):
            data[name] = re.sub(r'namespace \{\s*\}\s*// namespace\n', '', data[name])
    return data

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--tree', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    source = args.source.resolve()
    target = args.tree.resolve()
    if target.exists() or target == source or source in target.parents:
        raise ValueError('Retirement requires a fresh independent output tree')
    sources = {p.relative_to(source).as_posix(): p.read_text(encoding='utf-8')
               for p in source.rglob('*') if p.is_file() and p.suffix in {'.cc', '.h', '.gn', '.idl', '.json5'}}
    result = transform(sources)
    shutil.copytree(source, target)
    differences, changes = [], []
    for name in sorted(sources.keys() | result.keys()):
        before, after = sources.get(name), result.get(name)
        if before == after:
            continue
        output = target / name
        if after is None:
            output.unlink()
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(after, encoding='utf-8', newline='\n')
        differences.extend(difflib.unified_diff(
            before.splitlines(keepends=True) if before is not None else [],
            after.splitlines(keepends=True) if after is not None else [],
            fromfile='a/' + name if before is not None else '/dev/null',
            tofile='b/' + name if after is not None else '/dev/null'))
        changes.append({'path': name,
                        'before_sha256': hashlib.sha256(before.encode()).hexdigest() if before is not None else None,
                        'after_sha256': hashlib.sha256(after.encode()).hexdigest() if after is not None else None})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(''.join(differences), encoding='utf-8', newline='\n')
    args.output.with_suffix('.json').write_text(json.dumps({
        'schema': 1, 'kind': 'lite-win156-final-source-retirement',
        'changes': changes, 'source_tree': 'prepared-donor', 'result_tree': 'lite-retirement-target',
        'patch_sha256': hashlib.sha256(args.output.read_bytes()).hexdigest(),
        'native_build_accepted': False, 'runtime_accepted': False,
    }, indent=2) + '\n', encoding='utf-8', newline='\n')
    print(json.dumps({'changed_files': len(changes), 'patch': str(args.output), 'tree': str(target)}))

if __name__ == '__main__':
    main()
