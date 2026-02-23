"""Cross-file integration audit — deterministic checks run after each chunk.

Catches integration bugs that per-file auditors miss:
- Broken Python imports (module doesn't exist)
- Missing symbols (from X import Y where Y isn't exported by X)
- TypeScript compilation errors (if tsc available)
- Backend↔frontend schema field mismatches

Runs between Step 5 (per-file audits) and Step 6 (fixer) so issues
feed directly into the fix queue.
"""

from __future__ import annotations

import ast
import asyncio
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

logger = logging.getLogger(__name__)

# Lazy imports — avoid circular dependency with forge_ide
_python_intel = None
_ts_intel = None


def _get_python_intel():
    global _python_intel
    if _python_intel is None:
        from forge_ide.lang import python_intel
        _python_intel = python_intel
    return _python_intel


def _get_ts_intel():
    global _ts_intel
    if _ts_intel is None:
        from forge_ide.lang import ts_intel
        _ts_intel = ts_intel
    return _ts_intel


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntegrationIssue:
    """A single cross-file issue found by the integration audit."""
    file_path: str
    severity: str  # "error" | "warning"
    message: str
    related_file: str = ""
    check_name: str = ""


# ---------------------------------------------------------------------------
# Third-party package detection
# ---------------------------------------------------------------------------

# Common Python packages that map to different import names
_IMPORT_TO_PACKAGE: dict[str, str] = {
    "cv2": "opencv-python",
    "PIL": "Pillow",
    "sklearn": "scikit-learn",
    "yaml": "PyYAML",
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "jose": "python-jose",
    "jwt": "PyJWT",
    "starlette": "starlette",
    "pydantic": "pydantic",
    "uvicorn": "uvicorn",
    "fastapi": "fastapi",
    "sqlalchemy": "SQLAlchemy",
    "alembic": "alembic",
    "httpx": "httpx",
    "redis": "redis",
    "celery": "celery",
    "boto3": "boto3",
    "stripe": "stripe",
    "requests": "requests",
    "flask": "flask",
    "django": "django",
    "numpy": "numpy",
    "pandas": "pandas",
    "pytest": "pytest",
    "aiohttp": "aiohttp",
    "anthropic": "anthropic",
    "openai": "openai",
}


def _load_third_party_packages(working_dir: str) -> set[str]:
    """Build a set of known third-party top-level import names from dependency files."""
    packages: set[str] = set()
    wd = Path(working_dir)

    # Parse requirements.txt
    for req_file in ("requirements.txt", "requirements-dev.txt", "requirements_dev.txt"):
        req_path = wd / req_file
        if req_path.exists():
            try:
                for line in req_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("-"):
                        continue
                    # Extract package name before version specifier
                    pkg = re.split(r"[><=!~\[]", line)[0].strip()
                    if pkg:
                        # Normalize: package names use hyphens but imports use underscores
                        packages.add(pkg.lower().replace("-", "_"))
            except Exception:
                pass

    # Parse pyproject.toml (simple — just extract dependency names)
    pyproject = wd / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text(encoding="utf-8")
            # Match lines like: "fastapi>=0.100", "pydantic", etc.
            in_deps = False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("dependencies") and "=" in stripped:
                    in_deps = True
                    continue
                if in_deps:
                    if stripped.startswith("]"):
                        in_deps = False
                        continue
                    # Extract quoted package name
                    m = re.match(r'["\']([a-zA-Z0-9_-]+)', stripped)
                    if m:
                        packages.add(m.group(1).lower().replace("-", "_"))
        except Exception:
            pass

    # Parse package.json for JS/TS packages
    pkg_json = wd / "package.json"
    if not pkg_json.exists():
        # Check web/ subdirectory
        pkg_json = wd / "web" / "package.json"
    if pkg_json.exists():
        try:
            import json
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            for section in ("dependencies", "devDependencies"):
                for dep_name in data.get(section, {}):
                    packages.add(dep_name.lower())
        except Exception:
            pass

    # Add well-known reverse mappings
    packages.update(_IMPORT_TO_PACKAGE.keys())

    return packages


# ---------------------------------------------------------------------------
# Check 1: Python import resolution
# ---------------------------------------------------------------------------


