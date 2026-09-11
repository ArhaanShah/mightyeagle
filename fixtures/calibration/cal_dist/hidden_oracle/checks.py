from pathlib import Path
import ast
def check_d1_a(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'part_a.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'get_a':
            if node.returns and 'Optional' not in ast.unparse(node.returns):
                return 'pass'
    return 'fail'
def check_d2_b(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'part_b.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'get_b':
            if node.returns and 'Optional' not in ast.unparse(node.returns):
                return 'pass'
    return 'fail'
