"""Checks for the Windows 158 browser services and native widget port."""
import json
from pathlib import Path

if __package__:
    from . import verify_google_services_source as services
    from .verify_native_browser_source import body, compact, read, require, verify_security_sync_prefs
else:
    import verify_google_services_source as services
    from verify_native_browser_source import body, compact, read, require, verify_security_sync_prefs


def service_requirements():
    # The release's voice-mode tests are a separate suite from browser services.
    voice_files = {'base/BUILD.gn', 'base/uxr_surface_modes_test_main.cc', 'content/browser/speech/tts_win.cc'}
    required = {name: list(tokens) for name, tokens in services.REQUIRED.items() if name not in voice_files}
    for name, tokens in services.SECURITY_REQUIRED.items():
        required.setdefault(name, []).extend(tokens)
    # Chromium 158 now owns the small extension test target and uses its fully qualified label.
    deps = required['chrome/browser/extensions/BUILD.gn']
    deps[deps.index('public_deps = [ ":test_extension_system" ]')] = (
        'public_deps = [ "//chrome/browser/extensions:test_extension_system" ]')
    return required


def verify_build(src: Path) -> list[str]:
    path = 'components/BUILD.gn'
    label = '"//components/enterprise/connectors/core:cloud_content_scanning_unit_tests"'
    aggregate = read(src, path)
    if aggregate.count(label) != 1:
        raise ValueError('Cloud content scanning tests must appear exactly once in ' + path)
    require(aggregate, ['if (enterprise_cloud_content_analysis || is_android) { deps += [ ' + label + ' ] }'], path)
    test_graph = 'chrome/browser/extensions/BUILD.gn'
    if read(src, test_graph).count('source_set("test_extension_system")') != 1:
        raise ValueError('Duplicate or missing Chromium 158 extension test target')
    return [path, test_graph]


def verify_store(src: Path) -> list[str]:
    definitions = 'third_party/search_engines_data/resources/definitions/prepopulated_engines.json'
    lines = read(src, definitions).splitlines()
    engine = json.loads('\n'.join(line for line in lines if not line.lstrip().startswith('//')))['elements']['google']
    if (engine['keyword'] != 'google.com' or '{searchTerms}' not in engine['search_url']
            or not engine['suggest_url'].startswith('{google:baseSuggestURL}') or '{searchTerms}' not in engine['suggest_url']):
        raise ValueError('Missing native search or suggestion template')
    methods = [
        ('components/search_engines/template_url.cc', 'TemplateURLRef::HasGoogleBaseURLs',
         ['return std::ranges::any_of(replacements_', 'replacement.type == GOOGLE_BASE_URL ||',
          'replacement.type == GOOGLE_BASE_SUGGEST_URL']),
        ('chrome/browser/extensions/chrome_content_browser_client_extensions_part.cc', 'GetOriginsRequiringDedicatedProcess',
         ['list.push_back(url::Origin::Create(extension_urls::GetWebstoreLaunchURL()));',
          'list.push_back(url::Origin::Create(extension_urls::GetNewWebstoreLaunchURL()));']),
        ('chrome/browser/extensions/chrome_content_verifier_delegate.cc', 'ChromeContentVerifierDelegate::IsFromWebstore',
         ['if (!InstallVerifier::IsFromStore(extension, context_))', 'if (extension_management->GetEffectiveUpdateURL(extension) != '
          'extension_urls::GetDefaultWebstoreUpdateUrl()) { return false; }']),
        ('extensions/browser/webstore_installer.cc', 'WebstoreInstaller::StartCrxInstaller',
         ['crx_installer_->set_expected_id(approval->extension_id);']),
    ]
    for path, method, tokens in methods:
        require(body(read(src, path), method), tokens, path)
    return [definitions, *(path for path, _, _ in methods)]


def verify_popup(src: Path) -> list[str]:
    directory = 'third_party/blink/renderer/core/frame/'
    widget = directory + 'web_frame_widget_impl.cc'
    header = directory + 'web_frame_widget_impl.h'
    emulator = directory + 'screen_metrics_emulator.cc'
    test = directory + 'web_frame_widget_test.cc'
    require(read(src, header), ['void ApplyVisualPropertiesSizing(const VisualProperties& visual_properties);'], header)
    require(read(src, widget), ['void WebFrameWidgetImpl::ApplyVisualPropertiesSizing(const VisualProperties& visual_properties)'], widget)
    sizing = compact(body(read(src, widget), 'WebFrameWidgetImpl::ApplyVisualPropertiesSizing'))
    measure = 'widget_base_->BlinkSpaceToFlooredDIPs(size_.value_or(gfx::Size()))'
    delivery = 'DeviceEmulator()->UpdateVisualProperties(visual_properties,compositor_rect);'
    if measure not in sizing or delivery not in sizing or sizing.index(measure) > sizing.index(delivery):
        raise ValueError('Popup surface delivery must follow content measurement')
    require(sizing, ['auto_resize ? new_compositor_viewport_pixel_rect : visual_properties.compositor_viewport_pixel_rect'], widget)
    require(body(read(src, widget), 'WebFrameWidgetImpl::SetScreenInfoAndSize'),
            ['if (!AutoResizeMode()) { Resize(widget_base_->DIPsToCeiledBlinkSpace(widget_size_in_dips)); }'], widget)
    require(body(read(src, widget), 'WebFrameWidgetImpl::DidAutoResize'), ['GetOriginalScreenInfo().device_scale_factor'], widget)
    require(body(read(src, emulator), 'ScreenMetricsEmulator::UpdateVisualProperties'),
            ['frame_widget_->DeviceEmulator() != this', 'visual_properties_generation_ != generation',
             'frame_widget_->UpdateSurfaceAndCompositorRect(visual_properties.local_surface_id.value_or(viz::LocalSurfaceId()), '
             'compositor_viewport_pixel_rect);'], emulator)
    require(read(src, test), ['EmulatedAutoResizePreservesMeasuredViewport', 'frame_widget.DisableDeviceEmulation()',
                              'EXPECT_EQ(content_size, widget->Size())'], test)
    return [widget, header, emulator, test]


def verify(src: Path) -> dict:
    return {
        'googleServicesAndProtection': services.verify_requirements(src, service_requirements(), require_security=True),
        'securityPreferenceMetadata': verify_security_sync_prefs(src),
        'nativeBuildTargets': verify_build(src),
        'searchAndExtensionStore': verify_store(src),
        'extensionPopup': verify_popup(src),
    }
