"""Migration advisor — recommend concrete upgrade paths.

Takes pattern findings + version currency data and produces prioritized
migration recommendations with effort/risk estimates and step-by-step
instructions.  Pure heuristic — no LLM calls.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

MigrationRecommendation = dict[str, Any]
# {
#   "id": "MIG01",
#   "from_state": "...",
#   "to_state": "...",
#   "effort": "low" | "medium" | "high",
#   "risk": "low" | "medium" | "high",
#   "priority": "high" | "medium" | "low",
#   "category": "quality" | "security" | "modernization" | "devops",
#   "rationale": "...",
#   "steps": ["step 1", "step 2", ...],
#   "forge_automatable": bool,
# }


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------

# Each entry: (trigger_condition_fn, migration_builder_fn)
# trigger_condition_fn(stack, patterns, versions) -> bool
# migration_builder_fn(stack, patterns, versions) -> MigrationRecommendation


def _mig_js_to_ts(
    stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG01: Migrate JavaScript frontend to TypeScript."""
    if not any(p.get("id") == "AP01" for p in patterns):
        return None
    return {
        "id": "MIG01",
        "from_state": "JavaScript frontend",
        "to_state": "TypeScript frontend",
        "effort": "medium",
        "risk": "low",
        "priority": "high",
        "category": "quality",
        "rationale": (
            "TypeScript catches type errors at compile time, improves "
            "IDE support, and makes refactoring safer."
        ),
        "steps": [
            "Install typescript and @types packages",
            "Add tsconfig.json with strict mode",
            "Rename .js/.jsx files to .ts/.tsx incrementally",
            "Add type annotations to function parameters and return types",
            "Enable strict null checks once annotations are in place",
        ],
        "forge_automatable": False,
    }


