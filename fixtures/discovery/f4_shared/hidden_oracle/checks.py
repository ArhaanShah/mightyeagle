from pathlib import Path
import ast

def check_d1_timeout_type(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'appconfig.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'AppConfig':
            for sub in node.body:
                if isinstance(sub, ast.AnnAssign) and sub.target.id == 'timeout':
                    if ast.unparse(sub.annotation) == 'int':
                        return 'pass'
    return 'fail'
