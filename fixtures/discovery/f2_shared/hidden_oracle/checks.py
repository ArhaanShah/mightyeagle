from pathlib import Path


def check_runtime_render(workspace_root: Path) -> str:
    namespace: dict[str, object] = {}
    exec((workspace_root / "pkg" / "renderer.py").read_text(), namespace)
    renderer = namespace["MarkdownRenderer"]()
    return "pass" if (
        renderer.render("Hello") == b"*Hello*"
        and renderer.render("") == b"**"
    ) else "fail"
