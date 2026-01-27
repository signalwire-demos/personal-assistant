"""
SQLAlchemy database models for Ethan Voice AI Agent
"""
from datetime import datetime, time, date, timedelta
from typing import Optional, List
import json
import uuid
import secrets

from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    Text,
    Float,
    Boolean,
    DateTime,
    Date,
    Time,
    JSON,
    ForeignKey,
    Index,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, relationship

import config

# Create engine and session
engine = create_engine(
    config.DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in config.DATABASE_URL else {}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Get database session"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ==================== User & Authentication Models ====================

class User(Base):
    """User accounts for multi-tenant support"""
    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    name = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    is_verified = Column(Boolean, default=False)
    verification_token = Column(String(255), nullable=True)
    reset_token = Column(String(255), nullable=True)
    reset_token_expires = Column(DateTime, nullable=True)
    swml_token = Column(String(64), unique=True, nullable=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_login_at = Column(DateTime, nullable=True)

    # Relationships
    sessions = relationship("UserSession", back_populates="user", cascade="all, delete-orphan")
    google_token = relationship("UserGoogleToken", uselist=False, cascade="all, delete-orphan")
    phone_numbers = relationship("UserPhoneNumber", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "email": self.email,
            "name": self.name,
            "is_active": self.is_active,
            "is_verified": self.is_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }

    @classmethod
    def get_by_email(cls, db, email: str) -> Optional["User"]:
        """Get user by email address"""
        return db.query(cls).filter(cls.email == email.lower()).first()

    @classmethod
    def get_by_id(cls, db, user_id: str) -> Optional["User"]:
        """Get user by ID"""
        return db.query(cls).filter(cls.id == user_id).first()

    @classmethod
    def get_by_swml_token(cls, db, token: str) -> Optional["User"]:
        """Get user by SWML webhook token"""
        if not token:
            return None
        return db.query(cls).filter(cls.swml_token == token, cls.is_active == True).first()

    def regenerate_swml_token(self, db) -> str:
        """Generate a new SWML token for this user"""
        self.swml_token = secrets.token_urlsafe(32)
        db.commit()
        return self.swml_token

    def ensure_swml_token(self, db) -> str:
        """Ensure the user has an SWML token, creating one if needed"""
        if not self.swml_token:
            self.swml_token = secrets.token_urlsafe(32)
            db.commit()
        return self.swml_token


class UserSession(Base):
    """User sessions for cookie-based authentication"""
    __tablename__ = "user_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token = Column(String(255), unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_activity = Column(DateTime, default=datetime.utcnow)
    ip_address = Column(String(50), nullable=True)
    user_agent = Column(String(500), nullable=True)

    # Relationships
    user = relationship("User", back_populates="sessions")

    @classmethod
    def create_session(cls, db, user_id: str, ip_address: str = None,
                       user_agent: str = None, expires_days: int = 30) -> "UserSession":
        """Create a new session for a user"""
        session = cls(
            user_id=user_id,
            token=secrets.token_urlsafe(32),
            expires_at=datetime.utcnow() + timedelta(days=expires_days),
            ip_address=ip_address,
            user_agent=user_agent[:500] if user_agent else None,
        )
        db.add(session)
        db.commit()
        return session

    @classmethod
    def get_by_token(cls, db, token: str) -> Optional["UserSession"]:
        """Get session by token, checking expiration"""
        session = db.query(cls).filter(cls.token == token).first()
        if session and session.expires_at > datetime.utcnow():
            # Update last activity
            session.last_activity = datetime.utcnow()
            db.commit()
            return session
        return None

    @classmethod
    def delete_expired(cls, db):
        """Delete all expired sessions"""
        db.query(cls).filter(cls.expires_at < datetime.utcnow()).delete()
        db.commit()

    def is_valid(self) -> bool:
        """Check if session is still valid"""
        return self.expires_at > datetime.utcnow()

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_activity": self.last_activity.isoformat() if self.last_activity else None,
        }


class UserGoogleToken(Base):
    """Google OAuth tokens stored per user"""
    __tablename__ = "user_google_tokens"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    access_token = Column(Text, nullable=True)
    refresh_token = Column(Text, nullable=True)
    token_uri = Column(String(255), nullable=True)
    client_id = Column(String(255), nullable=True)
    client_secret = Column(String(255), nullable=True)
    scopes = Column(JSON, nullable=True)  # List of OAuth scopes
    expiry = Column(DateTime, nullable=True)
    email = Column(String(255), nullable=True)  # Connected Google account email
    selected_calendar = Column(String(255), nullable=True)  # Selected calendar ID
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    @classmethod
    def get_by_user(cls, db, user_id: str) -> Optional["UserGoogleToken"]:
        """Get Google token for a user"""
        return db.query(cls).filter(cls.user_id == user_id).first()

    @classmethod
    def save_credentials(cls, db, user_id: str, credentials: dict) -> "UserGoogleToken":
        """Save or update Google credentials for a user"""
        token = db.query(cls).filter(cls.user_id == user_id).first()
        if not token:
            token = cls(user_id=user_id)
            db.add(token)

        token.access_token = credentials.get("token")
        token.refresh_token = credentials.get("refresh_token")
        token.token_uri = credentials.get("token_uri")
        token.client_id = credentials.get("client_id")
        token.client_secret = credentials.get("client_secret")
        token.scopes = credentials.get("scopes")
        if credentials.get("expiry"):
            # Handle both datetime objects and ISO strings
            expiry = credentials["expiry"]
            if isinstance(expiry, str):
                token.expiry = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            else:
                token.expiry = expiry
        token.updated_at = datetime.utcnow()

        db.commit()
        db.refresh(token)
        return token

    @classmethod
    def delete_for_user(cls, db, user_id: str) -> bool:
        """Delete Google token for a user (disconnect)"""
        token = db.query(cls).filter(cls.user_id == user_id).first()
        if token:
            db.delete(token)
            db.commit()
            return True
        return False

    def to_credentials_dict(self) -> dict:
        """Convert to dictionary format for Google OAuth library"""
        return {
            "token": self.access_token,
            "refresh_token": self.refresh_token,
            "token_uri": self.token_uri,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scopes": self.scopes or [],
            "expiry": self.expiry.isoformat() if self.expiry else None,
        }

    def is_expired(self) -> bool:
        """Check if the access token is expired"""
        if not self.expiry:
            return True
        return datetime.utcnow() > self.expiry


class UserPhoneNumber(Base):
    """Phone numbers associated with users for call routing"""
    __tablename__ = "user_phone_numbers"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    phone_number = Column(String(20), unique=True, nullable=False, index=True)  # E.164 format
    friendly_name = Column(String(100), nullable=True)  # User-friendly label
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    @classmethod
    def get_user_by_phone(cls, db, phone_number: str) -> Optional["User"]:
        """Get user by phone number (called number)"""
        import re
        # Normalize phone number to digits only for comparison
        digits = re.sub(r'\D', '', phone_number)
        # Keep last 10 digits for US numbers
        if len(digits) > 10:
            digits = digits[-10:]

        # Find by exact match or normalized match
        mapping = db.query(cls).filter(
            cls.is_active == True
        ).all()

        for m in mapping:
            m_digits = re.sub(r'\D', '', m.phone_number)
            if len(m_digits) > 10:
                m_digits = m_digits[-10:]
            if m_digits == digits:
                return db.query(User).filter(User.id == m.user_id).first()

        return None

    @classmethod
    def get_for_user(cls, db, user_id: str) -> List["UserPhoneNumber"]:
        """Get all phone numbers for a user"""
        return db.query(cls).filter(cls.user_id == user_id).all()

    @classmethod
    def add_for_user(cls, db, user_id: str, phone_number: str, friendly_name: str = None) -> "UserPhoneNumber":
        """Add a phone number for a user"""
        import re
        # Normalize to E.164 format
        digits = re.sub(r'\D', '', phone_number)
        if len(digits) == 10:
            digits = "1" + digits  # Add US country code
        normalized = "+" + digits

        mapping = cls(
            user_id=user_id,
            phone_number=normalized,
            friendly_name=friendly_name,
        )
        db.add(mapping)
        db.commit()
        db.refresh(mapping)
        return mapping

    @classmethod
    def remove_for_user(cls, db, user_id: str, phone_number_id: str) -> bool:
        """Remove a phone number mapping"""
        mapping = db.query(cls).filter(
            cls.id == phone_number_id,
            cls.user_id == user_id
        ).first()
        if mapping:
            db.delete(mapping)
            db.commit()
            return True
        return False

    def to_dict(self):
        return {
            "id": self.id,
            "phone_number": self.phone_number,
            "friendly_name": self.friendly_name,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "email": self.email,
            "selected_calendar": self.selected_calendar,
            "is_expired": self.is_expired(),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Config(Base):
    """Business configuration key-value store (per user)"""
    __tablename__ = "config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    key = Column(String(255), nullable=False)
    value = Column(JSON)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Unique constraint on user_id + key
    __table_args__ = (
        Index('idx_config_user_key', 'user_id', 'key', unique=True),
    )

    @classmethod
    def get(cls, db, key: str, default=None, user_id: str = None):
        """Get a config value for a user"""
        query = db.query(cls).filter(cls.key == key)
        if user_id:
            query = query.filter(cls.user_id == user_id)
        else:
            query = query.filter(cls.user_id.is_(None))
        record = query.first()
        return record.value if record else default

    @classmethod
    def set(cls, db, key: str, value, user_id: str = None):
        """Set a config value for a user"""
        query = db.query(cls).filter(cls.key == key)
        if user_id:
            query = query.filter(cls.user_id == user_id)
        else:
            query = query.filter(cls.user_id.is_(None))
        record = query.first()
        if record:
            record.value = value
            record.updated_at = datetime.utcnow()
        else:
            record = cls(key=key, value=value, user_id=user_id)
            db.add(record)
        db.commit()
        return record

    @classmethod
    def get_all(cls, db, user_id: str = None) -> dict:
        """Get all config values for a user as a dictionary"""
        query = db.query(cls)
        if user_id:
            query = query.filter(cls.user_id == user_id)
        else:
            query = query.filter(cls.user_id.is_(None))
        records = query.all()
        return {r.key: r.value for r in records}


class BusinessHours(Base):
    """Business hours per day of week (per user)"""
    __tablename__ = "business_hours"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    day_of_week = Column(Integer, nullable=False)  # 0=Monday, 6=Sunday
    open_time = Column(Time, nullable=True)
    close_time = Column(Time, nullable=True)
    is_closed = Column(Boolean, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "day_of_week": self.day_of_week,
            "open_time": self.open_time.isoformat() if self.open_time else None,
            "close_time": self.close_time.isoformat() if self.close_time else None,
            "is_closed": self.is_closed,
        }


class Holiday(Base):
    """Holiday closures (per user)"""
    __tablename__ = "holidays"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    date = Column(Date, nullable=False)
    name = Column(String(255), nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "date": self.date.isoformat(),
            "name": self.name,
        }


class AppointmentType(Base):
    """Types of appointments that can be booked (per user)"""
    __tablename__ = "appointment_types"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    duration_minutes = Column(Integer, nullable=False, default=60)
    buffer_minutes = Column(Integer, nullable=False, default=15)
    price = Column(Float, nullable=True)
    description = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "duration_minutes": self.duration_minutes,
            "buffer_minutes": self.buffer_minutes,
            "price": self.price,
            "description": self.description,
            "is_active": self.is_active,
        }


class Service(Base):
    """Services offered by the business (per user)"""
    __tablename__ = "services"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    price = Column(String(100), nullable=True)  # Can be "150/hour" or "Contact for quote"
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "price": self.price,
            "is_active": self.is_active,
        }


