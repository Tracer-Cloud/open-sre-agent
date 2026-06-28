import ast
from pathlib import Path


def is_compatibility_shim(file_path: str, content: str) -> bool:
    """Check if a Python file is a compatibility-only forwarding module."""
    if Path(file_path).name == "__init__.py":
        return False
    try:
        tree = ast.parse(content)
    except Exception:
        return False

    has_import_or_alias = False

    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            has_import_or_alias = True
        elif isinstance(node, ast.Assign):
            is_alias = False
            is_all = False
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__all__":
                    is_all = True
                    continue
                elif isinstance(target, ast.Name):
                    if isinstance(node.value, (ast.Name, ast.Attribute)):
                        is_alias = True
                    else:
                        return False
                else:
                    return False
            if is_alias:
                has_import_or_alias = True
            elif is_all:
                continue
            else:
                return False
        elif (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
            or isinstance(node, ast.Pass)
        ):
            continue
        else:
            return False

    return has_import_or_alias


def analyze_misplaced(file_path: Path, repo_root: Path, content: str) -> tuple[bool, str]:
    """Check if a module is misplaced according to architectural guidelines."""
    # Check for vendors/ or services/ in path
    parts = file_path.relative_to(repo_root).parts
    if "vendors" in parts or "services" in parts:
        return True, "Do not add or import top-level or nested 'vendors/' or 'services/' packages."

    # Parse AST to check for tool decorators or base classes
    has_tool_decorator = False
    has_basetool_subclass = False
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for dec in node.decorator_list:
                    if (isinstance(dec, ast.Name) and dec.id == "tool") or (
                        isinstance(dec, ast.Call)
                        and isinstance(dec.func, ast.Name)
                        and dec.func.id == "tool"
                    ):
                        has_tool_decorator = True
            elif isinstance(node, ast.ClassDef):
                for base in node.bases:
                    if isinstance(base, ast.Name) and base.id == "BaseTool":
                        has_basetool_subclass = True
    except Exception:
        # Ignore AST parsing errors for invalid Python files
        pass

    # Tools must reside under tools/ directory
    is_tool_code = has_tool_decorator or has_basetool_subclass
    in_tools_dir = parts[0] == "tools"
    if is_tool_code and not in_tools_dir:
        return (
            True,
            "Tool definitions (functions decorated with @tool or classes inheriting from BaseTool) must reside inside the 'tools/' package.",
        )

    # Client/Verifier/Config code in tools is misplaced
    if in_tools_dir and "utils" not in parts:
        filename = file_path.name
        is_client_file = (
            filename == "client.py"
            or filename == "verifier.py"
            or filename == "config.py"
            or filename.endswith("_client.py")
            or filename.endswith("_verifier.py")
            or filename.endswith("_config.py")
        )
        if is_client_file:
            return True, (
                "Treat 'integrations/' as the canonical user/config and external-client boundary, "
                "and 'tools/' as the canonical agent-callable boundary. Client, config, or verifier "
                "logic should reside in 'integrations/' rather than 'tools/'."
            )

    return False, ""
