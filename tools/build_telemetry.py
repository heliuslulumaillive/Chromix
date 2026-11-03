#!/usr/bin/env python3
"""Sample this Windows runner and the owned Ninja tree, emitting only new log bytes."""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time

LOG_LIMIT = 64 * 1024


def log_delta(path: Path, previous: dict, limit=LOG_LIMIT):
    """Do not replay a tail on quiet builds; bound only newly appended output."""
    if not path.exists():
        return {'offset': 0, 'identity': None}, {'new_bytes': 0, 'text': '', 'missing': True}
    stat = path.stat()
    identity = [stat.st_dev, stat.st_ino]
    prior = previous.get('offset', 0)
    reset = previous.get('identity') != identity or stat.st_size < prior
    start = 0 if reset else prior
    new_bytes = stat.st_size - start
    skipped = max(0, new_bytes - limit)
    with path.open('rb') as stream:
        stream.seek(start + skipped)
        data = stream.read(min(new_bytes, limit))
    end = start + skipped + len(data)
    return {'offset': end, 'identity': identity}, {
        'new_bytes': end - start, 'omitted_new_bytes': skipped, 'reset': reset,
        'text': data.decode('utf-8', errors='replace'),
    }


def cpu_percent(current, previous):
    if not previous:
        return None
    total = current['kernel'] + current['user'] - previous['kernel'] - previous['user']
    idle = current['idle'] - previous['idle']
    return round(100 * max(0, min(total, total - idle)) / total, 1) if total > 0 else None


def tree_delta(current, previous, seconds, cpus):
    """Only compare processes present at both samples with the same creation time."""
    previous = previous or {}
    matched = [(item, previous[key]) for key, item in current.items()
               if key in previous and item['created'] == previous[key]['created']]
    cpu = sum(max(0, now['cpu_100ns'] - old['cpu_100ns']) for now, old in matched)
    return {
        'sampled_processes': len(current),
        'matched_processes': len(matched),
        'working_set_bytes': sum(item['working_set_bytes'] for item in current.values()),
        'private_bytes': sum(item['private_bytes'] for item in current.values()),
        'cpu_percent_of_host': round(cpu / 10_000_000 / seconds / max(cpus, 1) * 100, 1)
        if matched and seconds > 0 else None,
        'read_bytes_delta': sum(max(0, now['read_bytes'] - old['read_bytes']) for now, old in matched),
        'write_bytes_delta': sum(max(0, now['write_bytes'] - old['write_bytes']) for now, old in matched),
        'io_scope': 'Surviving owned processes only; short-lived children between samples are not counted',
    }


