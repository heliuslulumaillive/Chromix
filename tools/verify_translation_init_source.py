"""Require bounded native readiness polling and the original cancellation path."""
from pathlib import Path

TARGETS = (
    'components/translate/content/renderer/translate_agent.cc',
    'components/translate/content/renderer/translate_agent.h',
    'components/translate/core/common/translation_init_wait.h',
)


def verify(src: Path):
    cc, header, policy = ((src / name).read_text(encoding='utf-8') for name in TARGETS)
    required = (
        (cc, ('TranslationInitWait init_wait(base::TimeTicks::Now());',
              'TranslatePageImpl(init_wait);',
              'void TranslateAgent::TranslatePageImpl(TranslationInitWait init_wait)',
              'init_wait.NextDelay(base::TimeTicks::Now())',
              'NotifyBrowserTranslationFailed(TranslateErrors::TRANSLATION_TIMEOUT);',
              'weak_method_factory_.GetWeakPtr(), init_wait)',
              'AdjustDelay(static_cast<int>(delay->InMilliseconds()))',
              'weak_method_factory_.InvalidateWeakPtrs();',
              'if (error != TranslateErrors::NONE)',
              'if (!weak_this)', 'CancelPendingTranslation();')),
        (header, ('TranslatePageImpl(TranslationInitWait init_wait)',)),
        (policy, ('deadline_(now + base::Seconds(15))',
                  'const base::TimeDelta remaining = deadline_ - now;',
                  'if (remaining <= base::TimeDelta())', 'return std::nullopt;',
                  'attempt_ = std::min(attempt_ + 1, 7);',
                  'base::Milliseconds(attempt_ * 150), base::Seconds(1)',
                  'remaining});', 'const base::TimeTicks deadline_;')),
    )
    for data, tokens in required:
        if any(token not in data for token in tokens):
            raise ValueError('Native translation readiness/cancellation contract is incomplete')
    if 'kMaxTranslateInitCheckAttempts' in cc or 'TranslatePageImpl(0)' in cc:
        raise ValueError('Obsolete translation attempt budget remains active')
    return list(TARGETS)
