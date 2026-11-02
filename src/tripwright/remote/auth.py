"""FastMCP's OAuth proxy with Tripwright's GitHub allowlist policy."""
from __future__ import annotations

from cryptography.fernet import Fernet
from fastmcp.server.auth.jwt_issuer import derive_jwt_key
from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.github import GitHubTokenVerifier
from key_value.aio.stores.filetree import (
    FileTreeStore, FileTreeV1CollectionSanitizationStrategy, FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper
from mcp.server.auth.provider import TokenError
from pydantic import AnyHttpUrl, ConfigDict, TypeAdapter

from tripwright.remote.config import RemoteConfig


# A verified GitHub token is trusted this long before GitHub is asked again: without it every MCP request made two
# GitHub API calls, and a slow GitHub turned into 401s. Revoking access on GitHub takes effect within this window.
VERIFY_CACHE_SECONDS = 300


class AllowedGitHub(GitHubTokenVerifier):
    def __init__(self, accounts: dict[str, str], *, http_client=None, cache_seconds: int = VERIFY_CACHE_SECONDS):
        # GitHub App user tokens have no OAuth App scope header. Identity is read
        # from /user; repository access uses a separate per-repository deploy key.
        super().__init__(required_scopes=[], timeout_seconds=4, http_client=http_client,
                         cache_ttl_seconds=cache_seconds or None, max_cache_size=1000)
        self.accounts = accounts

    async def verify_token(self, token: str):
        verified = await super().verify_token(token)
        login = (verified.claims or {}).get("login") if verified else None
        if not isinstance(login, str) or login.casefold() not in self.accounts:
            return None
        return verified


class GitHubLogin(OAuthProxy):
    def __init__(self, *, identity: AllowedGitHub, **kwargs):
        self.identity = identity
        super().__init__(token_verifier=identity, **kwargs)

    async def _extract_upstream_claims(self, idp_tokens: dict):
        """Reject a non-owner before issuing downstream tokens, also on refresh."""
        verified = await self.identity.verify_token(idp_tokens.get("access_token", ""))
        if verified is None:
            raise TokenError("invalid_grant", "This GitHub account is not allowed to use this service")
        return {"login": verified.claims["login"].casefold(), "sub": verified.subject}


def create_provider(config: RemoteConfig, secrets: dict[str, str], *, github_client=None) -> GitHubLogin:
    """Keep protocol, consent, registration, PKCE, JWT and refresh handling upstream."""
    client_id = secrets["TRIPWRIGHT_GITHUB_CLIENT_ID"]
    client_secret = secrets["TRIPWRIGHT_GITHUB_CLIENT_SECRET"]
    storage_dir = config.data_dir / "oauth"
    storage_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    storage_dir.chmod(0o700)
    key = derive_jwt_key(low_entropy_material=secrets.get("TRIPWRIGHT_OAUTH_SIGNING_KEY") or client_secret,
                         salt="tripwright-oauth-storage")
    store = FileTreeStore(
        data_directory=storage_dir,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(storage_dir),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(storage_dir),
    )
    encrypted = FernetEncryptionWrapper(key_value=store, fernet=Fernet(key), raise_on_decryption_error=False)
    origin = TypeAdapter(AnyHttpUrl, config=ConfigDict(url_preserve_empty_path=True)).validate_python(config.public_url)
    return GitHubLogin(
        identity=AllowedGitHub(config.accounts, http_client=github_client),
        upstream_authorization_endpoint="https://github.com/login/oauth/authorize",
        upstream_token_endpoint="https://github.com/login/oauth/access_token",
        upstream_client_id=client_id,
        upstream_client_secret=client_secret,
        token_endpoint_auth_method="client_secret_post",
        base_url=origin,
        issuer_url=origin,
        redirect_path="/auth/callback",
        forward_pkce=True,
        forward_resource=False,
        valid_scopes=["user", "offline_access"],
        client_storage=encrypted,
        jwt_signing_key=secrets.get("TRIPWRIGHT_OAUTH_SIGNING_KEY") or None,
        require_authorization_consent=True,
        fallback_access_token_expiry_seconds=3600,
        fallback_refresh_token_expiry_seconds=30 * 86400,
        fastmcp_access_token_expiry_seconds=3600,
        enable_cimd=True,
    )
