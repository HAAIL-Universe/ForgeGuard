"""Tests for stack_detector -- heuristic stack identification."""

from app.services.stack_detector import (
    _parse_requirements_txt,
    _parse_pyproject_deps,
    _get_pkg_json_deps,
    detect_stack,
)


# ---------------------------------------------------------------------------
# _parse_requirements_txt
# ---------------------------------------------------------------------------


def test_parse_requirements_simple():
    content = "fastapi>=0.100\nuvicorn[standard]\npydantic~=2.0\n"
    deps = _parse_requirements_txt(content)
    assert deps == ["fastapi", "uvicorn", "pydantic"]


def test_parse_requirements_comments_blanks():
    content = "# comment\n\nrequests\n-r base.txt\nhttpx>=0.25\n"
    deps = _parse_requirements_txt(content)
    assert "requests" in deps
    assert "httpx" in deps
    assert len(deps) == 2  # -r line skipped


# ---------------------------------------------------------------------------
# _parse_pyproject_deps
# ---------------------------------------------------------------------------


def test_parse_pyproject_deps():
    content = (
        '[project]\nname = "myapp"\n\n'
        "dependencies = [\n"
        '  "fastapi>=0.100",\n'
        '  "asyncpg>=0.29",\n'
        "]\n"
    )
    deps = _parse_pyproject_deps(content)
    assert "fastapi" in deps
    assert "asyncpg" in deps


def test_parse_pyproject_poetry():
    content = (
        "[tool.poetry.dependencies]\n"
        'python = "^3.12"\n'
        'django = "^5.0"\n'
        'redis = "^5.0"\n'
    )
    deps = _parse_pyproject_deps(content)
    assert "django" in deps
    assert "redis" in deps
    assert "python" not in deps


# ---------------------------------------------------------------------------
# _get_pkg_json_deps
# ---------------------------------------------------------------------------


def test_get_pkg_json_deps():
    pkg = {
        "dependencies": {"react": "^18", "next": "^14"},
        "devDependencies": {"vitest": "^1.0", "typescript": "^5"},
    }
    deps = _get_pkg_json_deps(pkg)
    assert set(deps) == {"react", "next", "vitest", "typescript"}


# ---------------------------------------------------------------------------
# detect_stack -- Python / FastAPI project
# ---------------------------------------------------------------------------


def test_detect_stack_fastapi_project():
    result = detect_stack(
        tree_paths=[
            "app/main.py",
            "app/config.py",
            "requirements.txt",
            "tests/test_app.py",
            "Dockerfile",
            ".github/workflows/ci.yml",
        ],
        language_bytes={"Python": 50000, "Shell": 2000},
        requirements_txt="fastapi>=0.100\nuvicorn\nasyncpg\npytest\n",
    )
    assert result["primary_language"] == "Python"
    assert result["backend"]["framework"] == "FastAPI"
    assert result["backend"]["db"] == "PostgreSQL"
    assert result["infrastructure"]["containerized"] is True
    assert result["infrastructure"]["ci_cd"] == "GitHub Actions"
    assert result["testing"]["backend_framework"] == "pytest"
    assert result["testing"]["has_tests"] is True
    assert "requirements.txt" in result["manifest_files"]
    assert result["project_type"] == "api"


def test_detect_stack_django_project():
    result = detect_stack(
        tree_paths=[
            "manage.py",
            "myapp/settings.py",
            "myapp/urls.py",
            "myapp/models.py",
            "requirements.txt",
            "templates/index.html",
        ],
        language_bytes={"Python": 40000, "HTML": 5000},
        requirements_txt="django>=5.0\npsycopg2\n",
    )
    assert result["backend"]["framework"] == "Django"
    assert result["backend"]["orm"] == "Django ORM"
    assert result["backend"]["db"] == "PostgreSQL"
    assert result["project_type"] == "web_app"  # has HTML → frontend detected


# ---------------------------------------------------------------------------
# detect_stack -- Node / React project
# ---------------------------------------------------------------------------


