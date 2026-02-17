"""Tests for architecture_mapper -- heuristic architecture analysis."""

from app.services.architecture_mapper import (
    _classify_structure,
    _find_entry_points,
    _map_directories,
    _extract_routes,
    _find_data_models,
    _find_integrations,
    _find_config_sources,
    _check_for_boundaries,
    _estimate_test_coverage,
    map_architecture,
)


# ---------------------------------------------------------------------------
# _classify_structure
# ---------------------------------------------------------------------------


def test_classify_flat():
    paths = ["main.py", "utils.py", "README.md"]
    assert _classify_structure(paths) == "flat"


def test_classify_layered():
    paths = [
        "app/main.py",
        "app/services/user.py",
        "app/api/routers/users.py",
        "app/repos/user_repo.py",
    ]
    assert _classify_structure(paths) == "layered"


def test_classify_monorepo():
    paths = [
        "packages/frontend/src/App.tsx",
        "packages/backend/src/main.ts",
        "package.json",
    ]
    assert _classify_structure(paths) == "monorepo"


# ---------------------------------------------------------------------------
# _find_entry_points
# ---------------------------------------------------------------------------


def test_find_entry_points():
    paths = [
        "src/deep/app.py",
        "app/main.py",
        "manage.py",
        "web/src/main.tsx",
        "README.md",
    ]
    stack = {"primary_language": "Python"}
    entries = _find_entry_points(paths, stack)
    # manage.py is shallowest, then app/main.py, then src/deep/app.py, then web/src/main.tsx
    assert entries[0] == "manage.py"
    assert "app/main.py" in entries
    assert "web/src/main.tsx" in entries


# ---------------------------------------------------------------------------
# _map_directories
# ---------------------------------------------------------------------------


def test_map_directories():
    paths = [
        "app/services/foo.py",
        "app/api/routers/bar.py",
        "tests/test_foo.py",
        "db/migrations/001.sql",
    ]
    dirs = _map_directories(paths)
    assert "app/services/" in dirs
    assert dirs["app/services/"] == "Business logic layer"
    assert "tests/" in dirs
    assert dirs["tests/"] == "Test suite"
    assert "db/migrations/" in dirs
    assert "Database migrations" in dirs["db/migrations/"]


# ---------------------------------------------------------------------------
# _extract_routes
# ---------------------------------------------------------------------------


def test_extract_fastapi_routes():
    contents = {
        "app/api/routers/users.py": (
            '@router.get("/users")\n'
            "async def list_users(): ...\n\n"
            '@router.post("/users")\n'
            "async def create_user(): ...\n"
        ),
    }
    routes = _extract_routes(contents)
    assert len(routes) == 2
    methods = {r["method"] for r in routes}
    assert "GET" in methods
    assert "POST" in methods
    paths = {r["path"] for r in routes}
    assert "/users" in paths


def test_extract_flask_routes():
    contents = {
        "app.py": (
            '@app.route("/hello")\n'
            "def hello(): ...\n"
        ),
    }
    routes = _extract_routes(contents)
    assert len(routes) >= 1
    assert routes[0]["path"] == "/hello"


def test_extract_express_routes():
    contents = {
        "server.js": (
            "app.get('/api/items', getItems)\n"
            "app.post('/api/items', createItem)\n"
        ),
    }
    routes = _extract_routes(contents)
    assert len(routes) == 2


# ---------------------------------------------------------------------------
# _find_data_models
# ---------------------------------------------------------------------------


def test_find_sql_tables():
    contents = {
        "db/001.sql": (
            "CREATE TABLE IF NOT EXISTS users (\n  id UUID PRIMARY KEY\n);\n"
            "CREATE TABLE projects (\n  id SERIAL\n);\n"
            "ALTER TABLE users ADD COLUMN email TEXT;\n"
        ),
    }
    models = _find_data_models(["db/001.sql"], contents)
    assert "users" in models
    assert "projects" in models


