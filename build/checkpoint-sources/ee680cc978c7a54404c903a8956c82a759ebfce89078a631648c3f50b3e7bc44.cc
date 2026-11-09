// Copyright 2026 The ShardX Authors
// Use of this source code is governed by a BSD-style license.

#include <string>

#include "base/command_line.h"
#include "base/files/file_path.h"
#include "base/test/scoped_command_line.h"
#include "components/translate/core/common/translation_init_wait.h"
#include "components/sync_preferences/common_syncable_prefs_database.h"
#include "components/history/core/browser/shardx_history_retention.h"
#include "chrome/browser/profiles/profile_shortcut_manager.h"
#include "chrome/browser/shell_integration_win.h"
#include "net/base/shardx_environment_controls.h"
#include "testing/gtest/include/gtest/gtest.h"

namespace {

using shell_integration::win::GetAppUserModelIdForApp;
using shell_integration::win::GetAppUserModelIdForBrowser;
using shell_integration::win::IsShardXManagedEnvironment;


TEST(ShardXNativeBrowserTest, RestoredSecurityPrefsHaveNativeSyncMetadata) {
  sync_preferences::CommonSyncablePrefsDatabase database;
  struct Expected {
    const char* name;
    int id;
    syncer::DataType type;
  };
  const Expected expected[] = {
      {"safebrowsing.aesb_update_time_windows_epoch_micros", 46,
       syncer::PRIORITY_PREFERENCES},
      {"safebrowsing.enabled", 48, syncer::PREFERENCES},
      {"safebrowsing.enhanced", 79, syncer::PREFERENCES},
  };
  for (const auto& pref : expected) {
    const auto* metadata = database.GetSyncablePrefMetadata(pref.name);
    ASSERT_NE(metadata, nullptr) << pref.name;
    EXPECT_EQ(metadata->syncable_pref_id(), pref.id) << pref.name;
    EXPECT_EQ(metadata->data_type(), pref.type) << pref.name;
    EXPECT_EQ(metadata->pref_sensitivity(), sync_preferences::PrefSensitivity::kNone);
    EXPECT_EQ(metadata->merge_behavior(), sync_preferences::MergeBehavior::kNone);
    EXPECT_EQ(metadata->write_behavior(), sync_preferences::WriteBehavior::kWriteToBoth);
  }
  EXPECT_EQ(database.GetSyncablePrefMetadata("safebrowsing.unregistered"), nullptr);
}

TEST(ShardXNativeBrowserTest, ManagedEnvironmentOwnsProfileShellIdentity) {
  base::test::ScopedCommandLine scoped_command_line;
  auto* command_line = scoped_command_line.GetProcessCommandLine();
  command_line->AppendSwitchASCII("shardx-managed-environment",
                                 "12345678-1234-4234-8234-123456789abc");
  ASSERT_TRUE(IsShardXManagedEnvironment());
  const base::FilePath first(
      FILE_PATH_LITERAL("C:\\one\\engines\\chromix\\Default"));
  const base::FilePath moved(
      FILE_PATH_LITERAL("D:\\moved\\engines\\chromix\\Default"));
  const std::wstring identity = GetAppUserModelIdForBrowser(first);
  EXPECT_EQ(identity, GetAppUserModelIdForBrowser(moved));
  EXPECT_LE(identity.size(), 128u);
  EXPECT_EQ(std::wstring::npos, identity.find(L' '));
  EXPECT_NE(identity, GetAppUserModelIdForBrowser(
                          first.DirName().AppendASCII("Profile 1")));
  EXPECT_NE(
      GetAppUserModelIdForBrowser(first.DirName().AppendASCII("Profile 1")),
      GetAppUserModelIdForBrowser(first.DirName().AppendASCII("Profile1")));
  EXPECT_NE(identity, GetAppUserModelIdForApp(L"test-web-app", first));
  command_line->AppendSwitchASCII("shardx-managed-environment",
                                 "12345678-1234-4234-8234-123456789ABC");
  EXPECT_EQ(identity, GetAppUserModelIdForBrowser(first));
  command_line->AppendSwitchASCII("shardx-managed-environment",
                                 "87654321-4321-4321-8321-cba987654321");
  EXPECT_NE(identity, GetAppUserModelIdForBrowser(first));
}

TEST(ShardXNativeBrowserTest, InvalidManagedMarkerPreservesNativeIdentity) {
  base::test::ScopedCommandLine scoped_command_line;
  auto* command_line = scoped_command_line.GetProcessCommandLine();
  command_line->RemoveSwitch("shardx-managed-environment");
  const base::FilePath path(FILE_PATH_LITERAL("C:\\data\\Default"));
  const std::wstring native_identity = GetAppUserModelIdForBrowser(path);
  for (const char* invalid : {"", "not-a-uuid", "../Default", "12345678"}) {
    command_line->AppendSwitchASCII("shardx-managed-environment", invalid);
    EXPECT_FALSE(IsShardXManagedEnvironment());
    EXPECT_EQ(native_identity, GetAppUserModelIdForBrowser(path));
  }
}

TEST(ShardXNativeBrowserTest, ManagedProfileShortcutsRemainUnavailable) {
  base::test::ScopedCommandLine scoped_command_line;
  auto* command_line = scoped_command_line.GetProcessCommandLine();
  command_line->AppendSwitchASCII("shardx-managed-environment",
                                 "12345678-1234-4234-8234-123456789abc");
  command_line->AppendSwitch("enable-profile-shortcut-manager");
  EXPECT_FALSE(ProfileShortcutManager::IsFeatureEnabled());
}

TEST(ShardXNativeBrowserTest, BlockedPortParserPreservesBoundsAndDenySemantics) {
  const auto ports = net::shardx::ParseBlockedPorts("443,80,443,1,65535");
  ASSERT_TRUE(ports.valid);
  EXPECT_EQ(4u, ports.ports.size());
  for (int port : {1, 80, 443, 65535}) {
    EXPECT_TRUE(ports.Contains(port));
  }
  EXPECT_FALSE(ports.Contains(8080));
  EXPECT_FALSE(net::shardx::ParseBlockedPorts("").Contains(80));
  for (const char* invalid : {"0", "65536", "-1", " 80", "80,", ",80",
                              "80,,443", "80x"}) {
    const auto rejected = net::shardx::ParseBlockedPorts(invalid);
    EXPECT_FALSE(rejected.valid) << invalid;
    EXPECT_TRUE(rejected.Contains(80)) << invalid;
  }
  std::string too_many;
  for (int i = 0; i < 257; ++i) {
    if (i)
      too_many += ',';
    too_many += '1';
  }
  EXPECT_FALSE(net::shardx::ParseBlockedPorts(too_many).valid);
}


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


TEST(ShardXNativeBrowserTest, TranslationInitWaitAllowsSlowLibrary) {
  const auto start = base::TimeTicks() + base::Seconds(1);
  translate::TranslationInitWait wait(start);
  auto now = start;
  // The old five checks timed out at 1500ms. A 2545ms library is still eligible.
  while (now - start < base::Milliseconds(2545)) {
    const auto delay = wait.NextDelay(now);
    ASSERT_TRUE(delay.has_value());
    EXPECT_GT(*delay, base::TimeDelta());
    EXPECT_LE(*delay, base::Seconds(1));
    now += *delay;
  }
  EXPECT_TRUE(wait.NextDelay(now).has_value());
}

TEST(ShardXNativeBrowserTest, TranslationInitWaitExpiresAtDeadline) {
  const auto start = base::TimeTicks() + base::Seconds(1);
  translate::TranslationInitWait wait(start);
  auto now = start;
  int checks = 0;
  while (const auto delay = wait.NextDelay(now)) {
    ASSERT_GT(*delay, base::TimeDelta());
    ASSERT_LE(*delay, base::Seconds(1));
    now += *delay;
    ASSERT_LT(++checks, 30);
  }
  EXPECT_EQ(base::Seconds(15), now - start);
  EXPECT_FALSE(wait.NextDelay(start + base::Seconds(60)).has_value());
  translate::TranslationInitWait final_poll(start);
  EXPECT_EQ(base::Milliseconds(1),
            final_poll.NextDelay(start + base::Milliseconds(14999)));
}

TEST(ShardXNativeBrowserTest, TranslationInitWaitIsPerRequest) {
  const auto start = base::TimeTicks() + base::Seconds(1);
  translate::TranslationInitWait old_request(start);
  translate::TranslationInitWait new_request(start + base::Seconds(16));
  EXPECT_FALSE(old_request.NextDelay(start + base::Seconds(16)).has_value());
  EXPECT_EQ(base::Milliseconds(150),
            new_request.NextDelay(start + base::Seconds(16)));
}

}  // namespace