def _check_python_imports(
    chunk_files: dict[str, str],
    all_files: dict[str, str],
    working_dir: str,
    third_party: set[str],
) -> list[IntegrationIssue]:
    """Verify all Python imports in chunk files resolve to real modules."""
    pi = _get_python_intel()
    issues: list[IntegrationIssue] = []

    # Build workspace file list from both all_files and disk
    workspace_files: list[str] = list(all_files.keys())
    wd = Path(working_dir)
    for py_file in wd.rglob("*.py"):
        rel = str(py_file.relative_to(wd)).replace("\\", "/")
        if rel not in workspace_files:
            workspace_files.append(rel)

    for file_path, content in chunk_files.items():
        if not file_path.endswith(".py"):
            continue

        try:
            imports = pi.resolve_imports(content, file_path, workspace_files)
        except Exception:
            continue

        for imp in imports:
            if imp.is_stdlib:
                continue
            if imp.resolved_path is not None:
                continue

            # Check if it's a known third-party package
            top_module = imp.module.lstrip(".").split(".")[0].lower()
            if top_module in third_party:
                continue
            if top_module in _IMPORT_TO_PACKAGE:
                continue

            # Relative imports that didn't resolve are definitely broken
            is_relative = imp.module.startswith(".")
            # Absolute imports starting with "app." or project-like paths
            # that didn't resolve are also broken
            looks_internal = (
                top_module in ("app", "src", "lib", "core", "api", "models",
                               "services", "routers", "schemas", "db",
                               "config", "utils", "helpers", "tests")
            )

            if is_relative or looks_internal:
                names_str = f" (names: {', '.join(imp.names)})" if imp.names else ""
                issues.append(IntegrationIssue(
                    file_path=file_path,
                    severity="error",
                    message=(
                        f"Unresolved import: `{imp.module}`{names_str} — "
                        f"no matching module found in workspace"
                    ),
                    check_name="unresolved_import",
                ))

    return issues


# ---------------------------------------------------------------------------
# Check 2: Python symbol cross-reference
# ---------------------------------------------------------------------------


def _check_python_symbols(
    chunk_files: dict[str, str],
    all_files: dict[str, str],
) -> list[IntegrationIssue]:
    """Verify that `from X import Y` → Y actually exists in X's exports."""
    pi = _get_python_intel()
    issues: list[IntegrationIssue] = []

    # Build export map: {module_path: set(symbol_names)}
    export_map: dict[str, set[str]] = {}
    for file_path, content in all_files.items():
        if not file_path.endswith(".py"):
            continue
        try:
            symbols = pi.extract_symbols(content)
            names = {s.name for s in symbols}
            # Also add __all__ exports if defined
            try:
                tree = ast.parse(content)
                for node in ast.iter_child_nodes(tree):
                    if isinstance(node, ast.Assign):
                        for target in node.targets:
                            if isinstance(target, ast.Name) and target.id == "__all__":
                                if isinstance(node.value, (ast.List, ast.Tuple)):
                                    for elt in node.value.elts:
                                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                                            names.add(elt.value)
            except Exception:
                pass
            export_map[file_path] = names
        except Exception:
            continue

    # Build workspace file list for import resolution
    workspace_files = list(all_files.keys())

    # Check each chunk file's imports against the export map
    for file_path, content in chunk_files.items():
        if not file_path.endswith(".py"):
            continue

        try:
            imports = pi.resolve_imports(content, file_path, workspace_files)
        except Exception:
            continue

        for imp in imports:
            if imp.is_stdlib or imp.resolved_path is None:
                continue
            if not imp.names:
                continue

            target_exports = export_map.get(imp.resolved_path, None)
            if target_exports is None:
                continue

            for name in imp.names:
                if name == "*":
                    continue
                if name not in target_exports:
                    issues.append(IntegrationIssue(
                        file_path=file_path,
                        severity="error",
                        message=(
                            f"Symbol `{name}` imported from `{imp.module}` "
                            f"but not exported by `{imp.resolved_path}`"
                        ),
                        related_file=imp.resolved_path,
                        check_name="missing_symbol",
                    ))

    return issues


# ---------------------------------------------------------------------------
# Check 3: TypeScript/JS integration
# ---------------------------------------------------------------------------


