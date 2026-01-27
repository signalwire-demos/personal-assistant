"""
Google OAuth Service

Handles OAuth2 authentication flow for Google APIs (Gmail, Calendar).
Stores credentials per-user in the database for multi-tenant support.
"""
import os
import json
from datetime import datetime
from typing import Optional

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from google.auth.transport.requests import Request

import config
from models.database import SessionLocal, UserGoogleToken

# OAuth2 scopes required for Gmail, Calendar, and Contacts
SCOPES = [
    'openid',
    'https://www.googleapis.com/auth/userinfo.email',
    # Gmail
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.compose',
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/gmail.modify',  # For delete, archive, mark read
    # Calendar
    'https://www.googleapis.com/auth/calendar',
    'https://www.googleapis.com/auth/calendar.events',
    # Contacts
    'https://www.googleapis.com/auth/contacts.readonly',
]


class GoogleAuthService:
    """Handles Google OAuth2 authentication (per-user)"""

    def __init__(self):
        self.client_id = config.GOOGLE_CLIENT_ID
        self.client_secret = config.GOOGLE_CLIENT_SECRET
        self.redirect_uri = config.GOOGLE_REDIRECT_URI

    def is_configured(self) -> bool:
        """Check if Google OAuth is configured"""
        return bool(self.client_id and self.client_secret)

    def get_credentials(self, user_id: str = None) -> Optional[Credentials]:
        """Get stored credentials for a user, refreshing if needed"""
        if not user_id:
            return None  # No user context, not connected

        db = SessionLocal()
        try:
            token = UserGoogleToken.get_by_user(db, user_id)
            if not token or not token.access_token:
                return None

            creds = Credentials(
                token=token.access_token,
                refresh_token=token.refresh_token,
                token_uri=token.token_uri or "https://oauth2.googleapis.com/token",
                client_id=self.client_id,
                client_secret=self.client_secret,
                scopes=token.scopes or SCOPES,
            )

            # Set expiry if available
            if token.expiry:
                creds.expiry = token.expiry

            # Refresh if expired
            if creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    self._save_credentials(user_id, creds)
                except Exception as e:
                    print(f"[GoogleAuth] Error refreshing credentials for user {user_id}: {e}")
                    return None

            return creds

        finally:
            db.close()

    def _save_credentials(self, user_id: str, creds: Credentials):
        """Save credentials to database for a user"""
        db = SessionLocal()
        try:
            creds_data = {
                "token": creds.token,
                "refresh_token": creds.refresh_token,
                "token_uri": creds.token_uri,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "scopes": list(creds.scopes) if creds.scopes else SCOPES,
                "expiry": creds.expiry,
            }
            UserGoogleToken.save_credentials(db, user_id, creds_data)
            print(f"[GoogleAuth] Credentials saved for user {user_id}")
        finally:
            db.close()

    def get_authorization_url(self, state: str = None) -> str:
        """Get the OAuth2 authorization URL"""
        if not self.is_configured():
            raise ValueError("Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")

        flow = Flow.from_client_config(
            {
                "web": {
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [self.redirect_uri],
                }
            },
            scopes=SCOPES,
            redirect_uri=self.redirect_uri,
        )

        authorization_url, state = flow.authorization_url(
            access_type='offline',
            include_granted_scopes='true',
            prompt='consent',
            state=state,
        )

        return authorization_url

    def handle_callback(self, user_id: str, authorization_response: str) -> bool:
        """Handle OAuth2 callback and store credentials for user"""
        if not self.is_configured():
            raise ValueError("Google OAuth not configured")

        try:
            flow = Flow.from_client_config(
                {
                    "web": {
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                        "token_uri": "https://oauth2.googleapis.com/token",
                        "redirect_uris": [self.redirect_uri],
                    }
                },
                scopes=SCOPES,
                redirect_uri=self.redirect_uri,
            )

            flow.fetch_token(authorization_response=authorization_response)
            creds = flow.credentials

            self._save_credentials(user_id, creds)

            # Store connected email
            self._store_connected_email(user_id, creds)

            print(f"[GoogleAuth] OAuth completed for user {user_id}")
            return True

        except Exception as e:
            print(f"[GoogleAuth] Error handling OAuth callback: {e}")
            return False

    def _store_connected_email(self, user_id: str, creds: Credentials):
        """Get and store the connected Google account email"""
        try:
            from googleapiclient.discovery import build
            email = None

            # Try oauth2 userinfo first
            try:
                service = build('oauth2', 'v2', credentials=creds)
                user_info = service.userinfo().get().execute()
                email = user_info.get('email')
            except Exception:
                pass

            # Fallback: get email from primary calendar
            if not email:
                try:
                    calendar_service = build('calendar', 'v3', credentials=creds)
                    calendar = calendar_service.calendars().get(calendarId='primary').execute()
                    email = calendar.get('id')  # Primary calendar ID is usually the email
                except Exception:
                    pass

            if email:
                db = SessionLocal()
                try:
                    token = UserGoogleToken.get_by_user(db, user_id)
                    if token:
                        token.email = email
                        db.commit()
                        print(f"[GoogleAuth] Connected email stored: {email}")
                finally:
                    db.close()

        except Exception as e:
            print(f"[GoogleAuth] Error getting user email: {e}")

    def disconnect(self, user_id: str):
        """Remove stored credentials for a user"""
        db = SessionLocal()
        try:
            UserGoogleToken.delete_for_user(db, user_id)
            print(f"[GoogleAuth] Disconnected Google for user {user_id}")
        finally:
            db.close()

    def get_connected_email(self, user_id: str = None) -> Optional[str]:
        """Get the connected Google account email"""
        if not user_id:
            return None  # No user context

        db = SessionLocal()
        try:
            token = UserGoogleToken.get_by_user(db, user_id)
            if not token:
                return None

            if not token.email and self.is_connected(user_id):
                # Try to fetch email if not stored
                creds = self.get_credentials(user_id)
                if creds:
                    self._store_connected_email(user_id, creds)
                    db.refresh(token)

            return token.email
        finally:
            db.close()

    def is_connected(self, user_id: str = None) -> bool:
        """Check if Google account is connected for user"""
        if not user_id:
            return False  # No user context, not connected
        creds = self.get_credentials(user_id)
        return creds is not None and creds.valid

    def list_calendars(self, user_id: str = None) -> list:
        """List available Google Calendars for user"""
        if not user_id:
            return []  # No user context
        creds = self.get_credentials(user_id)
        if not creds:
            return []

        try:
            from googleapiclient.discovery import build
            service = build('calendar', 'v3', credentials=creds)
            calendars_result = service.calendarList().list().execute()
            calendars = calendars_result.get('items', [])

            return [
                {
                    "id": cal.get('id'),
                    "name": cal.get('summary'),
                    "primary": cal.get('primary', False),
                    "access_role": cal.get('accessRole'),
                }
                for cal in calendars
            ]
        except Exception as e:
            print(f"[GoogleAuth] Error listing calendars: {e}")
            return []

    def get_selected_calendar(self, user_id: str = None) -> str:
        """Get the selected calendar ID for user"""
        if not user_id:
            return "primary"  # Default when no user context

        db = SessionLocal()
        try:
            token = UserGoogleToken.get_by_user(db, user_id)
            if token and token.selected_calendar:
                return token.selected_calendar
            return "primary"
        finally:
            db.close()

    def set_selected_calendar(self, user_id: str = None, calendar_id: str = None):
        """Set the selected calendar ID for user"""
        if not user_id or not calendar_id:
            return  # No user context

        db = SessionLocal()
        try:
            token = UserGoogleToken.get_by_user(db, user_id)
            if token:
                token.selected_calendar = calendar_id
                db.commit()
                print(f"[GoogleAuth] Calendar set to {calendar_id} for user {user_id}")
        finally:
            db.close()


# Singleton instance
google_auth = GoogleAuthService()
