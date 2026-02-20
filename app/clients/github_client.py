"""GitHub API client -- OAuth token exchange, user info, repos, and webhooks."""

import httpx
from cachetools import TTLCache

GITHUB_OAUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_API_BASE = "https://api.github.com"

# ── Response caches (reduces GitHub API rate-limit pressure) ────────────────
# Key format: (access_token, full_name) or (access_token,)
# TTL = 300 s (5 min) — short enough to stay reasonably fresh.

_repo_list_cache: TTLCache[str, list[dict]] = TTLCache(maxsize=200, ttl=300)
_repo_meta_cache: TTLCache[tuple[str, str], dict] = TTLCache(maxsize=500, ttl=300)
_repo_lang_cache: TTLCache[tuple[str, str], dict[str, int]] = TTLCache(maxsize=500, ttl=300)

# ── Shared HTTP client (connection pooling) ─────────────────────────────────

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Return (or create) the shared httpx client for GitHub API calls."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0)
    return _client


async def close_client() -> None:
    """Close the shared HTTP client.  Called during app shutdown."""
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


# ── Helpers ──────────────────────────────────────────────────────────────────


def _auth_headers(access_token: str) -> dict:
    """Return standard GitHub API auth headers."""
    return {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/vnd.github+json",
    }


async def exchange_code_for_token(
    client_id: str,
    client_secret: str,
    code: str,
) -> str:
    """Exchange an OAuth authorization code for an access token."""
    client = _get_client()
    response = await client.post(
        GITHUB_TOKEN_URL,
        json={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
        },
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    data = response.json()
    token = data.get("access_token")
    if not token:
        error = data.get("error_description", data.get("error", "Unknown error"))
        raise ValueError(f"GitHub OAuth error: {error}")
    return token


async def get_github_user(access_token: str) -> dict:
    """Fetch the authenticated GitHub user profile."""
    client = _get_client()
    response = await client.get(
        GITHUB_USER_URL,
        headers=_auth_headers(access_token),
    )
    response.raise_for_status()
    data = response.json()
    return {
        "github_id": data["id"],
        "login": data["login"],
        "avatar_url": data.get("avatar_url"),
    }


async def list_user_repos(access_token: str) -> list[dict]:
    """List repositories accessible to the authenticated user.

    Paginates through all results (up to 300 repos).
    Returns a list of repo dicts with id, full_name, default_branch, private.
    Results are cached for 5 minutes per access token.
    """
    cached = _repo_list_cache.get(access_token)
    if cached is not None:
        return cached

    repos: list[dict] = []
    page = 1
    per_page = 100
    client = _get_client()
    while page <= 3:  # cap at 300 repos
        response = await client.get(
            f"{GITHUB_API_BASE}/user/repos",
            params={
                "per_page": per_page,
                "page": page,
                "sort": "updated",
                "direction": "desc",
                "affiliation": "owner,collaborator,organization_member",
            },
            headers=_auth_headers(access_token),
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            break
        for repo in data:
            repos.append({
                "github_repo_id": repo["id"],
                "full_name": repo["full_name"],
                "default_branch": repo.get("default_branch", "main"),
                "private": repo.get("private", False),
            })
        if len(data) < per_page:
            break
        page += 1

    _repo_list_cache[access_token] = repos
    return repos


async def create_github_repo(
    access_token: str,
    name: str,
    description: str | None = None,
    private: bool = False,
) -> dict:
    """Create a new repository on the authenticated user's GitHub account.

    Returns a dict with github_repo_id, full_name, default_branch, private.
    """
    # GitHub limits repo descriptions to 350 chars and rejects control characters
    safe_desc = (description or "").replace("\r", " ").replace("\n", " ")
    safe_desc = safe_desc[:350]

    client = _get_client()
    response = await client.post(
        f"{GITHUB_API_BASE}/user/repos",
        json={
            "name": name,
            "description": safe_desc,
            "private": private,
            "auto_init": True,
        },
        headers=_auth_headers(access_token),
    )
    if response.status_code == 422:
        errors = response.json().get("errors", [])
        msgs = [e.get("message", "") for e in errors]
        msg = response.json().get("message", "Validation failed")
        raise ValueError(f"{msg}: {'; '.join(msgs)}" if msgs else msg)
    response.raise_for_status()
    data = response.json()
    return {
        "github_repo_id": data["id"],
        "full_name": data["full_name"],
        "default_branch": data.get("default_branch", "main"),
        "private": data.get("private", False),
    }


async def create_webhook(
    access_token: str,
    full_name: str,
    webhook_url: str,
    webhook_secret: str,
) -> int:
    """Create a push webhook on a GitHub repo.

    Returns the webhook ID from GitHub.
    """
    client = _get_client()
    response = await client.post(
        f"{GITHUB_API_BASE}/repos/{full_name}/hooks",
        json={
            "name": "web",
            "active": True,
            "events": ["push"],
            "config": {
                "url": webhook_url,
                "content_type": "json",
                "secret": webhook_secret,
                "insecure_ssl": "0",
            },
        },
        headers=_auth_headers(access_token),
    )
    response.raise_for_status()
    return response.json()["id"]


async def delete_webhook(
    access_token: str,
    full_name: str,
    webhook_id: int,
) -> None:
    """Delete a webhook from a GitHub repo."""
    client = _get_client()
    response = await client.delete(
        f"{GITHUB_API_BASE}/repos/{full_name}/hooks/{webhook_id}",
        headers=_auth_headers(access_token),
    )
    # 404 is fine -- webhook may already be gone
    if response.status_code != 404:
        response.raise_for_status()


async def get_repo_file_content(
    access_token: str,
    full_name: str,
    path: str,
    ref: str,
) -> str | None:
    """Fetch a single file's content from a GitHub repo at a specific ref.

    Returns the decoded text content, or None if the file doesn't exist.
    """
    import base64

    client = _get_client()
    response = await client.get(
        f"{GITHUB_API_BASE}/repos/{full_name}/contents/{path}",
        params={"ref": ref},
        headers=_auth_headers(access_token),
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    data = response.json()
    if data.get("encoding") == "base64":
        return base64.b64decode(data["content"]).decode("utf-8", errors="replace")
    return data.get("content", "")


async def get_commit_files(
    access_token: str,
    full_name: str,
    commit_sha: str,
) -> list[str]:
    """Fetch the list of changed file paths for a specific commit."""
    client = _get_client()
    response = await client.get(
        f"{GITHUB_API_BASE}/repos/{full_name}/commits/{commit_sha}",
        headers=_auth_headers(access_token),
    )
    response.raise_for_status()
    data = response.json()
    return [f["filename"] for f in data.get("files", [])]


async def compare_commits(
    access_token: str,
    full_name: str,
    base_sha: str,
    head_sha: str,
) -> dict:
    """Compare two commits and return the diff summary.

    Uses GitHub's Compare API: GET /repos/{owner}/{repo}/compare/{base}...{head}

    Returns dict with keys:
      - files: list of changed file paths (union of all intermediate changes)
      - total_commits: number of commits between base and head
      - head_sha: the resolved head SHA
      - head_message: commit message of the head commit
      - head_author: author name of the head commit
    """
    client = _get_client()
    response = await client.get(
        f"{GITHUB_API_BASE}/repos/{full_name}/compare/{base_sha}...{head_sha}",
        headers=_auth_headers(access_token),
    )
    response.raise_for_status()
    data = response.json()

    files = [f["filename"] for f in data.get("files", [])]
    commits = data.get("commits", [])
    head_commit_data = commits[-1] if commits else {}
    commit_obj = head_commit_data.get("commit", {})
    author_obj = commit_obj.get("author", {})

    return {
        "files": files,
        "total_commits": len(commits),
        "head_sha": head_commit_data.get("sha", head_sha),
        "head_message": commit_obj.get("message", ""),
        "head_author": author_obj.get("name", ""),
    }


async def get_repo_tree(
    access_token: str,
    full_name: str,
    sha: str,
    recursive: bool = True,
) -> list[dict]:
    """Fetch the full file tree for a repo at a given SHA.

    Returns list of dicts with path, type ('blob'|'tree'), and size (bytes, blobs only).
    Truncated trees (>100k entries) return whatever GitHub provides.
    """
    params: dict[str, str | int] = {}
    if recursive:
        params["recursive"] = "1"
    client = _get_client()
    response = await client.get(
        f"{GITHUB_API_BASE}/repos/{full_name}/git/trees/{sha}",
        params=params,
        headers=_auth_headers(access_token),
    )
    response.raise_for_status()
    data = response.json()
    tree = data.get("tree", [])
    return [
        {
            "path": item["path"],
            "type": item["type"],
            "size": item.get("size", 0),
        }
        for item in tree
    ]


async def get_repo_languages(
    access_token: str,
    full_name: str,
) -> dict[str, int]:
    """Fetch language byte counts for a repo (cached 5 min).

    Returns e.g. {"Python": 45000, "TypeScript": 32000}.
    """
    key = (access_token, full_name)
    cached = _repo_lang_cache.get(key)
    if cached is not None:
        return cached

    client = _get_client()
    response = await client.get(
        f"{GITHUB_API_BASE}/repos/{full_name}/languages",
        headers=_auth_headers(access_token),
    )
    response.raise_for_status()
    result = response.json()
    _repo_lang_cache[key] = result
    return result


async def get_repo_health(
    access_token: str,
    full_name: str,
) -> tuple[int, dict | None]:
    """Fetch current status of a repo for health checking (no cache, fresh data).

    Returns (status_code, metadata_dict_or_None).
    On HTTP error returns (status_code, None).
    On network error returns (0, None).
    Metadata dict contains: full_name, archived.
    """
    client = _get_client()
    try:
        response = await client.get(
            f"{GITHUB_API_BASE}/repos/{full_name}",
            headers=_auth_headers(access_token),
        )
    except httpx.RequestError:
        return 0, None
    if response.status_code != 200:
        return response.status_code, None
    data = response.json()
    return 200, {
        "full_name": data.get("full_name", full_name),
        "archived": data.get("archived", False),
    }


async def get_repo_metadata(
    access_token: str,
    full_name: str,
) -> dict:
    """Fetch top-level metadata for a repo (cached 5 min).

    Returns dict with stargazers_count, forks_count, size, license, topics,
    created_at, updated_at, default_branch, description, private.
    """
    key = (access_token, full_name)
    cached = _repo_meta_cache.get(key)
    if cached is not None:
        return cached

    client = _get_client()
    response = await client.get(
        f"{GITHUB_API_BASE}/repos/{full_name}",
        headers=_auth_headers(access_token),
    )
    response.raise_for_status()
    data = response.json()
    result = {
        "stargazers_count": data.get("stargazers_count", 0),
        "forks_count": data.get("forks_count", 0),
        "size": data.get("size", 0),
        "license": (data.get("license") or {}).get("spdx_id"),
        "topics": data.get("topics", []),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "default_branch": data.get("default_branch", "main"),
        "description": data.get("description"),
        "private": data.get("private", False),
        "archived": data.get("archived", False),
        "full_name": data.get("full_name", full_name),
    }
    _repo_meta_cache[key] = result
    return result


async def delete_branch(
    access_token: str,
    full_name: str,
    branch: str,
) -> bool:
    """Delete a branch from a GitHub repo via the Git refs API.

    Returns True if the branch was deleted (or didn't exist), False on error.
    Does NOT delete the default branch (main/master) — returns False instead.
    """
    # Safety: never delete common default branches
    if branch in ("main", "master", "develop"):
        return False
    client = _get_client()
    ref = f"heads/{branch}"
    response = await client.delete(
        f"{GITHUB_API_BASE}/repos/{full_name}/git/refs/{ref}",
        headers=_auth_headers(access_token),
    )
    # 204 = deleted, 422 = ref not found (already gone) — both are fine
    return response.status_code in (204, 422)


async def list_commits(
    access_token: str,
    full_name: str,
    branch: str | None = None,
    since: str | None = None,
    per_page: int = 100,
    max_pages: int = 3,
) -> list[dict]:
    """List commits from a GitHub repo, newest first.

    Parameters
    ----------
    branch : default branch if None
    since  : ISO-8601 timestamp — only commits after this date
    per_page / max_pages : pagination caps (default 300 commits max)

    Returns list of dicts with sha, message, author, date, branch.
    """
    commits: list[dict] = []
    page = 1
    client = _get_client()
    while page <= max_pages:
        params: dict[str, str | int] = {"per_page": per_page, "page": page}
        if branch:
            params["sha"] = branch
        if since:
            params["since"] = since
        response = await client.get(
            f"{GITHUB_API_BASE}/repos/{full_name}/commits",
            params=params,
            headers=_auth_headers(access_token),
        )
        response.raise_for_status()
        data = response.json()
        if not data:
            break
        for c in data:
            commit_data = c.get("commit", {})
            author_data = commit_data.get("author", {})
            commits.append({
                "sha": c["sha"],
                "message": commit_data.get("message", ""),
                "author": author_data.get("name", ""),
                "date": author_data.get("date", ""),
            })
        if len(data) < per_page:
            break
        page += 1
    return commits
