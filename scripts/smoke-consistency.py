"""Compatibility entry point: demo generation and real Web UI tests run in CI."""
import runpy
from pathlib import Path

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).with_name('create-demo.py')), run_name='__main__')
