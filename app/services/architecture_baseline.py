"""Architecture baseline — capture ForgeGuard's Scout profile as gold standard.

Compares target project Scout profiles against the reference baseline
to produce an Architecture Quality score (0–100).

The baseline is captured by running Scout on ForgeGuard itself and freezing
the structural signals. Target projects are scored on how well they follow
the same architectural principles, adjusted for stack and scale.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

BaselineProfile = dict[str, Any]
# {
#   "captured_at": str,           # ISO timestamp of capture
#   "source": str,                # repo that was profiled (e.g. "ForgeGuard")
#   "stack_profile": { ... },     # Scout stack_profile snapshot
#   "architecture": { ... },      # Scout architecture snapshot
#   "health_grade": str,          # A / B / C / D / F
#   "quality_score": float | None,
#   "structural_signals": {
#     "has_service_layer": bool,
#     "has_data_access_layer": bool,
#     "has_client_abstraction": bool,
#     "has_middleware_layer": bool,
#     "has_config_external": bool,
#     "has_error_types": bool,
#     "has_test_parallel": bool,
#     "test_to_source_ratio": float,
#     "layer_count": int,
#     "avg_module_depth": float,
#   },
#   "rules": [ ... ],            # Architecture rules derived from baseline
# }

ArchitectureScore = dict[str, Any]
# {
#   "score": 0–100,
#   "grade": "A" | "B" | "C" | "D" | "F",
#   "details": [ str, ... ],
#   "rules_evaluated": int,
#   "rules_passed": int,
#   "rule_results": [ { "rule": str, "passed": bool, "detail": str }, ... ],
# }


# ---------------------------------------------------------------------------
# Architecture rules (stack-agnostic)
# ---------------------------------------------------------------------------

# Each rule has:
#   id:          short code
#   name:        human label
#   weight:      importance (critical=3, important=2, good-practice=1)
#   description: what it checks
#   check:       callable(target_signals, baseline_signals) -> (passed, detail)

def _check_layer_separation(target: dict, _baseline: dict) -> tuple[bool, str]:
    """Target should have distinct layers, not a monolith."""
    layer_count = target.get("layer_count", 0)
    if layer_count >= 3:
        return True, f"{layer_count} distinct layers detected"
    if layer_count >= 2:
        return True, f"{layer_count} layers (minimal but acceptable)"
    return False, f"Only {layer_count} layer(s) — insufficient separation"


def _check_service_layer(target: dict, _baseline: dict) -> tuple[bool, str]:
    """Dedicated service/business-logic layer exists."""
    if target.get("has_service_layer"):
        return True, "Service layer present"
    return False, "No dedicated service layer found"


def _check_data_access_layer(target: dict, _baseline: dict) -> tuple[bool, str]:
    """Data access isolated from business logic."""
    if target.get("has_data_access_layer"):
        return True, "Data access layer present"
    # Not always required for small projects
    tree_size = target.get("tree_size", 0)
    if tree_size < 15:
        return True, "Small project — separate data layer not required"
    return False, "No data access layer; DB queries may be in business logic"


def _check_client_abstraction(target: dict, _baseline: dict) -> tuple[bool, str]:
    """External service calls go through client wrappers."""
    if target.get("has_client_abstraction"):
        return True, "Client abstraction layer present"
    tree_size = target.get("tree_size", 0)
    if tree_size < 10:
        return True, "Small project — client abstraction not required"
    return False, "No client abstraction; external calls may be scattered"


def _check_config_externalisation(target: dict, _baseline: dict) -> tuple[bool, str]:
    """Config via env/settings, not hardcoded."""
    if target.get("has_config_external"):
        return True, "Externalised configuration found"
    return False, "No config externalisation detected"


def _check_error_types(target: dict, _baseline: dict) -> tuple[bool, str]:
    """Structured error/exception hierarchy."""
    if target.get("has_error_types"):
        return True, "Structured error types present"
    return False, "No structured error types found"


def _check_test_parallel(target: dict, _baseline: dict) -> tuple[bool, str]:
    """Test files mirror source module structure."""
    if target.get("has_test_parallel"):
        return True, "Test structure parallels source"
    ratio = target.get("test_to_source_ratio", 0)
    if ratio > 0:
        return False, f"Tests exist (ratio: {ratio:.2f}) but don't mirror source structure"
    return False, "No test files found"


def _check_test_coverage_ratio(target: dict, baseline: dict) -> tuple[bool, str]:
    """Test-to-source ratio approaches the baseline's ratio."""
    target_ratio = target.get("test_to_source_ratio", 0)
    baseline_ratio = baseline.get("test_to_source_ratio", 0.8)

    if baseline_ratio <= 0:
        baseline_ratio = 0.8  # Sensible default

    # Score proportionally — 70% of baseline ratio is passing
    threshold = baseline_ratio * 0.7
    if target_ratio >= threshold:
        return True, f"Test ratio {target_ratio:.2f} (baseline: {baseline_ratio:.2f})"
    if target_ratio > 0:
        return False, f"Test ratio {target_ratio:.2f} below threshold {threshold:.2f}"
    return False, "No test files — ratio is 0"