async def _check_typescript(
    working_dir: str,
    chunk_files: dict[str, str],
    all_files: dict[str, str],
) -> list[IntegrationIssue]:
    """Check TypeScript integration — tsc if available, regex fallback."""
    wd = Path(working_dir)
    issues: list[IntegrationIssue] = []

    # Find the frontend directory (common patterns)
    ts_root = None
    for candidate in ("web", "frontend", "client", "ui", "."):
        tsconfig = wd / candidate / "tsconfig.json"
        if tsconfig.exists():
            ts_root = wd / candidate
            break

    if ts_root is None:
        return issues  # No TypeScript project found

    has_node_modules = (ts_root / "node_modules").exists()

    if has_node_modules:
        # Try tsc --noEmit
        tsc_issues = await _run_tsc(ts_root)
        if tsc_issues is not None:
            return tsc_issues

    # Fallback: regex-based TS import checking
    return _check_ts_imports_regex(ts_root, chunk_files, all_files, working_dir)


async def _run_tsc(ts_root: Path) -> list[IntegrationIssue] | None:
    """Run tsc --noEmit and return issues, or None if tsc unavailable."""
    ts_intel = _get_ts_intel()

    # Find tsc binary
    tsc_bin = ts_root / "node_modules" / ".bin" / "tsc"
    if not tsc_bin.exists():
        # Try npx
        tsc_bin_str = "npx tsc"
    else:
        tsc_bin_str = str(tsc_bin)

    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, lambda: subprocess.run(
            [*tsc_bin_str.split(), "--noEmit", "--pretty", "false"],
            cwd=str(ts_root),
            capture_output=True,
            text=True,
            timeout=30,
        ))
        raw_output = result.stdout + "\n" + result.stderr
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None

    if not raw_output.strip():
        return []

    diagnostics = ts_intel.parse_tsc_output(raw_output)
    issues: list[IntegrationIssue] = []
    for diag in diagnostics:
        issues.append(IntegrationIssue(
            file_path=diag.file,
            severity="error" if diag.severity == "error" else "warning",
            message=f"[{diag.code}] {diag.message}" if diag.code else diag.message,
            check_name="tsc_error",
        ))

    return issues


