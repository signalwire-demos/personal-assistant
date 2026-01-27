#!/usr/bin/env python3
"""
SWAIG Function Test Apparatus

This module provides a testing framework for testing all SWAIG function handlers
without needing the full SignalWire infrastructure.

Usage:
    python test_swaig_functions.py                    # Run all tests
    python test_swaig_functions.py --category calendar  # Run calendar tests only
    python test_swaig_functions.py --function book_appointment  # Run specific function
    python test_swaig_functions.py --interactive      # Interactive mode

Categories: core, calendar, email, contacts, messages
"""

import sys
import json
import argparse
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from unittest.mock import MagicMock, patch
from dataclasses import dataclass, field


# ==================== Test Configuration ====================

@dataclass
class TestUser:
    """Test user configuration"""
    user_id: str = "test-user-123"
    owner_name: str = "Test Owner"
    owner_email: str = "owner@test.com"
    owner_phone: str = "+15551234567"
    business_name: str = "Test Business"
    timezone: str = "America/Los_Angeles"
    google_connected: bool = True


@dataclass
class TestContext:
    """Context for a test call"""
    user: TestUser = field(default_factory=TestUser)
    is_owner: bool = False
    caller_id: str = "+15559876543"
    caller_name: Optional[str] = None

    def to_raw_data(self) -> Dict[str, Any]:
        """Convert to raw_data format expected by handlers"""
        return {
            "global_data": {
                "user_id": self.user.user_id,
                "is_owner": self.is_owner,
                "caller_id": self.caller_id,
                "caller_name": self.caller_name,
                "owner_phone": self.user.owner_phone,
                "owner_name": self.user.owner_name,
                "owner_email": self.user.owner_email,
                "business_name": self.user.business_name,
                "timezone": self.user.timezone,
                "google_connected": self.user.google_connected,
            }
        }


@dataclass
class TestResult:
    """Result of a test case"""
    function_name: str
    passed: bool
    args: Dict[str, Any]
    response: str
    error: Optional[str] = None
    duration_ms: float = 0


# ==================== Mock Services ====================

class MockCalendarService:
    """Mock Google Calendar service"""

    def __init__(self):
        self.appointments = []
        self.available_slots = [
            {"start": "09:00", "end": "09:30"},
            {"start": "10:00", "end": "10:30"},
            {"start": "14:00", "end": "14:30"},
            {"start": "15:00", "end": "15:30"},
        ]

    def check_availability(self, user_id: str, date: str, **kwargs) -> Dict:
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        return {
            "success": True,
            "date": date or tomorrow,
            "available_slots": self.available_slots,
            "message": f"Found {len(self.available_slots)} available slots"
        }

    def book_appointment(self, user_id: str, **kwargs) -> Dict:
        event_id = f"evt_{datetime.now().strftime('%Y%m%d%H%M%S')}"
        self.appointments.append({
            "event_id": event_id,
            **kwargs
        })
        return {
            "success": True,
            "event_id": event_id,
            "message": "Appointment booked successfully"
        }

    def find_appointments(self, user_id: str, **kwargs) -> Dict:
        return {
            "success": True,
            "appointments": [
                {
                    "event_id": "evt_123",
                    "summary": "Meeting with John",
                    "start": (datetime.now() + timedelta(days=1)).isoformat(),
                    "end": (datetime.now() + timedelta(days=1, hours=1)).isoformat(),
                },
                {
                    "event_id": "evt_456",
                    "summary": "Consultation",
                    "start": (datetime.now() + timedelta(days=2)).isoformat(),
                    "end": (datetime.now() + timedelta(days=2, hours=1)).isoformat(),
                }
            ]
        }

    def cancel_appointment(self, event_id: str, **kwargs) -> Dict:
        return {
            "success": True,
            "message": f"Appointment {event_id} cancelled"
        }

    def update_appointment(self, event_id: str, **kwargs) -> Dict:
        return {
            "success": True,
            "message": f"Appointment {event_id} updated"
        }

    def respond_to_invite(self, event_id: str, response: str, **kwargs) -> Dict:
        return {
            "success": True,
            "message": f"Responded '{response}' to invitation"
        }


