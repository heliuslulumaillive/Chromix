"""Source admission checks for the deliberately retired Lite implementations."""
from __future__ import annotations
from pathlib import Path
import re

REMOVED_FILES = tuple('third_party/blink/renderer/platform/canvas_bridge/' + name for name in (
    'BUILD.gn', 'canvas_bridge_client.cc', 'canvas_bridge_client.h',
    'canvas_bridge_codec.cc', 'canvas_bridge_codec.h')) + ('base/uxr_audio_reference_data.h',)

CHECKED_FILES = (
    'base/uxr_config.cc', 'base/uxr_config.h', 'base/uxr_surface_modes_test_main.cc',
    'chrome/app/chrome_main.cc',
    'content/browser/renderer_host/render_process_host_impl.cc',
    'v8/src/flags/flag-definitions.h',
    'v8/src/inspector/v8-runtime-agent-impl.cc',
    'v8/src/inspector/v8-runtime-agent-impl.h',
    'third_party/blink/renderer/platform/BUILD.gn',
    'third_party/blink/renderer/platform/runtime_enabled_features.json5',
    'third_party/blink/renderer/platform/fonts/font_cache.cc',
    'third_party/blink/renderer/core/dom/element.cc',
    'third_party/blink/renderer/core/dom/element.h',
    'third_party/blink/renderer/core/dom/element.idl',
    'third_party/blink/renderer/core/html/canvas/html_canvas_element.cc',
    'third_party/blink/renderer/core/html/canvas/canvas_rendering_context.h',
    'third_party/blink/renderer/core/html/canvas/text_metrics.h',
    'third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc',
    'third_party/blink/renderer/core/frame/navigator_device_memory.cc',
    'third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.cc',
    'third_party/blink/renderer/modules/canvas/canvas2d/base_rendering_context_2d.h',
    'third_party/blink/renderer/modules/canvas/canvas2d/canvas_2d_recorder_context.cc',
    'third_party/blink/renderer/modules/canvas/canvas2d/canvas_path.cc',
    'third_party/blink/renderer/modules/canvas/canvas2d/canvas_path.h',
    'third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc',
    'third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.h',
    'third_party/blink/renderer/modules/webgl/webgl_object.h',
    'third_party/blink/renderer/modules/webgl/webgl_uniform_location.h',
    'third_party/blink/renderer/modules/webgpu/gpu_adapter_info.cc',
    'third_party/blink/renderer/modules/credentialmanagement/public_key_credential.cc',
    'third_party/blink/renderer/modules/plugins/dom_plugin_array.cc',
    'third_party/blink/renderer/modules/speech/speech_synthesis.cc',
    'third_party/blink/renderer/modules/keyboard/keyboard_layout.cc',
    'base/uxr_surface_modes.h', 'base/uxr_surface_reference_data.h',
    'base/uxr_surface_golden_tests.cc',
    'third_party/blink/renderer/modules/webaudio/audio_handler.cc',
    'third_party/blink/renderer/modules/webaudio/offline_audio_context.cc',
    'third_party/blink/renderer/platform/fonts/skia/skia_text_metrics.cc',
    'third_party/blink/renderer/platform/fonts/skia/skia_text_metrics.h',
    'third_party/blink/renderer/platform/fonts/simple_font_data.cc',
    'third_party/blink/renderer/platform/fonts/font_metrics.cc',
)
FORBIDDEN_SYMBOLS = re.compile(
    r'\b(?:CanvasBridgeClient|canvas_bridge|bridge_id_|bridge_canvas_id_|'
    r'BridgeId|SetBridgeId|RecordBridgeOp|ReadBridgePixels|RecordWebGLOp|'
    r'RecordWebGLCreate|OverrideFromBridge|ShadowRootForBindings|FakeShadowRoot|'
    r'uxr_devtools_runtime_suppression|GetSeeded\w+|SeedFromCanvasKey|'
    r'WindowsSpeechVoiceTable|UseWindowsSpeechVoiceTable|CloakFontCandidate|'
    r'TryCloakAudioReference|SkFontApplyWindowsMetrics|ApplyUxrAudioIsolation|AudioIsolationSeed)\b')

def verify_retirement(src: Path):
    errors = []
    for name in REMOVED_FILES:
        if (src / name).exists():
            errors.append(name + ': retired implementation still exists')
    for name in CHECKED_FILES:
        path = src / name
        if not path.is_file():
            errors.append(name + ': missing source for retirement verification')
            continue
        text = path.read_text(encoding='utf-8')
        # These identifiers have no permitted declaration or call after removal.
        # Unlike a presence-only flag check, checking the call sites prevents a
        # retained hidden subsystem from passing admission merely by default-off.
        found = sorted(set(FORBIDDEN_SYMBOLS.findall(text)))
        if found:
            errors.append(name + ': retired symbols remain: ' + ', '.join(found))
        if name == 'content/browser/renderer_host/render_process_host_impl.cc':
            if 'CopySwitchesFrom(browser_command_line, kUxrStartupSwitches)' not in text:
                errors.append(name + ': normal early UA/locale propagation was removed')
            if 'uxr-canvas-bridge' in text or 'uxr-devtools-runtime-suppression' in text:
                errors.append(name + ': retired unsafe/suppression renderer branch remains')
        if name.endswith('element.idl') and 'ImplementedAs=OpenShadowRoot' not in text:
            errors.append(name + ': native shadow binding not restored')
        if name.endswith('v8-runtime-agent-impl.h') and 'bool enabled() const { return m_enabled; }' not in text:
            errors.append(name + ': native Runtime enable state not restored')
        if name == 'chrome/app/chrome_main.cc' and 'Retired Lite kernel switch:' not in text:
            errors.append(name + ': absent-feature input rejection missing')
        if name != 'chrome/app/chrome_main.cc' and '"uxr-synthetic-device-tests"' in text:
            errors.append(name + ': synthetic fixture control remains reachable')
    if errors:
        raise ValueError('\n'.join(errors))
    return list(CHECKED_FILES) + list(REMOVED_FILES)
