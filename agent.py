"""
Ethan Voice AI Agent

A comprehensive voice AI assistant using SignalWire Agents SDK
with state machine, Google integrations, and knowledge base.
"""
import sys
import json
import functools
from datetime import datetime, time
from typing import Optional, Dict, Any, List
import pytz


# Debug logging for SWAIG functions
DEBUG_SWAIG = True  # Set to False to disable debug logging


def log_swaig_call(func):
    """Decorator to log SWAIG function calls with args and results"""
    @functools.wraps(func)
    def wrapper(self, args: Dict, raw_data: Dict):
        func_name = func.__name__.replace('_', '', 1)  # Remove leading underscore

        if DEBUG_SWAIG:
            # Log the call
            print(f"\n{'='*60}", file=sys.stderr)
            print(f"[SWAIG CALL] {func_name}", file=sys.stderr)
            print(f"[ARGS] {json.dumps(args, indent=2, default=str)}", file=sys.stderr)

            # Extract useful context from raw_data
            global_data = raw_data.get("global_data", {})
            caller_info = {
                "user_id": global_data.get("user_id"),
                "is_owner": global_data.get("is_owner"),
                "caller_id": global_data.get("caller_id"),
            }
            print(f"[CONTEXT] {json.dumps(caller_info, indent=2)}", file=sys.stderr)

        # Call the actual function
        result = func(self, args, raw_data)

        if DEBUG_SWAIG:
            # Log the result
            if hasattr(result, 'response'):
                response_text = result.response if isinstance(result.response, str) else str(result.response)
                # Truncate long responses for readability
                if len(response_text) > 500:
                    response_text = response_text[:500] + "... [truncated]"
                print(f"[RESULT] {response_text}", file=sys.stderr)
            else:
                print(f"[RESULT] {result}", file=sys.stderr)
            print(f"{'='*60}\n", file=sys.stderr)

        return result
    return wrapper

from signalwire import AgentBase
from signalwire.core.function_result import SwaigFunctionResult

import config
from models.database import (
    SessionLocal,
    Config as ConfigModel,
    BusinessHours,
    Holiday,
    AppointmentType,
    Service,
    FAQ,
    Message,
    CallLog,
)
from services.google_auth import google_auth
from services.calendar_service import calendar_service
from services.email_service import email_service
from services.contacts_service import contacts_service
from services.knowledge_service import knowledge_service


