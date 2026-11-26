// Copyright 2012 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "chrome/app/chrome_main.h"

#include <stdint.h>

#include <iostream>
#include <vector>
#include <memory>
#include <optional>

#include "base/command_line.h"
#include "base/uxr_cloak_hardware.h"
#include "base/environment.h"
#include "base/i18n/tag_converters.h"
#include "base/rand_util.h"
#include "base/strings/string_number_conversions.h"
#include "base/strings/string_split.h"
#include "base/strings/string_util.h"
#include "base/uxr_headless_screen.h"
#include "base/functional/bind.h"
#include "base/functional/callback_helpers.h"
#include "base/logging.h"
#include "base/no_destructor.h"
#include "base/sampling_heap_profiler/poisson_allocation_sampler.h"
#include "base/time/time.h"
#include "build/build_config.h"
#if !defined(BUILDING_CHROME_RENDERER)
#include "chrome/app/chrome_main_delegate.h"
#endif
#include "chrome/app/startup_timestamps.h"
#include "chrome/browser/headless/headless_mode_init.h"
#include "chrome/common/buildflags.h"
#include "chrome/common/chrome_result_codes.h"
#include "chrome/common/chrome_switches.h"
#include "content/public/app/content_main.h"
#include "content/public/common/content_switches.h"
#include "partition_alloc/buildflags.h"

#if BUILDFLAG(IS_MAC)
#include "chrome/app/chrome_main_mac.h"
#include "chrome/common/mac/detect_inappropriate_exit.h"
#endif

#if BUILDFLAG(IS_WIN) || BUILDFLAG(IS_LINUX)
#include "base/base_switches.h"
#endif

#if BUILDFLAG(IS_LINUX)
#include "chrome/app/chrome_main_linux.h"
#endif

#if BUILDFLAG(IS_WIN)
#include <timeapi.h>

#include "base/dcheck_is_on.h"
#include "base/debug/dump_without_crashing.h"
#include "base/debug/handle_hooks_win.h"
#include "base/win/current_module.h"
#include "base/win/win_util.h"
#include "chrome/chrome_elf/chrome_elf_main.h"
#include "chrome/common/chrome_constants.h"
#include "chrome/install_static/initialize_from_primary_module.h"
#include "chrome/install_static/install_details.h"

#define DLLEXPORT __declspec(dllexport)
#endif  // BUILDFLAG(IS_WIN)

namespace {

// Returns storage to hold the browser process's initial command line.
std::optional<base::CommandLine>& GetInitialCommandLineStorage() {
  static base::NoDestructor<std::optional<base::CommandLine>>
      initial_command_line;
  return *initial_command_line;
}

}  // namespace

const base::CommandLine& GetInitialBrowserCommandLine() {
  // Will `CHECK` if called without a previous assignment during browser
  // process startup.
  return GetInitialCommandLineStorage().value();
}

#if BUILDFLAG(IS_WIN)
// We use extern C for the prototype DLLEXPORT to avoid C++ name mangling.
extern "C" {
DLLEXPORT int __cdecl ChromeMain(HINSTANCE instance,
                                 sandbox::SandboxInterfaceInfo* sandbox_info,
                                 int64_t exe_main_entry_point_ticks,
                                 int64_t preread_begin_ticks,
                                 int64_t preread_end_ticks);
}
#elif BUILDFLAG(IS_POSIX)
extern "C" {
// This function must be marked with NO_STACK_PROTECTOR or it may crash on
// return, see the --change-stack-guard-on-fork command line flag.
NO_STACK_PROTECTOR __attribute__((visibility("default"))) int ChromeMain(
    int argc,
    const char** argv);
}
#else
#error Unknown platform.
#endif