def test_find_sqlalchemy_models():
    contents = {
        "models.py": (
            "class User(Base):\n"
            "    __tablename__ = 'users'\n"
            "class Project(db.Model):\n"
            "    pass\n"
        ),
    }
    models = _find_data_models(["models.py"], contents)
    assert "User" in models or "users" in models
    assert "Project" in models


# ---------------------------------------------------------------------------
# _find_integrations
# ---------------------------------------------------------------------------


def test_find_python_integrations():
    contents = {
        "client.py": "import httpx\nfrom redis import Redis\n",
        "db.py": "import asyncpg\n",
    }
    integrations = _find_integrations(contents)
    assert "HTTP Client (httpx)" in integrations
    assert "Redis" in integrations
    assert "PostgreSQL" in integrations


def test_find_no_integrations():
    contents = {"main.py": "print('hello')"}
    integrations = _find_integrations(contents)
    assert integrations == []


# ---------------------------------------------------------------------------
# _find_config_sources
# ---------------------------------------------------------------------------


def test_find_config_sources():
    paths = ["config.py", ".env", ".env.example", "forge.json", "app.py"]
    configs = _find_config_sources(paths)
    assert "config.py" in configs
    assert ".env" in configs
    assert "forge.json" in configs
    assert "app.py" not in configs


# ---------------------------------------------------------------------------
# _check_for_boundaries
# ---------------------------------------------------------------------------


def test_check_boundaries_present():
    paths = ["Forge/Contracts/boundaries.json", "forge.json"]
    result = _check_for_boundaries(paths)
    assert result is not None
    assert result["has_boundaries"] is True
    assert result["has_forge_json"] is True


def test_check_boundaries_absent():
    paths = ["main.py", "README.md"]
    result = _check_for_boundaries(paths)
    assert result is None


# ---------------------------------------------------------------------------
# _estimate_test_coverage
# ---------------------------------------------------------------------------


def test_estimate_coverage_high():
    assert _estimate_test_coverage(10, 15) == "high"


def test_estimate_coverage_medium():
    assert _estimate_test_coverage(5, 20) == "medium"


def test_estimate_coverage_low():
    assert _estimate_test_coverage(1, 20) == "low"


def test_estimate_coverage_none():
    assert _estimate_test_coverage(0, 10) == "none"


def test_estimate_coverage_no_source():
    assert _estimate_test_coverage(0, 0) == "none"


# ---------------------------------------------------------------------------
# map_architecture -- full integration
# ---------------------------------------------------------------------------


def test_map_architecture_complete():
    tree_paths = [
        "app/main.py",
        "app/services/user.py",
        "app/api/routers/users.py",
        "app/repos/user_repo.py",
        "tests/test_user.py",
        "db/migrations/001.sql",
        "config.py",
        "forge.json",
        "Dockerfile",
        ".github/workflows/ci.yml",
    ]
    stack_profile = {
        "primary_language": "Python",
        "backend": {"framework": "FastAPI"},
    }
    file_contents = {
        "app/api/routers/users.py": '@router.get("/users")\nasync def list_users(): ...\n',
        "app/repos/user_repo.py": "import asyncpg\n",
        "db/migrations/001.sql": "CREATE TABLE users (id UUID);\n",
    }

    result = map_architecture(
        tree_paths=tree_paths,
        stack_profile=stack_profile,
        file_contents=file_contents,
    )

    assert result["structure_type"] == "layered"
    assert "app/main.py" in result["entry_points"]
    assert "app/services/" in result["directories"]
    assert len(result["route_map"]) >= 1
    assert "users" in result["data_models"]
    assert "PostgreSQL" in result["external_integrations"]
    assert "config.py" in result["config_sources"]
    assert result["file_count"] > 0
    assert result["test_coverage_indicator"] in ("low", "medium", "high")


def test_map_architecture_empty():
    result = map_architecture(
        tree_paths=["README.md"],
        stack_profile={"primary_language": None},
        file_contents={},
    )
    assert result["structure_type"] == "flat"
    assert result["entry_points"] == []
    assert result["route_map"] == []
