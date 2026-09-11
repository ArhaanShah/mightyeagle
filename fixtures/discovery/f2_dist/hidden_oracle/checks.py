from pathlib import Path
import ast

def check_d1_handler_a(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'handler_a.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'AlphaHandler':
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == 'handle':
                    return 'pass'
    return 'fail'

def check_d2_handler_b(workspace_root: Path) -> str:
    src = (workspace_root / 'pkg' / 'handler_b.py').read_text()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == 'BetaHandler':
            for sub in node.body:
                if isinstance(sub, ast.FunctionDef) and sub.name == 'handle':
                    return 'pass'
    return 'fail'
