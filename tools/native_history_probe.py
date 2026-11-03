"""Fresh temporary History DB fixtures, queried/deleted through native history WebUI."""
import sqlite3
import time
from contextlib import closing

AGES = (10, 120, 301)
POLICIES = (('permanent', 0, [10, 120, 301]), ('days300', 300, [10, 120]),
            ('unmanaged', None, [10]))
QUERY = '''async term => {
  if (!window.__shardxHistory) {
    const {PageHandler, PageCallbackRouter} = await import(
      'chrome://resources/cr_components/history/history.mojom-webui.js');
    const handler = PageHandler.getRemote(), router = new PageCallbackRouter();
    handler.setPage(router.$.bindNewPipeAndPassRemote());
    window.__shardxHistory = {handler, router};
  }
  const {results} = await window.__shardxHistory.handler.queryHistory(term, 100, null, true, false);
  return results.value.map(row => ({url: row.url, time: row.time, timestamps: row.allTimestamps}));
}'''
DELETE = '''async row => {
  await window.__shardxHistory.handler.removeVisits([
    {url:row.url, timestamps:Object.values(row.timestamps).flat()}]);
}'''


def probe(playwright, browser, root, origin, options_for, retain_children):
    rows = []
    for name, days, expected in POLICIES:
        row = {'name': name, 'days': days, 'completed': False}
        rows.append(row)
        data_root = root / ('history-' + name)
        data_root.mkdir()
        options = options_for(data_root)
        # Seed with permanent retention, then age owned rows while Chrome is closed.
        context = None
        try:
            context = playwright.chromium.launch_persistent_context(str(data_root), **options)
            retain_children()
            page = context.new_page()
            for age in AGES:
                page.goto(origin + '/page/ShardXHistoryProbe' + str(age), wait_until='load', timeout=15000)
            context.close(); context = None
            history = data_root / 'Default/History'
            now_us = int((time.time() + 11644473600) * 1000000)
            with closing(sqlite3.connect(history)) as db:
                counts = {}
                for age in AGES:
                    url = origin + '/page/ShardXHistoryProbe' + str(age)
                    timestamp = now_us - age * 86400 * 1000000
                    result = db.execute('UPDATE visits SET visit_time=? WHERE url IN (SELECT id FROM urls WHERE url=?)', (timestamp, url))
                    counts[str(age)] = result.rowcount
                    db.execute('UPDATE urls SET last_visit_time=? WHERE url=?', (timestamp, url))
                db.commit()
            row['seeded_visit_counts'] = counts
            row['reference_unix_ms'] = now_us / 1000 - 11644473600000
            if any(value < 1 for value in counts.values()):
                raise ValueError('Native visits were not recorded in the owned History database')
            options['args'] = [arg for arg in options['args'] if not arg.startswith('--shardx-history-retention-days=')]
            if days is None:
                options['args'] = [arg for arg in options['args'] if not arg.startswith('--shardx-managed-environment=')]
            else:
                options['args'].append('--shardx-history-retention-days=' + str(days))
            row['arguments'] = options['args'][:]
            context = playwright.chromium.launch_persistent_context(str(data_root), **options)
            retain_children()
            session = context.browser.new_browser_cdp_session()
            row['actual_arguments'] = session.send('Browser.getBrowserCommandLine')['arguments']
            page = context.new_page()
            page.goto('chrome://history/', wait_until='load', timeout=15000)
            started = time.monotonic()
            page.wait_for_timeout(36000)  # Native expiry cadence is 30 seconds.
            row['expiration_wait_seconds'] = time.monotonic() - started
            found = page.evaluate(QUERY, 'ShardXHistoryProbe')
            row['found'] = found
            row['found_ages'] = sorted({int(item['url'].rsplit('ShardXHistoryProbe', 1)[1]) for item in found})
            if row['found_ages'] != expected:
                raise ValueError('Native historical search or age expiration differs from policy')
            if name == 'permanent':
                selected = next(item for item in found if item['url'].endswith('301'))
                page.evaluate(DELETE, selected)
                row['after_manual_delete'] = page.evaluate(QUERY, 'ShardXHistoryProbe301')
                private = context.browser.new_context()
                try:
                    tab = private.new_page()
                    tab.goto(origin + '/page/ShardXPrivateHistoryProbe', wait_until='load', timeout=15000)
                finally:
                    private.close()
                row['private_history'] = page.evaluate(QUERY, 'ShardXPrivateHistoryProbe')
            context.close(); context = None
            with closing(sqlite3.connect('file:' + history.as_posix() + '?mode=ro', uri=True)) as db:
                row['database_check'] = db.execute('PRAGMA quick_check').fetchone()[0]
            row['completed'] = True
        except Exception as error:
            row['error'] = type(error).__name__ + ': ' + str(error)
        finally:
            if context is not None:
                context.close()
    return rows


def assess(rows):
    checks = []
    if not isinstance(rows, list):
        rows = []
    by_name = {row.get('name'): row for row in rows if isinstance(row, dict)}
    checks.append({'name': 'history/complete_scenarios', 'passed':
        len(rows) == len(POLICIES) and set(by_name) == {item[0] for item in POLICIES}})
    for name, days, expected in POLICIES:
        row = by_name.get(name, {})
        raw_valid = False
        try:
            found = row['found']
            raw_ages = sorted({int(item['url'].rsplit('ShardXHistoryProbe', 1)[1]) for item in found})
            args = row['actual_arguments']
            policy = [arg for arg in args if arg.startswith('--shardx-history-retention-days=')]
            managed = [arg for arg in args if arg.startswith('--shardx-managed-environment=')]
            raw_valid = (raw_ages == expected and len(found) == len(expected)
                and policy == ([] if days is None else ['--shardx-history-retention-days=' + str(days)])
                and len(managed) == (0 if days is None else 1)
                and all(abs(row['reference_unix_ms'] - item['time'] -
                    int(item['url'].rsplit('ShardXHistoryProbe', 1)[1]) * 86400000) < 1000 for item in found))
        except (KeyError, TypeError, ValueError):
            pass
        good = (row.get('completed') is True and not row.get('error') and row.get('days') == days
                and raw_valid
                and row.get('found_ages') == expected and row.get('database_check') == 'ok'
                and type(row.get('expiration_wait_seconds')) in (int, float)
                and 35 <= row['expiration_wait_seconds'] < 120
                and all(type(row.get('seeded_visit_counts', {}).get(str(age))) is int
                        and row['seeded_visit_counts'][str(age)] > 0 for age in AGES))
        if name == 'permanent':
            good = good and row.get('after_manual_delete') == [] and row.get('private_history') == []
        checks.append({'name': 'history/' + name, 'passed': good is True})
    return checks
