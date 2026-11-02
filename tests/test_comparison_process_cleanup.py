"""A completed launcher cannot leave comparison workers holding GPU resources."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from mint.comparison.jobs import ComparisonManager
from mint.inference.cache_budget import _token
from tests.test_comparison import fake_backend


@pytest.mark.skipif(not Path('/proc/self/stat').exists(), reason='Linux process-session regression')
def test_manager_cleans_descendants_after_launcher_exit_without_touching_other_sessions(tmp_path, monkeypatch):
    monkeypatch.setattr('mint.comparison.jobs.backend_spec', fake_backend)
    monkeypatch.setattr('mint.comparison.prepare.prepare_sample',
                        lambda *args, **kwargs: {'frames': 1, 'width': 64, 'height': 48})
    monkeypatch.setenv('MINT_CACHE_MIN_FREE_GIB', '0')
    manager = ComparisonManager(tmp_path / 'store', autostart=False)
    monkeypatch.setattr(manager, '_acquire_gpu', lambda *args: ([], []))
    source, marker, launcher = tmp_path / 'source.mp4', tmp_path / 'descendant.json', tmp_path / 'launcher.py'
    source.write_bytes(b'CPU process-lifecycle fixture')
    launcher.write_text('''import json, subprocess, sys
from pathlib import Path
child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])
token = (Path('/proc') / str(child.pid) / 'stat').read_text().rsplit(')', 1)[1].split()[19]
Path(sys.argv[1]).write_text(json.dumps({'pid': child.pid, 'token': token}))
''')
    real_popen = subprocess.Popen
    external = real_popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
    launched = []

    def launch_fixture(command, **kwargs):
        child = real_popen([sys.executable, str(launcher), str(marker)], **kwargs)
        launched.append(child)
        return child

    monkeypatch.setattr('mint.comparison.jobs.subprocess.Popen', launch_fixture)
    try:
        run_id = manager.create(source, [{'algorithm': 'mint'}])['run_ids'][0]
        manager._execute(run_id)
        descendant = json.loads(marker.read_text())
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            proc = Path('/proc') / str(descendant['pid']) / 'stat'
            if _token(descendant['pid']) != descendant['token'] or proc.read_text().rsplit(')', 1)[1].split()[0] == 'Z':
                break
            time.sleep(.02)
        else:
            pytest.fail('A comparison descendant survived its completed launcher')
        assert external.poll() is None
        assert len(launched) == 1 and launched[0].returncode == 0
        with pytest.raises(ChildProcessError):
            os.waitpid(launched[0].pid, os.WNOHANG)
    finally:
        # A failing regression still cleans only the exact test-owned process.
        if marker.exists():
            descendant = json.loads(marker.read_text())
            if _token(descendant['pid']) == descendant['token']:
                try:
                    os.kill(descendant['pid'], signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for child in launched:
            if child.poll() is None:
                child.terminate()
                child.wait(5)
        external.terminate()
        external.wait(5)
        manager.close()
