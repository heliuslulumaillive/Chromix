// Copyright 2025 The Chromium Authors
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "chrome/browser/ui/views/frame/windows_taskbar_icon_updater.h"

#include "base/check_is_test.h"
#include "chrome/browser/browser_process.h"
#include "chrome/browser/profiles/profile.h"
#include "chrome/browser/profiles/profile_manager.h"
#include "chrome/browser/shell_integration_win.h"
#include "chrome/browser/signin/identity_manager_factory.h"
#include "chrome/browser/taskbar/taskbar_decorator_win.h"
#include "chrome/browser/ui/views/frame/browser_view.h"
#include "components/signin/public/base/consent_level.h"

WindowsTaskbarIconUpdater::WindowsTaskbarIconUpdater(BrowserView& browser_view)
    : browser_view_(browser_view) {
  // The profile manager may by null in tests.
  if (g_browser_process->profile_manager()) {
    profile_observation_.Observe(
        &g_browser_process->profile_manager()->GetProfileAttributesStorage());
  } else {
    CHECK_IS_TEST();
  }

  Profile* profile = browser_view_->browser()->GetProfile();
  if (shell_integration::win::IsShardXManagedEnvironment() &&
      profile->IsRegularProfile()) {
    if (auto* identity = IdentityManagerFactory::GetForProfile(profile)) {
      identity_observation_.Observe(identity);
    }
  }
  browser_view_observer_.Observe(&*browser_view_);
}

WindowsTaskbarIconUpdater::~WindowsTaskbarIconUpdater() = default;

void WindowsTaskbarIconUpdater::OnProfileAdded(
    const base::FilePath& profile_path) {
  UpdateIcon();
}

void WindowsTaskbarIconUpdater::OnProfileWasRemoved(
    const base::FilePath& profile_path,
    const std::u16string& profile_name) {
  UpdateIcon();
}

void WindowsTaskbarIconUpdater::OnProfileAvatarChanged(
    const base::FilePath& profile_path) {
  UpdateIcon();
}

void WindowsTaskbarIconUpdater::OnProfileHighResAvatarLoaded(
    const base::FilePath& profile_path) {
  UpdateIcon();
}

void WindowsTaskbarIconUpdater::OnPrimaryAccountChanged(
    const signin::PrimaryAccountChangeEvent&) {
  UpdateIcon();
}

void WindowsTaskbarIconUpdater::OnExtendedAccountInfoUpdated(
    const AccountInfo& info) {
  if (info.GetAccountId() == identity_observation_.GetSource()->GetPrimaryAccountId(
                             signin::ConsentLevel::kSignin)) {
    UpdateIcon();
  }
}

void WindowsTaskbarIconUpdater::OnExtendedAccountInfoRemoved(
    const AccountInfo& info) {
  OnExtendedAccountInfoUpdated(info);
}

void WindowsTaskbarIconUpdater::OnIdentityManagerShutdown(
    signin::IdentityManager*) {
  identity_observation_.Reset();
}

void WindowsTaskbarIconUpdater::OnViewAddedToWidget(
    views::View* observed_view) {
  // When the browser view is created, it does not yet have a widget; this
  // attaches the widget when it is created.
  browser_widget_observer_.Observe(observed_view->GetWidget());
  browser_view_observer_.Reset();
}

void WindowsTaskbarIconUpdater::OnWidgetVisibilityChanged(views::Widget* widget,
                                                          bool visible) {
  // UpdateTaskbarDecoration() calls DrawTaskbarDecoration(), but that does
  // nothing if the window is not visible.  So even if we've already gotten the
  // up-to-date decoration, we need to run the update procedure again here when
  // the window becomes visible.
  if (visible) {
    UpdateIcon();
  }
}

void WindowsTaskbarIconUpdater::UpdateIcon() {
  if (!browser_view_->GetWidget() || !browser_view_->GetNativeWindow()) {
    return;
  }
  taskbar::UpdateTaskbarDecoration(browser_view_->browser()->GetProfile(),
                                   browser_view_->GetNativeWindow());
}
