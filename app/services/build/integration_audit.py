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

    # Parse package.json for JS/TS packages — scan ALL subdirectories
    _parse_package_json_deps(wd / "package.json", packages)
    for subdir in ("web", "frontend", "client", "ui", "backend", "server", "api"):
        _parse_package_json_deps(wd / subdir / "package.json", packages)

    # Add well-known reverse mappings
    packages.update(_IMPORT_TO_PACKAGE.keys())

    return packages


def _parse_package_json_deps(pkg_json: Path, packages: set[str]) -> None:
    """Parse a package.json and add deps/devDeps to the package set."""
    if not pkg_json.exists():
        return
    try:
        import json
        data = json.loads(pkg_json.read_text(encoding="utf-8"))
        for section in ("dependencies", "devDependencies", "peerDependencies"):
            for dep_name in data.get(section, {}):
                packages.add(dep_name.lower())
    except Exception:
        pass


def _load_js_deps_by_directory(working_dir: str) -> dict[str, set[str]]:
    """Load JS dependencies per directory for precise import validation.

    Returns {dir_path: {dep_name, ...}} where dir_path is relative.
    Each file's imports are checked against the nearest package.json's deps.
    """
    wd = Path(working_dir)
    deps_by_dir: dict[str, set[str]] = {}

    # Scan root and common subdirectories
    for subdir in (".", "web", "frontend", "client", "ui", "backend", "server", "api"):
        pkg_json = wd / subdir / "package.json"
        if not pkg_json.exists():
            continue
        try:
            import json
            data = json.loads(pkg_json.read_text(encoding="utf-8"))
            dep_set: set[str] = set()
            for section in ("dependencies", "devDependencies", "peerDependencies"):
                for dep_name in data.get(section, {}):
                    dep_set.add(dep_name.lower())
            # Also add Node.js built-in modules
            dep_set.update(_NODE_BUILTINS)
            deps_by_dir[subdir if subdir != "." else ""] = dep_set
        except Exception:
            pass

    return deps_by_dir


