"""Auth service -- orchestrates GitHub OAuth flow and JWT creation."""

from app.auth import create_token
from app.clients.github_client import exchange_code_for_token, get_github_user
from app.config import settings
from app.repos.user_repo import upsert_user


async def handle_github_callback(code: str) -> dict:
    """Process the GitHub OAuth callback.

    1. Exchange code for GitHub access token.
    2. Fetch GitHub user profile.
    3. Upsert user in DB.
    4. Create and return a JWT.

    Returns dict with token and user info.
    """
    github_token = await exchange_code_for_token(
        client_id=settings.GITHUB_CLIENT_ID,
        client_secret=settings.GITHUB_CLIENT_SECRET,
        code=code,
    )

    github_user = await get_github_user(github_token)

    user = await upsert_user(
        github_id=github_user["github_id"],
        github_login=github_user["login"],
        avatar_url=github_user["avatar_url"],
        access_token=github_token,
    )

    token = create_token(
        user_id=str(user["id"]),
        github_login=user["github_login"],
    )

    return {
        "token": token,
        "user": {
            "id": str(user["id"]),
            "github_login": user["github_login"],
            "avatar_url": user.get("avatar_url"),
        },
    }