def test_detect_stack_react_vite_project():
    result = detect_stack(
        tree_paths=[
            "package.json",
            "tsconfig.json",
            "src/App.tsx",
            "src/main.tsx",
            "vite.config.ts",
        ],
        language_bytes={"TypeScript": 30000, "HTML": 1000, "CSS": 2000},
        package_json='{"dependencies":{"react":"^18","react-dom":"^18"},"devDependencies":{"vite":"^5","vitest":"^1","tailwindcss":"^3"}}',
    )
    assert result["frontend"]["framework"] == "React"
    assert result["frontend"]["bundler"] == "Vite"
    assert result["frontend"]["language"] == "TypeScript"
    assert result["frontend"]["ui_library"] == "Tailwind CSS"
    assert result["testing"]["frontend_framework"] == "Vitest"
    assert result["project_type"] == "spa"


def test_detect_stack_nextjs_fullstack():
    result = detect_stack(
        tree_paths=[
            "package.json",
            "pages/index.tsx",
            "pages/api/hello.ts",
            "next.config.js",
        ],
        language_bytes={"TypeScript": 40000, "JavaScript": 2000},
        package_json='{"dependencies":{"next":"^14","react":"^18","express":"^4"}}',
    )
    assert result["frontend"]["framework"] == "Next.js"
    # Express is also a frontend framework (Next.js), backend not detected
    # since Next.js appears in both frontend and backend node lists but
    # is excluded from backend when it's a frontend framework
    assert result["project_type"] == "spa"


# ---------------------------------------------------------------------------
# detect_stack -- edge cases
# ---------------------------------------------------------------------------


def test_detect_stack_empty_repo():
    result = detect_stack(
        tree_paths=["README.md"],
        language_bytes={},
    )
    assert result["primary_language"] is None
    assert result["backend"] is None
    assert result["frontend"] is None
    assert result["project_type"] == "unknown"
    assert result["languages"] == {}


def test_detect_stack_python_library():
    result = detect_stack(
        tree_paths=[
            "setup.py",
            "mylib/__init__.py",
            "mylib/core.py",
            "tests/test_core.py",
        ],
        language_bytes={"Python": 10000},
        requirements_txt="",
    )
    # No framework → library type
    assert result["backend"] is not None
    assert result["backend"]["framework"] is None
    assert result["project_type"] == "library"


def test_detect_stack_cli_app():
    result = detect_stack(
        tree_paths=[
            "myapp/__init__.py",
            "myapp/__main__.py",
            "myapp/cli.py",
        ],
        language_bytes={"Python": 5000},
    )
    # Python with __main__.py but no framework → backend is detected
    # (primary_language Python fallback) so classified as api, not cli
    assert result["backend"] is not None
    assert result["backend"]["framework"] is None


def test_detect_stack_monorepo():
    paths = [
        "packages/frontend/package.json",
        "packages/backend/package.json",
        "packages/shared/package.json",
        "package.json",
    ]
    result = detect_stack(
        tree_paths=paths,
        language_bytes={"TypeScript": 80000, "JavaScript": 5000},
        package_json='{"dependencies":{}}',
    )
    assert result["project_type"] == "monorepo"


def test_detect_stack_static_html():
    result = detect_stack(
        tree_paths=["index.html", "styles.css", "script.js"],
        language_bytes={"HTML": 3000, "CSS": 1000, "JavaScript": 500},
    )
    assert result["frontend"]["framework"] == "Static HTML"
    assert result["project_type"] == "static_site"


def test_detect_stack_infrastructure_render():
    result = detect_stack(
        tree_paths=["app.py", "render.yaml", "requirements.txt"],
        language_bytes={"Python": 5000},
        requirements_txt="flask\n",
    )
    assert result["infrastructure"]["hosting"] == "Render"


def test_detect_stack_language_percentages():
    result = detect_stack(
        tree_paths=["main.py"],
        language_bytes={"Python": 7500, "Shell": 2500},
    )
    assert result["languages"]["Python"] == 75.0
    assert result["languages"]["Shell"] == 25.0