#if BUILDFLAG(IS_WIN)
DLLEXPORT int __cdecl ChromeMain(HINSTANCE instance,
                                 sandbox::SandboxInterfaceInfo* sandbox_info,
                                 int64_t exe_entry_point_ticks,
                                 int64_t preread_begin_ticks,
                                 int64_t preread_end_ticks) {
#elif BUILDFLAG(IS_POSIX)
int ChromeMain(int argc, const char** argv) {
#else
#error Unknown platform.
#endif

#if BUILDFLAG(IS_LINUX)
  PossiblyDetermineFallbackChromeChannel(argv[0]);
#endif

#if BUILDFLAG(IS_WIN)
  install_static::InitializeFromPrimaryModule();
#if !defined(COMPONENT_BUILD) && DCHECK_IS_ON()
  // Patch the main EXE on non-component builds when DCHECKs are enabled.
  // This allows detection of third party code that might attempt to meddle with
  // Chrome's handles. This must be done when single-threaded to avoid other
  // threads attempting to make calls through the hooks while they are being
  // emplaced.
  // Note: The EXE is patched separately, in chrome/app/chrome_exe_main_win.cc.
  base::debug::HandleHooks::AddIATPatch(CURRENT_MODULE());
#endif  // !defined(COMPONENT_BUILD) && DCHECK_IS_ON()
  StartupTimestamps timestamps{
      base::TimeTicks::FromInternalValue(exe_entry_point_ticks),
      base::TimeTicks::FromInternalValue(preread_begin_ticks),
      base::TimeTicks::FromInternalValue(preread_end_ticks)};
  ChromeMainDelegate chrome_main_delegate(timestamps);
#else  // BUILDFLAG(IS_WIN)
  ChromeMainDelegate chrome_main_delegate(
      {.exe_entry_point_ticks = base::TimeTicks::Now()});
#endif
  content::ContentMainParams params(&chrome_main_delegate);

#if BUILDFLAG(IS_WIN)
  // The process should crash when going through abnormal termination, but we
  // must be sure to reset this setting when ChromeMain returns normally.
  auto crash_on_detach_resetter = base::ScopedClosureRunner(
      base::BindOnce(&base::win::SetShouldCrashOnProcessDetach,
                     base::win::ShouldCrashOnProcessDetach()));
  base::win::SetShouldCrashOnProcessDetach(true);
  base::win::SetAbortBehaviorForCrashReporting();
  params.instance = instance;
  params.sandbox_info = sandbox_info;

  // Pass chrome_elf's copy of DumpProcessWithoutCrash resolved via load-time
  // dynamic linking.
  base::debug::SetDumpWithoutCrashingFunction(&DumpProcessWithoutCrash);

  // Verify that chrome_elf and this module (chrome.dll) have the same version.
  if (install_static::InstallDetails::Get().VersionMismatch()) {
    base::debug::DumpWithoutCrashing();
  }
  base::CommandLine::Init(0, nullptr);
#else
  params.argc = argc;
  params.argv = argv;
  base::CommandLine::Init(params.argc, params.argv);
#endif  // BUILDFLAG(IS_WIN)

  base::CommandLine* command_line(base::CommandLine::ForCurrentProcess());

  // Normalize public switches only in the browser; children inherit the result.
  if (!command_line->HasSwitch(switches::kProcessType)) {
    const auto set_switch = [&](const char* key, const std::string& value) {
      command_line->RemoveSwitch(key);
      command_line->AppendSwitchASCII(key, value);
    };
    // These implementations are absent in the Lite kernel. Reject stale
    // inputs instead of silently accepting switches that cannot work.
    for (const char* retired : {
             "fingerprint-canvas-bridge",
             "uxr-canvas-bridge",
             "fingerprint-canvas-bridge-unsafe",
             "uxr-canvas-bridge-unsafe",
             "fingerprint-devtools-runtime-suppression",
             "uxr-devtools-runtime-suppression",
             "uxr-synthetic-device-tests",
             "fingerprint-keyboard-layout",
             "uxr-keyboard-layout",
             "uxr-webauthn-uvpaa",
             "uxr-plugins",
             "fingerprint-audio-render",
             "uxr-audio-render",
             "fingerprint-font-policy",
             "uxr-font-policy",
             "fingerprint-font-whitelist",
             "uxr-font-whitelist",
             "fingerprint-windows-font-metrics",
             "uxr-windows-font-metrics",
         }) {
      if (command_line->HasSwitch(retired)) {
        std::cerr << "Retired Lite kernel switch: " << retired << std::endl;
        return 1;
      }
    }

    for (const char* surface : {"fonts", "audio", "media"}) {
      for (const char* prefix : {"fingerprint-", "uxr-"}) {
        const std::string key = std::string(prefix) + surface + "-mode";
        if (!command_line->HasSwitch(key))
          continue;
        const std::string mode = command_line->GetSwitchValueASCII(key);
        if (mode != "native" && mode != "shardx") {
          std::cerr << "Unsupported Lite surface mode: " << key << "=" << mode << std::endl;
          return 1;
        }
      }
    }

    // Reject conflicting caller duplicates; repeated identical values are safe.
    base::CommandLine::SwitchMap seen;
    for (size_t i = 1; i < command_line->argv().size(); ++i) {
      const auto& arg = command_line->argv()[i];
      if (arg == FILE_PATH_LITERAL("--"))
        break;
      const auto parsed = base::CommandLine::FromArgvWithoutProgram({arg});
      for (const auto& [key, value] : parsed.GetSwitches()) {
        if (key == "fingerprint" || key.starts_with("fingerprint-") ||
            key.starts_with("uxr-")) {
          auto found = seen.find(key);
          if (found != seen.end() && found->second != value) {
            std::cerr << "Conflicting fingerprint switch: " << key << std::endl;
            return 1;
          }
          seen[key] = value;
        }
      }
    }
    for (const auto& [key, value] : seen) {
      command_line->RemoveSwitch(key);
      command_line->AppendSwitchNative(key, value);
    }
    // Canonicalize public booleans identically for direct and SDK launches.
    for (const char* key : {"fingerprint-noise", "fingerprint-sapi-voices",
                            "fingerprint-allow-3p-cookies", "fingerprint-windows-font-metrics",
                            "fingerprint-reduced-motion", "fingerprint-reduced-transparency",
                            "fingerprint-inverted-colors", "uxr-reduced-motion",
                            "uxr-reduced-transparency", "uxr-inverted-colors"}) {
      if (!command_line->HasSwitch(key))
        continue;
      const std::string value = base::ToLowerASCII(command_line->GetSwitchValueASCII(key));
      if (value == "off" || value == "false" || value == "0" ||
          value == "disable" || value == "disabled")
        set_switch(key, "false");
      else if (value.empty() || value == "true" || value == "1" ||
               value == "on" || value == "enable" || value == "enabled")
        set_switch(key, "true");
    }

    struct Alias { const char* fp; const char* uxr; };
    static constexpr Alias kAliases[] = {
      {"fingerprint-surface-modes-version", "uxr-surface-modes-version"},
      {"fingerprint-hardware-profile",   "uxr-hardware-profile"},
      {"fingerprint-hardware-vendor",    "uxr-hardware-vendor"},
      {"fingerprint-hardware-gpu-index", "uxr-hardware-gpu-index"},
      {"fingerprint-hardware-platform-version", "uxr-hardware-platform-version"},
      {"fingerprint-gpu-mode",           "uxr-gpu-mode"},
      {"fingerprint-webgl-identity-mode", "uxr-webgl-identity-mode"},
      {"fingerprint-webgpu-identity-mode", "uxr-webgpu-identity-mode"},
      {"fingerprint-webgl-capabilities-mode", "uxr-webgl-capabilities-mode"},
      {"fingerprint-webgpu-features-mode", "uxr-webgpu-features-mode"},
      {"fingerprint-webgpu-limits-mode", "uxr-webgpu-limits-mode"},
      {"fingerprint-webgl-extensions", "uxr-webgl-extensions"},
      {"fingerprint-webgpu-features", "uxr-webgpu-features"},
      {"fingerprint-webgl-masked-vendor", "uxr-webgl-masked-vendor"},
      {"fingerprint-webgl-masked-renderer", "uxr-webgl-masked-renderer"},
      {"fingerprint-fonts-allowlist", "uxr-fonts-allowlist"},
      {"fingerprint-fonts-local-allowlist", "uxr-fonts-local-allowlist"},
      {"fingerprint-audio-default-sample-rate", "uxr-audio-default-sample-rate"},
      {"fingerprint-audio-channel-limit", "uxr-audio-channel-limit"},
      {"fingerprint-audio-default-channel-count", "uxr-audio-default-channel-count"},
      {"fingerprint-media-audio-inputs", "uxr-media-audio-inputs"},
      {"fingerprint-media-audio-outputs", "uxr-media-audio-outputs"},
      {"fingerprint-media-video-inputs", "uxr-media-video-inputs"},
      {"fingerprint-gpu-backend",         "uxr-gpu-backend"},
      {"fingerprint-pixel-noise",         "uxr-pixel-noise"},
      {"fingerprint-audio-seed",          "uxr-audio-seed"},
      {"fingerprint-timer-resolution",    "uxr-timer-resolution"},
      {"fingerprint-max-touch-points",    "uxr-max-touch-points"},
      {"fingerprint-reduced-motion",      "uxr-reduced-motion"},
      {"fingerprint-reduced-transparency", "uxr-reduced-transparency"},
      {"fingerprint-inverted-colors",     "uxr-inverted-colors"},
      {"fingerprint-color-scheme",        "uxr-color-scheme"},
      {"fingerprint-preferred-contrast",  "uxr-preferred-contrast"},
      {"fingerprint-forced-colors",       "uxr-forced-colors"},
      {"fingerprint-pointer",             "uxr-pointer"},
      {"fingerprint-hover",               "uxr-hover"},
      {"fingerprint-hdr",                 "uxr-hdr"},
      {"fingerprint-gpu-vendor",          "uxr-webgl-vendor"},
      {"fingerprint-gpu-renderer",         "uxr-webgl-renderer"},
      {"disable-gpu-fingerprint",           "uxr-disable-gpu-fingerprint"},
      {"fingerprint-hardware-concurrency", "uxr-hw-concurrency"},
      {"fingerprint-device-memory",       "uxr-device-memory"},
      {"fingerprint-screen-width",        "uxr-screen-width"},
      {"fingerprint-screen-height",       "uxr-screen-height"},
      {"fingerprint-brand",               "uxr-ua-brand"},
      {"fingerprint-brand-version",        "uxr-ua-brand-version"},
      {"fingerprint-platform-version",    "uxr-ua-platform-version"},
      {"fingerprint-timezone",            "uxr-timezone"},
      {"fingerprint-storage-quota",        "uxr-storage-quota"},
      {"fingerprint-taskbar-height",      "uxr-taskbar-height"},
      {"fingerprint-webrtc-ip",           "uxr-webrtc-ip"},
      {"fingerprint-geolocation",         "uxr-geolocation"},
      {"fingerprint-codec-h264",          "uxr-codec-h264"},
      {"fingerprint-codec-vp8",           "uxr-codec-vp8"},
      {"fingerprint-codec-vp9",           "uxr-codec-vp9"},
      {"fingerprint-codec-av1",           "uxr-codec-av1"},
      {"fingerprint-codec-hevc",          "uxr-codec-hevc"},
    };
    for (const auto& a : kAliases) {
      if (command_line->HasSwitch(a.fp) &&
          !command_line->HasSwitch(a.uxr)) {
        command_line->AppendSwitchNative(
            a.uxr, command_line->GetSwitchValueNative(a.fp));
      }
    }

    // v1 adds independent surface choices. Unspecified surfaces retain the
    // existing switch contract; never enable synthetic-device-tests here.
    bool has_surface_mode = false;
    for (const char* surface : {"canvas", "webgl", "audio", "fonts", "rects",
                                "text", "offset", "sensors", "media", "voices"}) {
      for (const char* suffix : {"-mode", "-mode-seed", "-amount"}) {
        const std::string tail = std::string(surface) + suffix;
        const std::string public_key = "fingerprint-" + tail;
        const std::string internal_key = "uxr-" + tail;
        if (command_line->HasSwitch(public_key) &&
            !command_line->HasSwitch(internal_key)) {
          command_line->AppendSwitchASCII(
              internal_key, command_line->GetSwitchValueASCII(public_key));
        }
      }
      has_surface_mode |= command_line->HasSwitch(
          std::string("uxr-") + surface + "-mode");
    }
    if (has_surface_mode) {
      if (!command_line->HasSwitch("uxr-surface-modes-version"))
        set_switch("uxr-surface-modes-version", "1");
      if (command_line->HasSwitch("fingerprint-noise"))
        set_switch("uxr-mode-noise",
                   command_line->GetSwitchValueASCII("fingerprint-noise"));
      std::string mode_platform = base::ToLowerASCII(
          command_line->GetSwitchValueASCII("fingerprint-platform"));
      if (mode_platform == "win32")
        mode_platform = "windows";
      else if (mode_platform == "macintel")
        mode_platform = "macos";
      else if (mode_platform == "linux x86_64")
        mode_platform = "linux";
      if (mode_platform.empty()) {
#if BUILDFLAG(IS_WIN)
        mode_platform = "windows";
#elif BUILDFLAG(IS_MAC)
        mode_platform = "macos";
#else
        mode_platform = "linux";
#endif
      }
      set_switch("uxr-mode-platform", mode_platform);
      if (command_line->HasSwitch("fingerprint-sapi-voices"))
        set_switch("uxr-voices-mode-sapi",
                   command_line->GetSwitchValueASCII("fingerprint-sapi-voices"));
      if (command_line->HasSwitch("fingerprint-locale"))
        set_switch("uxr-voices-mode-locale",
                   command_line->GetSwitchValueASCII("fingerprint-locale"));
    }
    for (const char* field : {"identity", "cpu", "memory", "screen"}) {
      const std::string public_key = std::string("fingerprint-") + field + "-mode";
      const std::string internal_key = std::string("uxr-") + field + "-mode";
      if (command_line->HasSwitch(public_key) &&
          !command_line->HasSwitch(internal_key)) {
        command_line->AppendSwitchASCII(
            internal_key, command_line->GetSwitchValueASCII(public_key));
      }
      if (command_line->HasSwitch(internal_key)) {
        const std::string mode = command_line->GetSwitchValueASCII(internal_key);
        if (mode != "native" && mode != "custom") {
          std::cerr << "Invalid field mode: " << public_key << std::endl;
          return 1;
        }
      }
    }

    if (command_line->HasSwitch("fingerprint-locale") &&
        !command_line->HasSwitch("uxr-languages")) {
      command_line->AppendSwitchASCII("uxr-languages",
          command_line->GetSwitchValueASCII("fingerprint-locale"));
    }

    // Canonicalize the public off spellings before any consumer sees them.
    if (command_line->HasSwitch("fingerprint")) {
      const std::string mode = base::ToLowerASCII(
          command_line->GetSwitchValueASCII("fingerprint"));
      if (mode == "off" || mode == "false" || mode == "0" ||
          mode == "disable" || mode == "disabled") {
        set_switch("fingerprint", "off");
        command_line->RemoveSwitch("fingerprint-platform");
      }
    }

    if (command_line->HasSwitch("uxr-ua-brand")) {
      const std::string brand = base::ToLowerASCII(
          command_line->GetSwitchValueASCII("uxr-ua-brand"));
      if (brand == "chrome" || brand == "google chrome")
        set_switch("uxr-ua-brand", "Google Chrome");
      else if (brand == "edge" || brand == "microsoft edge")
        set_switch("uxr-ua-brand", "Microsoft Edge");
      else if (brand == "opera")
        set_switch("uxr-ua-brand", "Opera");
      else if (brand == "vivaldi")
        set_switch("uxr-ua-brand", "Vivaldi");
    }
    // Chrome's brand version is its engine version. Other brands have their
    // own version and must not change the Chromium token or GREASE seed.
    const std::string brand = command_line->GetSwitchValueASCII("uxr-ua-brand");
    if ((brand.empty() || brand == "Google Chrome" || brand == "Chromium") &&
        command_line->HasSwitch("uxr-ua-brand-version") &&
        !command_line->HasSwitch("uxr-ua-full-version")) {
      command_line->AppendSwitchASCII("uxr-ua-full-version",
          command_line->GetSwitchValueASCII("uxr-ua-brand-version"));
    }

    if (command_line->HasSwitch("fingerprint-platform")) {
      const std::string alias = base::ToLowerASCII(
          command_line->GetSwitchValueASCII("fingerprint-platform"));
      std::string platform;
      std::string ua_platform;
      std::string ua_os;
      std::string platform_version;
      bool native_platform = false;
      if (alias == "windows" || alias == "win32") {
        platform = "Win32";
        ua_platform = "Windows";
        ua_os = "Windows NT 10.0; Win64; x64";
        platform_version = "10.0.0";
#if BUILDFLAG(IS_WIN)
        native_platform = true;
#endif
      } else if (alias == "macos" || alias == "macintel") {
        platform = "MacIntel";
        ua_platform = "macOS";
        ua_os = "Macintosh; Intel Mac OS X 10_15_7";
        platform_version = "10.15.7";
#if BUILDFLAG(IS_MAC)
        native_platform = true;
#endif
      } else if (alias == "linux" || alias == "linux x86_64") {
        platform = "Linux x86_64";
        ua_platform = "Linux";
        ua_os = "X11; Linux x86_64";
#if BUILDFLAG(IS_LINUX)
        native_platform = true;
#endif
      }
      if (!platform.empty()) {
        const auto append_default = [&](const char* name,
                                        const std::string& value) {
          if (!command_line->HasSwitch(name))
            command_line->AppendSwitchASCII(name, value);
        };
        if (!native_platform) {
          // Cross-OS templates prevent host fallback; they are not device data.
          append_default("uxr-platform", platform);
          append_default("uxr-ua-os", ua_os);
          append_default("uxr-ua-platform", ua_platform);
          append_default("uxr-ua-arch", "x86");
          append_default("uxr-ua-bitness", "64");
          append_default("uxr-ua-model", "");
          append_default("uxr-ua-wow64", "false");
          append_default("uxr-ua-platform-version", platform_version);
        } else if (alias == "linux x86_64") {
          // This alias explicitly declares an x86_64 Linux persona.
          append_default("uxr-platform", platform);
          append_default("uxr-ua-os", ua_os);
          append_default("uxr-ua-platform", ua_platform);
          append_default("uxr-ua-arch", "x86");
          append_default("uxr-ua-bitness", "64");
        }
      }
    }

    if (command_line->HasSwitch("fingerprint")) {
      std::string seed =
          command_line->GetSwitchValueASCII("fingerprint");
      if (seed.empty()) {
        seed = command_line->GetSwitchValueASCII("uxr-fingerprint-seed");
        if (seed.empty())
          seed = command_line->GetSwitchValueASCII("uxr-canvas-seed");
        if (seed.empty()) {
          uint64_t random_seed = base::RandUint64();
          if (random_seed == 0)
            random_seed = 1;
          seed = base::NumberToString(random_seed);
        }
      }
      if (!seed.empty() && seed != "off") {
        set_switch("uxr-fingerprint-enabled", "true");
        set_switch("uxr-webgl-fingerprint", "true");
        if (!command_line->HasSwitch("uxr-fingerprint-seed"))
          command_line->AppendSwitchASCII("uxr-fingerprint-seed", seed);
        if (!command_line->HasSwitch("uxr-canvas-seed"))
          command_line->AppendSwitchASCII("uxr-canvas-seed", seed);
        if (!command_line->HasSwitch("uxr-audio-seed"))
          command_line->AppendSwitchASCII("uxr-audio-seed", seed);
      }
    }

    const bool public_fingerprint =
        command_line->GetSwitchValueASCII("uxr-fingerprint-enabled") == "true";
    if (public_fingerprint || command_line->HasSwitch("uxr-screen-width") ||
        command_line->HasSwitch("uxr-screen-height") ||
        command_line->HasSwitch("uxr-screen-avail-width") ||
        command_line->HasSwitch("uxr-screen-avail-height") ||
        command_line->HasSwitch("uxr-taskbar-height")) {
      const auto append_default = [&](const char* key, const char* value) {
        if (!command_line->HasSwitch(key))
          command_line->AppendSwitchASCII(key, value);
      };
      std::string platform = base::ToLowerASCII(
          command_line->GetSwitchValueASCII("uxr-ua-platform"));
      if (platform.empty())
        platform = base::ToLowerASCII(
            command_line->GetSwitchValueASCII("uxr-platform"));
      if (platform.empty()) {
#if BUILDFLAG(IS_WIN)
        platform = "windows";
#elif BUILDFLAG(IS_MAC)
        platform = "macos";
#else
        platform = "linux";
#endif
      }
      const bool mac = platform == "macos" || platform == "macintel";
      const bool win = platform == "windows" || platform == "win32";
      if (public_fingerprint) {
        append_default("uxr-hw-concurrency", "8");
        append_default("uxr-device-memory", "8");
      }
      append_default("uxr-screen-width", mac ? "1440" : "1920");
      append_default("uxr-screen-height", mac ? "900" : "1080");
      if (!command_line->HasSwitch("uxr-screen-avail-height"))
        append_default("uxr-taskbar-height", win ? "48" : mac ? "95" : "0");
      if (public_fingerprint)
        append_default("uxr-storage-quota", "102400");
      if (public_fingerprint && win && !command_line->HasSwitch("fingerprint-sapi-voices"))
        append_default("uxr-voices", "true");
    }

    if (command_line->GetSwitchValueASCII("fingerprint") != "off" &&
        !command_line->HasSwitch("uxr-hardware-profile") &&
        (command_line->HasSwitch("uxr-hardware-vendor") ||
         command_line->HasSwitch("uxr-hardware-gpu-index"))) {
      std::cerr << "Hardware vendor/index requires hardware-profile" << std::endl;
      return 1;
    }
    if (command_line->HasSwitch("uxr-hardware-profile") &&
        command_line->GetSwitchValueASCII("fingerprint") != "off") {
      if (command_line->GetSwitchValueASCII("uxr-hardware-profile") != "cloak") {
        std::cerr << "Unsupported hardware profile" << std::endl;
        return 1;
      }
      uint64_t hardware_seed = 0;
      std::string raw_seed =
          command_line->GetSwitchValueASCII("uxr-fingerprint-seed");
      if (raw_seed.empty())
        raw_seed = command_line->GetSwitchValueASCII("uxr-canvas-seed");
      int gpu_index = -1;
      if (!base::StringToUint64(raw_seed, &hardware_seed) || !hardware_seed ||
          (command_line->HasSwitch("uxr-hardware-gpu-index") &&
           (!base::StringToInt(
                command_line->GetSwitchValueASCII("uxr-hardware-gpu-index"),
                &gpu_index) || gpu_index < 0))) {
        std::cerr << "Invalid hardware profile seed or GPU index" << std::endl;
        return 1;
      }
      std::string hardware_platform = base::ToLowerASCII(
          command_line->GetSwitchValueASCII("fingerprint-platform"));
      if (hardware_platform == "win32")
        hardware_platform = "windows";
      else if (hardware_platform == "macintel")
        hardware_platform = "macos";
      else if (hardware_platform == "linux x86_64")
        hardware_platform = "linux";
      if (hardware_platform.empty()) {
#if BUILDFLAG(IS_WIN)
        hardware_platform = "windows";
#elif BUILDFLAG(IS_MAC)
        hardware_platform = "macos";
#else
        hardware_platform = "linux";
#endif
      }
      const auto choice = base::UxrSelectCloakHardwareV1(
          hardware_seed, hardware_platform,
          base::ToLowerASCII(
              command_line->GetSwitchValueASCII("uxr-hardware-vendor")),
          gpu_index);
      if (!choice) {
        std::cerr << "Unsupported hardware profile/platform/vendor/index"
                  << std::endl;
        return 1;
      }
      const auto caller_supplied = [&](const char* public_name,
                                       const char* internal_name) {
        return seen.contains(public_name) || seen.contains(internal_name);
      };
      if (!caller_supplied("fingerprint-hardware-concurrency", "uxr-hw-concurrency"))
        set_switch("uxr-hw-concurrency", base::NumberToString(choice->cpu));
      if (!caller_supplied("fingerprint-device-memory", "uxr-device-memory"))
        set_switch("uxr-device-memory", base::NumberToString(choice->memory_gb));
      if (!command_line->HasSwitch("uxr-memory-mode"))
        set_switch("uxr-memory-mode",
                   command_line->GetSwitchValueASCII("uxr-device-memory").empty()
                       ? "native" : "custom");
      if (!caller_supplied("fingerprint-gpu-vendor", "uxr-webgl-vendor"))
        set_switch("uxr-webgl-vendor", choice->webgl_vendor);
      if (!caller_supplied("fingerprint-gpu-renderer", "uxr-webgl-renderer"))
        set_switch("uxr-webgl-renderer", choice->webgl_renderer);
      if (!command_line->HasSwitch("uxr-gpu-mode"))
        set_switch("uxr-gpu-mode", "cloak");
      set_switch("uxr-hardware-resolved-family", choice->family);
      set_switch("uxr-hardware-resolved-index",
                 base::NumberToString(choice->index));
    }

    // Keep GPU profile version separate from the UA fields that a single
    // native identity choice removes below.
    if (!command_line->HasSwitch("uxr-hardware-platform-version") &&
        (command_line->HasSwitch("uxr-gpu-mode") ||
         command_line->HasSwitch("uxr-webgl-identity-mode") ||
         command_line->HasSwitch("uxr-webgpu-identity-mode") ||
         command_line->HasSwitch("uxr-webgpu-features-mode") ||
         command_line->HasSwitch("uxr-webgpu-limits-mode"))) {
      set_switch("uxr-hardware-platform-version",
                 command_line->GetSwitchValueASCII("uxr-ua-platform-version"));
    }

    // Per-field native choices run after legacy default insertion and only
    // remove values belonging to that field. Other seeds/policies survive.
    if (command_line->GetSwitchValueASCII("uxr-identity-mode") == "native") {
      std::vector<std::string> identity_keys;
      for (const auto& entry : command_line->GetSwitches()) {
        if (entry.first.starts_with("uxr-ua-"))
          identity_keys.push_back(entry.first);
      }
      for (const auto& key : identity_keys)
        command_line->RemoveSwitch(key);
      command_line->RemoveSwitch("user-agent");
    }
    if (command_line->GetSwitchValueASCII("uxr-cpu-mode") == "native")
      command_line->RemoveSwitch("uxr-hw-concurrency");
    if (command_line->GetSwitchValueASCII("uxr-memory-mode") == "native")
      command_line->RemoveSwitch("uxr-device-memory");
    if (command_line->GetSwitchValueASCII("uxr-screen-mode") == "native") {
      std::vector<std::string> screen_keys;
      for (const auto& entry : command_line->GetSwitches()) {
        if ((entry.first.starts_with("uxr-screen-") &&
             entry.first != "uxr-screen-mode") ||
            entry.first == "uxr-taskbar-height" ||
            entry.first == "uxr-device-pixel-ratio") {
          screen_keys.push_back(entry.first);
        }
      }
      for (const auto& key : screen_keys)
        command_line->RemoveSwitch(key);
    }

    if (command_line->HasSwitch("fingerprint-noise") &&
        command_line->GetSwitchValueASCII("fingerprint-noise") == "false") {
      // Disable perturbation, not persona selection. All seeds stay intact.
      set_switch("uxr-disable-fingerprint-noise", "");
    }

    if (command_line->HasSwitch("fingerprint-sapi-voices")) {
      const std::string value = base::ToLowerASCII(
          command_line->GetSwitchValueASCII("fingerprint-sapi-voices"));
      if (value == "false" || value == "0" || value == "off")
        command_line->RemoveSwitch("uxr-voices");
      else
        set_switch("uxr-voices", "true");
    }

    if (command_line->HasSwitch("fingerprint") &&
        command_line->GetSwitchValueASCII("fingerprint") == "off") {
      // Explicit regional settings are independent of fingerprint spoofing.
      const bool has_timezone = command_line->HasSwitch("uxr-timezone");
      const bool has_languages = command_line->HasSwitch("uxr-languages");
      const std::string timezone = command_line->GetSwitchValueASCII("uxr-timezone");
      const std::string languages = command_line->GetSwitchValueASCII("uxr-languages");
      const auto& sws = command_line->GetSwitches();
      std::vector<std::string> uxr_keys;
      for (const auto& s : sws) {
        if (s.first.compare(0, 4, "uxr-") == 0)
          uxr_keys.push_back(s.first);
      }
      for (const auto& k : uxr_keys)
        command_line->RemoveSwitch(k);
      command_line->AppendSwitchASCII("uxr-fingerprint-off", "true");
      command_line->RemoveSwitch("fingerprint-platform");
      command_line->RemoveSwitch("fingerprint-pixel-noise");
      if (has_timezone)
        command_line->AppendSwitchASCII("uxr-timezone", timezone);
      if (has_languages)
        command_line->AppendSwitchASCII("uxr-languages", languages);
      // GPU identity and readback noise otherwise have enabled defaults.
      command_line->AppendSwitch("uxr-webgl-real");
      set_switch("uxr-disable-fingerprint-noise", "");
    }

    if (command_line->HasSwitch("uxr-languages")) {
      std::vector<std::string> languages;
      bool valid = true;
      for (const auto& value : base::SplitString(
               command_line->GetSwitchValueASCII("uxr-languages"), ",",
               base::TRIM_WHITESPACE, base::SPLIT_WANT_NONEMPTY)) {
        const auto tag =
            base::i18n::LanguageTagConverter::GetInstance().FromString(value);
        if (!tag || tag->language_subtag() == "und") {
          valid = false;
          break;
        }
        const std::string canonical(tag->tag_string());
        bool duplicate = false;
        for (const auto& language : languages)
          duplicate |= language == canonical;
        if (!duplicate)
          languages.push_back(canonical);
      }
      if (valid && !languages.empty()) {
        const std::string normalized = base::JoinString(languages, ",");
        set_switch("uxr-languages", normalized);
        set_switch(switches::kAcceptLang, normalized);
      } else {
        command_line->RemoveSwitch("uxr-languages");
      }
    }

    // Synchronize an explicit virtual display with native headless rastering.
    // Run after per-field Native/global-off removal. Caller-owned native display
    // switches win, and ordinary headed windows keep their existing behavior.
    if (command_line->HasSwitch("headless") &&
        !command_line->HasSwitch("screen-info") &&
        !command_line->HasSwitch("force-device-scale-factor") &&
        command_line->HasSwitch("uxr-screen-width") &&
        command_line->HasSwitch("uxr-screen-height") &&
        command_line->HasSwitch("uxr-device-pixel-ratio")) {
      int width = 0;
      int height = 0;
      double scale = 0;
      bool valid = base::StringToInt(
                       command_line->GetSwitchValueASCII("uxr-screen-width"),
                       &width) &&
                   base::StringToInt(
                       command_line->GetSwitchValueASCII("uxr-screen-height"),
                       &height) &&
                   base::StringToDouble(
                       command_line->GetSwitchValueASCII("uxr-device-pixel-ratio"),
                       &scale);
      int available_width = width;
      int available_height = height;
      if (command_line->HasSwitch("uxr-screen-avail-width")) {
        valid &= base::StringToInt(command_line->GetSwitchValueASCII(
                                      "uxr-screen-avail-width"),
                                  &available_width);
      }
      if (command_line->HasSwitch("uxr-screen-avail-height")) {
        valid &= base::StringToInt(command_line->GetSwitchValueASCII(
                                      "uxr-screen-avail-height"),
                                  &available_height);
      }
      if (command_line->HasSwitch("uxr-taskbar-height")) {
        int taskbar = 0;
        valid &= base::StringToInt(
            command_line->GetSwitchValueASCII("uxr-taskbar-height"), &taskbar);
        if (taskbar < 0 || taskbar > height) {
          valid = false;
        } else {
          if (command_line->HasSwitch("uxr-screen-avail-height") &&
              available_height != height - taskbar) {
            valid = false;
          }
          available_height = height - taskbar;
        }
      }
      if (valid) {
        const auto display = base::MakeUxrHeadlessScreenInfo(
            width, height, available_width, available_height, scale);
        if (display) {
          command_line->AppendSwitchASCII(
              "screen-info",
              "{" + base::NumberToString(display->pixel_width) + "x" +
                  base::NumberToString(display->pixel_height) +
                  " devicePixelRatio=" + base::NumberToString(scale) +
                  " colorDepth=24 workAreaRight=" +
                  base::NumberToString(display->work_area_right) +
                  " workAreaBottom=" +
                  base::NumberToString(display->work_area_bottom) + "}");
        }
      }
    }

    // Set actual native window bounds. Getter overrides cannot resize a window
    // or supply valid negative monitor coordinates. Native caller flags win.
    constexpr char kWindowSize[] = "window-size";
    constexpr char kWindowPosition[] = "window-position";
    if (command_line->HasSwitch("uxr-outer-width") &&
        command_line->HasSwitch("uxr-outer-height") &&
        !command_line->HasSwitch(kWindowSize)) {
      command_line->AppendSwitchASCII(
          kWindowSize, command_line->GetSwitchValueASCII("uxr-outer-width") +
                           "," + command_line->GetSwitchValueASCII("uxr-outer-height"));
    }
    // An explicit native position must also win inside device emulation;
    // otherwise the real window and its web-exposed position would disagree.
    if (command_line->HasSwitch(kWindowPosition)) {
      command_line->RemoveSwitch("uxr-window-x");
      command_line->RemoveSwitch("uxr-window-y");
    } else if ((command_line->HasSwitch("uxr-window-x") ||
         command_line->HasSwitch("uxr-window-y")) &&
        !command_line->HasSwitch(kWindowPosition)) {
      const std::string x = command_line->HasSwitch("uxr-window-x")
                                ? command_line->GetSwitchValueASCII("uxr-window-x") : "0";
      const std::string y = command_line->HasSwitch("uxr-window-y")
                                ? command_line->GetSwitchValueASCII("uxr-window-y") : "0";
      command_line->AppendSwitchASCII(kWindowPosition, x + "," + y);
    }

    if (command_line->HasSwitch("fingerprint-allow-3p-cookies") &&
        command_line->GetSwitchValueASCII("fingerprint-allow-3p-cookies") != "false" &&
        command_line->GetSwitchValueASCII("fingerprint-allow-3p-cookies") != "0") {
      // This native switch is presence-based: appending '=false' ENABLES the
      // phaseout. CookieSettings consumes our explicit, non-persistent opt-in.
      command_line->RemoveSwitch("test-third-party-cookie-phaseout");
      set_switch("uxr-allow-3p-cookies", "true");
      constexpr char kDisableFeatures[] = "disable-features";
      std::string disabled = command_line->GetSwitchValueASCII(kDisableFeatures);
      auto features = base::SplitString(disabled, ",", base::TRIM_WHITESPACE,
                                        base::SPLIT_WANT_NONEMPTY);
      std::erase(features, "ForceThirdPartyCookieBlocking");
      disabled = base::JoinString(features, ",");
      command_line->RemoveSwitch(kDisableFeatures);
      if (!disabled.empty())
        disabled += ",";
      disabled += "ForceThirdPartyCookieBlocking";
      command_line->AppendSwitchASCII(kDisableFeatures, disabled);
    }

    // Apply the native socket policy for direct browser launches too. HTTP
    // proxying or SOCKS TCP authentication cannot carry arbitrary WebRTC UDP.
    // An explicit caller policy retains Chromium's normal precedence.
    if (!command_line->HasSwitch(switches::kNoProxyServer) &&
        (command_line->HasSwitch(switches::kProxyServer) ||
         command_line->HasSwitch(switches::kProxyPacUrl) ||
         command_line->HasSwitch(switches::kProxyAutoDetect)) &&
        !command_line->HasSwitch(switches::kForceWebRtcIPHandlingPolicy)) {
      command_line->AppendSwitchASCII(switches::kForceWebRtcIPHandlingPolicy,
                                      "disable_non_proxied_udp");
    }
  }

  // Capture the unpolluted command line snapshot in the browser process.
  // This must happen immediately after CommandLine::Init to ensure we capture
  // the state before any internal programmatic mutations.
  if (!command_line->HasSwitch(switches::kProcessType)) {
    GetInitialCommandLineStorage() = *command_line;
  }

#if BUILDFLAG(IS_WIN)
  if (command_line->HasSwitch(::switches::kRaiseTimerFrequency)) {
    // Raise the timer interrupt frequency and leave it raised.
    timeBeginPeriod(1);
  }
#endif

#if BUILDFLAG(IS_MAC)
  chrome::InitializeExitSixtyNineDetector();
  SetUpBundleOverrides();
#endif

#if BUILDFLAG(IS_LINUX)
  AppendExtraArgumentsToCommandLine(command_line);
#endif

  // PoissonAllocationSampler's TLS slots need to be set up before
  // MainThreadStackSamplingProfiler (in ChromeMainDelegate::ThreadPoolCreated),
  // which can allocate TLS slots of its own. On some platforms pthreads can
  // malloc internally to access higher-numbered TLS slots, which can cause
  // reentry in the heap profiler. (See the comment on
  // ReentryGuard::InitTLSSlot().)
  // TODO(crbug.com/40062835): Clean up other paths that call this Init()
  // function, which are now redundant.
  base::PoissonAllocationSampler::Init();

  // Chrome-specific process modes.
  std::unique_ptr<headless::HeadlessModeHandle> headless_mode_handle;
  if (headless::IsHeadlessMode()) {
    if (command_line->GetArgs().size() > 1) {
      LOG(ERROR) << "Multiple targets are not supported in headless mode.";
      return CHROME_RESULT_CODE_UNSUPPORTED_PARAM;
    }

    auto init_headless_mode = headless::InitHeadlessMode();
    if (!init_headless_mode.has_value()) {
      LOG(ERROR) << init_headless_mode.error();
      return EXIT_FAILURE;
    }

    headless_mode_handle = std::move(init_headless_mode.value());
  }

#if BUILDFLAG(IS_MAC)
  // Gracefully exit if a helper app was launched in an unexpected situation.
  if (IsHelperAppLaunchedBySystemOrThirdPartyApplication()) {
    return 0;
  }
#endif

  int rv = content::ContentMain(std::move(params));

  if (IsNormalResultCode(static_cast<ResultCode>(rv))) {
    return content::RESULT_CODE_NORMAL_EXIT;
  }
  return rv;
}