# Common Node.js built-in modules (not listed in package.json)
_NODE_BUILTINS: set[str] = {
    "assert", "buffer", "child_process", "cluster", "console", "constants",
    "crypto", "dgram", "dns", "domain", "events", "fs", "http", "http2",
    "https", "module", "net", "os", "path", "perf_hooks", "process",
    "punycode", "querystring", "readline", "repl", "stream", "string_decoder",
    "sys", "timers", "tls", "tty", "url", "util", "v8", "vm", "wasi",
    "worker_threads", "zlib", "node:fs", "node:path", "node:url",
    "node:http", "node:https", "node:crypto", "node:stream", "node:util",
    "node:os", "node:events", "node:buffer", "node:child_process",
    "node:net", "node:readline", "node:worker_threads", "node:zlib",
    # Also treat 'react' style imports that come via CDN/bundler as OK
    # when they ARE in the package.json (handled by dep_set)
}


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
    """Check TypeScript/JavaScript integration — tsc if available, regex fallback."""
    wd = Path(working_dir)
    issues: list[IntegrationIssue] = []

    # Find the frontend directory — first try tsconfig.json (TS projects)
    ts_root = None
    has_tsconfig = False
    for candidate in ("web", "frontend", "client", "ui", "."):
        tsconfig = wd / candidate / "tsconfig.json"
        if tsconfig.exists():
            ts_root = wd / candidate
            has_tsconfig = True
            break

    # Fallback: detect plain JS projects via package.json + JS/JSX source files
    if ts_root is None:
        for candidate in ("frontend", "client", "web", "ui", "."):
            pkg_json = wd / candidate / "package.json"
            if pkg_json.exists():
                # Verify it has JS/JSX source files (not just a config package)
                candidate_dir = wd / candidate
                has_js = any(
                    f.suffix in (".js", ".jsx", ".ts", ".tsx")
                    for f in candidate_dir.rglob("src/*")
                    if "node_modules" not in str(f)
                )
                if has_js:
                    ts_root = candidate_dir
                    break

        # Also check all_files for JS/JSX content if no directory found
        if ts_root is None:
            js_files_in_chunk = [
                f for f in chunk_files
                if any(f.endswith(ext) for ext in (".js", ".jsx", ".ts", ".tsx"))
                and "node_modules" not in f
            ]
            if js_files_in_chunk:
                # Use working_dir root as the JS root
                ts_root = wd

    if ts_root is None:
        return issues  # No TypeScript or JavaScript project found

    has_node_modules = (ts_root / "node_modules").exists()

    # Only try tsc if we have a tsconfig.json AND node_modules
    if has_tsconfig and has_node_modules:
        tsc_issues = await _run_tsc(ts_root)
        if tsc_issues is not None:
            return tsc_issues

    # Regex-based import checking (works for both TS and plain JS/JSX)
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
    """Regex-based TS/JS import checking — works for TS and plain JS/JSX."""
    issues: list[IntegrationIssue] = []
    wd = Path(working_dir)

    # Build set of ALL project files (not just JS — includes CSS, JSON, etc.)
    all_project_files: set[str] = set(all_files.keys())
    for project_file in wd.rglob("*"):
        if project_file.is_file() and "node_modules" not in str(project_file):
            rel = str(project_file.relative_to(wd)).replace("\\", "/")
            all_project_files.add(rel)

    # Load per-directory JS deps for non-relative import validation
    js_deps_by_dir = _load_js_deps_by_directory(working_dir)

    for file_path, content in chunk_files.items():
        if not any(file_path.endswith(ext) for ext in (".ts", ".tsx", ".js", ".jsx")):
            continue

        for match in _TS_IMPORT_RE.finditer(content):
            named = match.group(1)
            from_path = match.group(4)

            # --- Non-relative imports: validate against package.json ---
            if not from_path.startswith("."):
                pkg_name = from_path.split("/")[0]
                # Skip Node builtins
                if pkg_name in _NODE_BUILTINS or pkg_name.startswith("node:"):
                    continue
                # Find the nearest package.json deps for this file
                _nearest_deps = _find_nearest_js_deps(file_path, js_deps_by_dir)
                if _nearest_deps is not None and pkg_name.lower() not in _nearest_deps:
                    issues.append(IntegrationIssue(
                        file_path=file_path,
                        severity="error",
                        message=(
                            f"Package `{pkg_name}` imported but not listed in "
                            f"package.json dependencies"
                        ),
                        check_name="missing_js_dependency",
                    ))
                continue

            # --- Relative imports: resolve against filesystem ---
            file_dir = str(Path(file_path).parent).replace("\\", "/")
            resolved = os.path.normpath(os.path.join(file_dir, from_path)).replace("\\", "/")

            # Try code extensions first, then asset extensions
            _code_exts = ("", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx", "/index.js", "/index.jsx")
            _asset_exts = (".css", ".scss", ".sass", ".less", ".json", ".svg", ".png", ".jpg", ".gif", ".module.css")

            found = False
            for ext in (*_code_exts, *_asset_exts):
                candidate = resolved + ext
                if candidate in all_project_files or candidate in all_files:
                    found = True

                    # If named imports on a code file, verify exports
                    if named and candidate in all_files and not any(
                        candidate.endswith(ae) for ae in _asset_exts
                    ):
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

    # Also check bare imports: import './style.css' (no from clause)
    _bare_import_re = re.compile(r"""import\s+['"]([^'"]+)['"]""", re.MULTILINE)
    for file_path, content in chunk_files.items():
        if not any(file_path.endswith(ext) for ext in (".ts", ".tsx", ".js", ".jsx")):
            continue
        for match in _bare_import_re.finditer(content):
            from_path = match.group(1)
            if not from_path.startswith("."):
                continue  # Package bare imports (e.g., import 'dotenv/config')
            file_dir = str(Path(file_path).parent).replace("\\", "/")
            resolved = os.path.normpath(os.path.join(file_dir, from_path)).replace("\\", "/")
            _all_exts = ("", ".css", ".scss", ".sass", ".less", ".json", ".js", ".jsx", ".ts", ".tsx")
            found = any(
                (resolved + ext) in all_project_files or (resolved + ext) in all_files
                for ext in _all_exts
            )
            if not found:
                issues.append(IntegrationIssue(
                    file_path=file_path,
                    severity="error",
                    message=f"Unresolved bare import: `{from_path}` — file not found",
                    check_name="ts_unresolved_import",
                ))

    return issues


def _find_nearest_js_deps(file_path: str, deps_by_dir: dict[str, set[str]]) -> set[str] | None:
    """Find the package.json deps that apply to a given file path.

    Walks up from the file's directory to find the nearest match in deps_by_dir.
    Returns None if no package.json was found (skip validation).
    """
    parts = file_path.replace("\\", "/").split("/")
    # Try progressively shorter prefixes
    for i in range(len(parts) - 1, -1, -1):
        prefix = "/".join(parts[:i]) if i > 0 else ""
        if prefix in deps_by_dir:
            return deps_by_dir[prefix]
    return None


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
# Check 5: JavaScript/JSX syntax validation
# ---------------------------------------------------------------------------


def _check_js_syntax(
    chunk_files: dict[str, str],
) -> list[IntegrationIssue]:
    """Validate JS/JSX file syntax to catch cross-language contamination.

    Detects Python-style syntax in JavaScript files (e.g. triple-quote
    docstrings, decorators without transpiler, etc.) by checking for
    patterns that are valid Python but invalid JavaScript.
    """
    issues: list[IntegrationIssue] = []

    # Patterns that indicate Python syntax contamination in JS files
    _PYTHON_IN_JS = [
        (re.compile(r'"""'), "Python triple-quote docstring detected in JavaScript file"),
        (re.compile(r"'''"), "Python triple-quote string detected in JavaScript file"),
        (re.compile(r"^\s*def\s+\w+\s*\(", re.MULTILINE), "Python function definition (`def`) in JavaScript file"),
        (re.compile(r"^\s*class\s+\w+.*:\s*$", re.MULTILINE), "Python-style class with colon in JavaScript file"),
        (re.compile(r"\bprint\s*\(", re.MULTILINE), "Python `print()` call in JavaScript file (use console.log)"),
        (re.compile(r"\bself\.\w+", re.MULTILINE), "Python `self.` reference in JavaScript file (use `this.`)"),
    ]

    for file_path, content in chunk_files.items():
        if not any(file_path.endswith(ext) for ext in (".js", ".jsx", ".mjs", ".cjs")):
            continue

        for pattern, message in _PYTHON_IN_JS:
            matches = pattern.findall(content)
            if matches:
                issues.append(IntegrationIssue(
                    file_path=file_path,
                    severity="error",
                    message=message,
                    check_name="js_syntax_contamination",
                ))
                break  # One issue per file for contamination

    return issues


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

    # Check 3: TypeScript/JavaScript integration
    try:
        ts_issues = await _check_typescript(working_dir, chunk_files, all_files)
        issues.extend(ts_issues)
    except Exception as e:
        logger.warning("[INTEGRATION_AUDIT] TypeScript/JS check failed: %s", e)

    # Check 4: Backend↔frontend schema alignment
    try:
        issues.extend(_check_schema_alignment(all_files))
    except Exception as e:
        logger.warning("[INTEGRATION_AUDIT] Schema alignment check failed: %s", e)

    # Check 5: JavaScript syntax validation (cross-language contamination)
    try:
        issues.extend(_check_js_syntax(chunk_files))
    except Exception as e:
        logger.warning("[INTEGRATION_AUDIT] JS syntax check failed: %s", e)

    if issues:
        error_count = sum(1 for i in issues if i.severity == "error")
        warn_count = len(issues) - error_count
        logger.info(
            "[INTEGRATION_AUDIT] Build %s: %d errors, %d warnings across %d files",
            build_id, error_count, warn_count, len(chunk_files),
        )

    return issues
