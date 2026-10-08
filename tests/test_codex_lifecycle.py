"""Windows process-lifetime and real MCP initialization integration tests."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.request import urlopen
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugin/scripts'
spec = importlib.util.spec_from_file_location('lifecycle_test_module', SCRIPTS/'codex_lifecycle.py')
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


@unittest.skipUnless(os.name == 'nt', 'Windows plugin lifecycle')
class LifecycleTests(unittest.TestCase):
    def test_pid_reuse_and_multiple_owners(self):
        with tempfile.TemporaryDirectory() as root:
            owners = Path(root)/'service-owners'; owners.mkdir()
            (owners/'old.json').write_text(json.dumps({'pid': 11, 'created': 100}))
            (owners/'alive.json').write_text(json.dumps({'pid': 22, 'created': 200}))
            with patch.object(lifecycle, 'process_identity', side_effect=lambda pid: {'pid':pid,'created':200}):
                self.assertTrue(lifecycle.has_live_owner(root))
            (owners/'alive.json').unlink()
            with patch.object(lifecycle, 'process_identity', return_value={'pid':11,'created':999}):
                self.assertFalse(lifecycle.has_live_owner(root))

    def test_no_host_does_not_autostart(self):
        calls=[]
        with patch.object(lifecycle, 'codex_owner', return_value=None):
            lifecycle.on_initialize('unused-state', lambda: calls.append(True))
        self.assertEqual(calls, [])

    def test_real_initialize_starts_service_and_last_owner_exit_stops_it(self):
        owner = subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
        owner2 = subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
        mcp = None
        try:
            identity = lifecycle.process_identity(owner.pid)
            identity2 = lifecycle.process_identity(owner2.pid)
            self.assertIsNotNone(identity)
            with tempfile.TemporaryDirectory() as root:
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
                env={**os.environ,'OFFLINE_LIBRARY_STATE':root,'OFFLINE_LIBRARY_PORT':str(port),
                     'LIBRARY_HUB_DATA':root,'LIBRARY_HUB_PORT':str(port),'PYTHONUTF8':'1'}
                code=(f'import sys;sys.path.insert(0,{str(SCRIPTS)!r});import hub\n'
                      f'hub.codex_lifecycle.codex_owner=lambda:{identity!r}\n'
                      'original=hub.subprocess.Popen\n'
                      'def launch(*args,**kwargs):\n'
                      ' child=original(*args,**kwargs)\n'
                      f' hub.Path({str(Path(root)/"service-pid")!r}).write_text(str(child.pid))\n'
                      ' return child\n'
                      'hub.subprocess.Popen=launch\n'
                      'hub.mcp()')
                mcp=subprocess.Popen([sys.executable,'-u','-c',code],env=env,
                                     stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                     text=True,encoding='utf-8')
                # No tools/call or library_open is sent.
                mcp.stdin.write(json.dumps({'jsonrpc':'2.0','id':1,'method':'initialize',
                                           'params':{'protocolVersion':'2024-11-05'}})+'\n')
                mcp.stdin.flush()
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    response=json.loads(pool.submit(mcp.stdout.readline).result(timeout=15))
                self.assertEqual(response['id'],1)
                url=f'http://127.0.0.1:{port}/health'
                with urlopen(url,timeout=3) as reply:
                    self.assertIn(json.load(reply)['app'],['library-hub','offline-image-library'])
                folder=Path(root)/'service-owners'
                (folder/'second.json').write_text(json.dumps(identity2),encoding='utf-8')
                owner.terminate();owner.wait(timeout=5)
                time.sleep(2.5)
                with urlopen(url,timeout=3) as reply:self.assertEqual(reply.status,200)
                owner2.terminate();owner2.wait(timeout=5)
                deadline=time.monotonic()+8
                while time.monotonic()<deadline:
                    try:
                        with urlopen(url,timeout=.5):pass
                    except OSError:break
                    time.sleep(.2)
                else:self.fail('service remained after all Codex owners exited')
                service_pid=int((Path(root)/'service-pid').read_text())
                deadline=time.monotonic()+8
                while lifecycle.process_identity(service_pid) is not None and time.monotonic()<deadline:
                    time.sleep(.1)
                self.assertIsNone(lifecycle.process_identity(service_pid), 'service process did not exit')
                mcp.stdin.close();mcp.wait(timeout=5)
                self.assertEqual(mcp.returncode,0,mcp.stderr.read())
                mcp.stdout.close();mcp.stderr.close();mcp=None
        finally:
            for child in (owner,owner2,mcp):
                if child is not None and child.poll() is None:
                    child.terminate();child.wait(timeout=5)


if __name__ == '__main__':unittest.main()
