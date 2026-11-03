"""Generate the narrow history overlay from pinned upstream and shell sources."""
import argparse
import difflib
from pathlib import Path


POLICY = '''// Copyright 2026 The ShardX Authors
// Use of this source code is governed by a BSD-style license.
#ifndef COMPONENTS_HISTORY_CORE_BROWSER_SHARDX_HISTORY_RETENTION_H_
#define COMPONENTS_HISTORY_CORE_BROWSER_SHARDX_HISTORY_RETENTION_H_

#include <optional>
#include <string_view>
#include "base/check.h"
#include "base/command_line.h"
#include "base/uuid.h"

namespace history::shardx {
inline constexpr int kMaximumHistoryDays = 106751991;
inline constexpr char kHistoryDaysSwitch[] = "shardx-history-retention-days";

inline std::optional<int> ParseHistoryDays(std::string_view value) {
  if (value.empty() || (value.size() > 1 && value.front() == '0'))
    return std::nullopt;
  int days = 0;
  for (char character : value) {
    if (character < '0' || character > '9')
      return std::nullopt;
    const int digit = character - '0';
    if (days > (kMaximumHistoryDays - digit) / 10)
      return std::nullopt;
    days = days * 10 + digit;
  }
  return days;
}

// Missing policy is allowed for unmanaged Chromium only. Zero means permanent.
inline std::optional<int> HistoryDays(const base::CommandLine& command_line) {
  if (!command_line.HasSwitch("shardx-managed-environment")) {
    return command_line.HasSwitch(kHistoryDaysSwitch) ? std::nullopt
                                                    : std::optional<int>(90);
  }
  if (!base::Uuid::ParseCaseInsensitive(command_line.GetSwitchValueASCII(
          "shardx-managed-environment")).is_valid())
    return std::nullopt;
  return ParseHistoryDays(command_line.GetSwitchValueASCII(kHistoryDaysSwitch));
}

inline int CurrentHistoryDays() {
  const auto days = HistoryDays(*base::CommandLine::ForCurrentProcess());
  CHECK(days.has_value());
  return *days;
}
}  // namespace history::shardx
#endif  // COMPONENTS_HISTORY_CORE_BROWSER_SHARDX_HISTORY_RETENTION_H_
'''

TESTS = '''
TEST(ShardXNativeBrowserTest, HistoryRetentionRejectsInvalidValues) {
  using history::shardx::ParseHistoryDays;
  for (const char* invalid : {"", "-1", "+1", "1.5", " 300", "300 ", "01",
                              "106751992", "999999999999999999999999"}) {
    EXPECT_FALSE(ParseHistoryDays(invalid).has_value()) << invalid;
  }
  EXPECT_EQ(0, ParseHistoryDays("0"));
  EXPECT_EQ(1, ParseHistoryDays("1"));
  EXPECT_EQ(300, ParseHistoryDays("300"));
  EXPECT_EQ(106751991, ParseHistoryDays("106751991"));
}

TEST(ShardXNativeBrowserTest, ManagedHistoryRequiresExplicitPolicy) {
  base::CommandLine command_line(base::CommandLine::NO_PROGRAM);
  EXPECT_EQ(90, history::shardx::HistoryDays(command_line));
  command_line.AppendSwitchASCII("shardx-history-retention-days", "0");
  EXPECT_FALSE(history::shardx::HistoryDays(command_line).has_value());
  command_line.AppendSwitchASCII("shardx-managed-environment",
                                 "01234567-89ab-4cde-8123-456789abcdef");
  EXPECT_EQ(0, history::shardx::HistoryDays(command_line));
  command_line.AppendSwitchASCII("shardx-history-retention-days", "300");
  EXPECT_EQ(300, history::shardx::HistoryDays(command_line));
  command_line.RemoveSwitch("shardx-history-retention-days");
  EXPECT_FALSE(history::shardx::HistoryDays(command_line).has_value());
  command_line.AppendSwitchASCII("shardx-managed-environment", "invalid");
  EXPECT_FALSE(history::shardx::HistoryDays(command_line).has_value());
}
'''


