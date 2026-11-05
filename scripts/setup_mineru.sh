#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [ ! -x .venv-mineru/bin/python ]; then
  uv venv --python .venv/bin/python .venv-mineru
fi
.venv-mineru/bin/python -m ensurepip
# Reuse the installed ML runtime read-only; all MinerU-specific packages are
# installed in the worker environment and never alter the app environment.
.venv/bin/python - <<'PY'
from pathlib import Path
import sysconfig, subprocess, json
worker_site = json.loads(subprocess.check_output(['.venv-mineru/bin/python', '-c', 'import site,json; print(json.dumps(site.getsitepackages()[0]))']))
Path(worker_site, 'tanyuan_base.pth').write_text(sysconfig.get_paths()['purelib'] + '\n')
PY
.venv-mineru/bin/python -m pip install 'mineru==4.1.7'
.venv-mineru/bin/mineru-kit models download --tier basic
.venv-mineru/bin/mineru-kit models verify --tier basic
echo 'MinerU Basic 已安装并校验本地模型；详见 docs/document-parsing.md。'
