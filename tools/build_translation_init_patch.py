"""Generate the native translation readiness overlay from pinned 156 sources."""
import argparse
import difflib
from pathlib import Path


POLICY = '''// Copyright 2026 The ShardX Authors
// Use of this source code is governed by a BSD-style license.
#ifndef COMPONENTS_TRANSLATE_CORE_COMMON_TRANSLATION_INIT_WAIT_H_
#define COMPONENTS_TRANSLATE_CORE_COMMON_TRANSLATION_INIT_WAIT_H_

#include <algorithm>
#include <optional>

#include "base/time/time.h"

namespace translate {

// The downloaded element loads additional resources before libReady becomes
// true. A monotonic deadline permits slow initialization without unbounded
// polling. This value is copied only into the current request's cancellable task.
class TranslationInitWait {
 public:
  explicit TranslationInitWait(base::TimeTicks now)
      : deadline_(now + base::Seconds(15)) {}

  std::optional<base::TimeDelta> NextDelay(base::TimeTicks now) {
    const base::TimeDelta remaining = deadline_ - now;
    if (remaining <= base::TimeDelta())
      return std::nullopt;
    attempt_ = std::min(attempt_ + 1, 7);
    return std::min({base::Milliseconds(attempt_ * 150), base::Seconds(1),
                     remaining});
  }

 private:
  const base::TimeTicks deadline_;
  int attempt_ = 0;
};

}  // namespace translate
#endif  // COMPONENTS_TRANSLATE_CORE_COMMON_TRANSLATION_INIT_WAIT_H_
'''

TESTS = '''
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
'''


def generate(upstream, restored, output):
    edits = {}

    def edit(name, replacements, root=upstream):
        old = (root / name).read_text(encoding='utf-8')
        new = old
        for before, after in replacements:
            if new.count(before) != 1:
                raise ValueError('Translation overlay context changed: ' + name + ': ' + before[:70])
            new = new.replace(before, after)
        edits[name] = old, new

    header = 'components/translate/core/common/translation_init_wait.h'
    edits[header] = '', POLICY
    include = '#include "' + header + '"\n'
    edit('components/translate/core/common/BUILD.gn', [
        ('    "translate_errors.h",\n', '    "translate_errors.h",\n    "translation_init_wait.h",\n')])
    edit('components/translate/content/renderer/translate_agent.h', [
        ('#include "components/translate/core/common/translate_errors.h"\n',
         '#include "components/translate/core/common/translate_errors.h"\n' + include),
        ('  // Called by TranslatePage to do the actual translation.  |count| is used to\n'
         '  // limit the number of retries.\n  void TranslatePageImpl(int count);',
         '  // Polls readiness until the current request is ready, errors or expires.\n'
         '  void TranslatePageImpl(TranslationInitWait init_wait);')])
    edit('components/translate/content/renderer/translate_agent.cc', [
        ("// The delay in milliseconds that we'll wait before checking to see if the\n"
         '// translate library injected in the page is ready.\n'
         'const int kTranslateInitCheckDelayMs = 150;\n\n'
         "// The maximum number of times we'll check to see if the translate library\n"
         '// injected in the page is ready.\n'
         'const int kMaxTranslateInitCheckAttempts = 5;\n\n', ''),
        ('  // Set up v8 isolated world.\n',
         '  TranslationInitWait init_wait(base::TimeTicks::Now());\n\n'
         '  // Set up v8 isolated world.\n'),
        ('  TranslatePageImpl(0);', '  TranslatePageImpl(init_wait);'),
        ('void TranslateAgent::TranslatePageImpl(int count) {\n'
         '  DCHECK_LT(count, kMaxTranslateInitCheckAttempts);',
         'void TranslateAgent::TranslatePageImpl(TranslationInitWait init_wait) {'),
        ('    // The library is not ready, try again later, unless we have tried several\n'
         '    // times unsuccessfully already.\n'
         '    if (++count >= kMaxTranslateInitCheckAttempts) {',
         '    // Keep the original deadline across retries. Navigation, revert and a\n'
         '    // replacement request still cancel this task via weak_method_factory_.\n'
         '    const auto delay = init_wait.NextDelay(base::TimeTicks::Now());\n'
         '    if (!delay) {'),
        ('                       weak_method_factory_.GetWeakPtr(), count),\n'
         '        AdjustDelay(count * kTranslateInitCheckDelayMs));',
         '                       weak_method_factory_.GetWeakPtr(), init_wait),\n'
         '        AdjustDelay(static_cast<int>(delay->InMilliseconds())));')])
    edit('chrome/browser/profiles/shardx_native_browser_unittest.cc', [
        ('#include "base/test/scoped_command_line.h"\n',
         '#include "base/test/scoped_command_line.h"\n' + include),
        ('}  // namespace\n', TESTS + '\n}  // namespace\n')], root=restored)
    name = 'chrome/test/BUILD.gn'
    old = (restored / name).read_text(encoding='utf-8')
    start = old.index('  test("native_passkey_unittests") {')
    end = old.index('\n  }', start)
    block = old[start:end]
    before = '      "//components/history/core/browser",\n'
    if block.count(before) != 1:
        raise ValueError('Native test target dependency changed')
    new_block = block.replace(before, before + '      "//components/translate/core/common",\n')
    edits[name] = old, old[:start] + new_block + old[end:]
    data = ''.join(''.join(difflib.unified_diff(old.splitlines(True), new.splitlines(True),
        fromfile='a/' + name if old else '/dev/null', tofile='b/' + name))
        for name, (old, new) in sorted(edits.items()))
    output.parent.mkdir(parents=True, exist_ok=True)
    if not output.exists() or output.read_text(encoding='utf-8') != data:
        output.write_text(data, encoding='utf-8', newline='\n')
    return edits


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--restored', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    generate(args.upstream, args.restored, args.output)