def generate(upstream, shell, output):
    edits = {}

    def edit(name, changes, root=upstream):
        old = (root / name).read_text(encoding='utf-8')
        new = old
        for before, after in changes:
            if new.count(before) != 1:
                raise ValueError('History overlay context changed: ' + name + ': ' + before[:80])
            new = new.replace(before, after)
        edits[name] = old, new

    header = 'components/history/core/browser/shardx_history_retention.h'
    edits[header] = '', POLICY
    include = '#include "' + header + '"\n'
    edit('components/history/core/browser/BUILD.gn', [
        ('    "history_backend.h",\n', '    "history_backend.h",\n    "shardx_history_retention.h",\n')])
    edit('components/history/core/browser/history_backend.cc', [
        ('#include "components/history/core/browser/history_backend.h"\n',
         '#include "components/history/core/browser/history_backend.h"\n' + include),
        ('  expirer_.StartExpiringOldStuff(base::Days(kExpireDaysThreshold));',
         '  const int days = shardx::CurrentHistoryDays();\n'
         '  expirer_.StartExpiringOldStuff(\n'
         '      base::Days(days == 0 ? kExpireDaysThreshold : days), days != 0);')])
    edit('components/history/core/browser/expire_history_backend.h', [
        ('  void StartExpiringOldStuff(base::TimeDelta expiration_threshold);',
         '  // Disabling visit expiry retains periodic favicon and segment maintenance.\n'
         '  void StartExpiringOldStuff(base::TimeDelta expiration_threshold,\n'
         '                             bool expire_visits = true);'),
        ('    return base::Time::Now() - expiration_threshold_;',
         '    return expire_visits_ ? base::Time::Now() - expiration_threshold_\n'
         '                          : base::Time::Min();'),
        ('  base::TimeDelta expiration_threshold_;',
         '  base::TimeDelta expiration_threshold_;\n  bool expire_visits_ = true;')])
    edit('components/history/core/browser/expire_history_backend.cc', [
        ('    base::TimeDelta expiration_threshold) {\n  expiration_threshold_ = expiration_threshold;',
         '    base::TimeDelta expiration_threshold, bool expire_visits) {\n'
         '  expiration_threshold_ = expiration_threshold;\n  expire_visits_ = expire_visits;'),
        ('  bool more_to_expire = ExpireSomeOldHistory(\n      GetCurrentExpirationTime(), reader, kNumExpirePerIteration);',
         '  bool more_to_expire = expire_visits_ && ExpireSomeOldHistory(\n'
         '      GetCurrentExpirationTime(), reader, kNumExpirePerIteration);'),
        ('  ExpireOldSegmentData(GetCurrentExpirationTime());',
         '  // Segment aggregates are maintenance data, not retained visits.\n'
         '  ExpireOldSegmentData(base::Time::Now() - expiration_threshold_);')])
    edit('chrome/browser/chrome_browser_main.cc', [
        ('#include "chrome/common/chrome_result_codes.h"\n',
         '#include "chrome/common/chrome_result_codes.h"\n' + include),
        ('int ChromeBrowserMainParts::PreEarlyInitialization() {\n',
         'int ChromeBrowserMainParts::PreEarlyInitialization() {\n'
         '  if (!history::shardx::HistoryDays(\n'
         '          *base::CommandLine::ForCurrentProcess()).has_value()) {\n'
         '    LOG(ERROR) << "Invalid managed history retention policy";\n'
         '    return CHROME_RESULT_CODE_UNSUPPORTED_PARAM;\n  }\n')])
    edit('chrome/browser/profiles/shardx_native_browser_unittest.cc', [
        ('#include "base/test/scoped_command_line.h"\n',
         '#include "base/test/scoped_command_line.h"\n' + include),
        ('}  // namespace\n', TESTS + '\n}  // namespace\n')], root=shell)
    name = 'chrome/test/BUILD.gn'
    old = (shell / name).read_text(encoding='utf-8')
    start = old.index('  test("native_passkey_unittests") {')
    end = old.index('\n  }', start)
    block = old[start:end]
    changed = block.replace('      "//components/prefs",',
                            '      "//components/history/core/browser",\n      "//components/prefs",', 1)
    assert changed != block
    edits[name] = old, old[:start] + changed + old[end:]
    patch = ''.join(''.join(difflib.unified_diff(a.splitlines(True), b.splitlines(True),
        fromfile='a/' + name if a else '/dev/null', tofile='b/' + name))
        for name, (a, b) in sorted(edits.items()))
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists() or output.read_text(encoding='utf-8') != patch:
        output.write_text(patch, encoding='utf-8', newline='\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--shell', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generate(args.upstream, args.shell, args.output)