def _check_middleware_crosscut(target: dict, _baseline: dict) -> tuple[bool, str]:
    """Auth, logging, rate-limiting in middleware, not scattered."""
    if target.get("has_middleware_layer"):
        return True, "Middleware layer present for cross-cutting concerns"
    tree_size = target.get("tree_size", 0)
    if tree_size < 10:
        return True, "Small project — middleware layer not expected"
    return False, "No middleware layer; cross-cutting concerns may be scattered"


ARCHITECTURE_RULES: list[dict[str, Any]] = [
    {
        "id": "LAYER_SEP",
        "name": "Layer Separation",
        "weight": 3,
        "description": "Project has distinct architectural layers (API, service, data)",
        "check": _check_layer_separation,
    },
    {
        "id": "SVC_LAYER",
        "name": "Service Layer",
        "weight": 3,
        "description": "Dedicated service/business-logic layer exists",
        "check": _check_service_layer,
    },
    {
        "id": "DATA_LAYER",
        "name": "Data Access Layer",
        "weight": 2,
        "description": "Data access isolated from business logic",
        "check": _check_data_access_layer,
    },
    {
        "id": "CLIENT_ABS",
        "name": "Client Abstraction",
        "weight": 2,
        "description": "External service calls go through client wrappers",
        "check": _check_client_abstraction,
    },
    {
        "id": "CFG_EXT",
        "name": "Config Externalisation",
        "weight": 2,
        "description": "Configuration via environment/settings, not hardcoded",
        "check": _check_config_externalisation,
    },
    {
        "id": "ERR_TYPES",
        "name": "Error Types",
        "weight": 2,
        "description": "Structured error/exception hierarchy exists",
        "check": _check_error_types,
    },
    {
        "id": "TEST_PAR",
        "name": "Test Structure",
        "weight": 2,
        "description": "Test files mirror source module structure",
        "check": _check_test_parallel,
    },
    {
        "id": "TEST_RATIO",
        "name": "Test Coverage Ratio",
        "weight": 2,
        "description": "Test-to-source ratio approaches the baseline",
        "check": _check_test_coverage_ratio,
    },
    {
        "id": "MW_CROSS",
        "name": "Middleware Cross-Cutting",
        "weight": 1,
        "description": "Auth, logging, rate-limiting in middleware layer",
        "check": _check_middleware_crosscut,
    },
]


# ---------------------------------------------------------------------------
# Stack-aware signal extraction
# ---------------------------------------------------------------------------

# Maps abstract structural concepts to stack-specific folder/file patterns.
# Scout's architecture + stack_profile data tells us what the project is.

