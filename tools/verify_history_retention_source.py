"""Verify age expiry changes without replacing native query/deletion machinery."""
from pathlib import Path
try:
    from .verify_native_environment_controls_source import require, before, body
except ImportError:
    from verify_native_environment_controls_source import require, before, body

TARGETS = ('components/history/core/browser/history_backend.cc',
           'components/history/core/browser/expire_history_backend.cc',
           'components/history/core/browser/expire_history_backend.h',
           'components/history/core/browser/shardx_history_retention.h',
           'chrome/browser/chrome_browser_main.cc')


def verify(src: Path):
    texts = {name: (src / name).read_text(encoding='utf-8') for name in TARGETS}
    backend, expirer, header, policy, main = [texts[name] for name in TARGETS]
    require(backend, ['const int days = shardx::CurrentHistoryDays();',
        'expirer_.StartExpiringOldStuff(base::Days(days == 0 ? kExpireDaysThreshold : days), days != 0);',
        'return time < expirer_.GetCurrentExpirationTime();'], TARGETS[0])
    if 'keep-old-history' in backend:
        raise ValueError('Obsolete history maintenance bypass is still active')
    require(header, ['bool expire_visits_ = true;',
        'return expire_visits_ ? base::Time::Now() - expiration_threshold_ : base::Time::Min();'], TARGETS[2])
    iteration = body(expirer, 'ExpireHistoryBackend::DoExpireIteration')
    require(iteration, ['bool more_to_expire = expire_visits_ && ExpireSomeOldHistory(',
        'ClearOldOnDemandFaviconsIfPossible(',
        'ExpireOldSegmentData(base::Time::Now() - expiration_threshold_);', 'ScheduleExpire();'], TARGETS[1])
    require(body(expirer, 'ExpireHistoryBackend::StartExpiringOldStuff'),
            ['expire_visits_ = expire_visits;', 'InitWorkQueue();', 'ScheduleExpire();'], TARGETS[1])
    require(policy, ['kMaximumHistoryDays = 106751991', 'return std::nullopt;',
        'command_line.GetSwitchValueASCII(kHistoryDaysSwitch)', 'CHECK(days.has_value());'], TARGETS[3])
    startup = body(main, 'ChromeBrowserMainParts::PreEarlyInitialization')
    before(startup, 'history::shardx::HistoryDays(', 'std::make_unique<BrowserProcessImpl>', TARGETS[4])
    require(startup, ['return CHROME_RESULT_CODE_UNSUPPORTED_PARAM;'], TARGETS[4])
    return list(TARGETS)
