"""
Authentication Service

Handles user registration, login, password hashing, and session management.
"""
import re
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple

import bcrypt

from models.database import SessionLocal, User, UserSession, init_user_data


class AuthService:
    """Authentication service for user management"""

    # Password requirements
    MIN_PASSWORD_LENGTH = 8

    def hash_password(self, password: str) -> str:
        """Hash a password using bcrypt"""
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode('utf-8'), salt).decode('utf-8')

    def verify_password(self, password: str, password_hash: str) -> bool:
        """Verify a password against its hash"""
        try:
            return bcrypt.checkpw(password.encode('utf-8'), password_hash.encode('utf-8'))
        except Exception:
            return False

    def validate_email(self, email: str) -> Tuple[bool, str]:
        """Validate email format"""
        if not email:
            return False, "Email is required"

        # Basic email regex
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(pattern, email):
            return False, "Invalid email format"

        return True, ""

    def validate_password(self, password: str) -> Tuple[bool, str]:
        """Validate password strength"""
        if not password:
            return False, "Password is required"

        if len(password) < self.MIN_PASSWORD_LENGTH:
            return False, f"Password must be at least {self.MIN_PASSWORD_LENGTH} characters"

        # Check for at least one letter and one number
        if not re.search(r'[a-zA-Z]', password):
            return False, "Password must contain at least one letter"

        if not re.search(r'\d', password):
            return False, "Password must contain at least one number"

        return True, ""

    def create_user(self, email: str, password: str, name: str = None) -> Tuple[Optional[dict], str]:
        """
        Create a new user account.

        Returns:
            Tuple of (user dict or None, error message)
        """
        # Validate email
        valid, error = self.validate_email(email)
        if not valid:
            return None, error

        # Validate password
        valid, error = self.validate_password(password)
        if not valid:
            return None, error

        email = email.lower().strip()

        db = SessionLocal()
        try:
            # Check if email already exists
            existing = User.get_by_email(db, email)
            if existing:
                return None, "An account with this email already exists"

            # Create user
            user = User(
                email=email,
                password_hash=self.hash_password(password),
                name=name.strip() if name else None,
                is_active=True,
                is_verified=False,  # TODO: Implement email verification
                verification_token=secrets.token_urlsafe(32),
            )
            db.add(user)
            db.commit()
            db.refresh(user)

            # Initialize default data for the new user
            business_name = name if name else email.split('@')[0]
            init_user_data(user.id, business_name)

            # Extract user data before closing session
            user_data = user.to_dict()

            print(f"[Auth] Created user: {email}")
            return user_data, ""

        except Exception as e:
            db.rollback()
            print(f"[Auth] Error creating user: {e}")
            return None, "Failed to create account"
        finally:
            db.close()

    def authenticate(self, email: str, password: str) -> Tuple[Optional[dict], str]:
        """
        Authenticate a user with email and password.

        Returns:
            Tuple of (user dict or None, error message)
        """
        if not email or not password:
            return None, "Email and password are required"

        email = email.lower().strip()

        db = SessionLocal()
        try:
            user = User.get_by_email(db, email)

            if not user:
                return None, "Invalid email or password"

            if not user.is_active:
                return None, "Account is disabled"

            if not self.verify_password(password, user.password_hash):
                return None, "Invalid email or password"

            # Update last login
            user.last_login_at = datetime.utcnow()
            db.commit()

            # Extract user data before closing session
            user_data = user.to_dict()

            print(f"[Auth] User authenticated: {email}")
            return user_data, ""

        except Exception as e:
            print(f"[Auth] Error authenticating user: {e}")
            return None, "Authentication failed"
        finally:
            db.close()

    def create_session(self, user_id: str, ip_address: str = None,
                       user_agent: str = None) -> Optional[dict]:
        """Create a new session for a user. Returns dict with token and expiry."""
        db = SessionLocal()
        try:
            session = UserSession.create_session(
                db, user_id,
                ip_address=ip_address,
                user_agent=user_agent
            )
            # Extract values before closing the session
            result = {
                "token": session.token,
                "expires_at": session.expires_at,
                "user_id": session.user_id,
            }
            print(f"[Auth] Session created for user: {user_id}")
            return result
        except Exception as e:
            print(f"[Auth] Error creating session: {e}")
            return None
        finally:
            db.close()

    def get_session(self, token: str) -> Optional[UserSession]:
        """Get a session by token"""
        if not token:
            return None

        db = SessionLocal()
        try:
            return UserSession.get_by_token(db, token)
        finally:
            db.close()

    def get_user_from_session(self, token: str) -> Optional[dict]:
        """Get user from session token. Returns user dict or None."""
        if not token:
            return None

        db = SessionLocal()
        try:
            session = UserSession.get_by_token(db, token)
            if session:
                user = User.get_by_id(db, session.user_id)
                if user:
                    return user.to_dict()
            return None
        finally:
            db.close()

    def delete_session(self, token: str) -> bool:
        """Delete a session (logout)"""
        if not token:
            return False

        db = SessionLocal()
        try:
            session = db.query(UserSession).filter(UserSession.token == token).first()
            if session:
                db.delete(session)
                db.commit()
                print(f"[Auth] Session deleted")
                return True
            return False
        except Exception as e:
            print(f"[Auth] Error deleting session: {e}")
            return False
        finally:
            db.close()

    def delete_all_user_sessions(self, user_id: str) -> int:
        """Delete all sessions for a user (logout everywhere)"""
        db = SessionLocal()
        try:
            count = db.query(UserSession).filter(UserSession.user_id == user_id).delete()
            db.commit()
            print(f"[Auth] Deleted {count} sessions for user: {user_id}")
            return count
        except Exception as e:
            print(f"[Auth] Error deleting sessions: {e}")
            return 0
        finally:
            db.close()

    def generate_reset_token(self, email: str) -> Tuple[Optional[str], str]:
        """
        Generate a password reset token.

        Returns:
            Tuple of (token or None, error message)
        """
        email = email.lower().strip()

        db = SessionLocal()
        try:
            user = User.get_by_email(db, email)
            if not user:
                # Don't reveal if email exists
                return None, ""

            # Generate reset token
            token = secrets.token_urlsafe(32)
            user.reset_token = token
            user.reset_token_expires = datetime.utcnow() + timedelta(hours=1)
            db.commit()

            print(f"[Auth] Reset token generated for: {email}")
            return token, ""

        except Exception as e:
            print(f"[Auth] Error generating reset token: {e}")
            return None, "Failed to generate reset token"
        finally:
            db.close()

    def reset_password(self, token: str, new_password: str) -> Tuple[bool, str]:
        """
        Reset password using a reset token.

        Returns:
            Tuple of (success, error message)
        """
        if not token:
            return False, "Invalid reset token"

        valid, error = self.validate_password(new_password)
        if not valid:
            return False, error

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.reset_token == token).first()

            if not user:
                return False, "Invalid reset token"

            if user.reset_token_expires < datetime.utcnow():
                return False, "Reset token has expired"

            # Update password
            user.password_hash = self.hash_password(new_password)
            user.reset_token = None
            user.reset_token_expires = None
            db.commit()

            # Invalidate all sessions
            self.delete_all_user_sessions(user.id)

            print(f"[Auth] Password reset for: {user.email}")
            return True, ""

        except Exception as e:
            print(f"[Auth] Error resetting password: {e}")
            return False, "Failed to reset password"
        finally:
            db.close()

    def change_password(self, user_id: str, current_password: str,
                        new_password: str) -> Tuple[bool, str]:
        """
        Change password for authenticated user.

        Returns:
            Tuple of (success, error message)
        """
        valid, error = self.validate_password(new_password)
        if not valid:
            return False, error

        db = SessionLocal()
        try:
            user = User.get_by_id(db, user_id)
            if not user:
                return False, "User not found"

            if not self.verify_password(current_password, user.password_hash):
                return False, "Current password is incorrect"

            user.password_hash = self.hash_password(new_password)
            db.commit()

            print(f"[Auth] Password changed for: {user.email}")
            return True, ""

        except Exception as e:
            print(f"[Auth] Error changing password: {e}")
            return False, "Failed to change password"
        finally:
            db.close()

    def cleanup_expired_sessions(self):
        """Remove all expired sessions from database"""
        db = SessionLocal()
        try:
            UserSession.delete_expired(db)
            print("[Auth] Expired sessions cleaned up")
        finally:
            db.close()


# Singleton instance
auth_service = AuthService()