_STACK_PATTERNS: dict[str, dict[str, list[str]]] = {
    "python": {
        "service_layer": ["services/", "service/", "core/", "domain/", "logic/"],
        "data_access_layer": ["repos/", "repositories/", "dal/", "models/", "db/"],
        "client_abstraction": ["clients/", "adapters/", "integrations/", "external/"],
        "middleware_layer": ["middleware/", "middlewares/"],
        "config_files": ["config.py", "settings.py", ".env", "config/"],
        "error_files": ["errors.py", "exceptions.py", "exc.py"],
        "test_dirs": ["tests/", "test/", "spec/"],
    },
    "javascript": {
        "service_layer": ["services/", "service/", "core/", "domain/", "lib/"],
        "data_access_layer": ["repositories/", "models/", "dal/", "db/", "prisma/"],
        "client_abstraction": ["clients/", "adapters/", "integrations/", "api/"],
        "middleware_layer": ["middleware/", "middlewares/"],
        "config_files": ["config.js", "config.ts", ".env", "config/"],
        "error_files": ["errors.js", "errors.ts", "exceptions.ts"],
        "test_dirs": ["__tests__/", "tests/", "test/", "spec/"],
    },
    "typescript": {
        "service_layer": ["services/", "service/", "core/", "domain/", "lib/"],
        "data_access_layer": ["repositories/", "models/", "dal/", "db/", "prisma/"],
        "client_abstraction": ["clients/", "adapters/", "integrations/", "api/"],
        "middleware_layer": ["middleware/", "middlewares/"],
        "config_files": ["config.ts", "config.js", ".env", "config/"],
        "error_files": ["errors.ts", "exceptions.ts"],
        "test_dirs": ["__tests__/", "tests/", "test/", "spec/"],
    },
    "go": {
        "service_layer": ["internal/service/", "pkg/service/", "service/", "core/"],
        "data_access_layer": ["internal/repo/", "pkg/repo/", "repository/", "store/"],
        "client_abstraction": ["internal/client/", "pkg/client/", "adapter/"],
        "middleware_layer": ["middleware/", "internal/middleware/"],
        "config_files": ["config.go", "config/", ".env"],
        "error_files": ["errors.go", "internal/errors/"],
        "test_dirs": [],  # Go tests live alongside source files
    },
    "default": {
        "service_layer": ["services/", "service/", "core/", "domain/", "lib/"],
        "data_access_layer": ["repos/", "repositories/", "models/", "dal/", "db/"],
        "client_abstraction": ["clients/", "adapters/", "integrations/"],
        "middleware_layer": ["middleware/"],
        "config_files": ["config", ".env"],
        "error_files": ["errors", "exceptions"],
        "test_dirs": ["tests/", "test/", "__tests__/", "spec/"],
    },
}


def detect_stack_key(stack_profile: dict | None) -> str:
    """Determine stack key from Scout's stack_profile data."""
    if not stack_profile:
        return "default"

    # Check primary language
    primary = stack_profile.get("primary_language", "").lower()
    if "python" in primary:
        return "python"
    if "typescript" in primary:
        return "typescript"
    if "javascript" in primary:
        return "javascript"
    if "go" in primary or "golang" in primary:
        return "go"

    # Check frameworks
    frameworks = stack_profile.get("frameworks", [])
    if isinstance(frameworks, list):
        framework_str = " ".join(str(f).lower() for f in frameworks)
        if any(k in framework_str for k in ("fastapi", "django", "flask", "python")):
            return "python"
        if any(k in framework_str for k in ("next", "react", "express", "node")):
            return "typescript"

    return "default"