class FAQ(Base):
    """Frequently asked questions (per user)"""
    __tablename__ = "faqs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    question = Column(Text, nullable=False)
    answer = Column(Text, nullable=False)
    keywords = Column(JSON, default=list)  # List of keywords for matching
    category = Column(String(100), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "question": self.question,
            "answer": self.answer,
            "keywords": self.keywords,
            "category": self.category,
            "is_active": self.is_active,
        }


class Message(Base):
    """Messages left by callers (per user)"""
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    caller_name = Column(String(255), nullable=True)
    caller_phone = Column(String(50), nullable=True)
    message = Column(Text, nullable=False)
    urgency = Column(String(20), default="normal")  # normal, urgent
    status = Column(String(20), default="unread")   # unread, read, responded
    call_id = Column(String(255), nullable=True)    # SignalWire call ID
    created_at = Column(DateTime, default=datetime.utcnow)
    read_at = Column(DateTime, nullable=True)
    responded_at = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "caller_name": self.caller_name,
            "caller_phone": self.caller_phone,
            "message": self.message,
            "urgency": self.urgency,
            "status": self.status,
            "call_id": self.call_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "responded_at": self.responded_at.isoformat() if self.responded_at else None,
        }


class CallLog(Base):
    """Call history and recordings (per user)"""
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    call_id = Column(String(255), nullable=False, unique=True)
    caller_number = Column(String(50), nullable=True)
    caller_name = Column(String(255), nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    outcome = Column(String(50), nullable=True)  # appointment, message, info, transfer, hangup
    recording_url = Column(String(500), nullable=True)
    transcript = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)  # AI-generated summary
    created_at = Column(DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "call_id": self.call_id,
            "caller_number": self.caller_number,
            "caller_name": self.caller_name,
            "duration_seconds": self.duration_seconds,
            "outcome": self.outcome,
            "recording_url": self.recording_url,
            "transcript": self.transcript,
            "summary": self.summary,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class KnowledgeDocument(Base):
    """Knowledge base documents for RAG (per user)"""
    __tablename__ = "knowledge_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    title = Column(String(255), nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(100), default="general")  # policies, products, services, general
    tags = Column(JSON, default=list)
    file_path = Column(String(500), nullable=True)  # Path to indexed file
    is_indexed = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "content": self.content[:200] + "..." if self.content and len(self.content) > 200 else self.content,
            "full_content": self.content,
            "category": self.category,
            "tags": self.tags,
            "file_path": self.file_path,
            "is_indexed": self.is_indexed,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(bind=engine)

    # Run migrations for existing tables
    _run_migrations()

    print("Database tables created successfully")


def _run_migrations():
    """Run simple migrations for schema changes to existing tables"""
    from sqlalchemy import text, inspect

    with engine.connect() as conn:
        inspector = inspect(engine)

        # Check if users table exists
        if 'users' in inspector.get_table_names():
            # Get existing columns
            columns = [col['name'] for col in inspector.get_columns('users')]

            # Add swml_token column if it doesn't exist
            if 'swml_token' not in columns:
                print("Migration: Adding swml_token column to users table...")
                # SQLite doesn't support adding UNIQUE columns directly,
                # so we add the column first, then create a unique index
                conn.execute(text(
                    "ALTER TABLE users ADD COLUMN swml_token VARCHAR(64)"
                ))
                conn.commit()

            # Check if the unique index exists
            indexes = inspector.get_indexes('users')
            index_names = [idx['name'] for idx in indexes]
            if 'ix_users_swml_token' not in index_names:
                print("Migration: Creating unique index on swml_token...")
                conn.execute(text(
                    "CREATE UNIQUE INDEX ix_users_swml_token ON users (swml_token)"
                ))
                conn.commit()
                print("Migration: swml_token column and index added successfully")


def init_user_data(user_id: str, business_name: str = None):
    """
    Initialize default data for a new user.
    Called after user signup to set up their default configuration.
    """
    db = SessionLocal()
    try:
        # Check if user already has data
        existing_hours = db.query(BusinessHours).filter(BusinessHours.user_id == user_id).first()
        if existing_hours:
            print(f"User {user_id} already has data initialized")
            return

        # Initialize business hours
        default_hours = [
            # Monday - Friday: 9am - 5pm
            BusinessHours(user_id=user_id, day_of_week=0, open_time=time(9, 0), close_time=time(17, 0), is_closed=False),
            BusinessHours(user_id=user_id, day_of_week=1, open_time=time(9, 0), close_time=time(17, 0), is_closed=False),
            BusinessHours(user_id=user_id, day_of_week=2, open_time=time(9, 0), close_time=time(17, 0), is_closed=False),
            BusinessHours(user_id=user_id, day_of_week=3, open_time=time(9, 0), close_time=time(17, 0), is_closed=False),
            BusinessHours(user_id=user_id, day_of_week=4, open_time=time(9, 0), close_time=time(17, 0), is_closed=False),
            # Saturday: 10am - 2pm
            BusinessHours(user_id=user_id, day_of_week=5, open_time=time(10, 0), close_time=time(14, 0), is_closed=False),
            # Sunday: Closed
            BusinessHours(user_id=user_id, day_of_week=6, is_closed=True),
        ]
        db.add_all(default_hours)

        # Initialize config
        biz_name = business_name or config.DEFAULT_BUSINESS_NAME
        Config.set(db, "business_name", biz_name, user_id=user_id)
        Config.set(db, "timezone", config.DEFAULT_TIMEZONE, user_id=user_id)
        Config.set(db, "owner_email", "", user_id=user_id)
        Config.set(db, "owner_phone", "", user_id=user_id)
        Config.set(db, "agent_name", config.AGENT_NAME, user_id=user_id)
        Config.set(db, "greeting_message",
            "Hello! Thank you for calling {business_name}. My name is {agent_name} "
            "and I'm here to help you. How can I assist you today?",
            user_id=user_id
        )
        Config.set(db, "after_hours_message",
            "Thank you for calling {business_name}. We're currently closed. "
            "Our business hours are {business_hours}. Would you like to leave a message "
            "or schedule a callback?",
            user_id=user_id
        )

        # Initialize default appointment types
        default_types = [
            AppointmentType(
                user_id=user_id,
                name="Consultation",
                duration_minutes=60,
                buffer_minutes=15,
                price=150.00,
                description="Initial consultation for new clients"
            ),
            AppointmentType(
                user_id=user_id,
                name="Follow-up",
                duration_minutes=30,
                buffer_minutes=10,
                price=75.00,
                description="Follow-up meeting for existing clients"
            ),
            AppointmentType(
                user_id=user_id,
                name="Quick Call",
                duration_minutes=15,
                buffer_minutes=5,
                price=0.00,
                description="Brief phone call for quick questions"
            ),
        ]
        db.add_all(default_types)

        # Initialize default FAQs
        default_faqs = [
            FAQ(
                user_id=user_id,
                question="What are your business hours?",
                answer="We are open Monday through Friday from 9 AM to 5 PM, "
                       "Saturday from 10 AM to 2 PM, and closed on Sunday.",
                keywords=["hours", "open", "closed", "when"],
                category="general"
            ),
            FAQ(
                user_id=user_id,
                question="How do I schedule an appointment?",
                answer="You can schedule an appointment by speaking with me now, "
                       "or by calling during business hours.",
                keywords=["appointment", "schedule", "book", "meeting"],
                category="appointments"
            ),
            FAQ(
                user_id=user_id,
                question="What services do you offer?",
                answer="We offer various consulting services. I can provide more details "
                       "or connect you with someone who can help.",
                keywords=["services", "offer", "provide", "help"],
                category="services"
            ),
        ]
        db.add_all(default_faqs)

        db.commit()
        print(f"Default data initialized for user {user_id}")

    except Exception as e:
        db.rollback()
        print(f"Error initializing user data: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    init_db()
