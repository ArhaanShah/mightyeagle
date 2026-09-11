from pathlib import Path
import ast

def check_d1_db_port(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'db_config.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'DBConfig':
            for sub in node.body:
                if isinstance(sub, ast.AnnAssign) and sub.target.id == 'port':
                    if ast.unparse(sub.annotation) == 'int':
                        return 'pass'
    return 'fail'

def check_d2_web_workers(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'web_config.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'WebConfig':
            for sub in node.body:
                if isinstance(sub, ast.AnnAssign) and sub.target.id == 'workers':
                    if ast.unparse(sub.annotation) == 'int':
                        return 'pass'
    return 'fail'