class MockEmailService:
    """Mock Gmail service"""

    def __init__(self):
        self.emails = [
            {
                "id": "email_001",
                "from": "john@example.com",
                "subject": "Project Update",
                "snippet": "Here's the latest update on the project...",
                "date": datetime.now().isoformat(),
                "unread": True,
            },
            {
                "id": "email_002",
                "from": "jane@example.com",
                "subject": "Meeting Request",
                "snippet": "Can we schedule a meeting for...",
                "date": (datetime.now() - timedelta(hours=2)).isoformat(),
                "unread": True,
            },
            {
                "id": "email_003",
                "from": "sales@vendor.com",
                "subject": "Invoice #1234",
                "snippet": "Please find attached invoice...",
                "date": (datetime.now() - timedelta(days=1)).isoformat(),
                "unread": False,
            }
        ]

    def get_recent_emails(self, user_id: str, count: int = 5, **kwargs) -> Dict:
        return {
            "success": True,
            "emails": self.emails[:count]
        }

    def check_unread(self, user_id: str) -> Dict:
        unread = len([e for e in self.emails if e.get("unread")])
        return {
            "success": True,
            "unread_count": unread
        }

    def read_email(self, user_id: str, email_id: str) -> Dict:
        for email in self.emails:
            if email["id"] == email_id:
                return {
                    "success": True,
                    "email": {
                        **email,
                        "body": f"Full content of email from {email['from']}..."
                    }
                }
        return {"success": False, "error": "Email not found"}

    def send_email(self, user_id: str, to: str, subject: str, body: str, **kwargs) -> Dict:
        return {
            "success": True,
            "message_id": f"sent_{datetime.now().strftime('%Y%m%d%H%M%S')}",
            "message": f"Email sent to {to}"
        }

    def delete_email(self, user_id: str, email_id: str) -> Dict:
        return {"success": True, "message": f"Email {email_id} deleted"}

    def archive_email(self, user_id: str, email_id: str) -> Dict:
        return {"success": True, "message": f"Email {email_id} archived"}

    def mark_read(self, user_id: str, email_id: str) -> Dict:
        return {"success": True, "message": f"Email {email_id} marked as read"}


class MockContactsService:
    """Mock Google Contacts service"""

    def __init__(self):
        self.contacts = [
            {"name": "John Smith", "phone": "+15551112222", "email": "john@example.com"},
            {"name": "Jane Doe", "phone": "+15553334444", "email": "jane@example.com"},
            {"name": "Bob Wilson", "phone": "+15555556666", "email": "bob@example.com"},
        ]

    def lookup_by_phone(self, user_id: str, phone: str) -> Dict:
        for contact in self.contacts:
            if contact["phone"] == phone:
                return {"success": True, "contact": contact}
        return {"success": True, "contact": None}

    def search(self, user_id: str, query: str) -> Dict:
        results = [c for c in self.contacts if query.lower() in c["name"].lower()]
        return {"success": True, "contacts": results}


class MockDatabase:
    """Mock database session"""

    def __init__(self):
        self.messages = [
            {
                "id": 1,
                "caller_name": "John Caller",
                "caller_phone": "+15559998888",
                "message": "Please call me back about the proposal",
                "is_read": False,
                "urgency": "normal",
                "created_at": datetime.now() - timedelta(hours=2)
            },
            {
                "id": 2,
                "caller_name": "Emergency Contact",
                "caller_phone": "+15557776666",
                "message": "Urgent: Need to discuss contract ASAP",
                "is_read": False,
                "urgency": "urgent",
                "created_at": datetime.now() - timedelta(hours=1)
            }
        ]
        self.business_hours = {
            0: {"day_of_week": 0, "open_time": "09:00", "close_time": "17:00", "is_closed": False},
            1: {"day_of_week": 1, "open_time": "09:00", "close_time": "17:00", "is_closed": False},
            2: {"day_of_week": 2, "open_time": "09:00", "close_time": "17:00", "is_closed": False},
            3: {"day_of_week": 3, "open_time": "09:00", "close_time": "17:00", "is_closed": False},
            4: {"day_of_week": 4, "open_time": "09:00", "close_time": "17:00", "is_closed": False},
            5: {"day_of_week": 5, "is_closed": True},
            6: {"day_of_week": 6, "is_closed": True},
        }
        self.services = [
            {"name": "Consultation", "duration": 60, "price": 150.00},
            {"name": "Follow-up", "duration": 30, "price": 75.00},
        ]
        self.faqs = [
            {"question": "What are your hours?", "answer": "We are open Monday-Friday 9am-5pm"},
            {"question": "Do you offer virtual appointments?", "answer": "Yes, we offer both in-person and virtual appointments"},
        ]
        self.appointment_types = [
            {"name": "Initial Consultation", "duration_minutes": 60, "price": 150.00},
            {"name": "Follow-up", "duration_minutes": 30, "price": 75.00},
        ]


# ==================== Test Cases ====================

