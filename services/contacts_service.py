"""
Google Contacts Service

Handles contact lookup via Google People API.
Supports multi-tenant with per-user credentials.
"""
from typing import List, Dict, Optional
import re

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from services.google_auth import google_auth


class ContactsService:
    """Google Contacts integration for caller identification (multi-user)"""

    def _get_service(self, user_id: str = None):
        """Get Google People API service for a user"""
        creds = google_auth.get_credentials(user_id)
        if not creds:
            raise ValueError("Google account not connected")
        return build('people', 'v1', credentials=creds)

    def _normalize_phone(self, phone: str) -> str:
        """Normalize phone number for comparison - returns last 10 digits"""
        # Remove all non-digits
        digits = re.sub(r'\D', '', phone)
        # Handle US numbers - keep last 10 digits
        if len(digits) > 10:
            digits = digits[-10:]
        return digits

    def _get_phone_formats(self, phone: str) -> List[str]:
        """
        Generate multiple phone formats to try when searching.
        Google Contacts search depends on how the number is stored.
        """
        digits = re.sub(r'\D', '', phone)

        # Get last 10 digits for US numbers
        if len(digits) > 10:
            digits = digits[-10:]

        if len(digits) != 10:
            # Not a standard US number, just return original
            return [phone]

        area = digits[:3]
        exchange = digits[3:6]
        subscriber = digits[6:]

        formats = [
            phone,                                      # Original: +12014268875
            f"({area}) {exchange}-{subscriber}",        # (201) 426-8875
            f"{area}-{exchange}-{subscriber}",          # 201-426-8875
            f"{area}.{exchange}.{subscriber}",          # 201.426.8875
            f"{area} {exchange} {subscriber}",          # 201 426 8875
            digits,                                      # 2014268875
            f"1{digits}",                               # 12014268875
            f"+1{digits}",                              # +12014268875
        ]

        # Remove duplicates while preserving order
        seen = set()
        unique = []
        for f in formats:
            if f not in seen:
                seen.add(f)
                unique.append(f)

        return unique

    def lookup_by_phone(self, phone: str, user_id: str = None) -> Optional[Dict]:
        """
        Look up a contact by phone number.
        Tries multiple phone formats since Google search depends on stored format.

        Args:
            phone: Phone number to search for
            user_id: User ID (optional)

        Returns:
            Contact info dict or None if not found
        """
        try:
            service = self._get_service(user_id)
            normalized_phone = self._normalize_phone(phone)

            # Try multiple phone formats
            formats_to_try = self._get_phone_formats(phone)
            print(f"[Contacts] Looking up phone: {phone}")
            print(f"[Contacts] Trying formats: {formats_to_try[:3]}...")  # Show first 3

            for search_format in formats_to_try:
                try:
                    results = service.people().searchContacts(
                        query=search_format,
                        readMask='names,emailAddresses,phoneNumbers,organizations'
                    ).execute()

                    contacts = results.get('results', [])

                    if contacts:
                        print(f"[Contacts] Found {len(contacts)} result(s) with format: {search_format}")

                    for contact in contacts:
                        person = contact.get('person', {})
                        phones = person.get('phoneNumbers', [])

                        for p in phones:
                            contact_phone = self._normalize_phone(p.get('value', ''))
                            if contact_phone == normalized_phone:
                                # Found a match
                                names = person.get('names', [{}])
                                emails = person.get('emailAddresses', [])
                                orgs = person.get('organizations', [])

                                result = {
                                    "name": names[0].get('displayName', '') if names else '',
                                    "first_name": names[0].get('givenName', '') if names else '',
                                    "last_name": names[0].get('familyName', '') if names else '',
                                    "email": emails[0].get('value', '') if emails else '',
                                    "phone": phone,
                                    "company": orgs[0].get('name', '') if orgs else '',
                                    "title": orgs[0].get('title', '') if orgs else '',
                                }
                                print(f"[Contacts] Match found: {result['name']} <{result['email']}>")
                                return result

                except HttpError as e:
                    # Continue trying other formats
                    print(f"[Contacts] Format '{search_format}' failed: {e}")
                    continue

            print(f"[Contacts] No match found for {phone}")
            return None

        except Exception as e:
            print(f"[Contacts] Error looking up contact: {e}")
            return None

    def search_contacts(self, query: str, limit: int = 5, user_id: str = None) -> List[Dict]:
        """
        Search contacts by name or email.

        Args:
            query: Search query
            limit: Maximum results to return
            user_id: User ID (optional)

        Returns:
            List of matching contacts
        """
        try:
            service = self._get_service(user_id)

            results = service.people().searchContacts(
                query=query,
                readMask='names,emailAddresses,phoneNumbers,organizations',
                pageSize=limit
            ).execute()

            contacts = []
            for result in results.get('results', []):
                person = result.get('person', {})
                names = person.get('names', [{}])
                emails = person.get('emailAddresses', [])
                phones = person.get('phoneNumbers', [])
                orgs = person.get('organizations', [])

                contacts.append({
                    "name": names[0].get('displayName', '') if names else '',
                    "email": emails[0].get('value', '') if emails else '',
                    "phone": phones[0].get('value', '') if phones else '',
                    "company": orgs[0].get('name', '') if orgs else '',
                })

            return contacts

        except HttpError as e:
            print(f"[Contacts] API error: {e}")
            return []
        except Exception as e:
            print(f"[Contacts] Error searching contacts: {e}")
            return []

    def get_contact_info(self, phone: str, user_id: str = None) -> str:
        """
        Get formatted contact info string for a phone number.
        Used by the agent to greet known callers.

        Args:
            phone: Caller's phone number
            user_id: User ID (optional)

        Returns:
            Formatted string with contact info or empty string
        """
        contact = self.lookup_by_phone(phone, user_id)

        if not contact or not contact.get('name'):
            return ""

        parts = [f"Name: {contact['name']}"]

        if contact.get('company'):
            parts.append(f"Company: {contact['company']}")

        if contact.get('email'):
            parts.append(f"Email: {contact['email']}")

        return "\n".join(parts)


# Singleton instance
contacts_service = ContactsService()
