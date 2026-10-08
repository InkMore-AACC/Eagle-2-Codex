"""Start with a Codex MCP connection; stop when its owning Codex process exits.

No Windows startup entries, scheduler, model calls, or permanently running helper.
Owner records include process creation time so a reused PID cannot retain a server.
"""
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import threading
import time


def process_identity(pid):
    if os.name != 'nt':
        return None
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000 | 0x100000, False, int(pid))
    if not handle:
        return None
    try:
        if kernel.WaitForSingleObject(handle, 0) != 0x102:
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return {'pid': int(pid), 'created': (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime}
    finally:
        kernel.CloseHandle(handle)


def codex_owner():
    if os.name != 'nt':
        return None

    class Entry(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('usage', wintypes.DWORD),
                    ('pid', wintypes.DWORD), ('heap', ctypes.c_size_t),
                    ('module', wintypes.DWORD), ('threads', wintypes.DWORD),
                    ('parent', wintypes.DWORD), ('priority', wintypes.LONG),
                    ('flags', wintypes.DWORD), ('exe', wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ('Process32FirstW', 'Process32NextW'):
        getattr(kernel, name).argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateToolhelp32Snapshot(2, 0)
    if handle == ctypes.c_void_p(-1).value:
        return None
    try:
        entry = Entry(); entry.size = ctypes.sizeof(entry)
        processes = {}
        ok = kernel.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            processes[entry.pid] = (entry.parent, entry.exe.lower())
            ok = kernel.Process32NextW(handle, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(handle)
    pid = os.getpid(); seen = set()
    while pid in processes and pid not in seen:
        seen.add(pid)
        parent, name = processes[pid]
        if name == 'codex.exe':
            return process_identity(pid)
        pid = parent
    return None


def register_owner(state):
    owner = codex_owner()
    if owner is None:
        return False
    folder = Path(state) / 'service-owners'
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{owner['pid']}-{owner['created']}.json"
    if not target.exists():
        temp = folder / f'{os.getpid()}-{threading.get_ident()}.tmp'
        temp.write_text(json.dumps(owner), encoding='utf-8')
        temp.replace(target)
    return True


def has_live_owner(state):
    for record in (Path(state) / 'service-owners').glob('*.json'):
        try:
            owner = json.loads(record.read_text(encoding='utf-8'))
            if process_identity(owner['pid']) == owner:
                return True
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return False


def watch_owners(server, state):
    if os.environ.get('CODEX_LIBRARY_MANAGED') != '1':
        return

    def watch():
        while has_live_owner(state):
            time.sleep(2)
        server.shutdown()

    threading.Thread(target=watch, name='codex-library-lifetime', daemon=True).start()


def on_initialize(state, ensure_server):
    # Initialization is sent by the host, without a user message or tools/call.
    # Complete startup before acknowledging the plugin connection.
    try:
        if register_owner(state):
            ensure_server()
    except Exception as error:
        # Keep MCP usable for diagnostics if the port is occupied or startup fails.
        print(f'图库自动启动失败：{error}', file=sys.stderr, flush=True)