class SwaigFunctionTester:
    """Test harness for SWAIG functions"""

    def __init__(self):
        self.calendar_service = MockCalendarService()
        self.email_service = MockEmailService()
        self.contacts_service = MockContactsService()
        self.db = MockDatabase()
        self.results: List[TestResult] = []

    def _create_mock_agent(self):
        """Create a mock agent with handlers we can call"""
        # Import the actual agent
        from agent import EthanAgent

        # Create agent instance
        agent = EthanAgent()

        return agent

    def run_test(self, function_name: str, args: Dict, context: TestContext) -> TestResult:
        """Run a single function test"""
        import time
        start = time.time()

        try:
            # Import agent
            from agent import EthanAgent
            agent = EthanAgent()

            # Get the handler method
            handler_name = f"_{function_name}"
            if not hasattr(agent, handler_name):
                return TestResult(
                    function_name=function_name,
                    passed=False,
                    args=args,
                    response="",
                    error=f"Handler {handler_name} not found"
                )

            handler = getattr(agent, handler_name)

            # Call the handler with mocked services
            with patch.multiple(
                'agent',
                calendar_service=self.calendar_service,
                email_service=self.email_service,
                contacts_service=self.contacts_service,
            ):
                result = handler(args, context.to_raw_data())

            duration = (time.time() - start) * 1000

            # Extract response text
            response = result.response if hasattr(result, 'response') else str(result)

            return TestResult(
                function_name=function_name,
                passed=True,
                args=args,
                response=response,
                duration_ms=duration
            )

        except Exception as e:
            duration = (time.time() - start) * 1000
            return TestResult(
                function_name=function_name,
                passed=False,
                args=args,
                response="",
                error=str(e),
                duration_ms=duration
            )

    def run_category(self, category: str, context: Optional[TestContext] = None) -> List[TestResult]:
        """Run all tests in a category"""
        if context is None:
            context = TestContext()

        test_cases = self.get_test_cases(category)
        results = []

        for func_name, test_args_list in test_cases.items():
            for args in test_args_list:
                result = self.run_test(func_name, args, context)
                results.append(result)
                self.results.append(result)

        return results

    def get_test_cases(self, category: str) -> Dict[str, List[Dict]]:
        """Get test cases for a category"""

        if category == "core":
            return {
                "check_business_hours": [{}],
                "get_business_hours": [{}],
                "get_business_info": [
                    {},
                    {"info_type": "location"},
                    {"info_type": "hours"},
                ],
                "get_services": [{}],
                "get_location": [{}],
                "search_faqs": [
                    {"query": "hours"},
                    {"query": "virtual appointments"},
                ],
                "get_appointment_types": [{}],
            }

        elif category == "calendar":
            return {
                "check_calendar_availability": [
                    {"date": "tomorrow"},
                    {"date": "next Monday"},
                ],
                "book_appointment": [
                    {
                        "start_time": "2024-02-01T10:00:00",
                        "end_time": "2024-02-01T11:00:00",
                        "appointment_type": "Consultation",
                        "attendee_name": "Test Customer",
                        "attendee_email": "customer@test.com",
                        "attendee_phone": "+15551234567",
                    }
                ],
                "find_my_appointments": [
                    {},
                    {"phone": "+15551234567"},
                ],
                "cancel_appointment": [
                    {"event_id": "evt_123"},
                ],
                "update_appointment": [
                    {"event_id": "evt_123", "new_summary": "Updated Meeting"},
                ],
                "respond_to_invite": [
                    {"event_id": "evt_456", "response": "yes"},
                ],
            }

        elif category == "email":
            return {
                "send_email": [
                    {
                        "to_email": "recipient@test.com",
                        "subject": "Test Subject",
                        "message": "Test message body",
                    }
                ],
                "email_owner": [
                    {"message": "Customer inquiry about services"},
                ],
                "get_recent_emails": [
                    {},
                    {"count": 3},
                ],
                "check_unread_emails": [{}],
                "read_email": [
                    {"email_id": "email_001"},
                ],
                "delete_email": [
                    {"email_id": "email_003"},
                ],
                "mark_email_read": [
                    {"email_id": "email_001"},
                ],
                "archive_email": [
                    {"email_id": "email_002"},
                ],
            }

        elif category == "contacts":
            return {
                "lookup_contact": [
                    {"phone": "+15551112222"},
                    {"phone": "+15559999999"},  # Not found
                ],
                "search_contacts": [
                    {"query": "John"},
                    {"query": "Wilson"},
                ],
            }

        elif category == "messages":
            return {
                "save_message": [
                    {
                        "caller_name": "Test Caller",
                        "caller_phone": "+15551234567",
                        "message": "Please call me back",
                        "urgency": "normal",
                    }
                ],
                "get_messages": [
                    {},
                    {"status": "unread"},
                ],
                "mark_message_read": [
                    {"message_id": 1},
                ],
                "delete_message": [
                    {"message_id": 2},
                ],
            }

        else:
            return {}

    def print_results(self, results: Optional[List[TestResult]] = None):
        """Print test results"""
        if results is None:
            results = self.results

        passed = len([r for r in results if r.passed])
        failed = len([r for r in results if not r.passed])

        print("\n" + "=" * 70)
        print("TEST RESULTS")
        print("=" * 70)

        for result in results:
            status = "PASS" if result.passed else "FAIL"
            print(f"\n[{status}] {result.function_name}")
            print(f"  Args: {json.dumps(result.args, default=str)}")
            if result.passed:
                response_preview = result.response[:100] + "..." if len(result.response) > 100 else result.response
                print(f"  Response: {response_preview}")
            else:
                print(f"  Error: {result.error}")
            print(f"  Duration: {result.duration_ms:.1f}ms")

        print("\n" + "=" * 70)
        print(f"SUMMARY: {passed} passed, {failed} failed, {len(results)} total")
        print("=" * 70)


