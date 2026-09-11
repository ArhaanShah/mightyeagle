from pathlib import Path
import ast

def check_d1_host(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'config_a.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'get_host':
            if node.returns and 'Optional' not in ast.unparse(node.returns) and 'str' in ast.unparse(node.returns):
                return 'pass'
    return 'fail'

def check_d2_port(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'config_b.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'get_port':
            if node.returns and 'Optional' not in ast.unparse(node.returns) and 'int' in ast.unparse(node.returns):
                return 'pass'
    return 'fail'
