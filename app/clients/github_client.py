"""GitHub API client -- OAuth token exchange, user info, repos, and webhooks."""

import httpx

GITHUB_OAUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_API_BASE = "https://api.github.com"


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
    async with httpx.AsyncClient() as client:
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
    async with httpx.AsyncClient() as client:
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
    """
    repos: list[dict] = []
    page = 1
    per_page = 100
    async with httpx.AsyncClient() as client:
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
    return repos


async def create_webhook(
    access_token: str,
    full_name: str,
    webhook_url: str,
    webhook_secret: str,
) -> int:
    """Create a push webhook on a GitHub repo.

    Returns the webhook ID from GitHub.
    """
    async with httpx.AsyncClient() as client:
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
    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{GITHUB_API_BASE}/repos/{full_name}/hooks/{webhook_id}",
            headers=_auth_headers(access_token),
        )
        # 404 is fine -- webhook may already be gone
        if response.status_code != 404:
            response.raise_for_status()
