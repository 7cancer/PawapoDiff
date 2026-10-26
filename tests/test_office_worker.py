import json
from pathlib import Path
import subprocess
import sys
import time

import pytest
from pawapodiff.render import _run_worker, RenderError


def command(tmp_path, body):
    state = tmp_path / "state.json"
    script = (
        "from pathlib import Path\nimport json,time,os,sys\nfrom pawapodiff.office_worker import write_state\nstate=Path("
        + repr(str(state))
        + ")\n"
        + body
    )
    return [sys.executable, "-c", script], state, tmp_path / "stderr.log"


def test_worker_success_protocol(tmp_path):
    args, state, stderr = command(
        tmp_path, "write_state(state,{'status':'done','images':['0001.png']})"
    )
    assert _run_worker(args, state, stderr, timeout=5)["images"] == ["0001.png"]


@pytest.mark.parametrize(
    "body,message",
    [
        (
            "write_state(state,{'status':'error','message':'Open failed: 0x80020003'});sys.exit(1)",
            "Open failed: 0x80020003",
        ),
        ("sys.stderr.write('native worker crash');sys.exit(7)", "終了コード7"),
        ("state.write_text('invalid JSON')", "正常に完了"),
    ],
)
def test_worker_failures_are_reported(tmp_path, body, message):
    args, state, stderr = command(tmp_path, body)
    with pytest.raises(RenderError, match=message):
        _run_worker(args, state, stderr, timeout=5)


def test_stuck_worker_is_terminated_without_killing_office(tmp_path, monkeypatch):
    args, state, stderr = command(
        tmp_path,
        "write_state(state,{'status':'running','stage':'資料を開く'});time.sleep(60)",
    )
    created = []
    popen = subprocess.Popen

    def capture(*args, **kwargs):
        child = popen(*args, **kwargs)
        created.append(child)
        return child

    monkeypatch.setattr(subprocess, "Popen", capture)
    started = time.monotonic()
    with pytest.raises(RenderError, match="応答しない"):
        _run_worker(args, state, stderr, timeout=1)
    assert time.monotonic() - started < 10
    assert len(created) == 1 and created[0].poll() is not None


def test_progress_resets_inactivity_timeout(tmp_path):
    body = "for i in range(10):\n write_state(state,{'status':'running','stage':str(i)});time.sleep(.3)\nwrite_state(state,{'status':'done'})"
    args, state, stderr = command(tmp_path, body)
    assert _run_worker(args, state, stderr, timeout=2)["status"] == "done"
