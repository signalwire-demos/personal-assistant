"""Services for Ethan Voice AI Agent"""
from services.google_auth import google_auth, GoogleAuthService
from services.calendar_service import calendar_service, CalendarService
from services.email_service import email_service, EmailService
from services.knowledge_service import knowledge_service, KnowledgeService
from services.contacts_service import contacts_service, ContactsService
from services.auth_service import auth_service, AuthService

__all__ = [
    "google_auth",
    "GoogleAuthService",
    "calendar_service",
    "CalendarService",
    "email_service",
    "EmailService",
    "knowledge_service",
    "KnowledgeService",
    "contacts_service",
    "ContactsService",
    "auth_service",
    "AuthService",
]
