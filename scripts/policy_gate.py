"""Same checks the pipeline runs. A product repo fails closed if the kit is missing."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HEADINGS = ("## Summary", "## Impact", "## Alert", "## Mitigate", "## Fix", "## Verify", "## Escalate")
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AccountKey=[A-Za-z0-9+/=]{20,}"),
)
SCAN_SUFFIXES = {".py", ".yml", ".yaml", ".md", ".sql", ".js", ".txt", ".json", ".example"}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache"}


def problems(root: Path = ROOT) -> list[str]:
    found: list[str] = []
    found.extend(_slo_check(root))
    found.extend(_identity(root))
    found.extend(_runbooks(root))
    found.extend(_telemetry_standard(root))
    found.extend(_dockerfiles(root))
    found.extend(_compose_images(root))
    found.extend(_no_account_id_on_telemetry(root))
    found.extend(_secrets(root))
    found.extend(_pipeline_files(root))
    return found


def _slo_check(root: Path) -> list[str]:
    script = root / "golden-path" / "slo" / "generate.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--check"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = (completed.stdout or completed.stderr).strip()
        return [detail or "SLO rules are out of date"]
    return []


def _identity(root: Path) -> list[str]:
    spec = root / "services" / "account-balance" / "slo.yaml"
    if not spec.exists():
        return ["services/account-balance/slo.yaml is missing"]
    text = spec.read_text(encoding="utf-8")
    missing = []
    for key in ("owner:", "environment:", "cost_centre:", "service:"):
        if key not in text:
            missing.append(key[:-1])
    if "target: 0.999" not in text:
        missing.append("objective.target 0.999")
    if missing:
        return ["slo.yaml missing " + ", ".join(missing)]
    return []


def _runbooks(root: Path) -> list[str]:
    found = []
    paths = [
        root / "golden-path" / "runbooks" / "TEMPLATE.md",
        root / "services" / "account-balance" / "runbooks" / "ledger-degraded.md",
    ]
    for path in paths:
        if not path.exists():
            found.append(f"missing runbook {path.relative_to(root).as_posix()}")
            continue
        text = path.read_text(encoding="utf-8")
        absent = [heading for heading in HEADINGS if heading not in text]
        if absent:
            found.append(f"{path.relative_to(root).as_posix()} missing {', '.join(absent)}")
    filled = root / "services" / "account-balance" / "runbooks" / "ledger-degraded.md"
    if filled.exists() and "AccountBalanceReadLedgerConfirmErrorRateHigh" not in filled.read_text(encoding="utf-8"):
        found.append("ledger runbook does not name the dependency alert")
    return found


def _telemetry_standard(root: Path) -> list[str]:
    path = root / "golden-path" / "telemetry" / "STANDARD.md"
    if not path.exists():
        return ["golden-path/telemetry/STANDARD.md is missing"]
    text = path.read_text(encoding="utf-8").lower()
    missing = [word for word in ("cardinality", "sampling", "retention", "log") if word not in text]
    if missing:
        return ["telemetry standard does not cover " + ", ".join(missing)]
    return []


def _dockerfiles(root: Path) -> list[str]:
    found = []
    for path in (root / "services").glob("*/Dockerfile"):
        lines = path.read_text(encoding="utf-8").splitlines()
        if any(":latest" in line for line in lines):
            found.append(f"{path.relative_to(root).as_posix()} uses :latest")
        users = [line.split()[1] for line in lines if line.startswith("USER ")]
        if not users or users[-1] == "root":
            found.append(f"{path.relative_to(root).as_posix()} must drop root")
    return found


def _compose_images(root: Path) -> list[str]:
    text = (root / "docker-compose.yml").read_text(encoding="utf-8")
    found = []
    if ":latest" in text:
        found.append("docker-compose.yml uses :latest")
    return found


def _no_account_id_on_telemetry(root: Path) -> list[str]:
    path = root / "services" / "account-balance" / "app" / "telemetry.py"
    if "account_id" in path.read_text(encoding="utf-8"):
        return ["telemetry.py must not mention account_id (it would become a label or attribute)"]
    return []


def _secrets(root: Path) -> list[str]:
    found = []
    for path in _text_files(root):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                found.append(f"possible secret in {path.relative_to(root).as_posix()}")
                break
    return found


def _pipeline_files(root: Path) -> list[str]:
    required = [
        "golden-path/pipeline/azure-pipelines.yml",
        "golden-path/pipeline/README.md",
        ".github/workflows/reusable-service.yml",
        ".github/workflows/ci.yml",
    ]
    return [f"missing {path}" for path in required if not (root / path).exists()]


def _text_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() in SCAN_SUFFIXES or path.name in {"Dockerfile", "Caddyfile"}:
            yield path


def main() -> int:
    found = problems()
    if not found:
        print("policy gate passed")
        return 0
    print("policy gate failed:")
    for item in found:
        print(f"- {item}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