def windows_snapshot(root_pid, expected_root_created=None):
    if os.name != 'nt':
        raise OSError('Windows telemetry requires the runner hosting this build')
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    psapi = ctypes.WinDLL('psapi', use_last_error=True)

    class MemoryStatus(ctypes.Structure):
        _fields_ = [('length', wintypes.DWORD), ('load', wintypes.DWORD),
                    *[(name, ctypes.c_ulonglong) for name in (
                        'total', 'available', 'total_page_file', 'available_page_file',
                        'total_virtual', 'available_virtual', 'extended_virtual')]]

    class ProcessEntry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                    ('flags', wintypes.DWORD), ('exe', wintypes.WCHAR * 260)]

    class ProcessMemory(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('page_faults', wintypes.DWORD),
                    *[(name, ctypes.c_size_t) for name in (
                        'peak_working_set', 'working_set', 'peak_paged', 'paged',
                        'peak_nonpaged', 'nonpaged', 'pagefile', 'peak_pagefile', 'private')]]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            'read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]

    def ticks(value):
        return (value.dwHighDateTime << 32) | value.dwLowDateTime

    kernel.GetSystemTimes.argtypes = [ctypes.POINTER(wintypes.FILETIME)] * 3
    kernel.GetSystemTimes.restype = wintypes.BOOL
    kernel.GlobalMemoryStatusEx.argtypes = [ctypes.POINTER(MemoryStatus)]
    kernel.GlobalMemoryStatusEx.restype = wintypes.BOOL
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32FirstW.restype = wintypes.BOOL
    kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
    kernel.Process32NextW.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.GetProcessIoCounters.argtypes = [wintypes.HANDLE, ctypes.POINTER(IoCounters)]
    kernel.GetProcessIoCounters.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessMemory), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    idle, system, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    if not kernel.GetSystemTimes(ctypes.byref(idle), ctypes.byref(system), ctypes.byref(user)):
        raise ctypes.WinError(ctypes.get_last_error())
    memory = MemoryStatus()
    memory.length = ctypes.sizeof(memory)
    if not kernel.GlobalMemoryStatusEx(ctypes.byref(memory)):
        raise ctypes.WinError(ctypes.get_last_error())
    snapshot = kernel.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    parents = {}
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        more = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            parents[int(entry.pid)] = int(entry.parent)
            more = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)

    def process(pid):
        handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
        if not handle:
            return None
        try:
            created, exited, cpu_kernel, cpu_user = [wintypes.FILETIME() for _ in range(4)]
            stats, io = ProcessMemory(), IoCounters()
            stats.size = ctypes.sizeof(stats)
            if not kernel.GetProcessTimes(handle, *map(ctypes.byref, (created, exited, cpu_kernel, cpu_user))):
                return None
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(stats), stats.size):
                return None
            if not kernel.GetProcessIoCounters(handle, ctypes.byref(io)):
                return None
            return {'created': ticks(created), 'cpu_100ns': ticks(cpu_kernel) + ticks(cpu_user),
                    'working_set_bytes': stats.working_set, 'private_bytes': stats.private,
                    'read_bytes': io.read_bytes, 'write_bytes': io.write_bytes}
        finally:
            kernel.CloseHandle(handle)

    root = process(root_pid)
    tree = {}
    if root and (expected_root_created is None or root['created'] == expected_root_created):
        tree[str(root_pid)] = root
        pending = [root_pid]
        while pending:
            parent = pending.pop()
            for pid, ppid in parents.items():
                if ppid != parent or str(pid) in tree:
                    continue
                item = process(pid)
                # Guard PID reuse: a child must be newer than the parent whose
                # identity was sampled in this same traversal.
                if item and item['created'] >= tree[str(parent)]['created']:
                    tree[str(pid)] = item
                    pending.append(pid)
    return {'cpu_ticks': {'idle': ticks(idle), 'kernel': ticks(system), 'user': ticks(user)},
            'memory': {'total_bytes': memory.total, 'available_bytes': memory.available},
            'processes': tree, 'root_created': root['created'] if tree else expected_root_created,
            'root_alive': bool(tree)}


def sample(args):
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    moment = time.monotonic()
    record = {'schema_version': 1, 'time_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'Same GitHub-hosted Windows job and owned Ninja process tree',
              'root_pid': args.root_pid, 'logs': {}}
    try:
        snapshot = windows_snapshot(args.root_pid, state.get('root_created'))
        seconds = moment - state.get('monotonic', moment)
        record.update(cpu_percent=cpu_percent(snapshot['cpu_ticks'], state.get('cpu_ticks')),
                      memory=snapshot['memory'],
                      tree=tree_delta(snapshot['processes'], state.get('processes'), seconds, os.cpu_count() or 1),
                      root_alive=snapshot['root_alive'])
        state.update(snapshot)
    except Exception as error:
        record['metrics_error'] = str(error)
    try:
        disk = shutil.disk_usage(args.disk)
        record['disk'] = {'total_bytes': disk.total, 'free_bytes': disk.free, 'used_bytes': disk.used}
    except OSError as error:
        record['disk_error'] = str(error)
    state['monotonic'] = moment
    offsets = state.setdefault('logs', {})
    for path in args.log:
        key = str(path.resolve())
        try:
            offsets[key], record['logs'][path.name] = log_delta(path, offsets.get(key, {}))
        except OSError as error:
            record['logs'][path.name] = {'error': str(error), 'new_bytes': 0, 'text': ''}
    args.state.parent.mkdir(parents=True, exist_ok=True)
    args.state.write_text(json.dumps(state, indent=2) + '\n', encoding='utf-8')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(record) + '\n')
    summary = {key: value for key, value in record.items() if key != 'logs'}
    summary['new_log_bytes'] = sum(item['new_bytes'] for item in record['logs'].values())
    print('BUILD_TELEMETRY ' + json.dumps(summary), flush=True)
    for name, item in record['logs'].items():
        if item.get('omitted_new_bytes'):
            print(f'{name}: {item["omitted_new_bytes"]} new bytes retained only in the complete log')
        if item.get('text'):
            print(f'{name}: new output\n' + item['text'], end='' if item['text'].endswith('\n') else '\n')
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root-pid', type=int, required=True)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--disk', type=Path, required=True)
    parser.add_argument('--log', type=Path, action='append', default=[])
    args = parser.parse_args()
    if args.root_pid <= 0:
        parser.error('The owned build process ID must be positive')
    sample(args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
