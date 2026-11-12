"""Run the encryption suites and compile the application package."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKS = [
    [sys.executable, 'tests/test_cron_status.py'],
    [sys.executable, 'tests/test_theme.py'],
    [sys.executable, 'tests/test_wizard_dismiss.py'],
    [sys.executable, 'tests/test_encryption_ui.py'],
    [sys.executable, 'tests/test_encryption_integration.py'],
    [sys.executable, '-m', 'compileall', '-q', 'bitgnome'],
]

for command in CHECKS:
    result = subprocess.run(command, cwd=ROOT)
    if result.returncode:
        raise SystemExit(result.returncode)

print('REGRESSION_CHECKS_PASSED')
