import sys
from pathlib import Path

# Some environments run `pytest` with Python's safe-path behavior enabled (no CWD on sys.path),
# which breaks local imports like `import schema_analyzer` unless the package is installed.
# Ensure repository root is importable for the test suite.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

