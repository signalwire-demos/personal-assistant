#!/usr/bin/env python3
"""
SWAIG HTTP Endpoint Tester

Tests SWAIG function endpoints via HTTP POST requests,
simulating what SignalWire sends to the agent.

Usage:
    # Start the agent first:
    python agent.py

    # Then run tests:
    python test_swaig_http.py --base-url http://localhost:3000
    python test_swaig_http.py --function check_calendar_availability --args '{"date": "tomorrow"}'
    python test_swaig_http.py --all --user-id test-user-123
"""

import argparse
import json
import requests
from typing import Dict, Any, Optional
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TestConfig:
    """Test configuration"""
    base_url: str = "http://localhost:3000"
    user_id: str = "test-user-123"
    caller_id: str = "+15559876543"
    is_owner: bool = False


def make_swaig_request(
    base_url: str,
    function_name: str,
    args: Dict[str, Any],
    user_id: str = "test-user-123",
    caller_id: str = "+15559876543",
    is_owner: bool = False,
) -> Dict:
    """
    Make a SWAIG function call request

    This simulates the POST request SignalWire sends when
    the AI calls a tool function.
    """

    # SWAIG callback URL format
    url = f"{base_url}/swaig"

    # Build the request payload in SWAIG format
    payload = {
        "function": function_name,
        "argument": {
            "parsed": [args],
            "raw": json.dumps(args),
        },
        "call_id": f"test-call-{datetime.now().strftime('%Y%m%d%H%M%S')}",
        "node_id": "test-node",
        "params": {},
        "meta_data": {
            "user_id": user_id,
            "is_owner": is_owner,
            "caller_id": caller_id,
        },
        # Include user context in query params format
        "global_data": {
            "user_id": user_id,
            "is_owner": is_owner,
            "caller_id": caller_id,
        }
    }

    headers = {
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(url, json=payload, headers=headers, timeout=30)
        return {
            "status_code": response.status_code,
            "success": response.status_code == 200,
            "response": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text,
        }
    except requests.exceptions.RequestException as e:
        return {
            "status_code": 0,
            "success": False,
            "error": str(e),
        }


def get_test_cases() -> Dict[str, list]:
    """Get all test cases organized by category"""
    return {
        "core": [
            ("check_business_hours", {}),
            ("get_business_hours", {}),
            ("get_business_info", {}),
            ("get_business_info", {"info_type": "location"}),
            ("get_services", {}),
            ("get_location", {}),
            ("search_faqs", {"query": "hours"}),
            ("get_appointment_types", {}),
        ],
        "calendar": [
            ("check_calendar_availability", {"date": "tomorrow"}),
            ("check_calendar_availability", {"date": "next Monday"}),
            ("find_my_appointments", {}),
            ("book_appointment", {
                "start_time": "2024-02-01T10:00:00",
                "end_time": "2024-02-01T11:00:00",
                "appointment_type": "Consultation",
                "attendee_name": "Test Customer",
                "attendee_email": "test@example.com",
            }),
            ("cancel_appointment", {"event_id": "test_event_123"}),
        ],
        "email": [
            ("check_unread_emails", {}),
            ("get_recent_emails", {}),
            ("get_recent_emails", {"count": 3}),
            ("email_owner", {"message": "Test message from customer"}),
        ],
        "contacts": [
            ("lookup_contact", {"phone": "+15551234567"}),
            ("search_contacts", {"query": "John"}),
        ],
        "messages": [
            ("save_message", {
                "caller_name": "Test Caller",
                "caller_phone": "+15551234567",
                "message": "Please call me back",
            }),
            ("get_messages", {}),
            ("get_messages", {"status": "unread"}),
        ],
        "owner_only": [
            ("get_messages", {}),
            ("mark_message_read", {"message_id": 1}),
            ("delete_message", {"message_id": 1}),
            ("read_email", {"email_id": "test_email_123"}),
            ("delete_email", {"email_id": "test_email_123"}),
            ("archive_email", {"email_id": "test_email_123"}),
            ("update_appointment", {"event_id": "test_event_123", "new_summary": "Updated"}),
            ("respond_to_invite", {"event_id": "test_event_123", "response": "yes"}),
        ],
    }


def run_tests(config: TestConfig, category: Optional[str] = None, function: Optional[str] = None):
    """Run tests based on configuration"""
    test_cases = get_test_cases()

    results = []

    if function:
        # Run single function test
        args = {}
        for cat, cases in test_cases.items():
            for func_name, func_args in cases:
                if func_name == function:
                    args = func_args
                    break

        print(f"\nTesting: {function}")
        print(f"Args: {json.dumps(args, indent=2)}")

        result = make_swaig_request(
            config.base_url,
            function,
            args,
            config.user_id,
            config.caller_id,
            config.is_owner,
        )
        results.append((function, args, result))

    elif category:
        # Run category tests
        if category not in test_cases:
            print(f"Unknown category: {category}")
            print(f"Available: {', '.join(test_cases.keys())}")
            return

        is_owner = config.is_owner or category == "owner_only"

        print(f"\n{'='*60}")
        print(f"Testing category: {category.upper()}")
        if is_owner:
            print("(Running as OWNER)")
        print(f"{'='*60}")

        for func_name, args in test_cases[category]:
            print(f"\n  Testing: {func_name}")
            result = make_swaig_request(
                config.base_url,
                func_name,
                args,
                config.user_id,
                config.caller_id,
                is_owner,
            )
            results.append((func_name, args, result))

    else:
        # Run all tests
        for cat, cases in test_cases.items():
            is_owner = config.is_owner or cat == "owner_only"

            print(f"\n{'='*60}")
            print(f"Testing category: {cat.upper()}")
            if is_owner:
                print("(Running as OWNER)")
            print(f"{'='*60}")

            for func_name, args in cases:
                print(f"\n  Testing: {func_name}")
                result = make_swaig_request(
                    config.base_url,
                    func_name,
                    args,
                    config.user_id,
                    config.caller_id,
                    is_owner,
                )
                results.append((func_name, args, result))

    # Print summary
    print_results(results)


def print_results(results: list):
    """Print test results summary"""
    print(f"\n{'='*60}")
    print("TEST RESULTS")
    print(f"{'='*60}")

    passed = 0
    failed = 0

    for func_name, args, result in results:
        status = "PASS" if result.get("success") else "FAIL"
        if result.get("success"):
            passed += 1
        else:
            failed += 1

        print(f"\n[{status}] {func_name}")
        print(f"  Args: {json.dumps(args, default=str)}")

        if result.get("success"):
            response = result.get("response", {})
            if isinstance(response, dict):
                # Try to extract the response text
                resp_text = response.get("response", str(response))
                if len(str(resp_text)) > 200:
                    resp_text = str(resp_text)[:200] + "..."
                print(f"  Response: {resp_text}")
            else:
                print(f"  Response: {str(response)[:200]}")
        else:
            print(f"  Error: {result.get('error', 'Unknown error')}")
            print(f"  Status: {result.get('status_code')}")

    print(f"\n{'='*60}")
    print(f"SUMMARY: {passed} passed, {failed} failed, {len(results)} total")
    print(f"{'='*60}")


def test_swml_endpoint(base_url: str, user_id: str = "test-user-123"):
    """Test the main SWML endpoint to verify agent is running"""
    url = f"{base_url}/swml"

    # Simulate initial call parameters
    params = {
        "user_id": user_id,
    }

    try:
        response = requests.get(url, params=params, timeout=10)
        print(f"\nSWML Endpoint Test:")
        print(f"  URL: {url}")
        print(f"  Status: {response.status_code}")

        if response.status_code == 200:
            # Try to parse and show structure
            try:
                swml = response.json()
                print(f"  SWML Version: {swml.get('version', 'unknown')}")
                sections = swml.get('sections', {})
                main = sections.get('main', [])
                print(f"  Sections: {list(sections.keys())}")
                print(f"  Main steps: {len(main)}")
            except:
                print(f"  Response length: {len(response.text)} chars")
        return response.status_code == 200

    except requests.exceptions.RequestException as e:
        print(f"\nSWML Endpoint Test FAILED:")
        print(f"  Error: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="SWAIG HTTP Endpoint Tester")
    parser.add_argument("--base-url", "-u", default="http://localhost:3000",
                        help="Base URL of the agent")
    parser.add_argument("--user-id", default="test-user-123",
                        help="User ID for testing")
    parser.add_argument("--caller-id", default="+15559876543",
                        help="Caller ID to simulate")
    parser.add_argument("--owner", action="store_true",
                        help="Test as owner")
    parser.add_argument("--category", "-c",
                        help="Test category: core, calendar, email, contacts, messages, owner_only")
    parser.add_argument("--function", "-f",
                        help="Test specific function")
    parser.add_argument("--args", "-a", default="{}",
                        help="JSON args for function test")
    parser.add_argument("--all", action="store_true",
                        help="Run all tests")
    parser.add_argument("--check", action="store_true",
                        help="Just check if agent is running")

    args = parser.parse_args()

    config = TestConfig(
        base_url=args.base_url,
        user_id=args.user_id,
        caller_id=args.caller_id,
        is_owner=args.owner,
    )

    if args.check:
        success = test_swml_endpoint(config.base_url, config.user_id)
        sys.exit(0 if success else 1)

    # First check if agent is running
    print("Checking if agent is running...")
    if not test_swml_endpoint(config.base_url, config.user_id):
        print("\nAgent does not appear to be running.")
        print(f"Start it with: python agent.py")
        print(f"Then run tests against: {config.base_url}")
        return

    if args.function:
        # Parse custom args if provided
        try:
            custom_args = json.loads(args.args)
        except json.JSONDecodeError:
            print(f"Invalid JSON args: {args.args}")
            return

        result = make_swaig_request(
            config.base_url,
            args.function,
            custom_args,
            config.user_id,
            config.caller_id,
            config.is_owner,
        )
        print_results([(args.function, custom_args, result)])

    elif args.category:
        run_tests(config, category=args.category)

    elif args.all:
        run_tests(config)

    else:
        parser.print_help()
        print("\nExample commands:")
        print("  python test_swaig_http.py --check")
        print("  python test_swaig_http.py --all")
        print("  python test_swaig_http.py -c calendar")
        print("  python test_swaig_http.py -f check_calendar_availability -a '{\"date\": \"tomorrow\"}'")


if __name__ == "__main__":
    import sys
    main()
