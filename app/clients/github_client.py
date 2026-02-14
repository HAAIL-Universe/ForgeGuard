"""GitHub API client -- OAuth token exchange and user info."""

import httpx

GITHUB_OAUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"


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
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        response.raise_for_status()
        data = response.json()
        return {
            "github_id": data["id"],
            "login": data["login"],
            "avatar_url": data.get("avatar_url"),
        }
