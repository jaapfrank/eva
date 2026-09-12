"""Offline-repaired harness. Never used to dispatch the recorded attempt2."""
from pathlib import Path
import subprocess
import sys


def run(preflight_command, dispatch_command):
    subprocess.run(preflight_command, check=True)
    subprocess.run(dispatch_command, check=True)


if __name__ == '__main__':
    stage = Path(__file__).resolve().parent
    # One process enforces the dependency; an exit1 cannot be ignored by the orchestrator.
    run([sys.executable, str(stage/'preflight.py')],
        [sys.executable, str(stage/'A/runner.py')])
