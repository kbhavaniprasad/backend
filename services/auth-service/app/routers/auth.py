"""
routers/auth.py — Authentication endpoints for the AI Platform Auth Service.

Endpoints
---------
POST /api/v1/auth/register     — Create a new Tenant + owner User account.
POST /api/v1/auth/login        — Authenticate with e-mail + password.
POST /api/v1/auth/refresh      — Exchange a refresh token for a new access token.
POST /api/v1/auth/logout       — Invalidate the supplied refresh token.
GET  /api/v1/auth/me           — Return the currently authenticated user.
POST /api/v1/auth/oauth/google — Handle the Google OAuth2 authorisation-code flow.
"""

import logging
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.dependencies import CurrentUser, get_current_user
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models.user import RefreshToken, RoleEnum, Tenant, User
from app.schemas.auth import (
    GoogleOAuthRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])

# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"


def _build_token_response(user: User, settings: Settings) -> TokenResponse:
    """Create an access + refresh token pair for *user*."""
    access_token = create_access_token(
        data={
            "sub": str(user.id),
            "tenant_id": str(user.tenant_id),
            "role": user.role.value,
        },
        settings=settings,
    )
    refresh_token = create_refresh_token(
        data={"sub": str(user.id)},
        settings=settings,
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.jwt_expire_minutes * 60,
    )


async def _persist_refresh_token(
    user: User,
    refresh_token: str,
    settings: Settings,
    db: AsyncSession,
) -> None:
    """Store the refresh token in the database for later validation."""
    expires_at = datetime.now(tz=timezone.utc) + timedelta(
        days=settings.jwt_refresh_expire_days
    )
    db_token = RefreshToken(
        user_id=user.id,
        token=refresh_token,
        expires_at=expires_at,
    )
    db.add(db_token)
    # Commit is handled by the get_db() dependency on request exit


# --------------------------------------------------------------------------- #
# Routes                                                                       #
# --------------------------------------------------------------------------- #

