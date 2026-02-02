#!/usr/bin/env python3
"""
Personal Assistant AI Agent - Main Application Entry Point

This module sets up the FastAPI application with:
- SignalWire Voice Agent (EthanAgent)
- Admin Panel routes
- Static file serving
- Health checks
"""
import os
import sys
from pathlib import Path
from contextlib import asynccontextmanager

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
import uvicorn

import config
from models.database import init_db, SessionLocal, get_db
from models.database import Config as ConfigModel, Message, CallLog, FAQ, Service, AppointmentType, BusinessHours, Holiday, User
from agent import create_agent, EthanAgent
from routes.auth import router as auth_router, get_current_user, require_auth, get_session_token


# Global agent instance
agent: EthanAgent = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events"""
    global agent

    # Startup
    print("Initializing Personal Assistant AI Agent...")

    # Initialize database
    print("Initializing database...")
    init_db()

    # Create agent
    print("Creating agent...")
    agent = create_agent()

    # Mount agent routes at internal path (accessed via token-validated routes)
    print("Mounting agent routes...")
    agent_app = agent.get_app()
    app.mount("/_internal_agent", agent_app)

    print(f"Agent ready at /swml/{{token}}/")
    print(f"Admin panel at /admin")
    print(f"Health check at /health")

    # Mount agent testing module if enabled
    if os.environ.get("ENABLE_TESTING", "").lower() in ("1", "true", "yes"):
        from signalwire_agent_tester import TestSuite
        TestSuite(
            app=app,
            signalwire_space=config.SIGNALWIRE_SPACE_NAME,
            signalwire_project_id=config.SIGNALWIRE_PROJECT_ID,
            signalwire_token=config.SIGNALWIRE_TOKEN,
            public_url=config.SWML_PROXY_URL_BASE,
            from_number=os.environ.get("TESTER_FROM_NUMBER", ""),
            target_number=os.environ.get("TARGET_NUMBER", ""),
            tap_uri=os.environ.get("TESTER_TAP_URI", ""),
            personas_dir="tests/personas",
            goals_dir="tests/goals",
            results_dir="tests/results",
            basic_auth=config.get_basic_auth(),
        )
        print(f"Agent testing at /testing")

    yield

    # Shutdown
    print("Shutting down Personal Assistant AI Agent...")


# Initialize FastAPI app
app = FastAPI(
    title="Personal Assistant AI Agent",
    description="Voice AI Assistant with appointment scheduling, email, and knowledge base",
    version="1.0.0",
    lifespan=lifespan,
)

# Initialize templates
templates = Jinja2Templates(directory="templates")

# Include auth routes
app.include_router(auth_router)


# ==================== Auth Middleware ====================

@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Middleware to protect admin routes"""
    path = request.url.path

    # Protect admin pages and testing UI (HTML pages)
    if path.startswith("/admin") or path == "/testing" or path == "/testing/":
        user = await get_current_user(request)
        if not user:
            return RedirectResponse(url="/login", status_code=302)
        # Store user in request state for use in templates
        request.state.user = user

    # Protect API routes (except auth routes which handle their own auth)
    # /testing/_tester and /testing/_webhook stay unprotected (SignalWire needs access)
    elif path.startswith("/api/") or path.startswith("/testing/_api"):
        user = await get_current_user(request)
        if not user:
            return JSONResponse(
                status_code=401,
                content={"detail": "Not authenticated"}
            )
        request.state.user = user

    response = await call_next(request)
    return response


# ==================== Health Checks ====================

@app.get("/health")
async def health_check():
    """Kubernetes liveness probe"""
    return {"status": "healthy", "service": "ethan-voice"}


@app.get("/ready")
async def readiness_check():
    """Kubernetes readiness probe"""
    global agent
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")
    return {"status": "ready", "agent": "ethan-agent"}


# ==================== SWML Token-Validated Routes ====================

from starlette.routing import Mount
from starlette.responses import Response
import httpx

