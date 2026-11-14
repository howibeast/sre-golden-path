import importlib.util
from pathlib import Path


def test_policy_gate_accepts_this_repo():
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts" / "policy_gate.py"
    spec = importlib.util.spec_from_file_location("policy_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.problems(root) == []
