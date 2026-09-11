from pathlib import Path
import ast
def check_d1_fetch(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'long.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'fetch_data':
            if node.returns and 'Optional' not in ast.unparse(node.returns):
                return 'pass'
    return 'fail'
