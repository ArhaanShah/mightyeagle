from pathlib import Path
import ast

def check_d1_render_method(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'renderer.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'MarkdownRenderer':
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == 'render':
                    return 'pass'
    return 'fail'
