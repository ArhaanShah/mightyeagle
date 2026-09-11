from pathlib import Path


def check_runtime_value(workspace_root: Path) -> str:
    namespace: dict[str, object] = {}
    exec((workspace_root / "pkg" / "pipeline.py").read_text(), namespace)
    observed = [namespace["read_value"](i) for i in (-5, 0, 2, 9)]
    return "pass" if observed == [5, 10, 12, 19] else "fail"
