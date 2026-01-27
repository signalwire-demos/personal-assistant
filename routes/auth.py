"""
Authentication Routes

Handles user signup, login, logout, and password management.
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Response, HTTPException, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, EmailStr

from services.auth_service import auth_service
from models.database import SessionLocal, User

router = APIRouter(prefix="/auth", tags=["auth"])

# Session cookie settings
SESSION_COOKIE_NAME = "ethan_session"
SESSION_COOKIE_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


# ==================== Pydantic Models ====================

class SignupRequest(BaseModel):
    email: str
    password: str
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: str
    password: str
    remember: bool = True


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class ForgotPasswordRequest(BaseModel):
    email: str


class ResetPasswordRequest(BaseModel):
    token: str
    password: str


# ==================== Helper Functions ====================

def get_client_ip(request: Request) -> str:
    """Get client IP address from request"""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else ""


def set_session_cookie(response: Response, token: str, remember: bool = True):
    """Set session cookie on response"""
    max_age = SESSION_COOKIE_MAX_AGE if remember else None
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=False,  # Set to True in production with HTTPS
        samesite="lax",
    )


def clear_session_cookie(response: Response):
    """Clear session cookie"""
    response.delete_cookie(key=SESSION_COOKIE_NAME)


def get_session_token(request: Request) -> Optional[str]:
    """Get session token from cookie"""
    return request.cookies.get(SESSION_COOKIE_NAME)


async def get_current_user(request: Request) -> Optional[dict]:
    """Get current authenticated user from session. Returns user dict or None."""
    token = get_session_token(request)
    if not token:
        return None
    return auth_service.get_user_from_session(token)


async def require_auth(request: Request) -> dict:
    """Dependency that requires authentication. Returns user dict."""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


# ==================== Routes ====================

@router.post("/signup")
async def signup(request: Request, data: SignupRequest):
    """Create a new user account"""
    user, error = auth_service.create_user(
        email=data.email,
        password=data.password,
        name=data.name
    )

    if error:
        raise HTTPException(status_code=400, detail=error)

    # Create session for auto-login after signup
    session = auth_service.create_session(
        user_id=user["id"],
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent")
    )

    if not session:
        raise HTTPException(status_code=500, detail="Failed to create session")

    response = JSONResponse(content={
        "success": True,
        "user": user,
        "message": "Account created successfully"
    })

    set_session_cookie(response, session["token"], remember=True)
    return response


@router.post("/login")
async def login(request: Request, data: LoginRequest):
    """Login with email and password"""
    user, error = auth_service.authenticate(
        email=data.email,
        password=data.password
    )

    if error:
        raise HTTPException(status_code=401, detail=error)

    # Create session
    session = auth_service.create_session(
        user_id=user["id"],
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("User-Agent")
    )

    if not session:
        raise HTTPException(status_code=500, detail="Failed to create session")

    response = JSONResponse(content={
        "success": True,
        "user": user,
        "message": "Login successful"
    })

    set_session_cookie(response, session["token"], remember=data.remember)
    return response


@router.post("/logout")
async def logout(request: Request):
    """Logout and invalidate session"""
    token = get_session_token(request)

    if token:
        auth_service.delete_session(token)

    response = JSONResponse(content={
        "success": True,
        "message": "Logged out successfully"
    })

    clear_session_cookie(response)
    return response


@router.get("/me")
async def get_me(user: dict = Depends(require_auth)):
    """Get current authenticated user"""
    return {
        "success": True,
        "user": user
    }


@router.post("/change-password")
async def change_password(
    data: ChangePasswordRequest,
    user: dict = Depends(require_auth)
):
    """Change password for authenticated user"""
    success, error = auth_service.change_password(
        user_id=user["id"],
        current_password=data.current_password,
        new_password=data.new_password
    )

    if not success:
        raise HTTPException(status_code=400, detail=error)

    return {
        "success": True,
        "message": "Password changed successfully"
    }


@router.post("/forgot-password")
async def forgot_password(data: ForgotPasswordRequest):
    """Request password reset email"""
    token, error = auth_service.generate_reset_token(data.email)

    # Always return success to not reveal if email exists
    # In production, send email with reset link
    if token:
        # TODO: Send email with reset link
        print(f"[Auth] Reset token generated (would email): {token}")

    return {
        "success": True,
        "message": "If an account with that email exists, a reset link has been sent."
    }


@router.post("/reset-password")
async def reset_password(data: ResetPasswordRequest):
    """Reset password with token"""
    success, error = auth_service.reset_password(
        token=data.token,
        new_password=data.password
    )

    if not success:
        raise HTTPException(status_code=400, detail=error)

    return {
        "success": True,
        "message": "Password reset successfully. Please login with your new password."
    }


@router.get("/check")
async def check_auth(request: Request):
    """Check if user is authenticated"""
    user = await get_current_user(request)

    if user:
        return {
            "authenticated": True,
            "user": user
        }
    else:
        return {
            "authenticated": False,
            "user": None
        }


@router.post("/logout-all")
async def logout_all(user: dict = Depends(require_auth)):
    """Logout from all sessions"""
    count = auth_service.delete_all_user_sessions(user["id"])

    response = JSONResponse(content={
        "success": True,
        "message": f"Logged out from {count} session(s)"
    })

    clear_session_cookie(response)
    return response