def run_interactive():
    """Interactive test mode"""
    tester = SwaigFunctionTester()

    print("\nSWAIG Function Test - Interactive Mode")
    print("=" * 50)
    print("Commands:")
    print("  test <function_name> [args_json]  - Test a function")
    print("  category <name>                   - Run category tests")
    print("  list                              - List all functions")
    print("  owner                             - Switch to owner mode")
    print("  customer                          - Switch to customer mode")
    print("  quit                              - Exit")
    print()

    context = TestContext()

    while True:
        try:
            cmd = input(f"[{'owner' if context.is_owner else 'customer'}] > ").strip()
        except EOFError:
            break

        if not cmd:
            continue

        parts = cmd.split(maxsplit=1)
        command = parts[0].lower()

        if command == "quit" or command == "exit":
            break

        elif command == "owner":
            context.is_owner = True
            print("Switched to owner mode")

        elif command == "customer":
            context.is_owner = False
            print("Switched to customer mode")

        elif command == "list":
            categories = ["core", "calendar", "email", "contacts", "messages"]
            for cat in categories:
                print(f"\n{cat.upper()}:")
                for func in tester.get_test_cases(cat).keys():
                    print(f"  - {func}")

        elif command == "category":
            if len(parts) > 1:
                cat = parts[1].lower()
                results = tester.run_category(cat, context)
                tester.print_results(results)
            else:
                print("Usage: category <core|calendar|email|contacts|messages>")

        elif command == "test":
            if len(parts) > 1:
                func_parts = parts[1].split(maxsplit=1)
                func_name = func_parts[0]
                args = {}
                if len(func_parts) > 1:
                    try:
                        args = json.loads(func_parts[1])
                    except json.JSONDecodeError:
                        print("Invalid JSON args")
                        continue

                result = tester.run_test(func_name, args, context)
                tester.print_results([result])
            else:
                print("Usage: test <function_name> [args_json]")

        else:
            print(f"Unknown command: {command}")


def main():
    parser = argparse.ArgumentParser(description="SWAIG Function Test Apparatus")
    parser.add_argument("--category", "-c", help="Test category: core, calendar, email, contacts, messages")
    parser.add_argument("--function", "-f", help="Test specific function")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--owner", action="store_true", help="Run as owner")
    parser.add_argument("--all", "-a", action="store_true", help="Run all tests")

    args = parser.parse_args()

    if args.interactive:
        run_interactive()
        return

    tester = SwaigFunctionTester()
    context = TestContext(is_owner=args.owner)

    if args.function:
        # Get default args for the function
        for cat in ["core", "calendar", "email", "contacts", "messages"]:
            cases = tester.get_test_cases(cat)
            if args.function in cases:
                for test_args in cases[args.function]:
                    result = tester.run_test(args.function, test_args, context)
                    tester.print_results([result])
                break
        else:
            print(f"Function {args.function} not found")

    elif args.category:
        results = tester.run_category(args.category, context)
        tester.print_results(results)

    elif args.all:
        for cat in ["core", "calendar", "email", "contacts", "messages"]:
            print(f"\n{'='*20} {cat.upper()} {'='*20}")
            results = tester.run_category(cat, context)
            tester.print_results(results)

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
