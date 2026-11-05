"""Explicit isolated integration configuration; normal runs retain local opt-in."""
import json
import os
from pathlib import Path
from dotenv import dotenv_values


def test_config():
    path=os.getenv('TANYUAN_TEST_MYSQL_CONFIG')
    if path:
        return json.loads(Path(path).read_text())
    return dotenv_values(Path(__file__).resolve().parents[1]/'.env')
