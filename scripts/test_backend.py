"""Run offline regressions with an isolated application DB and fixed fixture model."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='backend-regression-') as directory:
        env = {**os.environ, 'APP_DB_PATH': str(Path(directory) / 'app.db'),
               'SEMANTIC_MODEL_PATH': str(ROOT / 'tests/fixtures/legacy_semantic.yaml'),
               'CASEWORK_DB_PATH':str(Path(directory)/'casework.db'),'CASEWORK_EMBEDDING':'0',
               'CASEWORK_MYSQL_MIRROR':'0','CASEWORK_SYNC_INTERVAL':'0','DATA_MODE':'demo'}
        arguments = sys.argv[1:] or ['discover', '-s', 'tests', '-q']
        raise SystemExit(subprocess.call([sys.executable, '-m', 'unittest', *arguments], cwd=ROOT, env=env))
