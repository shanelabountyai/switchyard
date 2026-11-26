"""SW-09: the package imports no network client or provider SDK and reads no environment variable."""

from __future__ import annotations

import ast
from pathlib import Path

from tests.helpers import ROOT

SRC = ROOT / "src" / "switchyard"
FORBIDDEN_MODULES = {
    "socket",
    "http",
    "urllib",
    "urllib3",
    "requests",
    "httpx",
    "aiohttp",
    "ssl",
    "ftplib",
    "smtplib",
    "websocket",
    "websockets",
    "grpc",
    "openai",
    "anthropic",
    "google",
    "cohere",
    "mistralai",
    "boto3",
    "botocore",
    "vertexai",
    "groq",
    "together",
    "replicate",
    "litellm",
    "dotenv",
}
FORBIDDEN_NAMES = {"environ", "getenv", "putenv", "environb"}


def _violations(path: Path) -> list[str]:
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules = [node.module]
        for module in modules:
            if module.split(".")[0] in FORBIDDEN_MODULES:
                found.append(f"{path.name}:{getattr(node, 'lineno', 0)} imports {module}")
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_NAMES:
            found.append(f"{path.name}:{getattr(node, 'lineno', 0)} reads {node.attr}")
        if isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            found.append(f"{path.name}:{getattr(node, 'lineno', 0)} uses {node.id}")
        if isinstance(node, ast.alias) and node.name in FORBIDDEN_NAMES:
            found.append(f"{path.name}:{getattr(node, 'lineno', 0)} imports {node.name}")
    return found


def test_source_is_fakes_only() -> None:
    files = sorted(SRC.rglob("*.py"))
    assert len(files) >= 15
    assert [v for f in files for v in _violations(f)] == []


def test_scanner_catches_each_kind(tmp_path: Path) -> None:
    sample = tmp_path / "bad.py"
    sample.write_text(
        "import os\nimport httpx\nfrom urllib import request\nfrom os import getenv\nx = os.environ\n"
    )
    assert len(_violations(sample)) == 4


def test_no_real_time_or_sleep_outside_clock() -> None:
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "clock.py":
            continue
        text = path.read_text()
        for needle in ("time.time", "datetime.now", "sleep(", "import time", "import random", "from random"):
            assert needle not in text, f"{path.name}: {needle}"