# Regex patterns for TS/JS import resolution
_TS_IMPORT_RE = re.compile(
    r"""import\s+(?:"""
    r"""\{([^}]+)\}|"""           # named: import { X, Y } from '...'
    r"""(\w+)|"""                  # default: import X from '...'
    r"""\*\s+as\s+(\w+)"""        # namespace: import * as X from '...'
    r""")\s+from\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)


def _check_ts_imports_regex(
    ts_root: Path,
    chunk_files: dict[str, str],
    all_files: dict[str, str],
    working_dir: str,
) -> list[IntegrationIssue]:
    """Regex-based TS/JS import checking when tsc is not available."""
    issues: list[IntegrationIssue] = []
    wd = Path(working_dir)

    # Build set of all TS/JS files
    ts_files: set[str] = set()
    for fp in all_files:
        if any(fp.endswith(ext) for ext in (".ts", ".tsx", ".js", ".jsx")):
            ts_files.add(fp)
    for ts_file in ts_root.rglob("*"):
        if ts_file.suffix in (".ts", ".tsx", ".js", ".jsx"):
            rel = str(ts_file.relative_to(wd)).replace("\\", "/")
            ts_files.add(rel)

    for file_path, content in chunk_files.items():
        if not any(file_path.endswith(ext) for ext in (".ts", ".tsx", ".js", ".jsx")):
            continue

        for match in _TS_IMPORT_RE.finditer(content):
            named = match.group(1)
            from_path = match.group(4)

            # Only check relative imports (starting with . or ..)
            if not from_path.startswith("."):
                continue

            # Resolve the import path relative to the importing file
            file_dir = str(Path(file_path).parent).replace("\\", "/")
            resolved = os.path.normpath(os.path.join(file_dir, from_path)).replace("\\", "/")

            # Try common extensions
            found = False
            for ext in ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js"):
                candidate = resolved + ext
                if candidate in ts_files or candidate in all_files:
                    found = True

                    # If named imports, verify they exist in the target file
                    if named and candidate in all_files:
                        _verify_ts_named_exports(
                            file_path, named, candidate,
                            all_files[candidate], issues,
                        )
                    break

            if not found:
                issues.append(IntegrationIssue(
                    file_path=file_path,
                    severity="error",
                    message=f"Unresolved import: `{from_path}` — file not found in workspace",
                    check_name="ts_unresolved_import",
                ))

    return issues


def _verify_ts_named_exports(
    importer: str,
    named_str: str,
    target_path: str,
    target_content: str,
    issues: list[IntegrationIssue],
) -> None:
    """Check that named imports exist as exports in the target file."""
    ts_intel = _get_ts_intel()

    # Parse the named imports
    names = [n.strip().split(" as ")[0].strip() for n in named_str.split(",")]
    names = [n for n in names if n]

    # Extract exports from target
    try:
        symbols = ts_intel.extract_symbols(target_content)
        exported_names = {s.name for s in symbols}
    except Exception:
        return

    # Also check for direct export statements not caught by extract_symbols
    for line in target_content.splitlines():
        m = re.match(r"export\s+(?:default\s+)?(?:function|class|const|let|var|type|interface|enum)\s+(\w+)", line.strip())
        if m:
            exported_names.add(m.group(1))
        # export { X, Y }
        m2 = re.match(r"export\s*\{([^}]+)\}", line.strip())
        if m2:
            for name in m2.group(1).split(","):
                name = name.strip().split(" as ")[0].strip()
                if name:
                    exported_names.add(name)

    for name in names:
        if name not in exported_names:
            issues.append(IntegrationIssue(
                file_path=importer,
                severity="error",
                message=(
                    f"Symbol `{name}` imported but not exported by `{target_path}`"
                ),
                related_file=target_path,
                check_name="ts_missing_export",
            ))


# ---------------------------------------------------------------------------
# Check 4: Backend↔Frontend schema alignment
# ---------------------------------------------------------------------------


def _check_schema_alignment(
    all_files: dict[str, str],
) -> list[IntegrationIssue]:
    """Compare Pydantic response models against TypeScript interfaces.

    Looks for field name mismatches between backend schemas and frontend types.
    """
    issues: list[IntegrationIssue] = []

    # Extract Pydantic model fields from Python files
    backend_models: dict[str, dict[str, set[str]]] = {}  # {file: {ModelName: {field_names}}}
    for fp, content in all_files.items():
        if not fp.endswith(".py"):
            continue
        models = _extract_pydantic_fields(content)
        if models:
            backend_models[fp] = models

    if not backend_models:
        return issues

    # Extract TypeScript interface fields
    frontend_types: dict[str, dict[str, set[str]]] = {}  # {file: {InterfaceName: {field_names}}}
    for fp, content in all_files.items():
        if not any(fp.endswith(ext) for ext in (".ts", ".tsx")):
            continue
        types = _extract_ts_interface_fields(content)
        if types:
            frontend_types[fp] = types

    if not frontend_types:
        return issues

    # Match backend models to frontend types by name similarity
    for be_file, be_models in backend_models.items():
        for model_name, be_fields in be_models.items():
            # Look for matching TS type (strip common suffixes for matching)
            base_name = model_name.replace("Response", "").replace("Schema", "").replace("State", "")
            if not base_name:
                continue

            for fe_file, fe_types in frontend_types.items():
                for type_name, fe_fields in fe_types.items():
                    fe_base = type_name.replace("Response", "").replace("Props", "").replace("State", "")
                    if not fe_base:
                        continue

                    # Check for name match (case-insensitive)
                    if base_name.lower() != fe_base.lower():
                        continue

                    # Found a match — compare fields
                    be_only = be_fields - fe_fields
                    fe_only = fe_fields - be_fields

                    # Filter out common non-matching fields (timestamps, etc.)
                    _ignore = {"created_at", "updated_at", "id", "createdAt", "updatedAt"}
                    be_only -= _ignore
                    fe_only -= _ignore

                    if be_only or fe_only:
                        parts = []
                        if be_only:
                            parts.append(f"backend-only: {', '.join(sorted(be_only))}")
                        if fe_only:
                            parts.append(f"frontend-only: {', '.join(sorted(fe_only))}")
                        issues.append(IntegrationIssue(
                            file_path=fe_file,
                            severity="warning",
                            message=(
                                f"Schema mismatch between `{model_name}` ({be_file}) "
                                f"and `{type_name}` ({fe_file}): {'; '.join(parts)}"
                            ),
                            related_file=be_file,
                            check_name="schema_mismatch",
                        ))

    return issues


def _extract_pydantic_fields(source: str) -> dict[str, set[str]]:
    """Extract field names from Pydantic BaseModel classes."""
    models: dict[str, set[str]] = {}

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return models

    for node in ast.iter_child_nodes(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        # Check if it inherits from BaseModel (simple heuristic)
        is_model = any(
            (isinstance(b, ast.Name) and b.id in ("BaseModel", "BaseSchema"))
            or (isinstance(b, ast.Attribute) and b.attr in ("BaseModel", "BaseSchema"))
            for b in node.bases
        )
        if not is_model:
            continue

        # Only care about response/state models for schema alignment
        name = node.name
        if not any(kw in name for kw in ("Response", "Schema", "State", "Output", "Result")):
            continue

        fields: set[str] = set()
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                field_name = child.target.id
                if not field_name.startswith("_") and field_name != "model_config":
                    fields.add(field_name)
        if fields:
            models[name] = fields

    return models


_TS_INTERFACE_RE = re.compile(
    r"(?:export\s+)?(?:interface|type)\s+(\w+)[\s<].*?\{([^}]+)\}",
    re.DOTALL,
)


def _extract_ts_interface_fields(source: str) -> dict[str, set[str]]:
    """Extract field names from TypeScript interfaces/types."""
    types: dict[str, set[str]] = {}

    for match in _TS_INTERFACE_RE.finditer(source):
        name = match.group(1)
        body = match.group(2)

        fields: set[str] = set()
        for line in body.splitlines():
            line = line.strip().rstrip(",;")
            if not line or line.startswith("//") or line.startswith("/*"):
                continue
            # Match: fieldName: type or fieldName?: type
            m = re.match(r"(\w+)\??\s*:", line)
            if m:
                fields.add(m.group(1))

        if fields:
            types[name] = fields

    return types


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def run_integration_audit(
    working_dir: str,
    chunk_files: dict[str, str],
    all_files: dict[str, str],
    build_id: UUID,
    user_id: UUID,
) -> list[IntegrationIssue]:
    """Run deterministic cross-file integration checks.

    Called between Step 5 (per-file audits) and Step 6 (fixer) in the
    build pipeline. Issues with severity "error" feed into the fix queue.

    Args:
        working_dir: Absolute path to the project workspace.
        chunk_files: Files just built in this chunk ({path: content}).
        all_files: All files written so far ({path: content}).
        build_id: Active build UUID (for logging).
        user_id: User UUID (for event broadcasting).

    Returns:
        List of IntegrationIssue objects. Empty list = all checks passed.
    """
    if not chunk_files:
        return []

    issues: list[IntegrationIssue] = []
    third_party = _load_third_party_packages(working_dir)

    # Check 1: Python import resolution
    try:
        issues.extend(_check_python_imports(chunk_files, all_files, working_dir, third_party))
    except Exception as e:
        logger.warning("[INTEGRATION_AUDIT] Python import check failed: %s", e)

    # Check 2: Python symbol cross-reference
    try:
        issues.extend(_check_python_symbols(chunk_files, all_files))
    except Exception as e:
        logger.warning("[INTEGRATION_AUDIT] Python symbol check failed: %s", e)

    # Check 3: TypeScript integration
    try:
        ts_issues = await _check_typescript(working_dir, chunk_files, all_files)
        issues.extend(ts_issues)
    except Exception as e:
        logger.warning("[INTEGRATION_AUDIT] TypeScript check failed: %s", e)

    # Check 4: Backend↔frontend schema alignment
    try:
        issues.extend(_check_schema_alignment(all_files))
    except Exception as e:
        logger.warning("[INTEGRATION_AUDIT] Schema alignment check failed: %s", e)

    if issues:
        error_count = sum(1 for i in issues if i.severity == "error")
        warn_count = len(issues) - error_count
        logger.info(
            "[INTEGRATION_AUDIT] Build %s: %d errors, %d warnings across %d files",
            build_id, error_count, warn_count, len(chunk_files),
        )

    return issues