class EthanAgent(AgentBase):
    """
    Ethan - Voice AI Assistant

    Handles:
    - Appointment scheduling (Google Calendar)
    - Email sending (Gmail API)
    - Message taking
    - Business information & FAQs
    - Knowledge base Q&A (RAG)
    """

    # Function groups for conditional registration per-call
    FAQ_FUNCTIONS = ["search_faqs"]
    KNOWLEDGE_FUNCTIONS = ["search_knowledge_base"]

    # Customer-only functions (disabled when owner calls)
    CUSTOMER_ONLY_FUNCTIONS = [
        "save_message",
        "transfer_to_owner",
        "email_owner",  # Customers email owner, owner doesn't email themselves
    ]

    # Owner-only functions (inbox/message management, only enabled when owner calls)
    OWNER_ONLY_FUNCTIONS = [
        # Email management
        "get_recent_emails",
        "check_unread_emails",
        "read_email",
        "delete_email",
        "mark_email_read",
        "archive_email",
        # Message management
        "get_messages",
        "mark_message_read",
        "delete_message",
        # Calendar management (owner-specific)
        "respond_to_invite",
        "update_appointment",
    ]

    # Business info functions (disabled for owner - they know their own business)
    BUSINESS_INFO_FUNCTIONS = [
        "get_business_hours",
        "check_business_hours",
        "get_business_info",
        "get_services",
        "get_location",
    ]

    GOOGLE_CALENDAR_FUNCTIONS = [
        "get_appointment_types",
        "check_calendar_availability",
        "book_appointment",
        "find_my_appointments",
        "cancel_appointment",
        "respond_to_invite",
        "update_appointment",
    ]
    GOOGLE_EMAIL_FUNCTIONS = [
        "send_email",
        "email_owner",
        "get_recent_emails",
        "check_unread_emails",
        "read_email",
        "delete_email",
        "mark_email_read",
        "archive_email",
    ]
    GOOGLE_CONTACTS_FUNCTIONS = [
        "lookup_contact",
        "search_contacts",
    ]
    # All Google-dependent functions combined
    GOOGLE_FUNCTIONS = (
        GOOGLE_CALENDAR_FUNCTIONS +
        GOOGLE_EMAIL_FUNCTIONS +
        GOOGLE_CONTACTS_FUNCTIONS
    )

    def __init__(self):
        # Get basic auth if configured
        basic_auth = config.get_basic_auth()

        super().__init__(
            name="ethan-agent",
            route="/",
            host=config.HOST,
            port=config.PORT,
            basic_auth=basic_auth,
            auto_answer=True,
            record_call=True,
            record_format="mp3",
            record_stereo=True,
        )

        # Configure language and voice
        self.add_language("English", config.AGENT_LANGUAGE, config.AGENT_VOICE)

        # Configure speech settings and AI model
        self.set_params({
            "end_of_speech_timeout": config.END_OF_SPEECH_TIMEOUT,
            "attention_timeout": config.ATTENTION_TIMEOUT,
            "inactivity_timeout": config.INACTIVITY_TIMEOUT,
            "ai_model": config.AI_MODEL,
            # "static_greeting": "Your call may be monitored or recorded for quality and training purposes.",
            "static_greeting_no_barge": True,
            "turn_detection_timeout": 1000,
        })

        # Add speech recognition hints
        self.add_hints([
            "appointment", "schedule", "book", "cancel", "reschedule",
            "email", "message", "callback",
            "hours", "location", "address", "directions",
            "services", "pricing", "cost",
            "transfer", "speak to someone", "human",
            "emergency", "urgent",
        ])

        # Add pronunciation rules for TTS (SignalWire uses ${1} syntax for backreferences)
        # Email usernames: spell out short usernames letter by letter (longest first)
        # 4-letter: "bkwt@" → "b, k, w, t, at "
        self.add_pronunciation(r"\b([a-zA-Z])([a-zA-Z])([a-zA-Z])([a-zA-Z])@", "${1}, ${2}, ${3}, ${4}, at ", ignore_case=True)
        # 3-letter: "bkw@" → "b, k, w, at "
        self.add_pronunciation(r"\b([a-zA-Z])([a-zA-Z])([a-zA-Z])@", "${1}, ${2}, ${3}, at ", ignore_case=True)
        # 2-letter: "bk@" → "b, k, at "
        self.add_pronunciation(r"\b([a-zA-Z])([a-zA-Z])@", "${1}, ${2}, at ", ignore_case=True)
        # Email addresses with dots: "john.doe" → "john dot doe"
        self.add_pronunciation(r"(\w)\.(\w)", "${1} dot ${2}", ignore_case=True)
        # Email at symbol for longer usernames: "@" → " at "
        self.add_pronunciation(r"@", " at ", ignore_case=True)
        # Phone numbers: group digits for natural reading
        # Format: (XXX) XXX-XXXX or XXX-XXX-XXXX
        self.add_pronunciation(r"\((\d{3})\)\s*(\d{3})-(\d{4})", "${1}. ${2}. ${3}", ignore_case=True)
        self.add_pronunciation(r"(\d{3})-(\d{3})-(\d{4})", "${1}. ${2}. ${3}", ignore_case=True)
        # +1XXXXXXXXXX format
        self.add_pronunciation(r"\+1(\d{3})(\d{3})(\d{4})", "1. ${1}. ${2}. ${3}", ignore_case=True)

        # Load configuration from database
        self._load_config()

        # Set up prompts
        self._setup_prompts()

        # Set up state machine
        self._setup_state_machine()

        # Register SWAIG functions
        self._register_functions()

        # Add datetime skill
        self.add_skill("datetime")

        # Add knowledge base search skill (if index exists)
        self._setup_knowledge_base()

        # Set up post-prompt for call summary
        self._setup_post_prompt()

        # Set up dynamic config callback for per-call configuration (auto caller lookup)
        self.set_dynamic_config_callback(self._on_call_received)

    def render_document(self) -> str:
        """Override to log SWML output to stderr for debugging"""
        import json
        swml = super().render_document()
        print("=" * 80, file=sys.stderr)
        print("[SWML OUTPUT]", file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        try:
            # Parse and pretty-print the JSON
            swml_dict = json.loads(swml)
            print(json.dumps(swml_dict, indent=2), file=sys.stderr)
        except json.JSONDecodeError:
            # If not valid JSON, print as-is
            print(swml, file=sys.stderr)
        print("=" * 80, file=sys.stderr)
        return swml

    def _on_call_received(self, query_params: dict, body_params: dict, headers: dict, agent: 'AgentBase'):
        """
        Dynamic configuration callback called for each incoming SWML request.

        This is called BEFORE the SWML is generated, allowing us to:
        1. Detect user from called phone number (multi-tenant routing)
        2. Look up the caller in contacts
        3. Add caller/user info to global_data
        4. Customize prompts with caller context

        Expected body_params structure:
        {
            "call": {
                "from": "+14842295149",
                "from_number": "+14842295149",
                "to": "+16503820000",
                ...
            },
            "vars": {...}
        }
        """
        from models.database import SessionLocal, UserPhoneNumber

        call_data = body_params.get("call", {})

        # Check if this is a SWAIG function callback (not initial SWML request)
        # SWAIG callbacks have different structure and shouldn't re-run all this setup
        if not call_data and "function" in body_params:
            # This is a SWAIG callback - context already set via query params
            return

        caller_id_num = call_data.get("from") or call_data.get("from_number", "")
        called_num = call_data.get("to") or call_data.get("to_number", "")

        # Also check for user_id in query params (path-based routing)
        user_id = query_params.get("user_id")

        # Initialize base global_data that will be built up throughout this method
        global_data = {
            "has_faqs": True,
            "has_knowledge": True,
            "caller_identified": False,
            "caller_phone": caller_id_num,
            "is_owner_calling": False,
        }

        print(f"Incoming call: from={caller_id_num}, to={called_num}, user_id from query={user_id}")
        print(f"Query params: {query_params}")

        # Step 1: Detect user from phone number mapping or query param
        db = SessionLocal()
        try:
            owner_user = None

            # Try to get user from query params first (path-based routing)
            if user_id:
                from models.database import User
                owner_user = User.get_by_id(db, user_id)
                if owner_user:
                    print(f"User identified from path: {owner_user.email} (ID: {user_id})")

            # If no user from path, try phone number mapping
            if not owner_user and called_num:
                owner_user = UserPhoneNumber.get_user_by_phone(db, called_num)
                if owner_user:
                    print(f"User identified from phone: {owner_user.email} (number: {called_num})")
                else:
                    print(f"No user mapped to phone number: {called_num}")

            # Load user-specific context and configuration
            if owner_user:
                user_id = owner_user.id

                # Load user-specific configuration from database
                user_business_name = ConfigModel.get(db, "business_name", config.DEFAULT_BUSINESS_NAME, user_id=user_id)
                user_timezone = ConfigModel.get(db, "timezone", config.DEFAULT_TIMEZONE, user_id=user_id)
                user_owner_email = ConfigModel.get(db, "owner_email", config.DEFAULT_OWNER_EMAIL, user_id=user_id)
                user_owner_phone = ConfigModel.get(db, "owner_phone", config.DEFAULT_OWNER_PHONE, user_id=user_id)
                user_agent_name = ConfigModel.get(db, "agent_name", config.AGENT_NAME, user_id=user_id)
                user_business_address = ConfigModel.get(db, "business_address", "", user_id=user_id)
                user_business_phone = ConfigModel.get(db, "business_phone", "", user_id=user_id)
                user_business_email = ConfigModel.get(db, "business_email", "", user_id=user_id)

                # Load user's business hours
                user_business_hours = {
                    h.day_of_week: h.to_dict()
                    for h in db.query(BusinessHours).filter(BusinessHours.user_id == user_id).all()
                }

                # Check if user has FAQs
                faq_count = db.query(FAQ).filter(
                    FAQ.user_id == user_id,
                    FAQ.is_active == True
                ).count()
                has_faqs = faq_count > 0

                # Check if user has knowledge base
                kb_index_path = knowledge_service._get_user_index_path(user_id)
                has_knowledge = kb_index_path.exists()

                # Check if user has Google connected
                google_connected = google_auth.is_connected(user_id)

                # Check if caller is the business owner
                # First check query params (for SWAIG callbacks where caller_id is empty)
                is_owner_from_param = query_params.get("is_owner") == "1"

                # Then check caller ID (for initial SWML request)
                is_owner_from_phone = False
                if caller_id_num and user_owner_phone:
                    # Normalize phone numbers for comparison (remove formatting)
                    caller_normalized = ''.join(c for c in caller_id_num if c.isdigit())
                    owner_normalized = ''.join(c for c in user_owner_phone if c.isdigit())
                    # Compare last 10 digits (handles country code differences)
                    is_owner_from_phone = len(caller_normalized) >= 10 and len(owner_normalized) >= 10 and \
                                          caller_normalized[-10:] == owner_normalized[-10:]

                is_owner_calling = is_owner_from_param or is_owner_from_phone
                if is_owner_calling:
                    source = "query param" if is_owner_from_param else "phone match"
                    print(f"Owner detected via {source}!")

                # Update global_data with all user context
                global_data.update({
                    "user_id": user_id,
                    "user_email": owner_user.email,
                    "user_name": owner_user.name,
                    "has_faqs": has_faqs,
                    "has_knowledge": has_knowledge,
                    "google_connected": google_connected,
                    "is_owner_calling": is_owner_calling,
                    # User config for function handlers
                    "business_name": user_business_name,
                    "timezone": user_timezone,
                    "owner_email": user_owner_email,
                    "owner_phone": user_owner_phone,
                    "agent_name": user_agent_name,
                    "business_address": user_business_address,
                    "business_phone": user_business_phone,
                    "business_email": user_business_email,
                })

                # Update personality prompt based on caller type
                if is_owner_calling:
                    # Owner mode: personal assistant
                    owner_name = owner_user.name or "the business owner"
                    agent.prompt_add_section(
                        "Personality",
                        f"You are {user_agent_name}, the personal assistant for {owner_name}. "
                        f"The business owner is calling to check on their business, {user_business_name}. "
                        f"Help them check messages, emails, appointments, and manage their schedule. "
                        f"Be efficient and professional - they're busy. Keep responses concise."
                    )
                else:
                    # Customer mode: receptionist
                    agent.prompt_add_section(
                        "Personality",
                        f"You are {user_agent_name}, the virtual receptionist for {user_business_name}. "
                        f"You answer incoming calls on behalf of {user_business_name} and help their customers and clients. "
                        f"You work FOR the business owner, NOT for the callers. "
                        f"Be warm, professional, and helpful. Keep responses concise for voice interaction."
                    )

                # Format and add business hours (only for customers, not owner)
                if not is_owner_calling:
                    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
                    hours_lines = []
                    for i, day in enumerate(days):
                        hours = user_business_hours.get(i, {})
                        if hours.get("is_closed"):
                            hours_lines.append(f"  {day}: Closed")
                        elif hours.get("open_time") and hours.get("close_time"):
                            hours_lines.append(f"  {day}: {hours['open_time']} - {hours['close_time']}")
                        else:
                            hours_lines.append(f"  {day}: Not set")

                    agent.prompt_add_section(
                        "Business Information",
                        body=f"Current business hours:\n" + "\n".join(hours_lines)
                    )

                # Add capabilities prompt based on what's available and caller type
                capabilities = self._get_available_capabilities(
                    has_faqs=has_faqs,
                    has_knowledge=has_knowledge,
                    google_connected=google_connected,
                    is_owner_calling=is_owner_calling
                )
                if is_owner_calling:
                    agent.prompt_add_section(
                        "Your Capabilities",
                        body="You can help the owner with:",
                        bullets=capabilities
                    )
                else:
                    agent.prompt_add_section(
                        "Your Capabilities",
                        body="You can help callers with:",
                        bullets=capabilities
                    )

                # Add unavailable features prompt section
                unavailable_features = []
                if not has_faqs:
                    unavailable_features.append("FAQ search (no FAQs configured)")
                if not has_knowledge:
                    unavailable_features.append("Knowledge base search (no documents indexed)")
                if not is_owner_calling and not user_owner_phone:
                    unavailable_features.append("Transfer to owner (no phone number configured)")
                if not google_connected:
                    unavailable_features.append("Appointment scheduling (Google Calendar not connected)")
                    unavailable_features.append("Sending emails (Gmail not connected)")
                    unavailable_features.append("Contact lookup (Google Contacts not connected)")

                if unavailable_features:
                    agent.prompt_add_section(
                        "Unavailable Features",
                        body="The following features are NOT available. Do NOT offer or attempt to use them:",
                        bullets=unavailable_features
                    )

                mode_str = "OWNER" if is_owner_calling else "customer"
                print(f"User {owner_user.email}: mode={mode_str}, business={user_business_name}, google={google_connected}, has_faqs={has_faqs}, has_knowledge={has_knowledge}")

                # Pass context to SWAIG callbacks so they can identify the user and mode
                agent.add_swaig_query_params({
                    "user_id": user_id,
                    "is_owner": "1" if is_owner_calling else "0",
                })

                # Set context based on caller mode (owner vs customer)
                # This completely segregates the flows - no LLM decision needed
                self._set_context_for_mode(agent, is_owner_calling)

                # Remove unavailable functions from this call's SWML
                self._remove_unavailable_functions(
                    agent=agent,
                    has_faqs=has_faqs,
                    has_knowledge=has_knowledge,
                    google_connected=google_connected,
                    has_owner_phone=bool(user_owner_phone),
                    is_owner_calling=is_owner_calling
                )
            else:
                print("No user context - using default configuration")
                user_id = None
                # Set customer mode context for anonymous calls
                self._set_context_for_mode(agent, is_owner_calling=False)
                # Remove all user-dependent functions for anonymous calls
                self._remove_unavailable_functions(
                    agent=agent,
                    has_faqs=False,
                    has_knowledge=False,
                    google_connected=False,
                    has_owner_phone=False,
                    is_owner_calling=False
                )

        finally:
            db.close()

        if not caller_id_num:
            print("No caller ID found in request")
            agent.set_global_data(global_data)
            return

        print(f"Incoming call from: {caller_id_num}")

        # Step 2: Handle owner vs customer caller
        is_owner_calling = global_data.get("is_owner_calling", False)

        if is_owner_calling:
            # Owner mode: Skip contact lookup, add owner greeting with status
            owner_name = global_data.get("user_name", "")
            owner_first_name = owner_name.split()[0] if owner_name else "there"

            # Build owner greeting with status summary
            greeting_parts = [f"Greet the owner warmly: 'Hi {owner_first_name}!'"]

            # Add status summary if Google is connected
            if google_auth.is_connected(user_id):
                status_items = []
                try:
                    # Check unread emails
                    from services.email_service import email_service
                    unread_count = email_service.get_unread_count(user_id=user_id)
                    if unread_count and unread_count > 0:
                        status_items.append(f"You have {unread_count} unread email{'s' if unread_count != 1 else ''}")
                except Exception as e:
                    print(f"Error getting unread count: {e}")

                try:
                    # Check upcoming appointments
                    from datetime import datetime
                    appointments = calendar_service.get_upcoming_appointments(user_id=user_id, max_results=5)
                    if appointments:
                        apt_count = len(appointments)
                        next_apt = appointments[0]
                        next_time = next_apt.get("start", "")
                        if next_time:
                            # Parse and format time
                            try:
                                apt_time = datetime.fromisoformat(next_time.replace("Z", "+00:00"))
                                time_str = apt_time.strftime("%-I:%M %p")
                                status_items.append(f"Your next appointment is at {time_str}")
                            except:
                                status_items.append(f"You have {apt_count} appointment{'s' if apt_count != 1 else ''} today")
                except Exception as e:
                    print(f"Error getting appointments: {e}")

                try:
                    # Check unread messages
                    db = SessionLocal()
                    try:
                        unread_messages = db.query(Message).filter(
                            Message.user_id == user_id,
                            Message.status == "unread"
                        ).count()
                        if unread_messages > 0:
                            status_items.append(f"You have {unread_messages} unread message{'s' if unread_messages != 1 else ''}")
                    finally:
                        db.close()
                except Exception as e:
                    print(f"Error getting messages: {e}")

                if status_items:
                    greeting_parts.append(f"Then give a quick status: {'. '.join(status_items)}.")
                else:
                    greeting_parts.append("Everything looks clear - no urgent items.")

            greeting_parts.append("Then ask: 'What would you like to check on?'")

            agent.prompt_add_section(
                "Owner Greeting",
                body="\n".join(greeting_parts)
            )

            # Add owner mode instructions (overrides customer-focused step prompts)
            agent.prompt_add_section(
                "OWNER MODE INSTRUCTIONS",
                body=(
                    "IMPORTANT: You are talking to the business OWNER, not a customer.\n\n"
                    "Available commands:\n"
                    "- 'Check my messages' → CALL get_messages tool\n"
                    "- 'Check my emails' → CALL get_recent_emails or check_unread_emails tool\n"
                    "- 'Read that email' → CALL read_email tool\n"
                    "- 'What's on my calendar?' → CALL find_my_appointments tool\n"
                    "- 'Send email to [person]' → FIRST call search_contacts, THEN call send_email\n"
                    "- 'Delete/archive email' → CALL delete_email or archive_email tool\n"
                    "- 'Mark message read' → CALL mark_message_read tool\n\n"
                    "DO NOT offer to take messages or transfer calls - they ARE the owner."
                )
            )

            print(f"Owner mode active for {owner_name}")
        else:
            # Customer mode: Look up caller in contacts
            if not google_auth.is_connected(user_id):
                print("Google not connected - skipping caller lookup")
                agent.set_global_data(global_data)
                return

            # Look up caller in contacts
            try:
                contact = contacts_service.lookup_by_phone(caller_id_num, user_id=user_id)

                if contact and contact.get("name"):
                    caller_name = contact.get("name", "")
                    caller_email = contact.get("email", "")
                    caller_company = contact.get("company", "")

                    print(f"Identified caller: {caller_name} ({caller_email})")

                    # Update global_data with caller info
                    global_data.update({
                        "caller_identified": True,
                        "caller_name": caller_name,
                        "caller_email": caller_email,
                        "caller_company": caller_company,
                    })

                    # Add a prompt section with caller context and personalized greeting
                    caller_info_parts = [f"Name: {caller_name}"]
                    if caller_company:
                        caller_info_parts.append(f"Company: {caller_company}")
                    if caller_email:
                        caller_info_parts.append(f"Email: {caller_email}")
                    caller_phone_display = global_data.get("caller_phone", "")
                    if caller_phone_display:
                        caller_info_parts.append(f"Phone: {caller_phone_display}")

                    first_name = caller_name.split()[0] if caller_name else ""
                    business_name = global_data.get("business_name", "our business")

                    # Build the greeting instructions
                    greeting_body = (
                        f"IMPORTANT: This caller has been identified from contacts:\n"
                        f"{chr(10).join('  - ' + p for p in caller_info_parts)}\n\n"
                        f"Greeting Instructions:\n"
                        f"- Greet them warmly by first name: 'Hi {first_name}! Thanks for calling {business_name}.'\n"
                        f"- Do NOT ask for their name - you already know it's {caller_name}\n"
                    )
                    if caller_email:
                        greeting_body += f"- Do NOT ask for their email - you already have {caller_email}\n"
                    greeting_body += (
                        f"- Do NOT ask for their phone number - you already have it\n"
                        f"- When booking appointments or taking messages, use this information automatically\n"
                        f"- If they want to update their contact info, that's fine, but don't ask unprompted"
                    )

                    agent.prompt_add_section(
                        "Identified Caller",
                        body=greeting_body
                    )
                else:
                    print(f"Caller {caller_id_num} not found in contacts")
            except Exception as e:
                print(f"Error looking up caller: {e}")

        # Set final global_data with all accumulated values
        agent.set_global_data(global_data)

    def _get_available_capabilities(self, has_faqs: bool = True, has_knowledge: bool = True,
                                      google_connected: bool = None, is_owner_calling: bool = False) -> list:
        """
        Get list of capabilities based on what's actually connected/available.
        This ensures prompts only mention features that can be used.

        Args:
            has_faqs: Whether the user has FAQs configured
            has_knowledge: Whether the user has knowledge base documents indexed
            google_connected: Whether Google is connected for this user (None = check globally)
            is_owner_calling: Whether the caller is the business owner
        """
        # Use passed value if provided, otherwise check globally (for backwards compat)
        is_google_connected = google_connected if google_connected is not None else google_auth.is_connected()

        if is_owner_calling:
            # Owner mode: personal assistant capabilities
            caps = [
                "Reviewing messages left by callers",
            ]

            if is_google_connected:
                caps.insert(0, "Checking and managing your calendar and appointments")
                caps.append("Reading and checking your emails")
                caps.append("Looking up contacts")

            # FAQ/knowledge for owner too
            if has_faqs or has_knowledge:
                caps.append("Searching your knowledge base and FAQs")

            return caps

        # Customer mode: receptionist capabilities
        caps = [
            "Answering questions about the business (hours, location, services, pricing)",
            "Taking messages for the business owner",
        ]

        # Conditionally add FAQ/knowledge base capabilities
        if has_faqs and has_knowledge:
            caps.append("Providing information from the knowledge base and FAQs")
        elif has_faqs:
            caps.append("Answering frequently asked questions")
        elif has_knowledge:
            caps.append("Searching the knowledge base for detailed information")

        # Google-dependent capabilities
        if is_google_connected:
            caps.insert(0, "Scheduling, rescheduling, or canceling appointments (Google Calendar)")
            caps.append("Sending emails on behalf of callers (Gmail)")
            caps.append("Looking up contacts to identify callers")

        # Transfer capability (depends on owner phone being configured)
        if self.owner_phone:
            caps.append("Transferring to the business owner when available")

        return caps

    def _setup_knowledge_base(self, user_id: str = None):
        """
        Set up knowledge base search skill.

        For multi-tenant, we use a custom search function that can:
        1. Get user context from call metadata
        2. Search the user-specific knowledge base
        3. Fall back to global knowledge base if no user context

        Args:
            user_id: Optional user ID for user-specific knowledge base
        """
        # Get the appropriate index path
        index_path = knowledge_service._get_user_index_path(user_id)

        if index_path.exists():
            # Use native vector search skill with the index
            self.add_skill("native_vector_search", {
                "tool_name": "search_knowledge_base",
                "description": (
                    f"Search the {self.business_name} knowledge base for detailed information "
                    "about products, services, policies, procedures, and frequently asked questions. "
                    "Use this when you need specific information to answer customer questions."
                ),
                "index_file": str(index_path),
                "count": 3,
                "similarity_threshold": 0.2,
                "response_prefix": (
                    "Based on our knowledge base, here's what I found: "
                ),
                "response_postfix": (
                    "\n\nIf this doesn't fully answer your question, I can take a message "
                    "for someone to get back to you with more details."
                ),
                "no_results_message": (
                    "I couldn't find specific information about '{query}' in our knowledge base. "
                    "Would you like me to take a message for someone to research this and call you back?"
                ),
                "swaig_fields": {
                    "fillers": {
                        "en-US": [
                            "Searching for that...",
                                    "Let me look that up...",
                            "Checking our docs...",
                            "One sec...",
                            "Looking into that...",
                        ]
                    }
                }
            })
            print(f"Knowledge base loaded from {index_path}")
        else:
            print(f"Knowledge base not found at {index_path} - skill not loaded")
            # Add a placeholder function that explains the knowledge base isn't available
            self.define_tool(
                name="search_knowledge_base",
                description="Search the knowledge base for information (currently unavailable)",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query"
                        }
                    },
                    "required": ["query"]
                },
                handler=self._knowledge_base_unavailable,
                fillers={
                    "en-US": [
                        "Searching for that...",
                            "Let me check...",
                        "Looking that up...",
                        "One sec...",
                        "Checking...",
                    ]
                }
            )

    @log_swaig_call
    def _knowledge_base_unavailable(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Handler when knowledge base is not available"""
        return SwaigFunctionResult(
            "I don't have access to a detailed knowledge base right now. "
            "I can still help you with our FAQs, business hours, services, and taking messages. "
            "What would you like to know?"
        )

    def _setup_post_prompt(self):
        """Configure post-prompt for call summary generation"""
        self.set_post_prompt("""
After the call ends, provide a JSON summary of the conversation:
{
    "caller_name": "The caller's name if provided, otherwise null",
    "caller_phone": "The caller's phone number",
    "outcome": "One of: appointment_booked, appointment_cancelled, message_left, info_provided, transfer, hangup",
    "summary": "A brief 1-2 sentence summary of what happened on the call",
    "follow_up_needed": true or false,
    "notes": "Any important details or follow-up items"
}
Return ONLY the JSON object, no other text.
""")

    def on_summary(self, summary: Optional[Dict], raw_data: Optional[Dict] = None):
        """
        Handle post-call summary data and store as call log.

        Args:
            summary: Parsed summary from post-prompt (may be None)
            raw_data: Full POST body with call details
        """
        if not raw_data:
            print("[CallLog] No raw_data in on_summary")
            return

        try:
            # Extract call info from raw_data
            call_id = raw_data.get("call_id")
            caller_number = raw_data.get("caller_id_number") or raw_data.get("caller_id_num")

            # Get user_id and caller info from global_data
            global_data = raw_data.get("global_data", {})
            user_id = global_data.get("user_id")
            caller_name = global_data.get("caller_name")

            # Get call duration (total_minutes is provided)
            duration_seconds = None
            total_minutes = raw_data.get("total_minutes")
            if total_minutes:
                duration_seconds = int(total_minutes * 60)
            else:
                # Calculate from timestamps (microseconds)
                call_start = raw_data.get("call_start_date")
                call_end = raw_data.get("call_end_date")
                if call_start and call_end:
                    duration_seconds = int((call_end - call_start) / 1_000_000)

            # Get recording URL from SWMLVars
            swml_vars = raw_data.get("SWMLVars", {})
            recording_url = swml_vars.get("record_call_url")

            # Build transcript from call_log (user and assistant messages only)
            transcript_parts = []
            call_log_entries = raw_data.get("call_log", [])
            for entry in call_log_entries:
                role = entry.get("role")
                content = entry.get("content", "")
                if role == "user" and content:
                    transcript_parts.append(f"Caller: {content}")
                elif role == "assistant" and content and not content.startswith("{"):
                    # Skip JSON responses (like the final summary)
                    transcript_parts.append(f"Agent: {content}")
            transcript = "\n".join(transcript_parts) if transcript_parts else None

            if not call_id:
                print("[CallLog] No call_id found in raw_data")
                return

            # Get parsed summary from post_prompt_data
            post_prompt_data = raw_data.get("post_prompt_data", {})
            parsed_summaries = post_prompt_data.get("parsed", [])
            if parsed_summaries and isinstance(parsed_summaries, list) and len(parsed_summaries) > 0:
                summary = parsed_summaries[0]
            elif isinstance(summary, str):
                # Fallback: parse summary if it's a string
                try:
                    import json
                    summary = json.loads(summary)
                except:
                    summary = {"summary": summary}

            # Extract fields from summary
            summary = summary or {}
            outcome = summary.get("outcome", "unknown")
            call_summary = summary.get("summary", "")
            caller_name_from_summary = summary.get("caller_name")
            if caller_name_from_summary and not caller_name:
                caller_name = caller_name_from_summary
            follow_up = summary.get("follow_up_needed", False)
            notes = summary.get("notes", "")

            # Combine summary and notes
            full_summary = call_summary
            if notes:
                full_summary += f"\n\nNotes: {notes}"
            if follow_up:
                full_summary += "\n\n[Follow-up needed]"

            # Store in database
            db = SessionLocal()
            try:
                # Check if call log already exists (avoid duplicates)
                existing = db.query(CallLog).filter(CallLog.call_id == call_id).first()
                if existing:
                    # Update existing record
                    existing.outcome = outcome
                    existing.summary = full_summary
                    existing.duration_seconds = duration_seconds
                    existing.recording_url = recording_url
                    existing.transcript = transcript
                    if caller_name:
                        existing.caller_name = caller_name
                    print(f"[CallLog] Updated existing call log for {call_id}")
                else:
                    # Create new call log
                    call_log = CallLog(
                        user_id=user_id,
                        call_id=call_id,
                        caller_number=caller_number,
                        caller_name=caller_name,
                        duration_seconds=duration_seconds,
                        outcome=outcome,
                        recording_url=recording_url,
                        transcript=transcript,
                        summary=full_summary,
                    )
                    db.add(call_log)
                    print(f"[CallLog] Created new call log for {call_id}")

                db.commit()
            finally:
                db.close()

        except Exception as e:
            print(f"[CallLog] Error storing call log: {e}")
            import traceback
            traceback.print_exc()

    def _get_user_id(self, raw_data: Dict) -> Optional[str]:
        """Extract user_id from call global_data for multi-tenant operations"""
        global_data = raw_data.get("global_data", {})
        return global_data.get("user_id")

    def _get_user_config(self, raw_data: Dict, key: str, default=None):
        """Get user-specific config value from global_data with fallback to self"""
        global_data = raw_data.get("global_data", {})
        # Try global_data first (user-specific), then fall back to self (defaults)
        if key in global_data:
            return global_data.get(key)
        return getattr(self, key, default)

    def _times_match(self, requested_time: str, slot_time: str) -> bool:
        """
        Compare a requested time with a slot time, handling various formats.

        Examples:
            - "2pm" matches "2:00 PM"
            - "10:30" matches "10:30 AM"
            - "3:00 PM" matches "3:00 PM"
        """
        import re

        def normalize_time(time_str: str) -> tuple:
            """Extract hour and minute from various time formats, return (hour_24, minute)"""
            time_str = time_str.lower().strip()

            # Remove common prefixes/suffixes
            time_str = time_str.replace("at ", "").replace("around ", "")

            # Match patterns like "2pm", "2:00pm", "2:00 pm", "14:00", "2:30 PM"
            patterns = [
                r'(\d{1,2}):(\d{2})\s*(am|pm)?',  # 10:30 AM, 10:30, 2:00pm
                r'(\d{1,2})\s*(am|pm)',            # 2pm, 10am
            ]

            for pattern in patterns:
                match = re.search(pattern, time_str)
                if match:
                    groups = match.groups()

                    if len(groups) == 3:  # Hour:Minute AM/PM
                        hour = int(groups[0])
                        minute = int(groups[1])
                        ampm = groups[2]
                    elif len(groups) == 2:  # Hour AM/PM (no minute)
                        hour = int(groups[0])
                        minute = 0
                        ampm = groups[1]
                    else:
                        continue

                    # Convert to 24-hour format
                    if ampm:
                        if ampm == 'pm' and hour != 12:
                            hour += 12
                        elif ampm == 'am' and hour == 12:
                            hour = 0
                    elif hour <= 7:
                        # Assume PM for business hours (1-7 without am/pm)
                        hour += 12

                    return (hour, minute)

            return None

        requested_norm = normalize_time(requested_time)
        slot_norm = normalize_time(slot_time)

        if requested_norm is None or slot_norm is None:
            return False

        return requested_norm == slot_norm

    def _set_context_for_mode(self, agent: 'AgentBase', is_owner_calling: bool):
        """
        Log the caller mode for debugging.

        Context switching is handled via:
        1. Function filtering in _remove_unavailable_functions (per-call)
        2. Mode-appropriate prompts added in _on_call_received (per-call)
        3. is_owner_calling flag in global_data (per-call)

        We no longer modify contexts directly to avoid state bleeding across calls.
        All contexts remain available; the AI uses the right flow based on
        available functions and prompts.
        """
        mode = "OWNER" if is_owner_calling else "CUSTOMER"
        print(f"[Contexts] Mode: {mode} (functions filtered per-call, contexts unchanged)")

    def _remove_unavailable_functions(self, agent: 'AgentBase',
                                       has_faqs: bool,
                                       has_knowledge: bool,
                                       google_connected: bool,
                                       has_owner_phone: bool,
                                       is_owner_calling: bool = False):
        """
        Remove functions from ephemeral agent based on feature availability.

        The SDK creates an ephemeral copy of the agent with its own tool registry
        for each call, so modifications here only affect this specific call.

        Args:
            agent: The ephemeral agent for this call
            has_faqs: Whether the user has FAQs configured
            has_knowledge: Whether the user has knowledge base indexed
            google_connected: Whether Google OAuth is connected
            has_owner_phone: Whether owner phone is configured
            is_owner_calling: Whether the caller is the business owner
        """
        functions_to_remove = []

        if not has_faqs:
            functions_to_remove.extend(self.FAQ_FUNCTIONS)

        if not has_knowledge:
            functions_to_remove.extend(self.KNOWLEDGE_FUNCTIONS)

        if is_owner_calling:
            # Owner mode: remove customer-only functions and business info functions
            functions_to_remove.extend(self.CUSTOMER_ONLY_FUNCTIONS)
            functions_to_remove.extend(self.BUSINESS_INFO_FUNCTIONS)
        else:
            # Customer mode: remove owner-only functions
            functions_to_remove.extend(self.OWNER_ONLY_FUNCTIONS)
            # Also remove transfer if no owner phone configured
            if not has_owner_phone:
                if "transfer_to_owner" not in functions_to_remove:
                    functions_to_remove.append("transfer_to_owner")

        if not google_connected:
            functions_to_remove.extend(self.GOOGLE_FUNCTIONS)

        # Remove functions from the ephemeral agent's registry
        for func_name in functions_to_remove:
            if func_name in agent._tool_registry._swaig_functions:
                del agent._tool_registry._swaig_functions[func_name]

        mode = "OWNER" if is_owner_calling else "customer"
        remaining = list(agent._tool_registry._swaig_functions.keys())
        print(f"[Functions] Mode: {mode}")
        if functions_to_remove:
            print(f"[Functions] Removed ({len(functions_to_remove)}): {functions_to_remove}")
        print(f"[Functions] Available ({len(remaining)}): {remaining}")

    def _load_config(self):
        """Load configuration from database"""
        db = SessionLocal()
        try:
            self.business_name = ConfigModel.get(db, "business_name", config.DEFAULT_BUSINESS_NAME)
            self.timezone = ConfigModel.get(db, "timezone", config.DEFAULT_TIMEZONE)
            self.owner_email = ConfigModel.get(db, "owner_email", config.DEFAULT_OWNER_EMAIL)
            self.owner_phone = ConfigModel.get(db, "owner_phone", config.DEFAULT_OWNER_PHONE)
            self.agent_name = ConfigModel.get(db, "agent_name", config.AGENT_NAME)
            self.business_address = ConfigModel.get(db, "business_address", "")
            self.business_phone = ConfigModel.get(db, "business_phone", "")
            self.business_email = ConfigModel.get(db, "business_email", "")
            self.greeting_message = ConfigModel.get(db, "greeting_message", "")
            self.after_hours_message = ConfigModel.get(db, "after_hours_message", "")

            # Load business hours
            self.business_hours = {
                h.day_of_week: h.to_dict()
                for h in db.query(BusinessHours).all()
            }

            # Load holidays
            self.holidays = [
                h.to_dict() for h in db.query(Holiday).filter(Holiday.date >= datetime.now().date()).all()
            ]
        finally:
            db.close()

    def _substitute_variables(self, text: str) -> str:
        """
        Substitute template variables in user-provided text.

        Supported variables:
        - {business_name} - Business name
        - {agent_name} - Agent's name
        - {business_phone} - Business phone number
        - {business_email} - Business email
        - {business_address} - Business address
        - {owner_phone} - Owner's phone number
        - {timezone} - Business timezone
        """
        if not text:
            return text

        variables = {
            "business_name": self.business_name,
            "agent_name": self.agent_name,
            "business_phone": self.business_phone,
            "business_email": self.business_email,
            "business_address": self.business_address,
            "owner_phone": self.owner_phone,
            "timezone": self.timezone,
        }

        result = text
        for key, value in variables.items():
            result = result.replace(f"{{{key}}}", value or "")

        return result

    def _setup_prompts(self):
        """Set up agent prompts using POM (defaults, overridden per-call)"""
        # Set default global data (will be overridden per-call in _on_call_received)
        self.set_global_data({
            "business_name": self.business_name,
            "agent_name": self.agent_name,
            "timezone": self.timezone,
            "owner_phone": self.owner_phone,
        })

        # Main personality and role (default, overridden per-call)
        self.prompt_add_section(
            "Personality",
            f"You are {self.agent_name}, the virtual receptionist for {self.business_name}. "
            f"You answer incoming calls on behalf of {self.business_name} and help their customers and clients. "
            f"You work FOR the business owner, NOT for the callers. "
            f"Be warm, professional, and helpful. Keep responses concise for voice interaction."
        )

        # Core instructions - no conditional restrictions here, handled per-call
        core_bullets = [
            "Keep responses brief and conversational - this is a phone call, not a chat",
            "Always confirm important details like names, phone numbers, and dates",
            "If you're unsure about something, ask clarifying questions",
            "Be empathetic and patient with callers",
            "If a caller seems frustrated or asks for a human, offer to take a message or transfer",
        ]

        self.prompt_add_section(
            "Core Instructions",
            body="Follow these guidelines:",
            bullets=core_bullets
        )

        # Explicit tool usage instructions
        self.prompt_add_section(
            "IMPORTANT: Using Tools",
            body=(
                "You have tools available. You MUST CALL them to complete tasks.\n\n"
                "DO NOT just describe what you could do - actually CALL the tool.\n\n"
                "KEY TOOLS:\n"
                "- APPOINTMENTS: check_calendar_availability, book_appointment, find_my_appointments, cancel_appointment\n"
                "- BUSINESS INFO: get_business_hours, check_business_hours, get_location, get_services, search_faqs\n"
                "- MESSAGES: save_message (customer leaves message), transfer_to_owner\n"
                "- EMAIL: email_owner (customer emails owner)\n\n"
                "Example booking flow:\n"
                "1. Customer asks to book → CALL check_calendar_availability with the date\n"
                "2. Tell them available times, get their choice\n"
                "3. Get their name, email, phone\n"
                "4. CALL book_appointment with all the details"
            )
        )

        # Capabilities - dynamically generated based on what's connected
        capabilities = self._get_available_capabilities()
        self.prompt_add_section(
            "Your Capabilities",
            body="You can help callers with:",
            bullets=capabilities
        )

    def _format_business_hours(self) -> str:
        """Format business hours for display (uses default/init config)"""
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        lines = []
        for i, day in enumerate(days):
            hours = self.business_hours.get(i, {})
            if hours.get("is_closed"):
                lines.append(f"  {day}: Closed")
            elif hours.get("open_time") and hours.get("close_time"):
                open_t = hours["open_time"]
                close_t = hours["close_time"]
                lines.append(f"  {day}: {open_t} - {close_t}")
            else:
                lines.append(f"  {day}: Not set")
        return "\n".join(lines)

    def _format_business_hours_for_user(self, user_id: Optional[str]) -> str:
        """Format business hours for display for a specific user"""
        db = SessionLocal()
        try:
            if user_id:
                business_hours = {
                    h.day_of_week: h.to_dict()
                    for h in db.query(BusinessHours).filter(BusinessHours.user_id == user_id).all()
                }
            else:
                business_hours = self.business_hours
        finally:
            db.close()

        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        lines = []
        for i, day in enumerate(days):
            hours = business_hours.get(i, {})
            if hours.get("is_closed"):
                lines.append(f"  {day}: Closed")
            elif hours.get("open_time") and hours.get("close_time"):
                open_t = hours["open_time"]
                close_t = hours["close_time"]
                lines.append(f"  {day}: {open_t} - {close_t}")
            else:
                lines.append(f"  {day}: Not set")
        return "\n".join(lines)

    def _setup_state_machine(self):
        """Set up the conversation state machine using contexts"""
        contexts = self.define_contexts()

        # ==================== CUSTOMER CONTEXTS ====================
        # These contexts are ONLY for customers calling the business
        # Owner mode has completely separate contexts (see below)
        # Context switching is done in code, not by LLM

        # Default context with main conversation flow (CUSTOMER ONLY)
        default_ctx = contexts.add_context("default")

        # GREETING state - Customer greeting
        greeting_prompt = self._substitute_variables(self.greeting_message) if self.greeting_message else (
            f"You are a virtual assistant for {self.business_name}.\n\n"
            f"Greet the caller warmly and ask how you can help them today.\n"
            f"You can help with appointments, business information, leaving messages, and more."
        )
        # Greeting functions include both customer and owner functions
        # Function filtering per-call removes the wrong mode's functions
        greeting_functions = [
            # Customer-only functions (removed for owner calls)
            "check_business_hours",
            "get_business_info",
            "search_faqs",
            "save_message",
            "transfer_to_owner",
            "email_owner",
            # Shared functions
            "check_calendar_availability",
            "find_my_appointments",
            "book_appointment",
            "cancel_appointment",
            "get_appointment_types",
            "lookup_contact",
            "search_contacts",
            "send_email",
            # Owner-only functions (removed for customer calls)
            "get_messages",
            "mark_message_read",
            "delete_message",
            "get_recent_emails",
            "check_unread_emails",
            "read_email",
            "delete_email",
            "mark_email_read",
            "archive_email",
            "update_appointment",
            "respond_to_invite",
        ]
        default_ctx.add_step("greeting") \
            .set_text(greeting_prompt) \
            .set_functions(greeting_functions) \
            .set_valid_steps(["main_menu"]) \
            .set_valid_contexts(["schedule", "cancel"]) \
            .set_step_criteria("Caller has responded with their request")

        # MAIN_MENU state - Full customer service
        main_menu_text = (
            "You are helping a CUSTOMER. This is the main menu.\n\n"
            "CALL THE APPROPRIATE TOOL based on what they need:\n\n"
            "APPOINTMENTS:\n"
            "- 'What times are available?' → CALL check_calendar_availability tool\n"
            "- 'What types of appointments?' → CALL get_appointment_types tool\n"
            "- 'I want to book/schedule' → Ask appointment type, CALL check_calendar_availability, get details, CALL book_appointment\n"
            "- 'What appointments do I have?' → CALL find_my_appointments tool\n"
            "- 'Cancel my appointment' → CALL find_my_appointments, then CALL cancel_appointment\n\n"
            "BUSINESS INFO:\n"
            "- 'What are your hours?' → CALL get_business_hours tool\n"
            "- 'Are you open?' → CALL check_business_hours tool\n"
            "- 'Where are you located?' → CALL get_location tool\n"
            "- 'What services do you offer?' → CALL get_services tool\n"
            "- General question → CALL search_faqs tool\n\n"
            "MESSAGES:\n"
            "- 'I'd like to leave a message' → Collect name, callback number, message, urgency THEN CALL save_message\n"
            "- 'Can I talk to someone?' → CALL transfer_to_owner tool\n"
            "- 'Email the owner' → CALL email_owner tool\n\n"
            "For messages: ALWAYS collect all details before calling save_message."
        )
        # Main menu includes both customer and owner functions
        # Function filtering per-call removes the wrong mode's functions
        main_menu_functions = [
            # Customer-only functions (removed for owner calls)
            "check_business_hours",
            "get_business_hours",
            "get_business_info",
            "get_services",
            "get_location",
            "save_message",
            "transfer_to_owner",
            "email_owner",
            # Shared functions
            "search_faqs",
            "get_appointment_types",
            "check_calendar_availability",
            "find_my_appointments",
            "book_appointment",
            "cancel_appointment",
            "send_email",
            "lookup_contact",
            "search_contacts",
            # Owner-only functions (removed for customer calls)
            "get_messages",
            "mark_message_read",
            "delete_message",
            "get_recent_emails",
            "check_unread_emails",
            "read_email",
            "delete_email",
            "mark_email_read",
            "archive_email",
            "update_appointment",
            "respond_to_invite",
        ]
        valid_contexts = ["schedule", "cancel", "info", "message"]

        default_ctx.add_step("main_menu") \
            .set_text(main_menu_text) \
            .set_functions(main_menu_functions) \
            .set_valid_steps(["wrap_up"]) \
            .set_valid_contexts(valid_contexts) \
            .set_step_criteria("Customer's primary need has been identified and addressed")

        # AFTER_HOURS state
        if self.after_hours_message:
            after_hours_prompt = self._substitute_variables(self.after_hours_message)
        else:
            after_hours_prompt = (
                "The business is currently CLOSED.\n\n"
                "Tell the customer:\n"
                "1. The business is closed right now\n"
                "2. What the normal business hours are\n"
                "3. Offer to take a message so someone can call them back\n\n"
                "Be polite and helpful even though the business is closed."
            )

        default_ctx.add_step("after_hours") \
            .set_text(after_hours_prompt) \
            .set_functions([
                "get_business_hours",
                "save_message",
                "email_owner",
                "check_calendar_availability",
                "book_appointment",
            ]) \
            .set_valid_steps(["wrap_up"]) \
            .set_valid_contexts(["message", "schedule"]) \
            .set_step_criteria("Caller has chosen to leave a message or end the call")

        # SCHEDULE context - CUSTOMER booking appointment
        schedule_ctx = contexts.add_context("schedule")
        schedule_ctx.add_section(
            "Appointment Scheduling",
            "You are helping a customer book an appointment with this business."
        )

        # Schedule context - booking appointments
        schedule_ctx.add_step("collect_details") \
            .set_text(
                "A customer wants to BOOK AN APPOINTMENT.\n\n"
                "STEP BY STEP:\n"
                "1. Ask what TYPE of appointment they need (or CALL get_appointment_types to list options)\n"
                "2. Ask what day works for them\n"
                "3. CALL check_calendar_availability tool with the date\n"
                "4. Tell them the available times and confirm their choice\n"
                "5. Get their name, email, and phone\n"
                "6. CALL book_appointment tool with all the details\n\n"
                "REQUIRED for booking: appointment_type, start_time, end_time, name, email."
            ) \
            .set_functions([
                "check_calendar_availability",
                "get_appointment_types",
                "book_appointment",
            ]) \
            .set_valid_steps(["confirm_booking"]) \
            .set_valid_contexts(["default", "cancel"]) \
            .set_step_criteria("Customer has provided all details needed to book")

        schedule_ctx.add_step("confirm_booking") \
            .set_text(
                "CONFIRM the appointment before booking.\n\n"
                "Read back: date, time, their name and email.\n"
                "Ask: 'Does that all sound correct?'\n\n"
                "If YES: CALL book_appointment tool to create the booking.\n"
                "Tell them they'll receive a confirmation email."
            ) \
            .set_functions(["book_appointment", "cancel_appointment"]) \
            .set_valid_contexts(["default", "cancel"]) \
            .set_step_criteria("Appointment booked or customer wants changes")

        # CANCEL context - CUSTOMER cancelling appointment
        cancel_ctx = contexts.add_context("cancel")
        cancel_ctx.add_section(
            "Appointment Cancellation",
            "You are helping a customer cancel their existing appointment."
        )

        # Cancel context - customer cancelling appointment
        cancel_ctx.add_step("identify_appointment") \
            .set_text(
                "A customer wants to CANCEL their appointment.\n\n"
                "STEP BY STEP:\n"
                "1. Ask the caller for the name the appointment is under\n"
                "2. CALL find_my_appointments with their name (and phone/email if they provide it)\n"
                "3. Tell them what appointments were found\n"
                "4. Ask which one they want to cancel\n"
                "5. CALL cancel_appointment tool with the event_id\n"
                "6. Confirm it's cancelled\n"
                "7. Ask if they'd like to rebook for another time"
            ) \
            .set_functions(["find_my_appointments", "cancel_appointment", "check_calendar_availability", "book_appointment"]) \
            .set_valid_contexts(["default", "schedule"]) \
            .set_step_criteria("Appointment cancelled or customer changed mind")

        # INFO context - Answering CUSTOMER questions
        info_ctx = contexts.add_context("info")
        info_ctx.add_section(
            "Answering Questions",
            "You are answering a customer's questions about the business."
        )

        # Info context - answer business questions
        info_ctx.add_step("answer_questions") \
            .set_text(
                "A customer is asking questions about the business.\n\n"
                "CALL THE APPROPRIATE TOOL:\n"
                "- 'What are your hours?' → CALL get_business_hours tool\n"
                "- 'Are you open?' → CALL check_business_hours tool\n"
                "- 'Where are you located?' → CALL get_location tool\n"
                "- 'What services do you offer?' → CALL get_services tool\n"
                "- 'What types of appointments?' → CALL get_appointment_types tool\n"
                "- General question → CALL search_faqs tool\n"
                "- 'What times are available?' → CALL check_calendar_availability tool\n\n"
                "CALL the tool first, then answer based on the result."
            ) \
            .set_functions([
                "check_business_hours",
                "get_business_hours",
                "get_business_info",
                "get_services",
                "get_location",
                "search_faqs",
                "get_appointment_types",
                "check_calendar_availability",
                "find_my_appointments",
                "cancel_appointment",
                "save_message",
            ]) \
            .set_valid_contexts(["default", "schedule", "cancel", "message"]) \
            .set_step_criteria("Question answered")

        # MESSAGE context - CUSTOMER leaving a message (NOT for owner reviewing messages)
        message_ctx = contexts.add_context("message")
        message_ctx.add_section(
            "Taking a Message",
            "You are taking a message from a customer to give to the business owner."
        )

        # Message context - customer leaving a message for the owner
        message_ctx.add_step("collect_message") \
            .set_text(
                "A customer wants to LEAVE A MESSAGE for the business owner.\n\n"
                "COLLECT ALL INFO BEFORE SAVING:\n"
                "1. Ask for their NAME (required)\n"
                "2. Ask for their CALLBACK NUMBER (required)\n"
                "3. Ask what MESSAGE they'd like to leave (required)\n"
                "4. Ask if it's URGENT or can wait (required)\n\n"
                "VERIFY before saving:\n"
                "5. Read back: 'So I have [name], callback number [phone], and your message is [message]. Is that correct?'\n"
                "6. If confirmed, CALL save_message tool with ALL four details\n"
                "7. If not correct, ask what needs to be changed\n\n"
                "DO NOT call save_message until you have all 4 pieces of info confirmed."
            ) \
            .set_functions(["save_message"]) \
            .set_valid_steps(["confirm_message"]) \
            .set_step_criteria("All message details collected and verified")

        message_ctx.add_step("confirm_message") \
            .set_text(
                "CONFIRM the message has been saved.\n\n"
                "1. Tell them the message has been saved\n"
                "2. Confirm the owner will receive it\n"
                "3. Ask if there's anything else you can help with\n"
            ) \
            .set_functions(["check_calendar_availability", "book_appointment", "find_my_appointments", "cancel_appointment"]) \
            .set_valid_contexts(["default", "schedule", "cancel"]) \
            .set_step_criteria("Message confirmed and customer satisfied")

        # WRAP_UP in default context (CUSTOMER)
        default_ctx.add_step("wrap_up") \
            .set_text(
                "WRAPPING UP the call.\n\n"
                "1. Ask: 'Is there anything else I can help you with today?'\n"
                "2. If yes, help them with their next request\n"
                "3. If no, thank them for calling and wish them a great day\n\n"
                "Be warm and friendly."
            ) \
            .set_functions([
                "check_calendar_availability",
                "find_my_appointments",
                "book_appointment",
                "cancel_appointment",
                "save_message",
            ]) \
            .set_valid_steps(["main_menu"]) \
            .set_valid_contexts(["schedule", "cancel", "info", "message"]) \
            .set_step_criteria("Customer is done or has another request")

        # ==================== OWNER CONTEXT ====================
        # This context is ONLY for the business owner calling
        # Context switching is done in code - this becomes 'default' for owner calls
        owner_ctx = contexts.add_context("owner")
        owner_ctx.add_section(
            "Owner Mode",
            "The business OWNER is calling. You are their personal assistant. "
            "Be efficient and professional. Help with messages, emails, calendar, contacts."
        )

        # Owner greeting - FIRST step for owner calls
        owner_ctx.add_step("owner_greeting") \
            .set_text(
                "The business OWNER is calling.\n\n"
                "GREET THEM:\n"
                "1. Say hi using their name (from 'Owner Greeting' section)\n"
                "2. Give a quick status: unread emails, messages, upcoming appointments\n"
                "3. Ask: 'What would you like to check on?'\n\n"
                "Be friendly but efficient - this is the business owner, not a customer.\n"
                "If they immediately ask for something, help them right away."
            ) \
            .set_functions([
                "find_my_appointments",
                "check_calendar_availability",
                "book_appointment",
                "cancel_appointment",
                "update_appointment",
                "respond_to_invite",
                "get_messages",
                "mark_message_read",
                "delete_message",
                "get_recent_emails",
                "check_unread_emails",
                "read_email",
                "delete_email",
                "mark_email_read",
                "archive_email",
                "send_email",
                "lookup_contact",
                "search_contacts",
                "search_faqs",
            ]) \
            .set_valid_steps(["owner_menu"]) \
            .set_step_criteria("Owner said what they want to do")

        # Owner main menu - full owner functionality
        owner_ctx.add_step("owner_menu") \
            .set_text(
                "You are helping the business OWNER manage their business.\n\n"
                "CALL THE RIGHT TOOL based on what they need:\n\n"
                "CALENDAR:\n"
                "- 'What's on my calendar?' → CALL find_my_appointments tool\n"
                "- 'What times are available?' → CALL check_calendar_availability tool\n"
                "- 'Cancel that appointment' → CALL cancel_appointment tool\n"
                "- 'Move it to 3pm' → CALL update_appointment tool\n"
                "- 'Say yes/no to that invite' → CALL respond_to_invite tool\n\n"
                "MESSAGES:\n"
                "- 'Check my messages' → CALL get_messages tool\n"
                "- 'Mark that as read' → CALL mark_message_read tool\n"
                "- 'Delete that message' → CALL delete_message tool\n\n"
                "EMAILS:\n"
                "- 'Check my emails' → CALL get_recent_emails tool\n"
                "- 'How many unread?' → CALL check_unread_emails tool\n"
                "- 'Read that email' → CALL read_email tool\n"
                "- 'Send an email to [person]' → FIRST call search_contacts to get their email, THEN call send_email\n"
                "- 'Delete/archive email' → CALL delete_email or archive_email tool\n\n"
                "CONTACTS:\n"
                "- 'Look up [name]' → CALL search_contacts tool\n\n"
                "ALWAYS call the tool first, then tell them the result."
            ) \
            .set_functions([
                "find_my_appointments",
                "check_calendar_availability",
                "book_appointment",
                "cancel_appointment",
                "update_appointment",
                "respond_to_invite",
                "get_messages",
                "mark_message_read",
                "delete_message",
                "get_recent_emails",
                "check_unread_emails",
                "read_email",
                "delete_email",
                "mark_email_read",
                "archive_email",
                "send_email",
                "lookup_contact",
                "search_contacts",
                "search_faqs",
            ]) \
            .set_valid_steps(["review_messages", "review_emails", "review_calendar", "owner_wrap_up"]) \
            .set_step_criteria("Owner has completed their current task")

        # Owner reviewing messages from callers
        owner_ctx.add_step("review_messages") \
            .set_text(
                "The owner wants to CHECK THEIR MESSAGES.\n\n"
                "CALL THE RIGHT TOOL:\n"
                "- 'Check my messages' → CALL get_messages tool (defaults to unread)\n"
                "- 'Show all messages' → CALL get_messages tool with status='all'\n"
                "- 'Mark that as read' → CALL mark_message_read tool with message_id\n"
                "- 'Delete that one' → CALL delete_message tool with message_id\n\n"
                "After reading messages, tell them:\n"
                "- Who left the message\n"
                "- Their callback number\n"
                "- The message content\n"
                "- If it was marked urgent"
            ) \
            .set_functions([
                "get_messages",
                "mark_message_read",
                "delete_message",
                "get_recent_emails",
                "check_unread_emails",
                "read_email",
                "delete_email",
                "mark_email_read",
                "archive_email",
                "send_email",
            ]) \
            .set_valid_steps(["owner_menu", "review_emails", "owner_wrap_up"]) \
            .set_step_criteria("Owner finished reviewing messages")

        # Owner checking emails
        owner_ctx.add_step("review_emails") \
            .set_text(
                "The owner wants to CHECK THEIR EMAILS.\n\n"
                "CALL THE RIGHT TOOL:\n"
                "- 'How many unread?' → CALL check_unread_emails tool\n"
                "- 'Check my emails' → CALL get_recent_emails tool\n"
                "- 'Read that one' → CALL read_email tool with email_id\n"
                "- 'Reply to that' → use the sender's email from the email you just read, then CALL send_email\n"
                "- 'Send email to [person]' → FIRST call search_contacts to get their email, THEN call send_email\n"
                "- 'Mark as read' → CALL mark_email_read tool with email_id\n"
                "- 'Delete it' → CALL delete_email tool with email_id\n"
                "- 'Archive it' → CALL archive_email tool with email_id\n\n"
                "When listing emails, tell them:\n"
                "- Who it's from\n"
                "- The subject\n"
                "- When it arrived\n"
                "- A brief preview if available"
            ) \
            .set_functions([
                "get_recent_emails",
                "check_unread_emails",
                "read_email",
                "delete_email",
                "mark_email_read",
                "archive_email",
                "send_email",
                "search_contacts",
                "lookup_contact",
                "get_messages",
                "mark_message_read",
                "delete_message",
            ]) \
            .set_valid_steps(["owner_menu", "review_messages", "owner_wrap_up"]) \
            .set_step_criteria("Owner finished reviewing emails")

        # Owner checking calendar - all calendar tools
        owner_ctx.add_step("review_calendar") \
            .set_text(
                "The owner wants to CHECK THEIR CALENDAR.\n\n"
                "CALL THE RIGHT TOOL:\n"
                "- 'What's on my calendar?' → CALL find_my_appointments tool\n"
                "- 'What times are available?' → CALL check_calendar_availability tool\n"
                "- 'Cancel that one' → CALL cancel_appointment tool with event_id\n"
                "- 'Move it to 3pm' → CALL update_appointment tool\n"
                "- 'Say yes/no/maybe' → CALL respond_to_invite tool\n\n"
                "CALL the tool first, then tell them what you found."
            ) \
            .set_functions([
                "find_my_appointments",
                "check_calendar_availability",
                "book_appointment",
                "cancel_appointment",
                "update_appointment",
                "respond_to_invite",
                "get_recent_emails",
                "check_unread_emails",
                "read_email",
                "send_email",
                "get_messages",
                "mark_message_read",
                "search_contacts",
                "lookup_contact",
            ]) \
            .set_valid_steps(["owner_menu", "review_messages", "review_emails", "owner_wrap_up"]) \
            .set_step_criteria("Owner finished with calendar")

        # Owner wrap up
        owner_ctx.add_step("owner_wrap_up") \
            .set_text(
                "WRAPPING UP with the owner.\n\n"
                "1. Ask: 'Anything else you need?'\n"
                "2. If yes, help them with their next request\n"
                "3. If no, say goodbye: 'Have a great day!'\n\n"
                "Keep it brief and professional."
            ) \
            .set_functions([
                "find_my_appointments",
                "check_calendar_availability",
                "book_appointment",
                "cancel_appointment",
                "update_appointment",
                "respond_to_invite",
                "get_messages",
                "mark_message_read",
                "delete_message",
                "get_recent_emails",
                "check_unread_emails",
                "read_email",
                "delete_email",
                "mark_email_read",
                "archive_email",
                "send_email",
                "lookup_contact",
                "search_contacts",
                "search_faqs",
            ]) \
            .set_valid_steps(["owner_menu"]) \
            .set_step_criteria("Owner is done or has another request")

    def _register_functions(self):
        """Register all SWAIG functions based on available integrations"""
        # Always register core functions
        self._register_core_functions()

        # Always register Google functions for multi-tenant support
        # Individual handlers check connection status per-user
        self._register_calendar_functions()
        self._register_email_functions()
        self._register_contacts_functions()

        # Register owner-only functions (message management)
        self._register_owner_functions()

    def _register_core_functions(self):
        """Register core functions that are always available"""
        # Business Hours Check
        self.define_tool(
            name="check_business_hours",
            description="Check if the business is currently open and get today's hours",
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=self._check_business_hours,
            fillers={
                "en-US": [
                    "Let me check...",
                    "Checking hours...",
                    "One sec...",
                    "Let me see...",
                    "Checking...",
                ]
            }
        )

        # Get Business Hours
        self.define_tool(
            name="get_business_hours",
            description="Get the full business hours schedule for all days",
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=self._get_business_hours,
            fillers={
                "en-US": [
                    "Let me get that...",
                    "Pulling up hours...",
                    "One sec...",
                    "Here are the hours...",
                    "Checking...",
                ]
            }
        )

        # Get Business Info
        self.define_tool(
            name="get_business_info",
            description="Get general business information like name, location, contact details",
            parameters={
                "type": "object",
                "properties": {
                    "info_type": {
                        "type": "string",
                        "description": "Type of info: 'all', 'location', 'contact', 'hours'",
                        "enum": ["all", "location", "contact", "hours"]
                    }
                },
            },
            handler=self._get_business_info,
            fillers={
                "en-US": [
                    "Let me get that...",
                    "Sure...",
                    "One sec...",
                    "Here's the info...",
                    "Let me check...",
                ]
            }
        )

        # Get Services
        self.define_tool(
            name="get_services",
            description="Get list of services offered and their pricing",
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=self._get_services,
            fillers={
                "en-US": [
                    "Let me check...",
                    "Pulling that up...",
                    "One sec...",
                    "Here's what we offer...",
                    "Sure...",
                ]
            }
        )

        # Get Location
        self.define_tool(
            name="get_location",
            description="Get the business address and directions",
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=self._get_location,
            fillers={
                "en-US": [
                    "Let me get that...",
                    "Sure...",
                    "One sec...",
                    "Here's our location...",
                    "Getting directions...",
                ]
            }
        )

        # Search FAQs
        self.define_tool(
            name="search_faqs",
            description="Search frequently asked questions for an answer",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The question or keywords to search for"
                    }
                },
                "required": ["query"]
            },
            handler=self._search_faqs,
            fillers={
                "en-US": [
                    "Checking that...",
                    "Let me see...",
                    "Looking that up...",
                    "One sec...",
                    "Checking our FAQs...",
                ]
            }
        )

        # Get Appointment Types
        self.define_tool(
            name="get_appointment_types",
            description="Get available appointment types with duration and pricing",
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=self._get_appointment_types,
            fillers={
                "en-US": [
                    "Let me check...",
                    "Pulling that up...",
                    "One sec...",
                    "Here's what's available...",
                    "Checking options...",
                ]
            }
        )

        # Save Message
        self.define_tool(
            name="save_message",
            description="Save a message from the caller for the business owner. IMPORTANT: You MUST collect caller_name, caller_phone, message content, and urgency BEFORE calling this tool. Do not call without all details.",
            parameters={
                "type": "object",
                "properties": {
                    "caller_name": {
                        "type": "string",
                        "description": "The caller's full name (REQUIRED - ask if not known)"
                    },
                    "caller_phone": {
                        "type": "string",
                        "description": "The caller's callback phone number (REQUIRED - ask if not known)"
                    },
                    "message": {
                        "type": "string",
                        "description": "The message content the caller wants to leave"
                    },
                    "urgency": {
                        "type": "string",
                        "description": "Message urgency: 'normal' or 'urgent' (ask if it's urgent or can wait)",
                        "enum": ["normal", "urgent"]
                    }
                },
                "required": ["caller_name", "caller_phone", "message", "urgency"]
            },
            handler=self._save_message,
            fillers={
                "en-US": [
                    "Got it...",
                    "Saving that...",
                    "Recording that...",
                    "Let me note that down...",
                    "I'll make sure they get this...",
                ]
            }
        )

        # Transfer to Owner
        self.define_tool(
            name="transfer_to_owner",
            description="Transfer the call to the business owner",
            parameters={
                "type": "object",
                "properties": {
                    "reason": {
                        "type": ["string", "null"],
                        "description": "Reason for the transfer"
                    }
                },
            },
            handler=self._transfer_to_owner,
            fillers={
                "en-US": [
                    "Connecting you now...",
                    "Transferring...",
                    "Let me get them on the line...",
                    "Putting you through...",
                    "Just a moment...",
                ]
            }
        )

    def _register_calendar_functions(self):
        """Register Google Calendar functions"""

        # Check Calendar Availability
        self.define_tool(
            name="check_calendar_availability",
            description="Check available appointment slots for a specific date. If the customer requested a specific time, include it - if that time is available, confirm it directly without listing other options.",
            parameters={
                "type": "object",
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "The date to check (e.g., 'today', 'tomorrow', 'next Monday', '2024-01-15')"
                    },
                    "requested_time": {
                        "type": ["string", "null"],
                        "description": "Specific time the customer asked for (e.g., '2pm', '10:30', '3:00 PM'). If they asked for a specific time, include it here."
                    }
                },
                "required": ["date"]
            },
            handler=self._check_calendar_availability,
            fillers={
                "en-US": [
                    "Checking availability...",
                    "Let me see what's open...",
                    "Looking at the schedule...",
                    "Checking the calendar...",
                    "One sec...",
                ]
            }
        )

        # Find Appointments (for owner to see what's on calendar)
        self.define_tool(
            name="find_my_appointments",
            description="Find upcoming appointments on the calendar. For OWNER: returns all appointments, no parameters needed. For CUSTOMERS: ASK the caller for their name to look up their appointment. Also ask for phone or email if needed. Pass whatever they provide.",
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Customer's name"
                    },
                    "phone": {
                        "type": "string",
                        "description": "Customer's phone number"
                    },
                    "email": {
                        "type": "string",
                        "description": "Customer's email address"
                    }
                },
            },
            handler=self._find_my_appointments,
            fillers={
                "en-US": [
                    "Checking your appointments...",
                    "Let me look that up...",
                    "Pulling up the schedule...",
                    "One sec...",
                    "Looking at the calendar...",
                ]
            }
        )

        # Book Appointment
        self.define_tool(
            name="book_appointment",
            description="Book an appointment on the calendar. CALL THIS after checking availability and collecting customer details.",
            parameters={
                "type": "object",
                "properties": {
                    "start_time": {
                        "type": "string",
                        "description": "ISO format start time from available slots"
                    },
                    "end_time": {
                        "type": "string",
                        "description": "ISO format end time from available slots"
                    },
                    "appointment_type": {
                        "type": "string",
                        "description": "Type of appointment"
                    },
                    "attendee_name": {
                        "type": "string",
                        "description": "Customer's full name"
                    },
                    "attendee_email": {
                        "type": "string",
                        "description": "Customer's email address"
                    },
                    "attendee_phone": {
                        "type": ["string", "null"],
                        "description": "Customer's phone number"
                    }
                },
                "required": ["start_time", "end_time", "appointment_type", "attendee_name", "attendee_email"]
            },
            handler=self._book_appointment,
            fillers={
                "en-US": [
                    "Booking that for you...",
                    "Setting that up...",
                    "Got it, scheduling now...",
                    "Adding that to the calendar...",
                    "Just a sec...",
                ]
            }
        )

        # Cancel Appointment
        self.define_tool(
            name="cancel_appointment",
            description="Cancel an existing appointment by event ID. CALL find_my_appointments first to get the event_id.",
            parameters={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The Google Calendar event ID to cancel"
                    }
                },
                "required": ["event_id"]
            },
            handler=self._cancel_appointment,
            fillers={
                "en-US": [
                    "Canceling that...",
                    "Removing that from the calendar...",
                    "Got it...",
                    "Taking care of that...",
                    "One sec...",
                ]
            }
        )

        # Respond to Calendar Invite (owner only)
        self.define_tool(
            name="respond_to_invite",
            description="Respond to a calendar invitation with Yes, No, or Maybe (owner only)",
            parameters={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The calendar event ID to respond to"
                    },
                    "response": {
                        "type": "string",
                        "description": "Response: 'yes', 'no', or 'maybe'",
                        "enum": ["yes", "no", "maybe"]
                    }
                },
                "required": ["event_id", "response"]
            },
            handler=self._respond_to_invite,
            fillers={
                "en-US": [
                    "Sending that response...",
                    "Got it...",
                    "Updating your RSVP...",
                    "Done...",
                    "Taking care of that...",
                ]
            }
        )

        # Update Appointment (owner only)
        self.define_tool(
            name="update_appointment",
            description="Update an existing appointment - change time, title, or notes (owner only)",
            parameters={
                "type": "object",
                "properties": {
                    "event_id": {
                        "type": "string",
                        "description": "The calendar event ID to update"
                    },
                    "new_start_time": {
                        "type": ["string", "null"],
                        "description": "New start time in ISO format (optional)"
                    },
                    "new_end_time": {
                        "type": ["string", "null"],
                        "description": "New end time in ISO format (optional)"
                    },
                    "new_summary": {
                        "type": ["string", "null"],
                        "description": "New title/summary (optional)"
                    },
                    "notes": {
                        "type": ["string", "null"],
                        "description": "New description/notes (optional)"
                    }
                },
                "required": ["event_id"]
            },
            handler=self._update_appointment,
            fillers={
                "en-US": [
                    "Updating that...",
                    "Making that change...",
                    "Got it...",
                    "Adjusting the calendar...",
                    "One sec...",
                ]
            }
        )

    def _register_email_functions(self):
        """Register Gmail functions"""
        # Send Email
        self.define_tool(
            name="send_email",
            description="Send an email via Gmail. IMPORTANT: For owner mode, ALWAYS call search_contacts FIRST to get the recipient's email address before calling this tool.",
            parameters={
                "type": "object",
                "properties": {
                    "to_email": {
                        "type": "string",
                        "description": "Recipient email address"
                    },
                    "subject": {
                        "type": "string",
                        "description": "Email subject line"
                    },
                    "message": {
                        "type": "string",
                        "description": "Email body content"
                    },
                    "caller_name": {
                        "type": ["string", "null"],
                        "description": "Name of the caller sending the email"
                    },
                    "caller_phone": {
                        "type": ["string", "null"],
                        "description": "Phone number of the caller"
                    }
                },
                "required": ["to_email", "subject", "message"]
            },
            handler=self._send_email,
            fillers={
                "en-US": [
                    "Sending that...",
                    "On its way...",
                    "Sending now...",
                    "Got it...",
                    "Just a sec...",
                ]
            }
        )

        # Send Message to Owner via Email
        self.define_tool(
            name="email_owner",
            description="Send an email directly to the business owner",
            parameters={
                "type": "object",
                "properties": {
                    "subject": {
                        "type": ["string", "null"],
                        "description": "Email subject"
                    },
                    "message": {
                        "type": "string",
                        "description": "Email message content"
                    },
                    "caller_name": {
                        "type": ["string", "null"],
                        "description": "Name of the caller"
                    },
                    "caller_phone": {
                        "type": ["string", "null"],
                        "description": "Phone number for callback"
                    },
                    "urgency": {
                        "type": ["string", "null"],
                        "description": "Urgency level: 'normal' or 'urgent'"
                    }
                },
                "required": ["message"]
            },
            handler=self._email_owner,
            fillers={
                "en-US": [
                    "Sending that over...",
                    "Got it...",
                    "Passing that along...",
                    "On its way...",
                    "Just a sec...",
                ]
            }
        )

        # Get Recent Emails
        self.define_tool(
            name="get_recent_emails",
            description="Get recent emails from the inbox",
            parameters={
                "type": "object",
                "properties": {
                    "count": {
                        "type": ["integer", "null"],
                        "description": "Number of emails to retrieve (default 5)"
                    },
                    "from_address": {
                        "type": ["string", "null"],
                        "description": "Filter by sender email address"
                    }
                },
            },
            handler=self._get_recent_emails,
            fillers={
                "en-US": [
                    "Checking your inbox...",
                    "Let me see...",
                    "Pulling up emails...",
                    "One sec...",
                    "Looking at your inbox...",
                ]
            }
        )

        # Check Unread Count
        self.define_tool(
            name="check_unread_emails",
            description="Check how many unread emails are in the inbox",
            parameters={
                "type": "object",
                "properties": {},
            },
            handler=self._check_unread_emails,
            fillers={
                "en-US": [
                    "Checking unread...",
                    "Let me see...",
                    "Looking at your inbox...",
                    "One sec...",
                    "Checking...",
                ]
            }
        )

        # Read Email
        self.define_tool(
            name="read_email",
            description="Read the content of a specific email. Use the email_id from the EMAIL_IDS mapping in get_recent_emails response (e.g., if user says 'read email 1', look up 1=xxx in the mapping and use xxx).",
            parameters={
                "type": "object",
                "properties": {
                    "email_id": {
                        "type": "string",
                        "description": "The actual email ID from EMAIL_IDS mapping (e.g., '194a8b2c3d4e5f'), NOT the number"
                    }
                },
                "required": ["email_id"]
            },
            handler=self._read_email,
            fillers={
                "en-US": [
                    "Opening that...",
                    "Let me read that...",
                    "Pulling that up...",
                    "One sec...",
                    "Here we go...",
                ]
            }
        )

        # Delete Email (owner only)
        self.define_tool(
            name="delete_email",
            description="Delete an email by moving it to trash (owner only). Use the email_id from get_recent_emails.",
            parameters={
                "type": "object",
                "properties": {
                    "email_id": {
                        "type": "string",
                        "description": "The actual email ID from get_recent_emails (e.g., '194a8b2c3d4e5f')"
                    }
                },
                "required": ["email_id"]
            },
            handler=self._delete_email,
            fillers={
                "en-US": [
                    "Deleting that...",
                    "Done...",
                    "Got it...",
                    "Removing that...",
                    "Taken care of...",
                ]
            }
        )

        # Mark Email as Read (owner only)
        self.define_tool(
            name="mark_email_read",
            description="Mark an email as read (owner only). Use the email_id from get_recent_emails.",
            parameters={
                "type": "object",
                "properties": {
                    "email_id": {
                        "type": "string",
                        "description": "The actual email ID from get_recent_emails (e.g., '194a8b2c3d4e5f')"
                    }
                },
                "required": ["email_id"]
            },
            handler=self._mark_email_read,
            fillers={
                "en-US": [
                    "Marking as read...",
                    "Done...",
                    "Got it...",
                    "Taken care of...",
                    "Updated...",
                ]
            }
        )

        # Archive Email (owner only)
        self.define_tool(
            name="archive_email",
            description="Archive an email - removes from inbox but keeps in All Mail (owner only). Use the email_id from get_recent_emails.",
            parameters={
                "type": "object",
                "properties": {
                    "email_id": {
                        "type": "string",
                        "description": "The actual email ID from get_recent_emails (e.g., '194a8b2c3d4e5f')"
                    }
                },
                "required": ["email_id"]
            },
            handler=self._archive_email,
            fillers={
                "en-US": [
                    "Archiving that...",
                    "Done...",
                    "Got it...",
                    "Moving to archive...",
                    "Taken care of...",
                ]
            }
        )

    def _register_contacts_functions(self):
        """Register Google Contacts functions"""
        # Lookup Contact
        self.define_tool(
            name="lookup_contact",
            description="Look up a contact by phone number to identify the caller",
            parameters={
                "type": "object",
                "properties": {
                    "phone": {
                        "type": "string",
                        "description": "Phone number to look up"
                    }
                },
                "required": ["phone"]
            },
            handler=self._lookup_contact,
            fillers={
                "en-US": [
                    "Looking that up...",
                    "Checking contacts...",
                    "Let me see...",
                    "One sec...",
                    "Searching...",
                ]
            }
        )

        # Search Contacts
        self.define_tool(
            name="search_contacts",
            description="Search contacts by name, email, or phone number",
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Name, email, or phone number to search for"
                    }
                },
                "required": ["query"]
            },
            handler=self._search_contacts,
            fillers={
                "en-US": [
                    "Searching contacts...",
                    "Let me find them...",
                    "Looking that up...",
                    "One sec...",
                    "Checking...",
                ]
            }
        )

    def _register_owner_functions(self):
        """Register owner-only functions (message management)"""
        # Get Messages (for owner to review)
        self.define_tool(
            name="get_messages",
            description="Get messages left by callers for the business owner. These are notes taken during calls. Use this when the owner asks about their messages, unread messages, or wants to check messages. Can filter by status (unread/read/all).",
            parameters={
                "type": "object",
                "properties": {
                    "status": {
                        "type": ["string", "null"],
                        "description": "Filter by status: 'unread', 'read', or 'all' (default: unread)"
                    },
                    "limit": {
                        "type": ["integer", "null"],
                        "description": "Maximum number of messages to return (default 5)"
                    }
                },
            },
            handler=self._get_messages,
            fillers={
                "en-US": [
                    "Checking messages...",
                    "Let me see...",
                    "Pulling those up...",
                    "One sec...",
                    "Looking at your messages...",
                ]
            }
        )

        # Mark Message as Read
        self.define_tool(
            name="mark_message_read",
            description="Mark a message as read after reviewing it",
            parameters={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "The ID of the message to mark as read"
                    }
                },
                "required": ["message_id"]
            },
            handler=self._mark_message_read,
            fillers={
                "en-US": [
                    "Marking as read...",
                    "Done...",
                    "Got it...",
                    "Taken care of...",
                    "Updated...",
                ]
            }
        )

        # Delete Message (owner only)
        self.define_tool(
            name="delete_message",
            description="Delete a message from the inbox (owner only)",
            parameters={
                "type": "object",
                "properties": {
                    "message_id": {
                        "type": "integer",
                        "description": "The ID of the message to delete"
                    }
                },
                "required": ["message_id"]
            },
            handler=self._delete_message,
            fillers={
                "en-US": [
                    "Deleting that...",
                    "Done...",
                    "Got it...",
                    "Removing that...",
                    "Taken care of...",
                ]
            }
        )

    # ==================== Function Handlers ====================

    @log_swaig_call
    def _check_business_hours(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Check if business is currently open"""
        try:
            user_id = self._get_user_id(raw_data)
            timezone = self._get_user_config(raw_data, "timezone", config.DEFAULT_TIMEZONE)
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
            day_of_week = now.weekday()  # 0=Monday

            # Load user-specific holidays and business hours
            db = SessionLocal()
            try:
                if user_id:
                    holidays = [
                        h.to_dict() for h in db.query(Holiday).filter(
                            Holiday.user_id == user_id,
                            Holiday.date >= datetime.now().date()
                        ).all()
                    ]
                    business_hours = {
                        h.day_of_week: h.to_dict()
                        for h in db.query(BusinessHours).filter(BusinessHours.user_id == user_id).all()
                    }
                else:
                    holidays = self.holidays
                    business_hours = self.business_hours
            finally:
                db.close()

            # Check if today is a holiday
            today_str = now.strftime("%Y-%m-%d")
            for holiday in holidays:
                if holiday["date"] == today_str:
                    return SwaigFunctionResult(
                        f"Today is {holiday['name']}, so we are closed. "
                        f"We will reopen on the next business day."
                    )

            # Get today's hours
            hours = business_hours.get(day_of_week, {})

            if hours.get("is_closed"):
                return SwaigFunctionResult(
                    f"We are closed today. {self._get_next_open_day_for_user(user_id, timezone)}"
                )

            open_time_str = hours.get("open_time")
            close_time_str = hours.get("close_time")

            if open_time_str and close_time_str:
                open_time = datetime.strptime(open_time_str, "%H:%M:%S").time()
                close_time = datetime.strptime(close_time_str, "%H:%M:%S").time()
                current_time = now.time()

                if open_time <= current_time <= close_time:
                    close_formatted = datetime.strptime(close_time_str, "%H:%M:%S").strftime("%I:%M %p")
                    return SwaigFunctionResult(
                        f"We are currently open! We're open until {close_formatted} today."
                    )
                elif current_time < open_time:
                    open_formatted = datetime.strptime(open_time_str, "%H:%M:%S").strftime("%I:%M %p")
                    return SwaigFunctionResult(
                        f"We're not open yet. We open at {open_formatted} today."
                    )
                else:
                    return SwaigFunctionResult(
                        f"We're closed for today. {self._get_next_open_day_for_user(user_id, timezone)}"
                    )

            return SwaigFunctionResult("I couldn't determine our current hours.")

        except Exception as e:
            return SwaigFunctionResult(f"I had trouble checking the hours: {str(e)}")

    def _get_next_open_day_for_user(self, user_id: Optional[str], timezone: str) -> str:
        """Get the next day the business is open for a specific user"""
        tz = pytz.timezone(timezone)
        now = datetime.now(tz)

        # Load user-specific business hours
        db = SessionLocal()
        try:
            if user_id:
                business_hours = {
                    h.day_of_week: h.to_dict()
                    for h in db.query(BusinessHours).filter(BusinessHours.user_id == user_id).all()
                }
            else:
                business_hours = self.business_hours
        finally:
            db.close()

        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

        for i in range(1, 8):
            next_day = (now.weekday() + i) % 7
            hours = business_hours.get(next_day, {})
            if not hours.get("is_closed") and hours.get("open_time"):
                open_time = datetime.strptime(hours["open_time"], "%H:%M:%S").strftime("%I:%M %p")
                return f"We reopen {days[next_day]} at {open_time}."

        return "Please check back for our next opening."

    @log_swaig_call
    def _get_business_hours(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Get full business hours schedule"""
        user_id = self._get_user_id(raw_data)
        hours_text = self._format_business_hours_for_user(user_id)
        return SwaigFunctionResult(f"Our business hours are:\n{hours_text}")

    @log_swaig_call
    def _get_business_info(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Get business information"""
        user_id = self._get_user_id(raw_data)
        business_name = self._get_user_config(raw_data, "business_name", config.DEFAULT_BUSINESS_NAME)

        db = SessionLocal()
        try:
            info_type = args.get("info_type", "all")

            info_parts = []

            if info_type in ["all", "contact"]:
                phone = ConfigModel.get(db, "business_phone", "", user_id=user_id)
                email = ConfigModel.get(db, "business_email", "", user_id=user_id)
                if phone:
                    info_parts.append(f"Phone: {phone}")
                if email:
                    info_parts.append(f"Email: {email}")

            if info_type in ["all", "location"]:
                address = ConfigModel.get(db, "business_address", "", user_id=user_id)
                if address:
                    info_parts.append(f"Address: {address}")

            if info_type in ["all", "hours"]:
                info_parts.append(f"Hours:\n{self._format_business_hours_for_user(user_id)}")

            if info_parts:
                return SwaigFunctionResult("\n".join(info_parts))
            else:
                return SwaigFunctionResult(
                    f"I don't have detailed information on file, but you can reach "
                    f"{business_name} for more details."
                )
        finally:
            db.close()

    @log_swaig_call
    def _get_services(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Get services offered"""
        user_id = self._get_user_id(raw_data)

        db = SessionLocal()
        try:
            query = db.query(Service).filter(Service.is_active == True)
            if user_id:
                query = query.filter(Service.user_id == user_id)
            services = query.all()

            if not services:
                return SwaigFunctionResult(
                    "I don't have a detailed services list, but I can take a message "
                    "for someone to call you back with that information."
                )

            service_list = []
            for s in services:
                line = f"- {s.name}"
                if s.price:
                    line += f" ({s.price})"
                if s.description:
                    line += f": {s.description}"
                service_list.append(line)

            return SwaigFunctionResult(
                f"We offer the following services:\n" + "\n".join(service_list)
            )
        finally:
            db.close()

    @log_swaig_call
    def _get_location(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Get business location"""
        user_id = self._get_user_id(raw_data)

        db = SessionLocal()
        try:
            address = ConfigModel.get(db, "business_address", "", user_id=user_id)

            if address:
                return SwaigFunctionResult(f"We're located at {address}")
            else:
                return SwaigFunctionResult(
                    "I don't have the address on file. Would you like me to "
                    "take a message for someone to call you with directions?"
                )
        finally:
            db.close()

    @log_swaig_call
    def _search_faqs(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Search FAQs for an answer"""
        # Check if FAQs are available for this user
        global_data = raw_data.get("global_data", {})
        if not global_data.get("has_faqs", True):
            return SwaigFunctionResult(
                "No FAQs are configured for this business. "
                "I can still help you with business hours, services, or take a message."
            )

        db = SessionLocal()
        try:
            query = args.get("query", "").lower()
            user_id = global_data.get("user_id")

            # Filter FAQs by user if user context is available
            faq_query = db.query(FAQ).filter(FAQ.is_active == True)
            if user_id:
                faq_query = faq_query.filter(FAQ.user_id == user_id)
            faqs = faq_query.all()

            # Simple keyword matching (will be enhanced with knowledge base later)
            best_match = None
            best_score = 0

            query_words = set(query.split())

            for faq in faqs:
                # Check keywords
                keywords = set(k.lower() for k in (faq.keywords or []))
                keyword_matches = len(query_words & keywords)

                # Check question text
                question_words = set(faq.question.lower().split())
                question_matches = len(query_words & question_words)

                score = keyword_matches * 2 + question_matches

                if score > best_score:
                    best_score = score
                    best_match = faq

            if best_match and best_score > 0:
                return SwaigFunctionResult(best_match.answer)
            else:
                return SwaigFunctionResult(
                    "I don't have a specific answer for that in our FAQs. "
                    "Would you like me to take a message for someone to get back to you?"
                )
        finally:
            db.close()

    @log_swaig_call
    def _get_appointment_types(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Get available appointment types"""
        user_id = self._get_user_id(raw_data)

        db = SessionLocal()
        try:
            query = db.query(AppointmentType).filter(AppointmentType.is_active == True)
            if user_id:
                query = query.filter(AppointmentType.user_id == user_id)
            types = query.all()

            if not types:
                return SwaigFunctionResult(
                    "I don't have appointment types configured. "
                    "Would you like me to take a message about scheduling?"
                )

            # If only one type, just use it - no need to ask
            if len(types) == 1:
                t = types[0]
                duration_text = f"{t.duration_minutes} minutes"
                price_text = ""
                if t.price and t.price > 0:
                    price_text = f" The cost is ${t.price:.2f}."
                elif t.price == 0:
                    price_text = " It's free."
                return SwaigFunctionResult(
                    f"We offer {t.name} appointments, which are {duration_text}.{price_text} "
                    f"What day would you like to schedule?"
                )

            # Multiple types - list them
            type_list = []
            for t in types:
                line = f"- {t.name} ({t.duration_minutes} minutes)"
                if t.price and t.price > 0:
                    line += f" - ${t.price:.2f}"
                elif t.price == 0:
                    line += " - Free"
                if t.description:
                    line += f": {t.description}"
                type_list.append(line)

            return SwaigFunctionResult(
                "We offer the following appointment types:\n" + "\n".join(type_list) +
                "\nWhich type of appointment would you like?"
            )
        finally:
            db.close()

    @log_swaig_call
    def _save_message(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Save a message from the caller"""
        user_id = self._get_user_id(raw_data)
        business_name = self._get_user_config(raw_data, "business_name", config.DEFAULT_BUSINESS_NAME)

        db = SessionLocal()
        try:
            # Get global_data which may contain caller info from contacts lookup
            global_data = raw_data.get("global_data", {})
            caller_identified = global_data.get("caller_identified", False)

            message_text = args.get("message", "")
            urgency = args.get("urgency", "normal")
            call_id = raw_data.get("call_id", "")

            # Auto-fill caller info from contacts if identified
            if caller_identified:
                caller_name = args.get("caller_name") or global_data.get("caller_name", "Unknown")
                caller_phone = args.get("caller_phone") or global_data.get("caller_phone", raw_data.get("caller_id_num", ""))
            else:
                caller_name = args.get("caller_name", "Unknown")
                caller_phone = args.get("caller_phone", raw_data.get("caller_id_num", ""))

            if not message_text:
                return SwaigFunctionResult(
                    "I need the message content. What would you like me to tell them?"
                )

            message = Message(
                caller_name=caller_name,
                caller_phone=caller_phone,
                message=message_text,
                urgency=urgency,
                call_id=call_id,
                status="unread",
                user_id=user_id,
            )

            db.add(message)
            db.commit()

            urgency_text = " as urgent" if urgency == "urgent" else ""
            return SwaigFunctionResult(
                f"I've saved your message{urgency_text}. "
                f"{business_name} will receive it and get back to you "
                f"at {caller_phone if caller_phone else 'the number you called from'}."
            )
        except Exception as e:
            db.rollback()
            return SwaigFunctionResult(
                "I had trouble saving your message. Let me try again. "
                "What would you like me to tell them?"
            )
        finally:
            db.close()

    @log_swaig_call
    def _transfer_to_owner(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Transfer call to owner"""
        owner_phone = self._get_user_config(raw_data, "owner_phone", config.DEFAULT_OWNER_PHONE)

        if not owner_phone:
            return SwaigFunctionResult(
                "I'm sorry, I don't have a number to transfer you to. "
                "Would you like to leave a message instead?"
            )

        reason = args.get("reason", "Caller requested transfer")

        result = SwaigFunctionResult(
            f"I'll transfer you now. Please hold while I connect you."
        )
        result.connect(owner_phone)

        return result

    # ==================== Owner Function Handlers ====================

    @log_swaig_call
    def _get_messages(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Get messages left by callers (owner only)"""
        user_id = self._get_user_id(raw_data)
        global_data = raw_data.get("global_data", {})

        # Verify owner is calling
        if not global_data.get("is_owner_calling", False):
            return SwaigFunctionResult(
                "I can only retrieve messages for the business owner."
            )

        status_filter = args.get("status", "unread")
        limit = args.get("limit", 5)

        db = SessionLocal()
        try:
            query = db.query(Message).filter(Message.user_id == user_id)

            if status_filter == "unread":
                query = query.filter(Message.status == "unread")
            elif status_filter == "read":
                query = query.filter(Message.status == "read")
            # 'all' doesn't filter

            messages = query.order_by(Message.created_at.desc()).limit(limit).all()

            if not messages:
                if status_filter == "unread":
                    return SwaigFunctionResult("You have no unread messages.")
                else:
                    return SwaigFunctionResult("You have no messages.")

            # Format messages for voice
            msg_parts = []
            for i, msg in enumerate(messages, 1):
                time_str = msg.created_at.strftime("%-I:%M %p on %B %-d") if msg.created_at else "unknown time"
                urgency_str = " - URGENT" if msg.urgency == "urgent" else ""
                msg_parts.append(
                    f"Message {i}{urgency_str}: From {msg.caller_name or 'Unknown'} at {time_str}. "
                    f"They said: {msg.message}"
                )

            count_str = f"You have {len(messages)} {'unread ' if status_filter == 'unread' else ''}message{'s' if len(messages) != 1 else ''}. "
            return SwaigFunctionResult(count_str + " ".join(msg_parts))

        except Exception as e:
            print(f"Error getting messages: {e}")
            return SwaigFunctionResult("I had trouble retrieving your messages. Please try again.")
        finally:
            db.close()

    @log_swaig_call
    def _mark_message_read(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Mark a message as read (owner only)"""
        user_id = self._get_user_id(raw_data)
        global_data = raw_data.get("global_data", {})

        # Verify owner is calling
        if not global_data.get("is_owner_calling", False):
            return SwaigFunctionResult(
                "I can only mark messages as read for the business owner."
            )

        message_id = args.get("message_id")
        if not message_id:
            return SwaigFunctionResult("Which message would you like me to mark as read?")

        db = SessionLocal()
        try:
            message = db.query(Message).filter(
                Message.id == message_id,
                Message.user_id == user_id
            ).first()

            if not message:
                return SwaigFunctionResult(f"I couldn't find message {message_id}.")

            message.status = "read"
            db.commit()

            return SwaigFunctionResult(f"I've marked the message from {message.caller_name or 'Unknown'} as read.")

        except Exception as e:
            db.rollback()
            print(f"Error marking message read: {e}")
            return SwaigFunctionResult("I had trouble updating that message. Please try again.")
        finally:
            db.close()

    @log_swaig_call
    def _delete_message(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Delete a message (owner only)"""
        user_id = self._get_user_id(raw_data)
        global_data = raw_data.get("global_data", {})

        # Verify owner is calling
        if not global_data.get("is_owner_calling", False):
            return SwaigFunctionResult(
                "I can only delete messages for the business owner."
            )

        message_id = args.get("message_id")
        if not message_id:
            return SwaigFunctionResult("Which message would you like me to delete?")

        db = SessionLocal()
        try:
            message = db.query(Message).filter(
                Message.id == message_id,
                Message.user_id == user_id
            ).first()

            if not message:
                return SwaigFunctionResult(f"I couldn't find message {message_id}.")

            caller_name = message.caller_name or 'Unknown'
            db.delete(message)
            db.commit()

            return SwaigFunctionResult(f"I've deleted the message from {caller_name}.")

        except Exception as e:
            db.rollback()
            print(f"Error deleting message: {e}")
            return SwaigFunctionResult("I had trouble deleting that message. Please try again.")
        finally:
            db.close()

    # ==================== Calendar Function Handlers ====================

    @log_swaig_call
    def _check_calendar_availability(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Check available calendar slots for a date"""
        try:
            user_id = self._get_user_id(raw_data)
            global_data = raw_data.get("global_data", {})
            is_owner = global_data.get("is_owner_calling", False)

            # Check if Google Calendar is connected for this user
            if not google_auth.is_connected(user_id):
                if is_owner:
                    return SwaigFunctionResult(
                        "I'm sorry, the calendar system isn't connected right now. "
                        "Would you like to try something else?"
                    )
                else:
                    return SwaigFunctionResult(
                        "I'm sorry, the calendar system isn't connected right now. "
                        "Would you like me to take a message and have someone call you "
                        "back to schedule your appointment?"
                    )

            date_str = args.get("date", "tomorrow")
            appointment_type = args.get("appointment_type", "")

            # Check for obviously past dates
            date_lower = date_str.lower().strip()
            if date_lower in ['yesterday', 'last week', 'last month']:
                return SwaigFunctionResult(
                    "I can only book appointments for future dates. "
                    "Would you like to check availability for today or tomorrow?"
                )

            # Get duration from appointment type
            duration = 60  # Default 60 minutes
            db = SessionLocal()
            try:
                if appointment_type:
                    # Look up specified type
                    query = db.query(AppointmentType).filter(
                        AppointmentType.name.ilike(f"%{appointment_type}%"),
                        AppointmentType.is_active == True
                    )
                    if user_id:
                        query = query.filter(AppointmentType.user_id == user_id)
                    apt_type = query.first()
                    if apt_type:
                        duration = apt_type.duration_minutes
                else:
                    # No type specified - check if only one exists
                    query = db.query(AppointmentType).filter(AppointmentType.is_active == True)
                    if user_id:
                        query = query.filter(AppointmentType.user_id == user_id)
                    types = query.all()
                    if len(types) == 1:
                        duration = types[0].duration_minutes
            finally:
                db.close()

            # Get available slots for the user's calendar
            slots = calendar_service.get_available_slots(date_str, duration, user_id=user_id)

            if not slots:
                return SwaigFunctionResult(
                    f"I don't see any available slots for {date_str}. "
                    "Would you like to check a different day?"
                )

            # Check if customer requested a specific time
            requested_time = args.get("requested_time", "")
            matched_slot = None

            if requested_time:
                # Try to find a matching slot
                for slot in slots:
                    slot_time = slot.get("start", "")
                    if self._times_match(requested_time, slot_time):
                        matched_slot = slot
                        break

            # If we found an exact match for their requested time
            if matched_slot:
                slot_time = matched_slot.get("start", "")
                caller_identified = global_data.get("caller_identified", False)
                if caller_identified:
                    caller_name = global_data.get("caller_name", "")
                    return SwaigFunctionResult(
                        f"Great news {caller_name}! {slot_time} on {date_str} is available. "
                        f"I can book that for you right now. Would you like me to confirm this appointment?"
                    )
                else:
                    return SwaigFunctionResult(
                        f"Great news! {slot_time} on {date_str} is available. "
                        f"I'll book that for you. I just need your name and email address."
                    )

            # List available slots (either no specific time requested, or requested time didn't match)
            displayed_slots = slots[:5]
            slot_text = ", ".join([s["start"] for s in displayed_slots])

            more_text = ""
            if len(slots) > 5:
                more_text = f" I have {len(slots) - 5} more slots available as well."

            caller_identified = global_data.get("caller_identified", False)
            if caller_identified:
                caller_name = global_data.get("caller_name", "")
                first_name = caller_name.split()[0] if caller_name else ""
                return SwaigFunctionResult(
                    f"For {date_str}, I have openings at: {slot_text}.{more_text} "
                    f"Which time works best for you{', ' + first_name if first_name else ''}?"
                )
            else:
                return SwaigFunctionResult(
                    f"For {date_str}, I have openings at: {slot_text}.{more_text} "
                    "Which time works best for you?"
                )

        except Exception as e:
            print(f"Error checking calendar: {e}")
            if is_owner:
                return SwaigFunctionResult(
                    "I had trouble checking the calendar. Would you like to try something else?"
                )
            else:
                return SwaigFunctionResult(
                    "I had trouble checking the calendar. Would you like me to take "
                    "a message and have someone call you back to schedule?"
                )

    @log_swaig_call
    def _book_appointment(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Book an appointment on Google Calendar"""
        try:
            user_id = self._get_user_id(raw_data)
            global_data = raw_data.get("global_data", {})
            is_owner = global_data.get("is_owner_calling", False)

            # Check if Google Calendar is connected for this user
            if not google_auth.is_connected(user_id):
                if is_owner:
                    return SwaigFunctionResult(
                        "I'm sorry, I can't book appointments right now because the "
                        "calendar system isn't connected. Would you like to try something else?"
                    )
                else:
                    return SwaigFunctionResult(
                        "I'm sorry, I can't book appointments right now because the "
                        "calendar system isn't connected. Would you like me to take "
                        "your information and have someone call you back?"
                    )

            start_time = args.get("start_time")
            end_time = args.get("end_time")
            appointment_type = args.get("appointment_type", "")

            # If no appointment type specified, check if only one exists
            if not appointment_type:
                db = SessionLocal()
                try:
                    query = db.query(AppointmentType).filter(AppointmentType.is_active == True)
                    if user_id:
                        query = query.filter(AppointmentType.user_id == user_id)
                    types = query.all()
                    if len(types) == 1:
                        appointment_type = types[0].name
                    else:
                        appointment_type = "General"
                finally:
                    db.close()

            # Get global_data which may contain caller info from contacts lookup
            global_data = raw_data.get("global_data", {})
            caller_identified = global_data.get("caller_identified", False)

            # Use provided args, falling back to identified caller info if available
            attendee_name = args.get("attendee_name")
            attendee_email = args.get("attendee_email")
            attendee_phone = args.get("attendee_phone", raw_data.get("caller_id_num", ""))

            # Auto-fill from contacts if caller was identified and args not provided
            if caller_identified:
                if not attendee_name:
                    attendee_name = global_data.get("caller_name", "")
                if not attendee_email:
                    attendee_email = global_data.get("caller_email", "")
                if not attendee_phone:
                    attendee_phone = global_data.get("caller_phone", "")

            # Validate required fields
            if not start_time or not end_time:
                return SwaigFunctionResult(
                    "I need the appointment time. What time would you like to book?"
                )

            # Validate appointment is not in the past
            try:
                start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                if start_dt < datetime.now(start_dt.tzinfo):
                    return SwaigFunctionResult(
                        "I can't book appointments in the past. "
                        "Would you like to schedule for a future date instead?"
                    )
            except (ValueError, TypeError):
                pass  # Let calendar service handle invalid format

            if not attendee_name:
                return SwaigFunctionResult(
                    "I need your name to book the appointment. May I have your name?"
                )
            if not attendee_email:
                return SwaigFunctionResult(
                    "I need your email address to send the confirmation. "
                    "What's your email?"
                )

            # Book the appointment
            result = calendar_service.book_appointment(
                start_time=start_time,
                end_time=end_time,
                appointment_type=appointment_type,
                attendee_name=attendee_name,
                attendee_email=attendee_email,
                attendee_phone=attendee_phone,
                user_id=user_id,
            )

            if result.get("success"):
                # Send confirmation email
                try:
                    timezone = self._get_user_config(raw_data, "timezone", config.DEFAULT_TIMEZONE)
                    tz = pytz.timezone(timezone)
                    start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
                    start_local = start_dt.astimezone(tz)

                    email_service.send_appointment_confirmation(
                        to=attendee_email,
                        attendee_name=attendee_name,
                        appointment_type=appointment_type,
                        appointment_time=start_local.strftime("%I:%M %p"),
                        appointment_date=start_local.strftime("%B %d, %Y"),
                        user_id=user_id,
                    )
                except Exception as email_error:
                    print(f"Failed to send confirmation email: {email_error}")

                event_id = result.get('event_id', '')
                if is_owner and event_id:
                    return SwaigFunctionResult(
                        f"I've booked your {appointment_type} appointment for "
                        f"{result.get('start_time')}. A confirmation has been sent to "
                        f"{attendee_email}. Is there anything else I can help you with? "
                        f"[EVENT_ID for tool use only: {event_id}]"
                    )
                else:
                    return SwaigFunctionResult(
                        f"I've booked your {appointment_type} appointment for "
                        f"{result.get('start_time')}. A confirmation has been sent to "
                        f"{attendee_email}. Is there anything else I can help you with?"
                    )
            else:
                return SwaigFunctionResult(
                    f"I couldn't book the appointment: {result.get('error')}. "
                    "Would you like to try a different time?"
                )

        except Exception as e:
            print(f"Error booking appointment: {e}")
            if is_owner:
                return SwaigFunctionResult(
                    "I had trouble booking that appointment. Would you like to try something else?"
                )
            else:
                return SwaigFunctionResult(
                    "I had trouble booking your appointment. Would you like me to "
                    "take your information and have someone call you back?"
                )

    @log_swaig_call
    def _find_my_appointments(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Find upcoming appointments - shows all for owner, filters by phone/email for customers"""
        try:
            user_id = self._get_user_id(raw_data)
            global_data = raw_data.get("global_data", {})
            is_owner_calling = global_data.get("is_owner_calling", False)

            if not google_auth.is_connected(user_id):
                if is_owner_calling:
                    return SwaigFunctionResult(
                        "I'm sorry, I can't access the calendar right now. "
                        "Would you like to try something else?"
                    )
                else:
                    return SwaigFunctionResult(
                        "I'm sorry, I can't access the calendar right now. "
                        "Would you like me to take a message about your appointment?"
                    )

            # Get upcoming appointments
            appointments = calendar_service.get_upcoming_appointments(max_results=10, user_id=user_id)

            if not appointments:
                if is_owner_calling:
                    return SwaigFunctionResult(
                        "You don't have any upcoming appointments on your calendar."
                    )
                else:
                    return SwaigFunctionResult(
                        "I don't see any upcoming appointments on the calendar. "
                        "Would you like to schedule one?"
                    )

            # For owner: show all appointments without filtering
            if is_owner_calling:
                matching = appointments
            else:
                # For customers: search by name, phone, or email
                name = args.get("name") or ""
                phone = args.get("phone") or ""
                email = args.get("email") or ""

                if not name and not phone and not email:
                    return SwaigFunctionResult(
                        "I need your name to look up your appointment. "
                        "Could you tell me the name it was booked under?"
                    )

                # Filter appointments that match the caller
                matching = []
                # Normalize phone for comparison (last 10 digits)
                normalized_phone = ''.join(filter(str.isdigit, phone))[-10:] if phone else ""
                normalized_name = name.strip().lower() if name else ""

                for apt in appointments:
                    summary = (apt.get("summary") or "").lower()
                    description = (apt.get("description") or "").lower()
                    description_digits = ''.join(filter(str.isdigit, description))
                    attendees = [a.lower() for a in apt.get("attendees", []) if a]

                    name_match = normalized_name and (
                        normalized_name in summary or normalized_name in description
                    )
                    phone_match = normalized_phone and normalized_phone in description_digits
                    email_match = email and email.lower() in attendees

                    if name_match or phone_match or email_match:
                        matching.append(apt)

                if not matching:
                    return SwaigFunctionResult(
                        f"I couldn't find any upcoming appointments matching your information. "
                        "Are you sure you have an appointment scheduled with us?"
                    )

            # Format the appointments for voice
            timezone = self._get_user_config(raw_data, "timezone", config.DEFAULT_TIMEZONE)
            tz = pytz.timezone(timezone)

            apt_list = []
            for apt in matching[:3]:  # Limit to 3 for voice
                start = apt.get("start")
                if start:
                    start_dt = datetime.fromisoformat(start.replace('Z', '+00:00'))
                    start_local = start_dt.astimezone(tz)
                    date_str = start_local.strftime("%A, %B %d")
                    time_str = start_local.strftime("%I:%M %p")
                    apt_list.append({
                        "id": apt.get("id"),
                        "description": f"{apt.get('summary', 'Appointment')} on {date_str} at {time_str}"
                    })

            if is_owner_calling:
                # Owner mode: list appointments with event IDs for tool use
                event_ids = ",".join([f"{i+1}={a['id']}" for i, a in enumerate(apt_list)])
                if len(apt_list) == 1:
                    return SwaigFunctionResult(
                        f"You have one upcoming appointment: {apt_list[0]['description']}. "
                        f"Would you like to do anything with it? [EVENT_IDS for tool use only: {event_ids}]"
                    )
                else:
                    apt_descriptions = ". ".join([f"{i+1}. {a['description']}" for i, a in enumerate(apt_list)])
                    return SwaigFunctionResult(
                        f"You have {len(apt_list)} upcoming appointments: {apt_descriptions}. "
                        f"Would you like details on any of these? [EVENT_IDS for tool use only: {event_ids}]"
                    )
            else:
                # Customer mode: offer to cancel
                if len(apt_list) == 1:
                    return SwaigFunctionResult(
                        f"I found your appointment: {apt_list[0]['description']}. "
                        f"Would you like me to cancel this one? The appointment ID is {apt_list[0]['id']}."
                    )
                else:
                    apt_descriptions = "\n".join([f"- {a['description']} (ID: {a['id']})" for a in apt_list])
                    return SwaigFunctionResult(
                        f"I found {len(apt_list)} upcoming appointments:\n{apt_descriptions}\n"
                        "Which one would you like to cancel?"
                    )

        except Exception as e:
            print(f"Error finding appointments: {e}")
            if is_owner_calling:
                return SwaigFunctionResult(
                    "I had trouble looking up your appointments. Would you like to try something else?"
                )
            else:
                return SwaigFunctionResult(
                    "I had trouble looking up your appointments. Would you like me to "
                    "take a message for someone to help with your cancellation?"
                )

    @log_swaig_call
    def _cancel_appointment(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Cancel an existing appointment"""
        try:
            user_id = self._get_user_id(raw_data)
            global_data = raw_data.get("global_data", {})
            is_owner = global_data.get("is_owner_calling", False)

            if not google_auth.is_connected(user_id):
                if is_owner:
                    return SwaigFunctionResult(
                        "I'm sorry, I can't access the calendar right now. "
                        "Would you like to try something else?"
                    )
                else:
                    return SwaigFunctionResult(
                        "I'm sorry, I can't access the calendar right now. "
                        "Would you like me to take a message about the cancellation?"
                    )

            event_id = args.get("event_id")
            if not event_id:
                return SwaigFunctionResult(
                    "I need the appointment details to cancel it. "
                    "Could you provide more information about the appointment?"
                )

            result = calendar_service.cancel_appointment(event_id, user_id=user_id)

            if result.get("success"):
                return SwaigFunctionResult(
                    "The appointment has been cancelled. You should receive a "
                    "confirmation email shortly. Is there anything else I can help with?"
                )
            else:
                if is_owner:
                    return SwaigFunctionResult(
                        f"I couldn't cancel the appointment: {result.get('error')}. "
                        "Would you like to try something else?"
                    )
                else:
                    return SwaigFunctionResult(
                        f"I couldn't cancel the appointment: {result.get('error')}. "
                        "Would you like me to take a message about this?"
                    )

        except Exception as e:
            print(f"Error cancelling appointment: {e}")
            if is_owner:
                return SwaigFunctionResult(
                    "I had trouble cancelling that appointment. Would you like to try something else?"
                )
            else:
                return SwaigFunctionResult(
                    "I had trouble cancelling your appointment. Would you like me to "
                    "take a message for someone to call you back?"
                )

    # ==================== Email Function Handlers ====================

    @log_swaig_call
    def _send_email(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Send an email via Gmail"""
        try:
            user_id = self._get_user_id(raw_data)
            global_data = raw_data.get("global_data", {})
            is_owner = global_data.get("is_owner_calling", False)
            caller_identified = global_data.get("caller_identified", False)

            if not google_auth.is_connected(user_id):
                if is_owner:
                    return SwaigFunctionResult(
                        "I'm sorry, the email system isn't connected right now. "
                        "Would you like to try something else?"
                    )
                else:
                    return SwaigFunctionResult(
                        "I'm sorry, the email system isn't connected right now. "
                        "Would you like me to take a message instead?"
                    )

            to_email = args.get("to_email")
            subject = args.get("subject")
            message = args.get("message")

            # Auto-fill caller info from contacts if identified
            if caller_identified:
                caller_name = args.get("caller_name") or global_data.get("caller_name", "A caller")
                caller_phone = args.get("caller_phone") or global_data.get("caller_phone", raw_data.get("caller_id_num", ""))
            else:
                caller_name = args.get("caller_name", "A caller")
                caller_phone = args.get("caller_phone", raw_data.get("caller_id_num", ""))

            if not to_email:
                return SwaigFunctionResult("Who would you like me to send this email to?")
            if not subject:
                return SwaigFunctionResult("What should the subject line be?")
            if not message:
                return SwaigFunctionResult("What would you like the email to say?")

            result = email_service.send_custom_email(
                to=to_email,
                subject=subject,
                message=message,
                from_caller=caller_name,
                caller_phone=caller_phone,
                user_id=user_id,
            )

            if result.get("success"):
                return SwaigFunctionResult(
                    f"I've sent the email to {to_email}. Is there anything else "
                    "I can help you with?"
                )
            else:
                if is_owner:
                    return SwaigFunctionResult(
                        f"I couldn't send the email: {result.get('error')}. "
                        "Would you like to try something else?"
                    )
                else:
                    return SwaigFunctionResult(
                        f"I couldn't send the email: {result.get('error')}. "
                        "Would you like me to take a message instead?"
                    )

        except Exception as e:
            print(f"Error sending email: {e}")
            if is_owner:
                return SwaigFunctionResult(
                    "I had trouble sending the email. Would you like to try something else?"
                )
            else:
                return SwaigFunctionResult(
                    "I had trouble sending the email. Would you like me to take "
                    "a message instead?"
                )

    @log_swaig_call
    def _email_owner(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Send an email notification to the business owner"""
        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                # Fall back to saving as a message
                return self._save_message(args, raw_data)

            # Get global_data which may contain caller info from contacts lookup
            global_data = raw_data.get("global_data", {})
            caller_identified = global_data.get("caller_identified", False)

            message = args.get("message")
            subject = args.get("subject", "Message from Caller")
            urgency = args.get("urgency", "normal")

            # Auto-fill caller info from contacts if identified
            if caller_identified:
                caller_name = args.get("caller_name") or global_data.get("caller_name", "Unknown Caller")
                caller_phone = args.get("caller_phone") or global_data.get("caller_phone", raw_data.get("caller_id_num", ""))
            else:
                caller_name = args.get("caller_name", "Unknown Caller")
                caller_phone = args.get("caller_phone", raw_data.get("caller_id_num", ""))

            if not message:
                return SwaigFunctionResult("What message would you like me to send?")

            result = email_service.send_message_notification(
                caller_name=caller_name,
                caller_phone=caller_phone,
                message=message,
                urgency=urgency,
                user_id=user_id,
            )

            if result.get("success"):
                urgency_text = " as urgent" if urgency == "urgent" else ""
                return SwaigFunctionResult(
                    f"I've sent your message{urgency_text} to the business owner. "
                    f"They'll receive it right away. Is there anything else I can help with?"
                )
            else:
                # Fall back to database message
                save_result = self._save_message(args, raw_data)
                return save_result

        except Exception as e:
            print(f"Error emailing owner: {e}")
            # Fall back to database message
            return self._save_message(args, raw_data)

    # ==================== Contacts Function Handlers ====================

    @log_swaig_call
    def _lookup_contact(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Look up a contact by phone number"""
        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                return SwaigFunctionResult(
                    "I don't have access to contacts right now."
                )

            phone = args.get("phone", raw_data.get("caller_id_num", ""))
            if not phone:
                return SwaigFunctionResult("I need a phone number to look up.")

            contact = contacts_service.lookup_by_phone(phone, user_id=user_id)

            if contact and contact.get("name"):
                info_parts = [f"I found a contact: {contact['name']}"]
                if contact.get("company"):
                    info_parts.append(f"from {contact['company']}")
                if contact.get("email"):
                    info_parts.append(f"Email: {contact['email']}")

                return SwaigFunctionResult(". ".join(info_parts))
            else:
                return SwaigFunctionResult(
                    "I don't have that phone number in the contacts."
                )

        except Exception as e:
            print(f"Error looking up contact: {e}")
            return SwaigFunctionResult("I couldn't look up that contact.")

    @log_swaig_call
    def _search_contacts(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Search contacts by name, email, or phone number"""
        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                return SwaigFunctionResult(
                    "I don't have access to contacts right now."
                )

            query = args.get("query", "")
            if not query:
                return SwaigFunctionResult("What name, email, or phone would you like me to search for?")

            contacts = []

            # Check if query looks like a phone number (has mostly digits)
            digits_only = ''.join(filter(str.isdigit, query))
            if len(digits_only) >= 7:
                # Try phone lookup first
                contact = contacts_service.lookup_by_phone(query, user_id=user_id)
                if contact and contact.get("name"):
                    contacts = [contact]

            # If no phone match, or query doesn't look like a phone, search by name/email
            if not contacts:
                contacts = contacts_service.search_contacts(query, limit=3, user_id=user_id)

            if contacts:
                global_data = raw_data.get("global_data", {})
                is_owner = global_data.get("is_owner_calling", False)

                results = []
                for c in contacts:
                    parts = [c['name']]
                    if c.get('phone'):
                        parts.append(f"phone: {c['phone']}")
                    if c.get('email'):
                        parts.append(f"email: {c['email']}")
                    if c.get('company'):
                        parts.append(f"company: {c['company']}")
                    results.append(", ".join(parts))

                return SwaigFunctionResult(
                    f"I found {len(contacts)} contact(s): " + "; ".join(results)
                )
            else:
                return SwaigFunctionResult(f"I couldn't find any contacts matching '{query}'.")

        except Exception as e:
            print(f"Error searching contacts: {e}")
            return SwaigFunctionResult("I had trouble searching contacts.")

    # ==================== Email Read Function Handlers ====================

    @log_swaig_call
    def _get_recent_emails(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Get recent emails from inbox"""
        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                return SwaigFunctionResult(
                    "I don't have access to emails right now."
                )

            count = args.get("count", 5)
            from_address = args.get("from_address", "")

            query = f"from:{from_address}" if from_address else ""
            emails = email_service.get_recent_emails(max_results=count, query=query, user_id=user_id)

            if not emails:
                if from_address:
                    return SwaigFunctionResult(f"I don't see any recent emails from {from_address}.")
                return SwaigFunctionResult("The inbox appears to be empty.")

            # Format email summaries for voice (don't read IDs aloud)
            summaries = []
            id_mapping = []
            for i, email in enumerate(emails, 1):
                # Extract sender name from "Name <email>" format
                sender = email['from']
                if '<' in sender:
                    sender = sender.split('<')[0].strip().strip('"')

                unread = " (unread)" if email.get('is_unread') else ""
                email_id = email.get('id', '')
                summaries.append(
                    f"Email {i}{unread}: From {sender}, Subject: {email['subject']}"
                )
                id_mapping.append(f"{i}={email_id}")

            # Voice-friendly part first, then ID mapping for the AI (not to be read aloud)
            result_text = f"Here are the {len(emails)} most recent emails. " + ". ".join(summaries)
            result_text += f" [EMAIL_IDS for tool use only, do not read aloud: {','.join(id_mapping)}]"

            return SwaigFunctionResult(result_text)

        except Exception as e:
            print(f"Error getting emails: {e}")
            return SwaigFunctionResult("I had trouble checking the emails.")

    @log_swaig_call
    def _check_unread_emails(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Check unread email count"""
        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                return SwaigFunctionResult(
                    "I don't have access to emails right now."
                )

            count = email_service.get_unread_count(user_id=user_id)

            if count == 0:
                return SwaigFunctionResult("There are no unread emails in the inbox.")
            elif count == 1:
                return SwaigFunctionResult("There is 1 unread email in the inbox.")
            else:
                return SwaigFunctionResult(f"There are {count} unread emails in the inbox.")

        except Exception as e:
            print(f"Error checking unread count: {e}")
            return SwaigFunctionResult("I had trouble checking the unread count.")

    @log_swaig_call
    def _read_email(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Read full content of an email"""
        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                return SwaigFunctionResult(
                    "I don't have access to emails right now."
                )

            email_id = args.get("email_id")
            if not email_id:
                return SwaigFunctionResult("Which email would you like me to read?")

            email = email_service.get_email_content(email_id, user_id=user_id)

            if not email:
                return SwaigFunctionResult("I couldn't find that email.")

            # Format for voice reading
            sender = email['from']
            if '<' in sender:
                sender = sender.split('<')[0].strip().strip('"')

            # Truncate body for voice
            body = email.get('body', email.get('snippet', ''))
            if len(body) > 500:
                body = body[:500] + "... The email continues but I'll stop there."

            return SwaigFunctionResult(
                f"Email from {sender}. Subject: {email['subject']}. "
                f"Message: {body}"
            )

        except Exception as e:
            print(f"Error reading email: {e}")
            return SwaigFunctionResult("I had trouble reading that email.")

    @log_swaig_call
    def _delete_email(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Delete an email (owner only)"""
        global_data = raw_data.get("global_data", {})

        # Verify owner is calling
        if not global_data.get("is_owner_calling", False):
            return SwaigFunctionResult(
                "I can only delete emails for the business owner."
            )

        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                return SwaigFunctionResult(
                    "I don't have access to emails right now."
                )

            email_id = args.get("email_id")
            if not email_id:
                return SwaigFunctionResult("Which email would you like me to delete?")

            result = email_service.delete_email(email_id, user_id=user_id)

            if result.get("success"):
                return SwaigFunctionResult(
                    "I've deleted that email. Is there anything else?"
                )
            else:
                return SwaigFunctionResult(
                    f"I couldn't delete that email: {result.get('error')}. "
                    "Would you like to try something else?"
                )

        except Exception as e:
            print(f"Error deleting email: {e}")
            return SwaigFunctionResult("I had trouble deleting that email.")

    @log_swaig_call
    def _mark_email_read(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Mark an email as read (owner only)"""
        global_data = raw_data.get("global_data", {})

        # Verify owner is calling
        if not global_data.get("is_owner_calling", False):
            return SwaigFunctionResult(
                "I can only mark emails as read for the business owner."
            )

        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                return SwaigFunctionResult(
                    "I don't have access to emails right now."
                )

            email_id = args.get("email_id")
            if not email_id:
                return SwaigFunctionResult("Which email would you like me to mark as read?")

            result = email_service.mark_email_read(email_id, user_id=user_id)

            if result.get("success"):
                return SwaigFunctionResult(
                    "I've marked that email as read. Anything else?"
                )
            else:
                return SwaigFunctionResult(
                    f"I couldn't mark that email as read: {result.get('error')}"
                )

        except Exception as e:
            print(f"Error marking email read: {e}")
            return SwaigFunctionResult("I had trouble updating that email.")

    @log_swaig_call
    def _archive_email(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Archive an email (owner only)"""
        global_data = raw_data.get("global_data", {})

        # Verify owner is calling
        if not global_data.get("is_owner_calling", False):
            return SwaigFunctionResult(
                "I can only archive emails for the business owner."
            )

        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                return SwaigFunctionResult(
                    "I don't have access to emails right now."
                )

            email_id = args.get("email_id")
            if not email_id:
                return SwaigFunctionResult("Which email would you like me to archive?")

            result = email_service.archive_email(email_id, user_id=user_id)

            if result.get("success"):
                return SwaigFunctionResult(
                    "I've archived that email. It's still in All Mail if you need it. "
                    "Anything else?"
                )
            else:
                return SwaigFunctionResult(
                    f"I couldn't archive that email: {result.get('error')}"
                )

        except Exception as e:
            print(f"Error archiving email: {e}")
            return SwaigFunctionResult("I had trouble archiving that email.")

    @log_swaig_call
    def _respond_to_invite(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Respond to a calendar invitation (owner only)"""
        global_data = raw_data.get("global_data", {})

        # Verify owner is calling
        if not global_data.get("is_owner_calling", False):
            return SwaigFunctionResult(
                "I can only respond to calendar invitations for the business owner."
            )

        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                return SwaigFunctionResult(
                    "I don't have access to the calendar right now."
                )

            event_id = args.get("event_id")
            response = args.get("response")

            if not event_id:
                return SwaigFunctionResult("Which event would you like me to respond to?")
            if not response:
                return SwaigFunctionResult("Would you like me to respond yes, no, or maybe?")

            result = calendar_service.respond_to_invite(
                event_id=event_id,
                response=response,
                user_id=user_id
            )

            if result.get("success"):
                return SwaigFunctionResult(
                    f"{result.get('message')}. Is there anything else?"
                )
            else:
                return SwaigFunctionResult(
                    f"I couldn't respond to that invitation: {result.get('error')}"
                )

        except Exception as e:
            print(f"Error responding to invite: {e}")
            return SwaigFunctionResult("I had trouble responding to that invitation.")

    @log_swaig_call
    def _update_appointment(self, args: Dict, raw_data: Dict) -> SwaigFunctionResult:
        """Update an existing appointment (owner only)"""
        global_data = raw_data.get("global_data", {})

        # Verify owner is calling
        if not global_data.get("is_owner_calling", False):
            return SwaigFunctionResult(
                "I can only update appointments for the business owner."
            )

        try:
            user_id = self._get_user_id(raw_data)

            if not google_auth.is_connected(user_id):
                return SwaigFunctionResult(
                    "I don't have access to the calendar right now."
                )

            event_id = args.get("event_id")
            if not event_id:
                return SwaigFunctionResult("Which appointment would you like me to update?")

            # Get optional update fields
            new_start_time = args.get("new_start_time")
            new_end_time = args.get("new_end_time")
            new_summary = args.get("new_summary")
            notes = args.get("notes")

            if not any([new_start_time, new_end_time, new_summary, notes]):
                return SwaigFunctionResult(
                    "What would you like to change? I can update the time, title, or notes."
                )

            result = calendar_service.update_appointment(
                event_id=event_id,
                new_start_time=new_start_time,
                new_end_time=new_end_time,
                new_summary=new_summary,
                new_description=notes,
                user_id=user_id
            )

            if result.get("success"):
                msg = "I've updated that appointment"
                if result.get("new_time"):
                    msg += f" to {result['new_time']}"
                msg += ". Is there anything else?"
                return SwaigFunctionResult(msg)
            else:
                return SwaigFunctionResult(
                    f"I couldn't update that appointment: {result.get('error')}"
                )

        except Exception as e:
            print(f"Error updating appointment: {e}")
            return SwaigFunctionResult("I had trouble updating that appointment.")


# Create agent instance
def create_agent() -> EthanAgent:
    """Factory function to create agent instance"""
    return EthanAgent()


# User-specific agent cache for multi-tenant
_user_agent_cache: Dict[str, EthanAgent] = {}


def get_agent_for_user(user_id: str = None) -> EthanAgent:
    """
    Get or create an agent configured for a specific user.

    This is used for per-user routing in Phase 5.
    Each user gets their own agent instance with:
    - User-specific knowledge base
    - User-specific configuration (future)
    - User-specific Google credentials (future)

    Args:
        user_id: User ID for user-specific configuration.
                 If None, returns the default global agent.

    Returns:
        Configured EthanAgent instance
    """
    global _user_agent_cache

    if not user_id:
        # Return default agent (no user context)
        if "default" not in _user_agent_cache:
            _user_agent_cache["default"] = create_agent()
        return _user_agent_cache["default"]

    # Check cache first
    if user_id in _user_agent_cache:
        return _user_agent_cache[user_id]

    # Create user-specific agent
    # For now, we'll just create a standard agent
    # In Phase 5, this will load user-specific config, knowledge base, etc.
    agent = create_agent()
    _user_agent_cache[user_id] = agent

    print(f"Created agent for user: {user_id}")
    return agent


def clear_agent_cache(user_id: str = None):
    """
    Clear cached agent instances.

    Args:
        user_id: Specific user to clear, or None to clear all
    """
    global _user_agent_cache

    if user_id:
        if user_id in _user_agent_cache:
            del _user_agent_cache[user_id]
            print(f"Cleared agent cache for user: {user_id}")
    else:
        _user_agent_cache.clear()
        print("Cleared all agent caches")


if __name__ == "__main__":
    agent = create_agent()
    agent.run()
