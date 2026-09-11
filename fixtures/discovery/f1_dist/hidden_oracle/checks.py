from pathlib import Path
import ast

def check_d1_normalize(workspace_root: Path) -> str:
    p = workspace_root / 'pkg' / 'normalize.py'
    if not p.exists(): return 'fail'
    src = p.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'normalize':
            if node.returns and 'Optional' not in ast.unparse(node.returns) and 'float' in ast.unparse(node.returns):
                return 'pass'
    return 'fail'

def check_d2_scale_batch(workspace_root: Path) -> str:
    p = workspace_root / 'pkg' / 'scale.py'
    if not p.exists(): return 'fail'
    src = p.read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'scale_batch':
            if node.returns and 'Optional' not in ast.unparse(node.returns) and 'List' in ast.unparse(node.returns):
                return 'pass'
    return 'fail'