@app.api_route("/swml/{swml_token}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def swml_token_route(request: Request, swml_token: str, path: str = ""):
    """
    Token-validated SWML webhook endpoint.
    Validates the token, looks up the user, and forwards to the agent.
    """
    print(f"[SWML Route] Token: {swml_token[:8]}..., path: {path}")
    db = SessionLocal()
    try:
        # Look up user by SWML token
        user = User.get_by_swml_token(db, swml_token)
        print(f"[SWML Route] User lookup: {user.email if user else 'NOT FOUND'}")
        if not user:
            raise HTTPException(status_code=401, detail="Invalid SWML token")

        # Get the agent app
        global agent
        if agent is None:
            raise HTTPException(status_code=503, detail="Agent not initialized")

        agent_app = agent.get_app()

        # Build the forwarded path
        forward_path = f"/{path}" if path else "/"

        # Add user_id to query params
        query_params = dict(request.query_params)
        query_params["user_id"] = user.id

        # Build new query string
        from urllib.parse import urlencode
        query_string = urlencode(query_params)

        # Create a new scope for the agent app
        scope = dict(request.scope)
        scope["path"] = forward_path
        scope["query_string"] = query_string.encode()
        scope["root_path"] = ""

        # Create a new request for the agent
        from starlette.requests import Request as StarletteRequest

        # Call the agent app directly
        response_started = False
        response_body = []
        response_status = 200
        response_headers = []

        async def receive():
            return await request.receive()

        async def send(message):
            nonlocal response_started, response_status, response_headers
            if message["type"] == "http.response.start":
                response_started = True
                response_status = message["status"]
                response_headers = message.get("headers", [])
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    response_body.append(body)

        await agent_app(scope, receive, send)

        # Build response
        body = b"".join(response_body)
        headers = {k.decode(): v.decode() for k, v in response_headers}

        return Response(content=body, status_code=response_status, headers=headers)

    finally:
        db.close()


@app.api_route("/swml/{swml_token}/", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def swml_token_route_root(request: Request, swml_token: str):
    """Token-validated SWML webhook endpoint (root path)."""
    return await swml_token_route(request, swml_token, "")


@app.api_route("/swaig/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@app.api_route("/swaig/", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def swaig_route(request: Request, path: str = ""):
    """
    SWAIG function callback endpoint.
    The SDK's __token parameter handles authentication for these requests.
    """
    global agent
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    agent_app = agent.get_app()

    # Build the forwarded path
    forward_path = f"/swaig/{path}" if path else "/swaig/"

    # Create a new scope for the agent app
    scope = dict(request.scope)
    scope["path"] = forward_path
    scope["root_path"] = ""

    response_body = []
    response_status = 200
    response_headers = []

    async def receive():
        return await request.receive()

    async def send(message):
        nonlocal response_status, response_headers
        if message["type"] == "http.response.start":
            response_status = message["status"]
            response_headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                response_body.append(body)

    await agent_app(scope, receive, send)

    body = b"".join(response_body)
    headers = {k.decode(): v.decode() for k, v in response_headers}

    return Response(content=body, status_code=response_status, headers=headers)


@app.api_route("/post_prompt/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
@app.api_route("/post_prompt/", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def post_prompt_route(request: Request, path: str = ""):
    """
    Post-prompt callback endpoint for call summaries.
    The SDK's __token parameter handles authentication for these requests.
    """
    global agent
    if agent is None:
        raise HTTPException(status_code=503, detail="Agent not initialized")

    # Debug: dump the request body
    body_bytes = b""
    try:
        body_bytes = await request.body()
        if body_bytes:
            import json
            body_json = json.loads(body_bytes)
            print(f"[PostPrompt] Received data: {json.dumps(body_json, indent=2)}")
    except Exception as e:
        print(f"[PostPrompt] Error reading body: {e}")

    agent_app = agent.get_app()

    # Build the forwarded path
    forward_path = f"/post_prompt/{path}" if path else "/post_prompt/"

    # Create a new scope for the agent app
    scope = dict(request.scope)
    scope["path"] = forward_path
    scope["root_path"] = ""

    response_body = []
    response_status = 200
    response_headers = []

    async def receive():
        return {"type": "http.request", "body": body_bytes}

    async def send(message):
        nonlocal response_status, response_headers
        if message["type"] == "http.response.start":
            response_status = message["status"]
            response_headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                response_body.append(body)

    await agent_app(scope, receive, send)

    body = b"".join(response_body)
    headers = {k.decode(): v.decode() for k, v in response_headers}

    return Response(content=body, status_code=response_status, headers=headers)


# ==================== Authentication Pages ====================

@app.get("/")
async def root(request: Request):
    """Redirect root to login or admin"""
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url="/admin", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    """Login page"""
    # If already logged in, redirect to admin
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url="/admin", status_code=302)

    return templates.TemplateResponse("auth/login.html", {
        "request": request,
    })


@app.get("/signup", response_class=HTMLResponse)
async def signup_page(request: Request):
    """Signup page"""
    # If already logged in, redirect to admin
    user = await get_current_user(request)
    if user:
        return RedirectResponse(url="/admin", status_code=302)

    return templates.TemplateResponse("auth/signup.html", {
        "request": request,
    })


@app.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_page(request: Request):
    """Forgot password page"""
    return templates.TemplateResponse("auth/forgot-password.html", {
        "request": request,
    })


@app.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request, token: str = ""):
    """Reset password page"""
    return templates.TemplateResponse("auth/reset-password.html", {
        "request": request,
        "token": token,
    })


# ==================== Admin Panel ====================

async def get_authenticated_user(request: Request) -> User:
    """Get authenticated user or redirect to login"""
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return user


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Admin dashboard"""
    # User is set by auth middleware
    user = request.state.user

    db = SessionLocal()
    try:
        # Get stats filtered by user
        unread_messages = db.query(Message).filter(Message.user_id == user["id"], Message.status == "unread").count()
        total_messages = db.query(Message).filter(Message.user_id == user["id"]).count()
        total_calls = db.query(CallLog).filter(CallLog.user_id == user["id"]).count()
        total_faqs = db.query(FAQ).filter(FAQ.user_id == user["id"], FAQ.is_active == True).count()

        business_name = ConfigModel.get(db, "business_name", config.DEFAULT_BUSINESS_NAME, user_id=user["id"])

        return templates.TemplateResponse("admin/dashboard.html", {
            "request": request,
            "business_name": business_name,
            "unread_messages": unread_messages,
            "total_messages": total_messages,
            "total_calls": total_calls,
            "total_faqs": total_faqs,
            "active_page": "dashboard",
            "user": user,
        })
    finally:
        db.close()


@app.get("/admin/messages", response_class=HTMLResponse)
async def admin_messages(request: Request):
    """Messages management page"""
    user = request.state.user
    return templates.TemplateResponse("admin/messages.html", {
        "request": request,
        "active_page": "messages",
        "user": user,
    })


@app.get("/admin/calls", response_class=HTMLResponse)
async def admin_calls(request: Request):
    """Call history page"""
    user = request.state.user
    return templates.TemplateResponse("admin/calls.html", {
        "request": request,
        "active_page": "calls",
        "user": user,
    })


@app.get("/admin/settings", response_class=HTMLResponse)
async def admin_settings(request: Request):
    """Settings page"""
    user = request.state.user
    return templates.TemplateResponse("admin/settings.html", {
        "request": request,
        "active_page": "settings",
        "user": user,
    })


@app.get("/admin/knowledge", response_class=HTMLResponse)
async def admin_knowledge(request: Request):
    """Knowledge base management page"""
    user = request.state.user
    return templates.TemplateResponse("admin/knowledge.html", {
        "request": request,
        "active_page": "knowledge",
        "user": user,
    })


@app.get("/admin/faqs", response_class=HTMLResponse)
async def admin_faqs(request: Request):
    """FAQs management page"""
    user = request.state.user
    return templates.TemplateResponse("admin/faqs.html", {
        "request": request,
        "active_page": "faqs",
        "user": user,
    })


@app.get("/admin/hours", response_class=HTMLResponse)
async def admin_hours(request: Request):
    """Business hours management page"""
    user = request.state.user
    return templates.TemplateResponse("admin/hours.html", {
        "request": request,
        "active_page": "hours",
        "user": user,
    })


@app.get("/admin/appointments", response_class=HTMLResponse)
async def admin_appointments(request: Request):
    """Appointment types management page"""
    user = request.state.user
    return templates.TemplateResponse("admin/appointments.html", {
        "request": request,
        "active_page": "appointments",
        "user": user,
    })


@app.get("/admin/integrations", response_class=HTMLResponse)
async def admin_integrations(request: Request):
    """Integrations page"""
    user = request.state.user
    return templates.TemplateResponse("admin/integrations.html", {
        "request": request,
        "active_page": "integrations",
        "user": user,
    })


@app.get("/admin/account", response_class=HTMLResponse)
async def admin_account(request: Request):
    """Account settings page"""
    user = request.state.user
    return templates.TemplateResponse("admin/account.html", {
        "request": request,
        "active_page": "account",
        "user": user,
    })


# ==================== Profile API ====================

@app.post("/api/profile")
async def update_profile(request: Request):
    """Update user profile"""
    user_dict = request.state.user
    data = await request.json()

    db = SessionLocal()
    try:
        user = User.get_by_id(db, user_dict["id"])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        if "name" in data:
            user.name = data["name"]
            db.commit()

        return {"status": "success", "name": user.name}
    finally:
        db.close()


# ==================== Admin API ====================

@app.get("/api/config")
async def get_config(request: Request):
    """Get all configuration"""
    user = request.state.user
    db = SessionLocal()
    try:
        return {
            "business_name": ConfigModel.get(db, "business_name", "", user_id=user["id"]),
            "timezone": ConfigModel.get(db, "timezone", "America/Los_Angeles", user_id=user["id"]),
            "owner_email": ConfigModel.get(db, "owner_email", "", user_id=user["id"]),
            "owner_phone": ConfigModel.get(db, "owner_phone", "", user_id=user["id"]),
            "agent_name": ConfigModel.get(db, "agent_name", "Ethan", user_id=user["id"]),
            "greeting_message": ConfigModel.get(db, "greeting_message", "", user_id=user["id"]),
            "after_hours_message": ConfigModel.get(db, "after_hours_message", "", user_id=user["id"]),
            "business_address": ConfigModel.get(db, "business_address", "", user_id=user["id"]),
            "business_phone": ConfigModel.get(db, "business_phone", "", user_id=user["id"]),
            "business_email": ConfigModel.get(db, "business_email", "", user_id=user["id"]),
        }
    finally:
        db.close()


@app.post("/api/config")
async def update_config(request: Request):
    """Update configuration"""
    user = request.state.user
    db = SessionLocal()
    try:
        data = await request.json()
        for key, value in data.items():
            ConfigModel.set(db, key, value, user_id=user["id"])

        # Reload agent config
        global agent
        if agent:
            agent._load_config()

        return {"status": "success"}
    finally:
        db.close()


@app.get("/api/swml-url")
async def get_swml_url(request: Request):
    """Get SWML webhook URL with token for the user"""
    from urllib.parse import urlparse, urlunparse

    user_dict = request.state.user

    # Use configured proxy URL if set, otherwise use request base URL
    if config.SWML_PROXY_URL_BASE:
        base_url = config.SWML_PROXY_URL_BASE.rstrip('/')
    else:
        base_url = str(request.base_url).rstrip('/')

    db = SessionLocal()
    try:
        # Get the actual User object to access/create SWML token
        user = User.get_by_id(db, user_dict["id"])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Ensure user has an SWML token
        swml_token = user.ensure_swml_token(db)

        # Build the token-based URL with basic auth if configured
        basic_auth = config.get_basic_auth()
        if basic_auth:
            # Parse the base URL and insert credentials
            parsed = urlparse(base_url)
            username, password = basic_auth
            netloc_with_auth = f"{username}:{password}@{parsed.netloc}"
            base_url_with_auth = urlunparse((
                parsed.scheme,
                netloc_with_auth,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment
            ))
            full_url = f"{base_url_with_auth}/swml/{swml_token}/"
        else:
            full_url = f"{base_url}/swml/{swml_token}/"

        return {
            "url": full_url,
            "user_id": user.id,
        }
    finally:
        db.close()


@app.post("/api/swml-token/regenerate")
async def regenerate_swml_token(request: Request):
    """Regenerate the SWML token for the user (invalidates old URL)"""
    from urllib.parse import urlparse, urlunparse

    user_dict = request.state.user

    # Use configured proxy URL if set, otherwise use request base URL
    if config.SWML_PROXY_URL_BASE:
        base_url = config.SWML_PROXY_URL_BASE.rstrip('/')
    else:
        base_url = str(request.base_url).rstrip('/')

    db = SessionLocal()
    try:
        # Get the actual User object
        user = User.get_by_id(db, user_dict["id"])
        if not user:
            raise HTTPException(status_code=404, detail="User not found")

        # Generate new token
        new_token = user.regenerate_swml_token(db)

        # Build the new URL with basic auth if configured
        basic_auth = config.get_basic_auth()
        if basic_auth:
            parsed = urlparse(base_url)
            username, password = basic_auth
            netloc_with_auth = f"{username}:{password}@{parsed.netloc}"
            base_url_with_auth = urlunparse((
                parsed.scheme,
                netloc_with_auth,
                parsed.path,
                parsed.params,
                parsed.query,
                parsed.fragment
            ))
            full_url = f"{base_url_with_auth}/swml/{new_token}/"
        else:
            full_url = f"{base_url}/swml/{new_token}/"

        return {
            "url": full_url,
            "message": "SWML token regenerated. Update your SignalWire webhook URL.",
        }
    finally:
        db.close()


# ==================== Phone Number Management API ====================

from models.database import UserPhoneNumber


@app.get("/api/phone-numbers")
async def get_phone_numbers(request: Request):
    """Get phone numbers associated with the user"""
    user = request.state.user
    db = SessionLocal()
    try:
        numbers = UserPhoneNumber.get_for_user(db, user["id"])
        return [n.to_dict() for n in numbers]
    finally:
        db.close()


@app.post("/api/phone-numbers")
async def add_phone_number(request: Request):
    """Add a phone number for call routing"""
    user = request.state.user
    data = await request.json()

    phone_number = data.get("phone_number")
    friendly_name = data.get("friendly_name")

    if not phone_number:
        raise HTTPException(status_code=400, detail="Phone number is required")

    db = SessionLocal()
    try:
        # Check if number is already registered
        existing = UserPhoneNumber.get_user_by_phone(db, phone_number)
        if existing:
            raise HTTPException(status_code=400, detail="This phone number is already registered")

        number = UserPhoneNumber.add_for_user(db, user["id"], phone_number, friendly_name)
        return number.to_dict()
    finally:
        db.close()


@app.delete("/api/phone-numbers/{number_id}")
async def remove_phone_number(request: Request, number_id: str):
    """Remove a phone number from the user"""
    user = request.state.user
    db = SessionLocal()
    try:
        success = UserPhoneNumber.remove_for_user(db, user["id"], number_id)
        if not success:
            raise HTTPException(status_code=404, detail="Phone number not found")
        return {"status": "success"}
    finally:
        db.close()


@app.get("/api/messages")
async def get_messages(request: Request, status: str = None, limit: int = 50):
    """Get messages"""
    user = request.state.user
    db = SessionLocal()
    try:
        query = db.query(Message).filter(Message.user_id == user["id"])
        if status:
            query = query.filter(Message.status == status)
        messages = query.order_by(Message.created_at.desc()).limit(limit).all()
        return [m.to_dict() for m in messages]
    finally:
        db.close()


@app.post("/api/messages/{message_id}/read")
async def mark_message_read(request: Request, message_id: int):
    """Mark message as read"""
    user = request.state.user
    db = SessionLocal()
    try:
        message = db.query(Message).filter(Message.id == message_id, Message.user_id == user["id"]).first()
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")

        message.status = "read"
        from datetime import datetime
        message.read_at = datetime.utcnow()
        db.commit()

        return {"status": "success"}
    finally:
        db.close()


@app.delete("/api/messages/{message_id}")
async def delete_message(request: Request, message_id: int):
    """Delete a message"""
    user = request.state.user
    db = SessionLocal()
    try:
        message = db.query(Message).filter(Message.id == message_id, Message.user_id == user["id"]).first()
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")

        db.delete(message)
        db.commit()

        return {"status": "success"}
    finally:
        db.close()


@app.get("/api/faqs")
async def get_faqs(request: Request):
    """Get all FAQs"""
    user = request.state.user
    db = SessionLocal()
    try:
        faqs = db.query(FAQ).filter(FAQ.user_id == user["id"]).order_by(FAQ.category, FAQ.id).all()
        return [f.to_dict() for f in faqs]
    finally:
        db.close()


@app.post("/api/faqs")
async def create_faq(request: Request):
    """Create a new FAQ"""
    user = request.state.user
    db = SessionLocal()
    try:
        data = await request.json()
        faq = FAQ(
            user_id=user["id"],
            question=data.get("question"),
            answer=data.get("answer"),
            keywords=data.get("keywords", []),
            category=data.get("category"),
            is_active=data.get("is_active", True),
        )
        db.add(faq)
        db.commit()
        return faq.to_dict()
    finally:
        db.close()


@app.put("/api/faqs/{faq_id}")
async def update_faq(faq_id: int, request: Request):
    """Update an FAQ"""
    user = request.state.user
    db = SessionLocal()
    try:
        faq = db.query(FAQ).filter(FAQ.id == faq_id, FAQ.user_id == user["id"]).first()
        if not faq:
            raise HTTPException(status_code=404, detail="FAQ not found")

        data = await request.json()
        faq.question = data.get("question", faq.question)
        faq.answer = data.get("answer", faq.answer)
        faq.keywords = data.get("keywords", faq.keywords)
        faq.category = data.get("category", faq.category)
        faq.is_active = data.get("is_active", faq.is_active)
        db.commit()

        return faq.to_dict()
    finally:
        db.close()


@app.delete("/api/faqs/{faq_id}")
async def delete_faq(faq_id: int, request: Request):
    """Delete an FAQ"""
    user = request.state.user
    db = SessionLocal()
    try:
        faq = db.query(FAQ).filter(FAQ.id == faq_id, FAQ.user_id == user["id"]).first()
        if not faq:
            raise HTTPException(status_code=404, detail="FAQ not found")

        db.delete(faq)
        db.commit()

        return {"status": "success"}
    finally:
        db.close()


@app.post("/api/faqs/search")
async def search_faqs(request: Request):
    """Test FAQ search - simulates what the agent does"""
    user = request.state.user
    db = SessionLocal()
    try:
        data = await request.json()
        query = data.get("query", "").lower()

        faqs = db.query(FAQ).filter(FAQ.user_id == user["id"], FAQ.is_active == True).all()

        query_words = set(query.split())
        results = []

        for faq in faqs:
            # Check keywords
            keywords = set(k.lower() for k in (faq.keywords or []))
            keyword_matches = len(query_words & keywords)

            # Check question text
            question_words = set(faq.question.lower().split())
            question_matches = len(query_words & question_words)

            score = keyword_matches * 2 + question_matches

            if score > 0:
                results.append({
                    "id": faq.id,
                    "question": faq.question,
                    "answer": faq.answer,
                    "category": faq.category,
                    "keywords": faq.keywords,
                    "score": score,
                    "keyword_matches": keyword_matches,
                    "question_matches": question_matches,
                })

        # Sort by score descending
        results.sort(key=lambda x: x["score"], reverse=True)

        return {"query": query, "results": results[:5]}
    finally:
        db.close()


@app.get("/api/services")
async def get_services(request: Request):
    """Get all services"""
    user = request.state.user
    db = SessionLocal()
    try:
        services = db.query(Service).filter(Service.user_id == user["id"]).all()
        return [s.to_dict() for s in services]
    finally:
        db.close()


@app.get("/api/appointment-types")
async def get_appointment_types(request: Request):
    """Get all appointment types"""
    user = request.state.user
    db = SessionLocal()
    try:
        types = db.query(AppointmentType).filter(AppointmentType.user_id == user["id"]).all()
        return [t.to_dict() for t in types]
    finally:
        db.close()


@app.post("/api/appointment-types")
async def create_appointment_type(request: Request):
    """Create a new appointment type"""
    user = request.state.user
    data = await request.json()
    db = SessionLocal()
    try:
        apt_type = AppointmentType(
            user_id=user["id"],
            name=data["name"],
            description=data.get("description"),
            duration_minutes=data.get("duration_minutes", 60),
            buffer_minutes=data.get("buffer_minutes", 15),
            price=data.get("price"),
            is_active=data.get("is_active", True)
        )
        db.add(apt_type)
        db.commit()
        db.refresh(apt_type)
        return apt_type.to_dict()
    finally:
        db.close()


@app.put("/api/appointment-types/{type_id}")
async def update_appointment_type(request: Request, type_id: int):
    """Update an appointment type"""
    user = request.state.user
    data = await request.json()
    db = SessionLocal()
    try:
        apt_type = db.query(AppointmentType).filter(
            AppointmentType.id == type_id,
            AppointmentType.user_id == user["id"]
        ).first()
        if not apt_type:
            raise HTTPException(status_code=404, detail="Appointment type not found")

        apt_type.name = data.get("name", apt_type.name)
        apt_type.description = data.get("description", apt_type.description)
        apt_type.duration_minutes = data.get("duration_minutes", apt_type.duration_minutes)
        apt_type.buffer_minutes = data.get("buffer_minutes", apt_type.buffer_minutes)
        apt_type.price = data.get("price", apt_type.price)
        apt_type.is_active = data.get("is_active", apt_type.is_active)

        db.commit()
        db.refresh(apt_type)
        return apt_type.to_dict()
    finally:
        db.close()


@app.delete("/api/appointment-types/{type_id}")
async def delete_appointment_type(request: Request, type_id: int):
    """Delete an appointment type"""
    user = request.state.user
    db = SessionLocal()
    try:
        apt_type = db.query(AppointmentType).filter(
            AppointmentType.id == type_id,
            AppointmentType.user_id == user["id"]
        ).first()
        if not apt_type:
            raise HTTPException(status_code=404, detail="Appointment type not found")

        db.delete(apt_type)
        db.commit()
        return {"success": True}
    finally:
        db.close()


@app.get("/api/call-logs")
async def get_call_logs(request: Request, limit: int = 50):
    """Get call logs"""
    user = request.state.user
    db = SessionLocal()
    try:
        logs = db.query(CallLog).filter(CallLog.user_id == user["id"]).order_by(CallLog.created_at.desc()).limit(limit).all()
        return [l.to_dict() for l in logs]
    finally:
        db.close()


# ==================== Business Hours API ====================

@app.get("/api/business-hours")
async def get_business_hours(request: Request):
    """Get all business hours"""
    user = request.state.user
    db = SessionLocal()
    try:
        hours = db.query(BusinessHours).filter(BusinessHours.user_id == user["id"]).order_by(BusinessHours.day_of_week).all()
        return [h.to_dict() for h in hours]
    finally:
        db.close()


@app.post("/api/business-hours")
async def update_business_hours(request: Request):
    """Update business hours"""
    user = request.state.user
    db = SessionLocal()
    try:
        data = await request.json()

        for day_data in data:
            day = day_data.get("day_of_week")
            hours = db.query(BusinessHours).filter(BusinessHours.day_of_week == day, BusinessHours.user_id == user["id"]).first()

            if not hours:
                hours = BusinessHours(user_id=user["id"], day_of_week=day)
                db.add(hours)

            hours.is_closed = day_data.get("is_closed", False)
            if not hours.is_closed:
                from datetime import time
                open_parts = day_data.get("open_time", "09:00:00").split(":")
                close_parts = day_data.get("close_time", "17:00:00").split(":")
                hours.open_time = time(int(open_parts[0]), int(open_parts[1]))
                hours.close_time = time(int(close_parts[0]), int(close_parts[1]))

        db.commit()

        # Reload agent config
        global agent
        if agent:
            agent._load_config()

        return {"status": "success"}
    finally:
        db.close()


# ==================== Holidays API ====================

@app.get("/api/holidays")
async def get_holidays(request: Request):
    """Get all holidays"""
    user = request.state.user
    db = SessionLocal()
    try:
        from datetime import datetime
        holidays = db.query(Holiday).filter(
            Holiday.user_id == user["id"],
            Holiday.date >= datetime.now().date()
        ).order_by(Holiday.date).all()
        return [h.to_dict() for h in holidays]
    finally:
        db.close()


@app.post("/api/holidays")
async def create_holiday(request: Request):
    """Create a new holiday"""
    user = request.state.user
    db = SessionLocal()
    try:
        from datetime import datetime
        data = await request.json()

        date_str = data.get("date")
        name = data.get("name")

        if not date_str or not name:
            raise HTTPException(status_code=400, detail="Date and name are required")

        date = datetime.strptime(date_str, "%Y-%m-%d").date()

        holiday = Holiday(user_id=user["id"], date=date, name=name)
        db.add(holiday)
        db.commit()
        db.refresh(holiday)

        # Reload agent config
        global agent
        if agent:
            agent._load_config()

        return holiday.to_dict()
    finally:
        db.close()


@app.delete("/api/holidays/{holiday_id}")
async def delete_holiday(request: Request, holiday_id: int):
    """Delete a holiday"""
    user = request.state.user
    db = SessionLocal()
    try:
        holiday = db.query(Holiday).filter(Holiday.id == holiday_id, Holiday.user_id == user["id"]).first()
        if not holiday:
            raise HTTPException(status_code=404, detail="Holiday not found")

        db.delete(holiday)
        db.commit()

        # Reload agent config
        global agent
        if agent:
            agent._load_config()

        return {"status": "success"}
    finally:
        db.close()


# ==================== Google OAuth API ====================

from services.google_auth import google_auth


@app.get("/api/google/status")
async def google_status(request: Request):
    """Check Google OAuth connection status"""
    user = request.state.user
    return {
        "configured": google_auth.is_configured(),
        "connected": google_auth.is_connected(user["id"]),
        "email": google_auth.get_connected_email(user["id"]),
    }


@app.get("/api/google/connect")
async def google_connect(request: Request):
    """Initiate Google OAuth flow"""
    user = request.state.user
    if not google_auth.is_configured():
        raise HTTPException(
            status_code=400,
            detail="Google OAuth not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET."
        )

    # Generate state token for security - include user_id for verification
    import secrets
    state = secrets.token_urlsafe(32)

    # Store state in session or database for verification
    db = SessionLocal()
    try:
        ConfigModel.set(db, "google_oauth_state", state, user_id=user["id"])
    finally:
        db.close()

    auth_url = google_auth.get_authorization_url(state=state)
    return {"authorization_url": auth_url}


@app.get("/oauth/google/callback")
async def google_callback(request: Request, code: str = None, state: str = None, error: str = None):
    """Handle Google OAuth callback"""
    if error:
        raise HTTPException(status_code=400, detail=f"OAuth error: {error}")

    if not code:
        raise HTTPException(status_code=400, detail="No authorization code received")

    # Get authenticated user (callback is in popup with same session)
    user = await get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Verify state
    db = SessionLocal()
    try:
        stored_state = ConfigModel.get(db, "google_oauth_state", user_id=user["id"])
        if state != stored_state:
            raise HTTPException(status_code=400, detail="Invalid state parameter")

        # Clear the stored state
        ConfigModel.set(db, "google_oauth_state", None, user_id=user["id"])
    finally:
        db.close()

    # Exchange code for credentials
    # Build the full authorization response URL
    auth_response = str(request.url)

    success = google_auth.handle_callback(user["id"], auth_response)

    if success:
        # Redirect to admin panel with success message
        return HTMLResponse(
            content="""
            <html>
            <body>
                <script>
                    window.opener.postMessage({type: 'google_connected', success: true}, '*');
                    window.close();
                </script>
                <p>Google account connected successfully! You can close this window.</p>
            </body>
            </html>
            """,
            status_code=200
        )
    else:
        raise HTTPException(status_code=400, detail="Failed to connect Google account")


@app.post("/api/google/disconnect")
async def google_disconnect(request: Request):
    """Disconnect Google account"""
    user = request.state.user
    google_auth.disconnect(user["id"])
    return {"status": "success", "message": "Google account disconnected"}


@app.get("/api/google/calendars")
async def list_google_calendars(request: Request):
    """List available Google Calendars"""
    user = request.state.user
    if not google_auth.is_connected(user["id"]):
        raise HTTPException(status_code=400, detail="Google account not connected")

    calendars = google_auth.list_calendars(user["id"])
    selected = google_auth.get_selected_calendar(user["id"])

    return {
        "calendars": calendars,
        "selected": selected
    }


@app.post("/api/google/calendars")
async def set_google_calendar(request: Request):
    """Set the selected Google Calendar"""
    user = request.state.user
    if not google_auth.is_connected(user["id"]):
        raise HTTPException(status_code=400, detail="Google account not connected")

    data = await request.json()
    calendar_id = data.get("calendar_id")

    if not calendar_id:
        raise HTTPException(status_code=400, detail="calendar_id is required")

    google_auth.set_selected_calendar(user["id"], calendar_id)
    return {"status": "success", "selected": calendar_id}


# ==================== Knowledge Base API ====================

from services.knowledge_service import knowledge_service


@app.get("/api/knowledge/status")
async def knowledge_status(request: Request):
    """Get knowledge base index status"""
    user = request.state.user
    return knowledge_service.get_index_status(user_id=user["id"])


@app.get("/api/knowledge/documents")
async def get_knowledge_documents(request: Request, category: str = None):
    """Get all knowledge documents"""
    user = request.state.user
    return knowledge_service.get_documents(category=category, user_id=user["id"])


@app.get("/api/knowledge/documents/{doc_id}")
async def get_knowledge_document(request: Request, doc_id: int):
    """Get a specific document"""
    user = request.state.user
    doc = knowledge_service.get_document(doc_id, user_id=user["id"])
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@app.post("/api/knowledge/documents")
async def create_knowledge_document(request: Request):
    """Create a new knowledge document"""
    user = request.state.user
    data = await request.json()

    if not data.get("title"):
        raise HTTPException(status_code=400, detail="Title is required")
    if not data.get("content"):
        raise HTTPException(status_code=400, detail="Content is required")

    doc = knowledge_service.add_document(
        title=data["title"],
        content=data["content"],
        category=data.get("category", "general"),
        tags=data.get("tags", []),
        user_id=user["id"],
    )
    return doc


@app.put("/api/knowledge/documents/{doc_id}")
async def update_knowledge_document(doc_id: int, request: Request):
    """Update a knowledge document"""
    user = request.state.user
    data = await request.json()

    doc = knowledge_service.update_document(
        doc_id=doc_id,
        title=data.get("title"),
        content=data.get("content"),
        category=data.get("category"),
        tags=data.get("tags"),
        user_id=user["id"],
    )

    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@app.delete("/api/knowledge/documents/{doc_id}")
async def delete_knowledge_document(request: Request, doc_id: int):
    """Delete a knowledge document"""
    user = request.state.user
    success = knowledge_service.delete_document(doc_id, user_id=user["id"])
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "success"}


@app.post("/api/knowledge/build-index")
async def build_knowledge_index(request: Request, force: bool = False):
    """Build or rebuild the search index for the user"""
    user = request.state.user
    result = knowledge_service.build_index(force=force, user_id=user["id"])
    if not result["success"]:
        raise HTTPException(status_code=500, detail=result.get("error", "Failed to build index"))
    return result


@app.get("/api/knowledge/categories")
async def get_knowledge_categories(request: Request):
    """Get list of document categories"""
    user = request.state.user
    return knowledge_service.get_categories(user_id=user["id"])


@app.post("/api/knowledge/search")
async def search_knowledge(request: Request):
    """Search the knowledge base for the user (for testing)"""
    user = request.state.user
    data = await request.json()
    query = data.get("query", "")
    count = data.get("count", 5)

    if not query:
        raise HTTPException(status_code=400, detail="Query is required")

    results = knowledge_service.search(query, count=count, user_id=user["id"])
    return {"query": query, "results": results}


@app.post("/api/knowledge/import")
async def import_knowledge_documents(request: Request):
    """Import documents from a directory"""
    user = request.state.user
    data = await request.json()
    directory = data.get("directory")
    category = data.get("category", "imported")

    if not directory:
        raise HTTPException(status_code=400, detail="Directory path is required")

    result = knowledge_service.import_documents_from_directory(directory, category, user_id=user["id"])
    return result


# ==================== Static Files ====================

# Mount static files (CSS, JS, images)
static_path = Path(__file__).parent / "static"
if static_path.exists():
    app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


# ==================== Main Entry Point ====================

def main():
    """Main entry point"""
    print(f"Starting Personal Assistant AI Agent on {config.HOST}:{config.PORT}")

    uvicorn.run(
        "app:app",
        host=config.HOST,
        port=config.PORT,
        reload=os.getenv("DEBUG", "false").lower() == "true",
    )


if __name__ == "__main__":
    main()
