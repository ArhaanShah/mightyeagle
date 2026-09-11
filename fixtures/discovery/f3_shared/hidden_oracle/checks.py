from pathlib import Path
import ast

def check_d1_pop_event_return(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'events.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == 'pop_event':
            if node.returns and 'Optional' not in ast.unparse(node.returns) and 'str' in ast.unparse(node.returns):
                return 'pass'
    return 'fail'
