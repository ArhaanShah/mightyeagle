from pathlib import Path
import ast
import sys

def check_d1_return_type(workspace_root: Path) -> str:
    """Check that compute_summary has float return type annotation."""
    src = (workspace_root / 'pkg' / 'pipeline.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'compute_summary':
            if node.returns is None:
                return 'fail'
            ann = ast.unparse(node.returns)
            if 'Optional' not in ann and 'None' not in ann and 'float' in ann:
                return 'pass'
            return 'fail'
    return 'fail'