def _mig_add_tests(
    stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG02: Add test framework and initial test suite."""
    if not any(p.get("id") == "AP02" for p in patterns):
        return None

    lang = stack.get("primary_language", "")
    if lang == "Python":
        framework = "pytest"
        steps = [
            "Install pytest: pip install pytest pytest-asyncio",
            "Create tests/ directory with __init__.py",
            "Write a smoke test for the main entry point",
            "Add test configuration to pyproject.toml",
            "Run: python -m pytest tests/ -v",
        ]
    else:
        framework = "vitest" if stack.get("frontend", {}).get("bundler") == "Vite" else "jest"
        steps = [
            f"Install {framework}: npm install -D {framework}",
            "Create __tests__/ directory or add .test.ts files",
            "Write a smoke test for the main component/module",
            f"Add test script to package.json: \"{framework}\"",
            f"Run: npx {framework} run",
        ]

    return {
        "id": "MIG02",
        "from_state": "No automated tests",
        "to_state": f"{framework} test suite",
        "effort": "medium",
        "risk": "low",
        "priority": "high",
        "category": "quality",
        "rationale": (
            "Automated tests catch regressions, enable refactoring, "
            "and are required for CI/CD pipelines."
        ),
        "steps": steps,
        "forge_automatable": False,
    }


def _mig_add_ci(
    _stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG03: Add GitHub Actions CI pipeline."""
    if not any(p.get("id") == "AP03" for p in patterns):
        return None
    return {
        "id": "MIG03",
        "from_state": "No CI/CD pipeline",
        "to_state": "GitHub Actions CI",
        "effort": "low",
        "risk": "low",
        "priority": "high",
        "category": "devops",
        "rationale": (
            "CI automatically runs tests on every push/PR, catching "
            "regressions before they reach production."
        ),
        "steps": [
            "Create .github/workflows/ci.yml",
            "Configure trigger: on push to main + pull requests",
            "Add steps: checkout, setup runtime, install deps, run tests",
            "Add status badge to README",
        ],
        "forge_automatable": True,
    }


def _mig_add_docker(
    stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG04: Add Dockerfile and docker-compose."""
    if not any(p.get("id") == "AP04" for p in patterns):
        return None
    return {
        "id": "MIG04",
        "from_state": "No containerization",
        "to_state": "Docker + docker-compose",
        "effort": "low",
        "risk": "low",
        "priority": "medium",
        "category": "devops",
        "rationale": (
            "Containers ensure consistent environments across development, "
            "staging, and production."
        ),
        "steps": [
            "Create a Dockerfile with multi-stage build",
            "Create docker-compose.yml for local development",
            "Add .dockerignore for build efficiency",
            "Test: docker compose up --build",
        ],
        "forge_automatable": True,
    }


def _mig_add_env_example(
    _stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG05: Add .env.example template."""
    if not any(p.get("id") == "AP06" for p in patterns):
        return None
    return {
        "id": "MIG05",
        "from_state": "No .env.example",
        "to_state": ".env.example template",
        "effort": "low",
        "risk": "low",
        "priority": "medium",
        "category": "devops",
        "rationale": (
            "An .env.example documents required environment variables "
            "so new developers can set up quickly."
        ),
        "steps": [
            "Copy .env to .env.example",
            "Replace all secret values with placeholder descriptions",
            "Add .env to .gitignore if not already present",
            "Document each variable in README or comments",
        ],
        "forge_automatable": True,
    }


def _mig_class_to_hooks(
    _stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG06: Migrate React class components to functional + hooks."""
    if not any(p.get("id") == "AP08" for p in patterns):
        return None
    return {
        "id": "MIG06",
        "from_state": "React class components",
        "to_state": "Functional components + Hooks",
        "effort": "medium",
        "risk": "low",
        "priority": "medium",
        "category": "modernization",
        "rationale": (
            "Functional components with hooks are simpler, more composable, "
            "and the recommended pattern since React 16.8."
        ),
        "steps": [
            "Convert lifecycle methods: componentDidMount → useEffect",
            "Convert this.state → useState hooks",
            "Replace this.props with function parameters",
            "Remove class syntax, convert to arrow functions",
            "Test each converted component individually",
        ],
        "forge_automatable": False,
    }


def _mig_callbacks_to_async(
    _stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG07: Replace callback patterns with async/await."""
    if not any(p.get("id") == "AP09" for p in patterns):
        return None
    return {
        "id": "MIG07",
        "from_state": "Callback-heavy async code",
        "to_state": "async/await",
        "effort": "medium",
        "risk": "medium",
        "priority": "medium",
        "category": "modernization",
        "rationale": (
            "async/await produces flatter, more readable code and "
            "makes error handling with try/catch straightforward."
        ),
        "steps": [
            "Identify callback-heavy functions",
            "Wrap callback APIs in Promise wrappers if needed",
            "Convert to async functions with await",
            "Replace nested error callbacks with try/catch",
            "Test thoroughly — async behavior changes can be subtle",
        ],
        "forge_automatable": False,
    }


def _mig_pin_deps(
    _stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG08: Pin dependency versions."""
    if not any(p.get("id") == "AP15" for p in patterns):
        return None
    return {
        "id": "MIG08",
        "from_state": "Unpinned dependencies",
        "to_state": "Pinned with version constraints",
        "effort": "low",
        "risk": "low",
        "priority": "medium",
        "category": "quality",
        "rationale": (
            "Pinning dependencies prevents unexpected breaking changes "
            "when new versions are released."
        ),
        "steps": [
            "Run: pip freeze > requirements.txt (or pip-compile)",
            "Or migrate to pyproject.toml with version ranges",
            "Use ~= for compatible releases (e.g., fastapi~=0.100)",
            "Consider using pip-tools or poetry for lock files",
        ],
        "forge_automatable": True,
    }


def _mig_add_linter(
    stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG09: Add linter / formatter configuration."""
    # Only suggest if there are quality findings but no linter detected
    quality_findings = [p for p in patterns if p.get("category") == "quality"]
    if not quality_findings:
        return None

    lang = stack.get("primary_language", "")
    if lang == "Python":
        return {
            "id": "MIG09",
            "from_state": "No linter configured",
            "to_state": "Ruff linter + formatter",
            "effort": "low",
            "risk": "low",
            "priority": "low",
            "category": "quality",
            "rationale": (
                "Ruff is an extremely fast Python linter that replaces "
                "flake8, isort, and black in a single tool."
            ),
            "steps": [
                "Install ruff: pip install ruff",
                "Create ruff.toml or add [tool.ruff] to pyproject.toml",
                "Run: ruff check . --fix",
                "Add to CI pipeline",
            ],
            "forge_automatable": True,
        }
    else:
        return {
            "id": "MIG09",
            "from_state": "No linter configured",
            "to_state": "ESLint + Prettier",
            "effort": "low",
            "risk": "low",
            "priority": "low",
            "category": "quality",
            "rationale": (
                "ESLint catches common bugs and enforces code style. "
                "Prettier auto-formats for consistency."
            ),
            "steps": [
                "Install eslint prettier: npm install -D eslint prettier",
                "Create .eslintrc.json and .prettierrc",
                "Add lint script to package.json",
                "Run: npx eslint . --fix",
            ],
            "forge_automatable": True,
        }


def _mig_upgrade_outdated(
    _stack: dict, _patterns: list[dict], versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG10: Upgrade outdated dependencies."""
    outdated = [v for v in versions if v.get("status") == "outdated"]
    if not outdated:
        return None

    names = [v["package"] for v in outdated[:5]]
    return {
        "id": "MIG10",
        "from_state": f"{len(outdated)} outdated dependencies",
        "to_state": "All dependencies up to date",
        "effort": "low" if len(outdated) <= 3 else "medium",
        "risk": "medium",
        "priority": "medium",
        "category": "quality",
        "rationale": (
            "Outdated dependencies miss bug fixes, security patches, "
            "and new features."
        ),
        "steps": [
            f"Update: {', '.join(names)}" + (" and more" if len(outdated) > 5 else ""),
            "Check changelogs for breaking changes",
            "Run test suite after each major update",
            "Consider updating one package at a time",
        ],
        "forge_automatable": False,
    }


def _mig_replace_eol(
    _stack: dict, _patterns: list[dict], versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG11: Replace end-of-life dependencies."""
    eol = [v for v in versions if v.get("status") == "eol"]
    if not eol:
        return None

    names = [f"{v['package']} {v.get('current', '?')}" for v in eol]
    return {
        "id": "MIG11",
        "from_state": f"{len(eol)} end-of-life dependencies",
        "to_state": "Supported versions",
        "effort": "medium" if len(eol) <= 2 else "high",
        "risk": "high",
        "priority": "high",
        "category": "security",
        "rationale": (
            "End-of-life software receives no security patches, "
            "creating known vulnerability exposure."
        ),
        "steps": [
            f"Replace: {', '.join(names)}",
            "Check migration guides for major version jumps",
            "Update all dependent code for API changes",
            "Run full test suite and fix breakages",
            "Consider a staged rollout if risk is high",
        ],
        "forge_automatable": False,
    }


def _mig_add_error_handling(
    _stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG12: Add global error handling."""
    if not any(p.get("id") == "AP10" for p in patterns):
        return None
    return {
        "id": "MIG12",
        "from_state": "No error handling middleware",
        "to_state": "Global exception handler",
        "effort": "low",
        "risk": "low",
        "priority": "medium",
        "category": "quality",
        "rationale": (
            "A global error handler prevents stack traces from leaking "
            "to users and ensures consistent error responses."
        ),
        "steps": [
            "Add a global exception handler to the framework",
            "Return structured error responses (JSON with status code)",
            "Log full stack traces server-side",
            "Add specific handlers for common errors (404, 422, 500)",
        ],
        "forge_automatable": True,
    }


def _mig_remove_secrets(
    _stack: dict, patterns: list[dict], _versions: list[dict],
) -> MigrationRecommendation | None:
    """MIG13: Remove hardcoded secrets from source code."""
    finding = next((p for p in patterns if p.get("id") == "AP11"), None)
    if finding is None:
        return None
    return {
        "id": "MIG13",
        "from_state": "Secrets hardcoded in source",
        "to_state": "Environment variables / secrets manager",
        "effort": "low",
        "risk": "low",
        "priority": "high",
        "category": "security",
        "rationale": (
            "Hardcoded secrets in source code can be exposed through "
            "version control, logs, or error messages."
        ),
        "steps": [
            "Move all secrets to environment variables",
            "Create .env file for local development",
            "Create .env.example with placeholder values",
            "Add .env to .gitignore",
            "Rotate any secrets that were committed to git history",
        ],
        "forge_automatable": False,
    }


# ---------------------------------------------------------------------------
# Migration registry
# ---------------------------------------------------------------------------

_MIGRATIONS: list = [
    _mig_js_to_ts,
    _mig_add_tests,
    _mig_add_ci,
    _mig_add_docker,
    _mig_add_env_example,
    _mig_class_to_hooks,
    _mig_callbacks_to_async,
    _mig_pin_deps,
    _mig_add_linter,
    _mig_upgrade_outdated,
    _mig_replace_eol,
    _mig_add_error_handling,
    _mig_remove_secrets,
]


# ---------------------------------------------------------------------------
# Priority computation
# ---------------------------------------------------------------------------

_EFFORT_SCORE = {"low": 1, "medium": 2, "high": 3}
_RISK_SCORE = {"low": 1, "medium": 2, "high": 3}

# Priority rules:
#   high severity pattern + low effort = high priority
#   medium severity + low effort = high priority
#   high effort + low severity = low priority
#   anything with risk=high AND is security = high priority


def _compute_priority(rec: MigrationRecommendation) -> str:
    """Compute priority for a migration recommendation."""
    effort = _EFFORT_SCORE.get(rec.get("effort", "medium"), 2)
    risk = _RISK_SCORE.get(rec.get("risk", "medium"), 2)
    category = rec.get("category", "")

    # Security issues are always high priority
    if category == "security":
        return "high"

    # Low effort + any category = at least medium
    if effort == 1:
        return "high" if risk <= 1 else "medium"

    # Medium effort
    if effort == 2:
        return "medium"

    # High effort = low priority (unless already set)
    return "low"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def recommend_migrations(
    stack_profile: dict[str, Any],
    pattern_findings: list[dict],
    version_currency: list[dict],
) -> list[MigrationRecommendation]:
    """Generate migration recommendations from analysis results.

    Parameters
    ----------
    stack_profile : output from detect_stack()
    pattern_findings : output from analyze_patterns()
    version_currency : output from check_all_dependencies()

    Returns
    -------
    List of MigrationRecommendation dicts, sorted by priority (high first).
    """
    recommendations: list[MigrationRecommendation] = []

    for migration_fn in _MIGRATIONS:
        try:
            result = migration_fn(stack_profile, pattern_findings, version_currency)
            if result is not None:
                # Recompute priority based on effort/risk/category
                result["priority"] = _compute_priority(result)
                recommendations.append(result)
        except Exception:
            pass

    # Sort by priority
    priority_order = {"high": 0, "medium": 1, "low": 2}
    recommendations.sort(key=lambda r: priority_order.get(r.get("priority", "low"), 2))

    return recommendations