@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new tenant and owner account",
)
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """
    Create a new **Tenant** and its first **User** (role: ``business_owner``).

    - The tenant slug must be globally unique.
    - The e-mail address must be globally unique across all tenants.
    - Returns a token pair so the caller can proceed without a second login.
    """
    # Check slug uniqueness
    existing_tenant = await db.scalar(
        select(Tenant).where(Tenant.slug == payload.tenant_slug)
    )
    if existing_tenant:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tenant slug '{payload.tenant_slug}' is already taken.",
        )

    # Check e-mail uniqueness
    existing_user = await db.scalar(
        select(User).where(User.email == payload.email)
    )
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this e-mail address already exists.",
        )

    # Create tenant
    tenant = Tenant(name=payload.tenant_name, slug=payload.tenant_slug)
    db.add(tenant)
    await db.flush()  # Flush to get tenant.id before creating user

    # Create owner user
    user = User(
        tenant_id=tenant.id,
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role=RoleEnum.business_owner,
        is_verified=False,
    )
    db.add(user)
    await db.flush()  # Flush to get user.id before creating token

    token_response = _build_token_response(user, settings)
    await _persist_refresh_token(user, token_response.refresh_token, settings, db)

    logger.info(
        "Registered new tenant '%s' with owner user '%s'.",
        tenant.slug,
        user.email,
    )
    return token_response


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Authenticate with e-mail and password",
)
async def login(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """
    Validate credentials and return an access + refresh token pair.

    Deliberately uses the same error message for unknown e-mail and wrong
    password to avoid user enumeration attacks.
    """
    user: User | None = await db.scalar(
        select(User).where(User.email == payload.email)
    )

    invalid_credentials = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid e-mail address or password.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if user is None or user.hashed_password is None:
        raise invalid_credentials

    if not verify_password(payload.password, user.hashed_password):
        raise invalid_credentials

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled. Contact your administrator.",
        )

    # Update last_login timestamp
    user.last_login = datetime.now(tz=timezone.utc)
    db.add(user)

    token_response = _build_token_response(user, settings)
    await _persist_refresh_token(user, token_response.refresh_token, settings, db)

    logger.info("User '%s' logged in successfully.", user.email)
    return token_response


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh access token using a refresh token",
)
async def refresh_token(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """
    Consume a valid refresh token and return a **new** access + refresh pair.

    The old refresh token is deleted (rotation); presenting it again will fail.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired refresh token.",
        headers={"WWW-Authenticate": "Bearer"},
    )

    token_payload = decode_token(payload.refresh_token, settings, is_refresh=True)
    if token_payload is None or token_payload.get("type") != "refresh":
        raise credentials_exception

    # Look up the token in the database (single-use rotation)
    db_token: RefreshToken | None = await db.scalar(
        select(RefreshToken).where(RefreshToken.token == payload.refresh_token)
    )
    if db_token is None:
        raise credentials_exception

    # Check database-level expiry (belt-and-suspenders alongside JWT exp)
    if db_token.expires_at.replace(tzinfo=timezone.utc) < datetime.now(tz=timezone.utc):
        await db.delete(db_token)
        raise credentials_exception

    user_id: str = token_payload["sub"]
    user: User | None = await db.scalar(select(User).where(User.id == user_id))
    if user is None or not user.is_active:
        raise credentials_exception

    # Rotate: delete old token, issue new pair
    await db.delete(db_token)
    token_response = _build_token_response(user, settings)
    await _persist_refresh_token(user, token_response.refresh_token, settings, db)

    return token_response


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate a refresh token (logout)",
)
async def logout(
    payload: RefreshRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    """
    Permanently invalidate the supplied refresh token.

    The access token is short-lived and cannot be server-side revoked here;
    clients must discard it locally.  Clients should call this endpoint when
    the user explicitly logs out.
    """
    db_token: RefreshToken | None = await db.scalar(
        select(RefreshToken).where(RefreshToken.token == payload.refresh_token)
    )
    if db_token is not None:
        await db.delete(db_token)
        logger.info("Refresh token invalidated for user_id=%s.", db_token.user_id)


@router.get(
    "/me",
    response_model=UserResponse,
    summary="Return the currently authenticated user",
)
async def get_me(current_user: CurrentUser) -> UserResponse:
    """
    Return the profile of the user identified by the Bearer token.

    No database query is needed here because ``get_current_user`` already
    fetched the latest user record.
    """
    return UserResponse.model_validate(current_user)


@router.post(
    "/oauth/google",
    response_model=TokenResponse,
    summary="Authenticate via Google OAuth2",
)
async def google_oauth(
    payload: GoogleOAuthRequest,
    db: AsyncSession = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> TokenResponse:
    """
    Handle the server-side Google OAuth2 authorisation-code exchange.

    Flow
    ----
    1. Exchange *code* for Google tokens.
    2. Fetch the user's profile from the Google UserInfo endpoint.
    3. Look up or lazily create the platform user record.
    4. Return a platform token pair.

    The caller must have completed the front-channel OAuth2 consent flow and
    obtained a ``code`` from Google's authorisation endpoint before calling this.
    """
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth2 is not configured on this server.",
        )

    # Exchange authorisation code for Google access token
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": payload.code,
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "redirect_uri": payload.redirect_uri,
                "grant_type": "authorization_code",
            },
        )

    if token_resp.status_code != 200:
        logger.warning(
            "Google token exchange failed: %s", token_resp.text
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to exchange Google authorisation code.",
        )

    google_access_token: str = token_resp.json().get("access_token", "")

    # Fetch Google profile
    async with httpx.AsyncClient(timeout=10.0) as client:
        profile_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {google_access_token}"},
        )

    if profile_resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to retrieve Google user profile.",
        )

    profile = profile_resp.json()
    google_email: str = profile.get("email", "")
    google_name: str = profile.get("name", "")

    if not google_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Google profile does not include an e-mail address.",
        )

    # Locate or create the platform user
    user: User | None = await db.scalar(
        select(User).where(User.email == google_email)
    )

    if user is None:
        # Auto-provision: create a tenant + user for first-time Google logins.
        # In a real system you might redirect to a registration screen instead.
        slug_base = google_email.split("@")[0].lower().replace(".", "-")
        slug = slug_base
        counter = 0
        while await db.scalar(select(Tenant).where(Tenant.slug == slug)):
            counter += 1
            slug = f"{slug_base}-{counter}"

        tenant = Tenant(name=google_name or google_email, slug=slug)
        db.add(tenant)
        await db.flush()

        user = User(
            tenant_id=tenant.id,
            email=google_email,
            full_name=google_name,
            hashed_password=None,  # OAuth-only; no local password
            role=RoleEnum.business_owner,
            is_verified=True,  # Google has already verified the e-mail
        )
        db.add(user)
        await db.flush()

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled.",
        )

    user.last_login = datetime.now(tz=timezone.utc)
    db.add(user)

    token_response = _build_token_response(user, settings)
    await _persist_refresh_token(user, token_response.refresh_token, settings, db)

    logger.info("User '%s' authenticated via Google OAuth2.", user.email)
    return token_response