def extract_structural_signals(
    scout_data: dict | None,
    *,
    stack_key: str | None = None,
) -> dict[str, Any]:
    """Extract structural signals from Scout profile data.

    Analyses Scout's architecture and stack_profile output to determine
    which architectural patterns are present in the target project.

    Parameters
    ----------
    scout_data : dict | None
        Scout data section from CertificateData (the 'scout' key).
    stack_key : str | None
        Override the auto-detected stack. If None, detected from stack_profile.

    Returns
    -------
    dict with boolean flags and numeric signals for each architecture rule.
    """
    if not scout_data:
        return _empty_signals()

    architecture = scout_data.get("architecture") or {}
    stack_profile = scout_data.get("stack_profile") or {}

    if stack_key is None:
        stack_key = detect_stack_key(stack_profile)
    patterns = _STACK_PATTERNS.get(stack_key, _STACK_PATTERNS["default"])

    # Architecture field can vary — Scout outputs folder trees, module maps, etc.
    # We look for structural evidence in multiple places.
    arch_str = json.dumps(architecture, default=str).lower()
    tree_size = scout_data.get("tree_size", 0)
    files_analysed = scout_data.get("files_analysed", 0)

    def _has_pattern(category: str) -> bool:
        """Check if any pattern from category appears in architecture data."""
        for pat in patterns.get(category, []):
            if pat.lower() in arch_str:
                return True
            # Also match without trailing slash (nested dict keys)
            bare = pat.lower().rstrip("/")
            if bare and bare in arch_str:
                return True
        return False

    # Count distinct layers found
    layer_checks = [
        _has_pattern("service_layer"),
        _has_pattern("data_access_layer"),
        _has_pattern("client_abstraction"),
        _has_pattern("middleware_layer"),
    ]
    # Also count API/router layer if architecture mentions routes/routers/api
    has_api_layer = any(kw in arch_str for kw in ("router", "route", "endpoint", "controller", "handler", "api/"))
    layer_count = sum(layer_checks) + (1 if has_api_layer else 0)

    # Test signals
    has_test_dir = _has_pattern("test_dirs")
    checks_passed = scout_data.get("checks_passed", 0)
    checks_failed = scout_data.get("checks_failed", 0)
    checks_total = checks_passed + checks_failed + scout_data.get("checks_warned", 0)

    # Estimate test-to-source ratio from Scout data
    # Scout's files_analysed typically counts source files;
    # we infer test files from the architecture tree
    test_file_count = arch_str.count("test_") + arch_str.count("_test.") + arch_str.count(".test.")
    source_estimate = max(files_analysed - test_file_count, 1)
    test_to_source_ratio = test_file_count / source_estimate if source_estimate > 0 else 0

    return {
        "has_service_layer": _has_pattern("service_layer"),
        "has_data_access_layer": _has_pattern("data_access_layer"),
        "has_client_abstraction": _has_pattern("client_abstraction"),
        "has_middleware_layer": _has_pattern("middleware_layer"),
        "has_config_external": _has_pattern("config_files"),
        "has_error_types": _has_pattern("error_files"),
        "has_test_parallel": has_test_dir,
        "test_to_source_ratio": round(test_to_source_ratio, 2),
        "layer_count": layer_count,
        "tree_size": tree_size,
        "files_analysed": files_analysed,
        "stack_key": stack_key,
    }


def _empty_signals() -> dict[str, Any]:
    """Return empty/default signals when no Scout data is available."""
    return {
        "has_service_layer": False,
        "has_data_access_layer": False,
        "has_client_abstraction": False,
        "has_middleware_layer": False,
        "has_config_external": False,
        "has_error_types": False,
        "has_test_parallel": False,
        "test_to_source_ratio": 0.0,
        "layer_count": 0,
        "tree_size": 0,
        "files_analysed": 0,
        "stack_key": "default",
    }


# ---------------------------------------------------------------------------
# Baseline capture
# ---------------------------------------------------------------------------


def capture_baseline(
    scout_data: dict,
    *,
    source_name: str = "ForgeGuard",
) -> BaselineProfile:
    """Capture a Scout profile as the architecture baseline.

    Run Scout on ForgeGuard (or any gold-standard project) and pass
    the scout section of CertificateData here to freeze it as the
    reference profile.

    Parameters
    ----------
    scout_data : dict
        The 'scout' section from CertificateData.
    source_name : str
        Label for the source project.

    Returns
    -------
    BaselineProfile dict that can be saved to JSON for reuse.
    """
    from datetime import datetime, timezone

    signals = extract_structural_signals(scout_data)

    return {
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source": source_name,
        "stack_profile": scout_data.get("stack_profile"),
        "architecture": scout_data.get("architecture"),
        "health_grade": scout_data.get("health_grade"),
        "quality_score": scout_data.get("quality_score"),
        "structural_signals": signals,
        "rules": [
            {"id": r["id"], "name": r["name"], "weight": r["weight"]}
            for r in ARCHITECTURE_RULES
        ],
    }


