"""Generate managed session/whole-browser exit changes from pinned sources."""
import argparse
import difflib
from pathlib import Path


POLICY = '''// Copyright 2026 The ShardX Authors
// Use of this source code is governed by a BSD-style license.
#ifndef CHROME_BROWSER_PROFILES_SHARDX_SESSION_POLICY_H_
#define CHROME_BROWSER_PROFILES_SHARDX_SESSION_POLICY_H_
#include <optional>
#include "base/check.h"
#include "base/command_line.h"
#include "base/uuid.h"

namespace shardx {
inline constexpr char kRestoreSession[] = "shardx-restore-previous-session";
inline constexpr wchar_t kExitEnvironmentMessage[] = L"ShardX.Lite.ExitEnvironment.v1";
inline constexpr unsigned long kExitEnvironmentAck = 0x53484c58;

// An explicit launcher choice overrides native startup preferences for this
// process only. Unmanaged browsers retain Chromium's own startup behavior.
inline std::optional<bool> ManagedSessionRestore(const base::CommandLine& command) {
  if (!command.HasSwitch(kRestoreSession))
    return std::nullopt;
  CHECK(base::Uuid::ParseCaseInsensitive(command.GetSwitchValueASCII(
      "shardx-managed-environment")).is_valid());
  const auto value = command.GetSwitchValueASCII(kRestoreSession);
  CHECK(value == "true" || value == "false");
  return value == "true";
}
}  // namespace shardx
#endif
'''


