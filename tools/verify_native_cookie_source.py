"""Source contract for native same-process Cookie bootstrap; not runtime proof."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
try:
    from .verify_native_environment_controls_source import body, code, compact, require, before
except ImportError:
    from verify_native_environment_controls_source import body, code, compact, require, before

CAPABILITIES = 'build/native-cookie-capabilities.json'
OVERLAY = 'patches/native-cookie/0001-profile-cookie-bootstrap.patch'
PREFIX = 'chrome/browser/profiles/'
TARGETS = (
    PREFIX + 'shardx_cookie_bootstrap.h',
    PREFIX + 'shardx_cookie_bootstrap_internal.h',
    PREFIX + 'shardx_cookie_bootstrap_internal.cc',
    PREFIX + 'shardx_cookie_bootstrap.cc',
    PREFIX + 'shardx_native_cookie_unittest.cc',
    PREFIX + 'chrome_browser_main_extra_parts_profiles.cc',
    PREFIX + 'BUILD.gn',
    'chrome/browser/chrome_content_browser_client_navigation_throttles.cc',
    'chrome/browser/BUILD.gn', 'chrome/test/BUILD.gn',
    PREFIX + 'keep_alive/profile_keep_alive_types.h',
    PREFIX + 'keep_alive/profile_keep_alive_types.cc',
    'components/keep_alive_registry/keep_alive_types.h',
    'components/keep_alive_registry/keep_alive_types.cc',
    'tools/metrics/histograms/metadata/profile/enums.xml',
)


def verify(src: Path, repo: Path, *, config=None) -> list[str]:
    legacy = config is None and not (repo / 'build/lite-windows157.json').is_file()
    if config is None and not legacy:
        try:
            from .lite_patch_stack import load
        except ImportError:
            from lite_patch_stack import load
        config = load(repo)['runtime']
    if legacy:
        config = json.loads((repo / 'build/google-services-win155.json').read_text(encoding='utf-8'))
    if (config.get('native_cookie') != {'version': 1, 'capabilities': CAPABILITIES, 'source_gate_required': True}
            or (legacy and OVERLAY not in config.get('restoration_overlays', []))):
        raise ValueError('Native Cookie restoration is not explicitly configured')
    caps = json.loads((repo / CAPABILITIES).read_text(encoding='utf-8'))
    if (caps.get('schema_version') != 1 or caps.get('contract') != 'shardx-native-cookie-v1'
            or caps.get('chromium_version') != config.get('chromium_version')
            or config.get('chromium_version') not in ('156.0.8078.79', '157.0.8103.38')
            or caps.get('limits', {}).get('envelope_bytes') != 16 * 1024 * 1024
            or caps.get('limits', {}).get('cookie_count') != 10000):
        raise ValueError('Native Cookie capability contract is invalid')
    sources = {}
    for name in TARGETS:
        path = src / name
        if not path.is_file(): raise ValueError('Missing native Cookie source: ' + name)
        sources[name] = path.read_text(encoding='utf-8')
    internal = sources[TARGETS[2]]
    service = sources[TARGETS[3]]
    header = sources[TARGETS[1]]
    require(header, ['kMaxEnvelopeBytes = 16 * 1024 * 1024', 'kMaxCookies = 10000',
        '"shardx-cookie-import"', '"shardx-cookie-import-sha256"'], TARGETS[1])
    for forbidden in ('SetForceKeepSessionState', 'NetworkHandler', 'DevToolsAgentHost',
                      'restore_on_startup', 'preserveLaunchState'):
        if forbidden in code(internal + service):
            raise ValueError('Native Cookie bootstrap uses a forbidden compatibility path: ' + forbidden)
    require(body(internal, 'ReadBounded'), ['PlainPath(path)', 'FILE_FLAG_OPEN_REPARSE_POINT',
        'FILE_SHARE_READ', 'FILE_ATTRIBUTE_REPARSE_POINT', 'file.GetInfo(&info)',
        'static_cast<uint64_t>(info.size) > limit', 'base::as_writable_byte_span(bytes)'], 'bounded request read')
    require(body(internal, 'PlainPath', 'const base::FilePath& path'), ['FILE_ATTRIBUTE_REPARSE_POINT', 'current.DirName() == current'], 'reparse checks')
    binding = body(internal, 'ParseBinding')
    require(binding, ['"incognito"', '"guest"', '"profile-directory"',
        'base::Uuid::ParseLowercase(binding.environment_id)', 'Hex64(binding.envelope_sha256)',
        '"Default"', '"requests"', '"receipts"', 'binding.token + ".json"',
        'base::FilePath::CompareEqualIgnoreCase(expected.value(), binding.envelope_path.value())'], 'request binding')
    parser = body(internal, 'ParseEnvelope')
    require(parser, ['crypto::hash::Sha256(bytes)', 'base::JSONReader::Read',
        'envelope.FindDouble("deadlineUnixMs")', 'batch->deadline = base::Time::FromMillisecondsSinceUnixEpoch',
        'cookies->size() > kMaxCookies', 'cookie->IsCanonical()', 'cookie->UniqueKey()',
        '"conflicting_cookie_key"', 'SameImportedAttributes', 'batch->duplicate_count'], 'envelope prevalidation')
    if 'SetCanonicalCookie' in code(internal):
        raise ValueError('Cookie mutations were moved into input prevalidation')
    cookie = body(internal, 'ParseCookie')
    require(cookie, ['canonical_url = source;', 'path_string ? *path_string : ""',
        'const bool secure = item.FindBool("secure").value_or(false)',
        'seconds < 0 && seconds != -1', 'base::Time::UnixEpoch()',
        'net::CanonicalCookie::CreateSanitizedCookie', '*cookie_source_url = canonical_url',
        '"priority"', 'net::CookiePriority::COOKIE_PRIORITY_HIGH',
        'net::CookieSameSite same_site = net::CookieSameSite::UNSPECIFIED', '"Unspecified"'], 'canonical cookie semantics')
    for forbidden in ('secure || source.SchemeIsCryptographic()', "source_path.rfind('/')", 'SimulatedCookieSource'):
        if compact(forbidden) in compact(cookie + service):
            raise ValueError('Cookie attributes are silently rewritten: ' + forbidden)
    require(body(internal, 'SameImportedAttributes'), [
        'expected.Value() == actual.Value()', 'expected.Domain() == actual.Domain()',
        'expected.Path() == actual.Path()', 'expected.ExpiryDate() == actual.ExpiryDate()',
        'expected.IsPersistent() == actual.IsPersistent()',
        'expected.Priority() == actual.Priority()', 'expected.SourcePort() == actual.SourcePort()'], 'exact cookie readback')
    readback = body(internal, 'VerifyReadback')
    require(readback, ['expected->IsExpired(now)', 'if (found != stored.end()) return false;',
        'SameImportedAttributes', 'counts->verified', 'counts->expired'], 'quota and expiry verification')
    completed = body(internal, 'IsCompletedReceipt')
    require(completed, ['"environmentId"', '"profileDirectory"', '"sourceSha256"',
        '"envelopeSha256"', '"requestId"', '"browserPid"', '*accepted != *unique',
        '*expired != *unique - *verified', '*attempted != *accepted',
        'receipt.FindBool("writeOutcomeUnknown") != false',
        'receipt.FindBool("rollbackPerformed") != false'], 'completion ledger bindings')
    previous = body(internal, 'ReadAndPrepare')
    require(previous, ['IsCompletedReceipt(*old, binding, nullptr, true)',
        'IsCompletedReceipt(*old, binding, result.batch.get(), false)'], 'one-shot recovery')
    before(previous, 'IsCompletedReceipt(*old, binding, nullptr, true)',
           'ReadBounded(binding.envelope_path', 'recreated Profile ledger')
    require(body(internal, 'PublishReceipt'), ['Equals(*old, "status", "completed")',
        'base::ImportantFileWriter::WriteFileAtomically', 'binding.receipt_path'], 'atomic receipt publication')
    require(body(internal, 'MakeReceipt'), ['"writeOutcomeUnknown"', '"partialApplied"',
        'receipt.Set("rollbackPerformed", false)', '"completed" : "failed"'], 'failure accounting')

    require(body(service, 'TargetProfile'), ['profile->IsRegularProfile()', '!profile->IsOffTheRecord()',
        '!profile->IsGuestSession()', '"Default"'], 'target Profile')
    require(service, ['ProfileSelection::kOriginalOnly', 'ProfileSelection::kNone',
        'base::RepeatingCallbackList<void(bool)>', 'base::WeakPtr<ImportService> service_',
        'State state_ = State::kPending'], 'Profile-owned bootstrap')
    require(body(service, 'ServiceIsCreatedWithBrowserContext'), ['return true;'], 'eager factory')
    require(service, ['PostTask(', 'base::BindOnce(&ImportService::Start,'], 'navigation-independent start')
    start = body(service, 'Start')
    require(start, ['ScopedProfileKeepAlive::TryAcquire', 'ProfileKeepAliveOrigin::kShardXCookieImport',
        'base::Seconds(60)', 'ReadAndPrepare'], 'bounded import start')
    before(service, 'KeepAliveOrigin::SHARDX_COOKIE_IMPORT',
           'base::BindOnce(&ImportService::Start,', 'no-window process ownership before posted start')
    prepared = body(service, 'OnPrepared')
    before(prepared, 'if (!batch_)', 'CloneInterface(', 'all-cookie validation before writes')
    require(prepared, ['batch_->deadline - base::Time::Now()',
        'std::min(remaining, base::Seconds(60))'], 'shared attempt deadline')
    next_cookie = body(service, 'SetNext')
    require(next_cookie, ['manager_->FlushCookieStore', 'manager_->SetCanonicalCookie',
        'batch_->source_urls.at(counts_.accepted)', 'options.set_include_httponly()',
        'CookieOptions::SameSiteCookieContext::MakeInclusive()'], 'native CookieManager writes')
    status = body(service, 'OnSet')
    before(status, 'if (!result.status.IsInclude())', '++counts_.accepted', 'each native access status')
    require(status, ['Fail("cookie_set_rejected")'], 'excluded cookie failure')
    require(body(service, 'OnFlushed'), ['manager_->GetAllCookies', 'OnReadback'], 'flush then readback')
    verify = body(service, 'OnReadback')
    before(verify, 'VerifyReadback(', 'PublishReceipt', 'readback before publication')
    require(body(service, 'OnPublished'), ['Finish(success && written)'], 'receipt before ready')
    require(body(service, 'OnPublished'), ['if (success && state_ == State::kFailed) return;'], 'late completion stays failed')
    require(body(service, 'OnPublished'), ['base::Time::Now() >= batch_->deadline', 'Finish(false)'], 'late receipt deadline')
    wait = body(service, 'WillStartRequest')
    require(wait, ['State::kCompleted', 'State::kFailed', 'return DEFER', 'service_->Observe',
        'base::BindPostTask(base::SingleThreadTaskRunner::GetCurrentDefault()'], 'navigation gating')
    require(body(service, 'OnComplete'), ['if (success) Resume();', 'CancelDeferredNavigation'], 'navigation release')
    for event in ('Shutdown', 'Finish'):
        require(body(service, event), ['ReleaseKeepalives' if event != 'Finish' else 'std::move(profile_keepalive_)'], 'lifetime/' + event)
    require(body(service, 'OnTimeout'), ['if (publishing_)', 'Finish(false)', 'Fail("timeout")'], 'bounded navigation timeout')
    require(body(service, 'Shutdown'), ['weak_factory_.InvalidateWeakPtrs()', '"cancelled"'], 'shutdown cancellation')

    factories = sources[TARGETS[5]]
    require(factories, ['shardx::cookie_import::EnsureFactoryBuilt();'], 'factory registration')
    navigation = sources[TARGETS[7]]
    before(body(navigation, 'CreateAndAddChromeThrottlesForNavigation'),
           'shardx::cookie_import::MaybeCreateThrottle(registry)',
           'DSEPrewarmNavigationThrottle::MaybeCreateAndAdd(registry)', 'startup navigation order')
    require(sources[TARGETS[6]], ['source_set("shardx_cookie_bootstrap")',
        '"shardx_cookie_bootstrap_internal.cc"', '"//services/network/public/mojom"'], 'native GN target')
    require(sources[TARGETS[8]], ['"//chrome/browser/profiles:shardx_cookie_bootstrap"'], 'browser dependency')
    require(sources[TARGETS[9]], ['"../browser/profiles/shardx_native_cookie_unittest.cc"',
        '"//chrome/browser/profiles:shardx_cookie_bootstrap"'], 'focused native test target')
    require(sources[TARGETS[10]], ['kShardXCookieImport = 47', 'kMaxValue = kShardXCookieImport'], 'profile keepalive enum')
    require(sources[TARGETS[11]], ['case ProfileKeepAliveOrigin::kShardXCookieImport:'], 'profile keepalive printing')
    require(sources[TARGETS[12]], ['SHARDX_COOKIE_IMPORT'], 'browser keepalive enum')
    require(sources[TARGETS[13]], ['case KeepAliveOrigin::SHARDX_COOKIE_IMPORT:'], 'browser keepalive printing')
    if '<int value="47" label="kShardXCookieImport"/>' not in sources[TARGETS[14]]:
        raise ValueError('Profile keepalive metric metadata is missing')
    return list(TARGETS)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--src', type=Path, required=True)
    parser.add_argument('--repo', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps({'status': 'source-contract-passed', 'checked': verify(args.src, args.repo),
                      'runtime_acceptance': 'not_tested'}))