def save_baseline(baseline: BaselineProfile, path: str | Path) -> None:
    """Persist a baseline profile to disk as JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(baseline, indent=2, default=str), encoding="utf-8")
    logger.info("Architecture baseline saved to %s", p)


def load_baseline(path: str | Path) -> BaselineProfile:
    """Load a baseline profile from disk.

    Raises
    ------
    FileNotFoundError – if baseline file doesn't exist.
    json.JSONDecodeError – if file is malformed.
    """
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Comparison scorer
# ---------------------------------------------------------------------------

# Default baseline signals if no saved baseline is available.
# These represent ForgeGuard's known architecture.
_FORGEGUARD_FALLBACK_SIGNALS: dict[str, Any] = {
    "has_service_layer": True,
    "has_data_access_layer": True,
    "has_client_abstraction": True,
    "has_middleware_layer": True,
    "has_config_external": True,
    "has_error_types": True,
    "has_test_parallel": True,
    "test_to_source_ratio": 0.85,
    "layer_count": 5,
    "tree_size": 120,
    "files_analysed": 90,
    "stack_key": "python",
}


def compare_against_baseline(
    target_scout_data: dict | None,
    baseline: BaselineProfile | None = None,
) -> ArchitectureScore:
    """Compare a target project against the architecture baseline.

    Parameters
    ----------
    target_scout_data : dict | None
        The 'scout' section from the target project's CertificateData.
    baseline : BaselineProfile | None
        Loaded baseline profile. If None, uses built-in ForgeGuard defaults.

    Returns
    -------
    ArchitectureScore with score (0–100), grade, and per-rule results.
    """
    # Extract baseline signals
    if baseline and "structural_signals" in baseline:
        baseline_signals = baseline["structural_signals"]
    else:
        baseline_signals = _FORGEGUARD_FALLBACK_SIGNALS

    # Extract target signals
    target_signals = extract_structural_signals(target_scout_data)

    # Evaluate each rule
    rule_results: list[dict[str, Any]] = []
    total_weight = 0
    passed_weight = 0
    rules_passed = 0

    for rule in ARCHITECTURE_RULES:
        check_fn = rule["check"]
        weight = rule["weight"]
        total_weight += weight

        passed, detail = check_fn(target_signals, baseline_signals)
        rule_results.append({
            "rule": rule["id"],
            "name": rule["name"],
            "passed": passed,
            "weight": weight,
            "detail": detail,
        })

        if passed:
            passed_weight += weight
            rules_passed += 1

    # Weighted score
    score = round((passed_weight / total_weight) * 100) if total_weight > 0 else 0

    # Health grade bonus/penalty from Scout
    if target_scout_data:
        health_grade = target_scout_data.get("health_grade")
        if health_grade:
            bonus = {"A": 5, "B": 2, "C": 0, "D": -5, "F": -10}.get(health_grade, 0)
            score = max(0, min(100, score + bonus))

    # Details summary
    details: list[str] = []
    details.append(f"{rules_passed}/{len(ARCHITECTURE_RULES)} architecture rules passed")

    failed_critical = [r for r in rule_results if not r["passed"] and r["weight"] >= 3]
    if failed_critical:
        names = ", ".join(r["name"] for r in failed_critical)
        details.append(f"Critical failures: {names}")

    if target_scout_data and target_scout_data.get("health_grade"):
        details.append(f"Scout health grade: {target_scout_data['health_grade']}")

    details.append(f"Stack: {target_signals.get('stack_key', 'unknown')}")
    details.append(f"Tree size: {target_signals.get('tree_size', 0)} files")

    # Letter grade
    if score >= 90:
        grade = "A"
    elif score >= 80:
        grade = "B"
    elif score >= 70:
        grade = "C"
    elif score >= 60:
        grade = "D"
    else:
        grade = "F"

    return {
        "score": score,
        "grade": grade,
        "details": details,
        "rules_evaluated": len(ARCHITECTURE_RULES),
        "rules_passed": rules_passed,
        "rule_results": rule_results,
    }