def generate(upstream: Path, effective: Path, output: Path):
    edits = {}

    def edit(name, replacements):
        source = effective / name
        if not source.is_file():
            source = upstream / name
        old = source.read_text(encoding='utf-8')
        new = old
        for before, after in replacements:
            if new.count(before) != 1:
                raise ValueError('Session lifecycle context changed: ' + name + ': ' + before[:90])
            new = new.replace(before, after)
        edits[name] = old, new

    header = 'chrome/browser/profiles/shardx_session_policy.h'
    edits[header] = '', POLICY
    include = '#include "' + header + '"\n'
    edit('chrome/browser/profiles/BUILD.gn', [
        ('    "profile.h",\n', '    "profile.h",\n    "shardx_session_policy.h",\n')])
    edit('chrome/browser/ui/startup/startup_browser_creator.cc', [
        ('#include "chrome/browser/profiles/profile.h"\n',
         '#include "chrome/browser/profiles/profile.h"\n' + include),
        ('  SessionStartupPref pref = SessionStartupPref::GetStartupPref(prefs);\n',
         '  SessionStartupPref pref = SessionStartupPref::GetStartupPref(prefs);\n'
         '  if (!profile->IsOffTheRecord() && !profile->IsGuestSession()) {\n'
         '    if (const auto restore = shardx::ManagedSessionRestore(command_line)) {\n'
         '      pref.type = *restore ? SessionStartupPref::LAST : SessionStartupPref::DEFAULT;\n'
         '      pref.urls.clear();\n'
         '      return pref;\n'
         '    }\n'
         '  }\n')])
    edit('chrome/browser/prefs/chrome_command_line_pref_store.cc', [
        ('#include "chrome/browser/prefs/chrome_command_line_pref_store.h"\n',
         '#include "chrome/browser/prefs/chrome_command_line_pref_store.h"\n' + include),
        ('  ApplyExplicitlyAllowedPortSwitch();\n',
         '  ApplyExplicitlyAllowedPortSwitch();\n'
         '  if (const auto restore = shardx::ManagedSessionRestore(*command_line)) {\n'
         '    SetValue(prefs::kRestoreOnStartup, base::Value(*restore ? 1 : 5),\n'
         '             WriteablePrefStore::DEFAULT_PREF_WRITE_FLAGS);\n'
         '  }\n')])
    edit('chrome/browser/ui/startup/startup_browser_creator_impl.cc', [
        ('#include "chrome/browser/profiles/profile.h"\n',
         '#include "chrome/browser/profiles/profile.h"\n' + include),
        ('  bool is_post_crash_launch = HasPendingUncleanExit(profile_);\n',
         '  bool is_post_crash_launch = HasPendingUncleanExit(profile_);\n'
         '  const bool managed_restore = !is_incognito_or_guest &&\n'
         '      shardx::ManagedSessionRestore(*command_line_).value_or(false);\n'),
        ('          *command_line_, is_first_run_, is_post_crash_launch,\n',
         '          *command_line_, is_first_run_, is_post_crash_launch && !managed_restore,\n'),
        ('  if (is_post_crash_launch) {\n    behavior_options |= IS_POST_CRASH_LAUNCH;\n',
         '  // A managed opt-in requests the native restore path after a crash too.\n'
         '  // ExitTypeService acknowledges recovery when restore actually completes;\n'
         '  // no Preferences/exit-type files are forged by the launcher.\n'
         '  if (is_post_crash_launch && !managed_restore) {\n'
         '    behavior_options |= IS_POST_CRASH_LAUNCH;\n'),
        ('  LaunchResult launch_result =\n      tabs.empty() ? LaunchResult::kNormally : LaunchResult::kWithGivenUrls;\n',
         '  LaunchResult launch_result =\n'
         '      tabs.empty() ? LaunchResult::kNormally : LaunchResult::kWithGivenUrls;\n'
         '  if (!is_incognito_or_guest && shardx::ManagedSessionRestore(*command_line_).has_value()) {\n'
         '    // The launcher supplies the environment startup page. Native restore\n'
         '    // receives no artificial new tab and disabled restore cannot reopen\n'
         '    // pinned tabs or native startup URLs behind the global choice.\n'
         '    return {std::move(tabs), launch_result};\n'
         '  }\n')])
    edit('chrome/browser/sessions/session_restore.cc', [
        ('#include "chrome/browser/profiles/profile.h"\n',
         '#include "chrome/browser/profiles/profile.h"\n' + include),
        ('    PruneWindows(windows);\n',
         '    PruneWindows(windows);\n'
         '    if (shardx::ManagedSessionRestore(*base::CommandLine::ForCurrentProcess()).value_or(false)) {\n'
         '      // Deduplicate only additional startup pages. The saved session may\n'
         '      // intentionally contain duplicate tabs and must remain unchanged.\n'
         '      std::erase_if(startup_tabs_, [&](const StartupTab& startup) {\n'
         '        for (const auto& window : *windows) {\n'
         '          for (const auto& tab : window->tabs) {\n'
         '            if (tab->navigations.empty()) continue;\n'
         '            const int index = std::clamp(tab->current_navigation_index, 0,\n'
         '                static_cast<int>(tab->navigations.size()) - 1);\n'
         '            if (tab->navigations[index].virtual_url() == startup.url) return true;\n'
         '          }\n'
         '        }\n'
         '        return false;\n'
         '      });\n'
         '    }\n')])
    edit('chrome/browser/ui/views/frame/browser_desktop_window_tree_host_win.cc', [
        ('#include "chrome/browser/ui/views/frame/browser_desktop_window_tree_host_win.h"\n',
         '#include "chrome/browser/ui/views/frame/browser_desktop_window_tree_host_win.h"\n'
         '#include "chrome/browser/lifetime/application_lifetime.h"\n'
         '#include "base/task/single_thread_task_runner.h"\n' + include),
        ('                                                   LRESULT* result) {\n  switch (message) {',
         '                                                   LRESULT* result) {\n'
         '  static const UINT exit_message = ::RegisterWindowMessageW(shardx::kExitEnvironmentMessage);\n'
         '  if (exit_message && message == exit_message && w_param == shardx::kExitEnvironmentAck &&\n'
         '      shardx::ManagedSessionRestore(*base::CommandLine::ForCurrentProcess()).has_value()) {\n'
         '    // Post the native Exit command after acknowledging the owner. This\n'
         '    // preserves the whole session and native beforeunload cancellation.\n'
         '    *result = shardx::kExitEnvironmentAck;\n'
         '    base::SingleThreadTaskRunner::GetCurrentDefault()->PostTask(\n'
         '        FROM_HERE, base::BindOnce(&chrome::AttemptExit));\n'
         '    return true;\n'
         '  }\n'
         '  switch (message) {')])
    patch = ''.join(''.join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
        fromfile='a/' + name if a else '/dev/null', tofile='b/' + name))
        for name, (a, b) in sorted(edits.items()))
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists() or output.read_text(encoding='utf-8') != patch:
        output.write_text(patch, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--effective', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generate(args.upstream, args.effective, args.output)
