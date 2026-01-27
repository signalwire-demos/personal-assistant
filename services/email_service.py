"""
Gmail Service

Handles sending and reading emails via Gmail API.
Supports multi-tenant with per-user credentials and configuration.
"""
import base64
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Dict, Optional, List
from datetime import datetime

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config
from models.database import SessionLocal, Config as ConfigModel
from services.google_auth import google_auth


class EmailService:
    """Gmail API integration for sending emails (multi-user)"""

    def __init__(self):
        pass

    def _get_service(self, user_id: str = None):
        """Get Gmail API service for a user"""
        creds = google_auth.get_credentials(user_id)
        if not creds:
            raise ValueError("Gmail not connected. Please connect your Google account.")
        return build('gmail', 'v1', credentials=creds)

    def _get_business_info(self, user_id: str = None) -> Dict:
        """Get business information for email signatures"""
        db = SessionLocal()
        try:
            return {
                "business_name": ConfigModel.get(db, "business_name", config.DEFAULT_BUSINESS_NAME, user_id=user_id),
                "business_phone": ConfigModel.get(db, "business_phone", "", user_id=user_id),
                "business_email": ConfigModel.get(db, "business_email", "", user_id=user_id),
                "business_address": ConfigModel.get(db, "business_address", "", user_id=user_id),
                "owner_email": ConfigModel.get(db, "owner_email", config.DEFAULT_OWNER_EMAIL, user_id=user_id),
            }
        finally:
            db.close()

    def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
        user_id: str = None,
    ) -> Dict:
        """
        Send an email via Gmail.

        Args:
            user_id: User ID
            to: Recipient email address
            subject: Email subject
            body: Plain text body
            html_body: Optional HTML body

        Returns:
            Dict with success status and message ID or error
        """
        try:
            service = self._get_service(user_id)

            # Create message
            if html_body:
                message = MIMEMultipart('alternative')
                message.attach(MIMEText(body, 'plain'))
                message.attach(MIMEText(html_body, 'html'))
            else:
                message = MIMEText(body, 'plain')

            message['to'] = to
            message['subject'] = subject

            # Encode message
            raw = base64.urlsafe_b64encode(message.as_bytes()).decode()

            # Send
            result = service.users().messages().send(
                userId='me',
                body={'raw': raw}
            ).execute()

            return {
                "success": True,
                "message_id": result.get('id'),
                "to": to,
                "subject": subject,
            }

        except HttpError as e:
            print(f"[Email] Gmail API error: {e}")
            return {
                "success": False,
                "error": f"Could not send email: {str(e)}"
            }
        except Exception as e:
            print(f"[Email] Error sending email: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def send_to_owner(self, subject: str, body: str, user_id: str = None) -> Dict:
        """Send email to the business owner"""
        info = self._get_business_info(user_id)
        owner_email = info.get("owner_email")

        if not owner_email:
            return {
                "success": False,
                "error": "Owner email not configured"
            }

        return self.send_email(owner_email, subject, body, user_id=user_id)

    def send_appointment_confirmation(
        self,
        to: str,
        attendee_name: str,
        appointment_type: str,
        appointment_time: str,
        appointment_date: str,
        user_id: str = None,
    ) -> Dict:
        """Send appointment confirmation email"""
        info = self._get_business_info(user_id)

        subject = f"Appointment Confirmed - {appointment_date}"

        body = f"""Dear {attendee_name},

Your appointment has been confirmed!

Appointment Details:
- Type: {appointment_type}
- Date: {appointment_date}
- Time: {appointment_time}

If you need to reschedule or cancel, please call us at {info.get('business_phone', 'our office')}.

Thank you for choosing {info.get('business_name', 'us')}!

Best regards,
{info.get('business_name', '')}
{info.get('business_phone', '')}
{info.get('business_address', '')}
"""

        html_body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <h2 style="color: #4a5568;">Appointment Confirmed!</h2>

    <p>Dear {attendee_name},</p>

    <p>Your appointment has been confirmed!</p>

    <div style="background-color: #f7fafc; padding: 20px; border-radius: 8px; margin: 20px 0;">
        <h3 style="margin-top: 0; color: #2d3748;">Appointment Details</h3>
        <p><strong>Type:</strong> {appointment_type}</p>
        <p><strong>Date:</strong> {appointment_date}</p>
        <p><strong>Time:</strong> {appointment_time}</p>
    </div>

    <p>If you need to reschedule or cancel, please call us at <strong>{info.get('business_phone', 'our office')}</strong>.</p>

    <p>Thank you for choosing {info.get('business_name', 'us')}!</p>

    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">

    <p style="color: #718096; font-size: 14px;">
        {info.get('business_name', '')}<br>
        {info.get('business_phone', '')}<br>
        {info.get('business_address', '')}
    </p>
</body>
</html>
"""

        return self.send_email(to, subject, body, html_body, user_id=user_id)

    def send_message_notification(
        self,
        caller_name: str,
        caller_phone: str,
        message: str,
        urgency: str = "normal",
        user_id: str = None
    ) -> Dict:
        """Send notification to owner about a new message"""
        info = self._get_business_info(user_id)
        owner_email = info.get("owner_email")

        if not owner_email:
            return {
                "success": False,
                "error": "Owner email not configured"
            }

        urgency_prefix = "URGENT: " if urgency == "urgent" else ""
        subject = f"{urgency_prefix}New Message from {caller_name}"

        body = f"""You have a new message:

From: {caller_name}
Phone: {caller_phone}
Urgency: {urgency.upper()}

Message:
{message}

---
Reply to this caller or view all messages in your admin panel.
"""

        html_body = f"""
<html>
<body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
    <h2 style="color: {'#e53e3e' if urgency == 'urgent' else '#4a5568'};">
        {'URGENT: ' if urgency == 'urgent' else ''}New Message
    </h2>

    <div style="background-color: #f7fafc; padding: 20px; border-radius: 8px; margin: 20px 0;">
        <p><strong>From:</strong> {caller_name}</p>
        <p><strong>Phone:</strong> <a href="tel:{caller_phone}">{caller_phone}</a></p>
        <p><strong>Urgency:</strong>
            <span style="color: {'#e53e3e' if urgency == 'urgent' else '#38a169'}; font-weight: bold;">
                {urgency.upper()}
            </span>
        </p>
    </div>

    <div style="background-color: #fff; padding: 20px; border-left: 4px solid {'#e53e3e' if urgency == 'urgent' else '#4299e1'}; margin: 20px 0;">
        <h3 style="margin-top: 0;">Message:</h3>
        <p>{message}</p>
    </div>

    <p>
        <a href="tel:{caller_phone}" style="background-color: #4299e1; color: white; padding: 10px 20px; text-decoration: none; border-radius: 5px; display: inline-block;">
            Call Back
        </a>
    </p>

    <hr style="border: none; border-top: 1px solid #e2e8f0; margin: 20px 0;">

    <p style="color: #718096; font-size: 14px;">
        This message was received by your Personal Assistant.
    </p>
</body>
</html>
"""

        return self.send_email(owner_email, subject, body, html_body, user_id=user_id)

    def send_custom_email(
        self,
        to: str,
        subject: str,
        message: str,
        from_caller: str = "",
        caller_phone: str = "",
        user_id: str = None
    ) -> Dict:
        """Send a custom email composed by a caller"""
        info = self._get_business_info(user_id)

        # Build the body
        body_parts = []
        if from_caller:
            body_parts.append(f"Message from: {from_caller}")
        if caller_phone:
            body_parts.append(f"Phone: {caller_phone}")
        body_parts.append("")
        body_parts.append(message)
        body_parts.append("")
        body_parts.append("---")
        body_parts.append(f"This email was sent via {info.get('business_name', 'our')} voice assistant.")

        body = "\n".join(body_parts)

        return self.send_email(to, subject, body, user_id=user_id)

    # ==================== Read Methods ====================

    def get_recent_emails(self, max_results: int = 5, query: str = "", user_id: str = None) -> List[Dict]:
        """
        Get recent emails from inbox.

        Args:
            user_id: User ID
            max_results: Maximum number of emails to return
            query: Optional Gmail search query (e.g., "from:john@example.com")

        Returns:
            List of email summaries
        """
        try:
            service = self._get_service(user_id)

            # Build the query - default to inbox
            search_query = query if query else "in:inbox"

            # List messages
            results = service.users().messages().list(
                userId='me',
                q=search_query,
                maxResults=max_results
            ).execute()

            messages = results.get('messages', [])
            emails = []

            for msg in messages:
                email_data = self._get_email_summary(service, msg['id'])
                if email_data:
                    emails.append(email_data)

            return emails

        except HttpError as e:
            print(f"[Email] Gmail API error: {e}")
            return []
        except Exception as e:
            print(f"[Email] Error getting emails: {e}")
            return []

    def _get_email_summary(self, service, message_id: str) -> Optional[Dict]:
        """Get summary info for a single email"""
        try:
            msg = service.users().messages().get(
                userId='me',
                id=message_id,
                format='metadata',
                metadataHeaders=['From', 'To', 'Subject', 'Date']
            ).execute()

            headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}

            # Parse date
            date_str = headers.get('Date', '')
            try:
                # Simple date parsing - Gmail dates can be complex
                if date_str:
                    # Remove timezone name in parentheses if present
                    if '(' in date_str:
                        date_str = date_str[:date_str.index('(')].strip()
                    date_obj = datetime.strptime(date_str, '%a, %d %b %Y %H:%M:%S %z')
                    formatted_date = date_obj.strftime('%b %d, %Y %I:%M %p')
                else:
                    formatted_date = 'Unknown date'
            except Exception:
                formatted_date = date_str

            return {
                "id": message_id,
                "from": headers.get('From', ''),
                "to": headers.get('To', ''),
                "subject": headers.get('Subject', '(No subject)'),
                "date": formatted_date,
                "snippet": msg.get('snippet', ''),
                "is_unread": 'UNREAD' in msg.get('labelIds', []),
            }

        except Exception as e:
            print(f"[Email] Error getting email summary: {e}")
            return None

    def get_email_content(self, message_id: str, user_id: str = None) -> Optional[Dict]:
        """
        Get full content of an email.

        Args:
            user_id: User ID
            message_id: Gmail message ID

        Returns:
            Dict with email details and body
        """
        try:
            service = self._get_service(user_id)

            msg = service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()

            headers = {h['name']: h['value'] for h in msg.get('payload', {}).get('headers', [])}

            # Extract body
            body = self._extract_body(msg.get('payload', {}))

            return {
                "id": message_id,
                "from": headers.get('From', ''),
                "to": headers.get('To', ''),
                "subject": headers.get('Subject', '(No subject)'),
                "date": headers.get('Date', ''),
                "body": body,
                "snippet": msg.get('snippet', ''),
            }

        except HttpError as e:
            print(f"[Email] Gmail API error: {e}")
            return None
        except Exception as e:
            print(f"[Email] Error getting email: {e}")
            return None

    def _extract_body(self, payload: Dict) -> str:
        """Extract plain text body from email payload"""
        body = ""

        if 'body' in payload and payload['body'].get('data'):
            body = base64.urlsafe_b64decode(payload['body']['data']).decode('utf-8', errors='ignore')

        elif 'parts' in payload:
            for part in payload['parts']:
                if part.get('mimeType') == 'text/plain':
                    if part.get('body', {}).get('data'):
                        body = base64.urlsafe_b64decode(part['body']['data']).decode('utf-8', errors='ignore')
                        break
                elif 'parts' in part:
                    # Nested multipart
                    body = self._extract_body(part)
                    if body:
                        break

        return body

    def get_unread_count(self, user_id: str = None) -> int:
        """Get count of unread emails in inbox"""
        try:
            service = self._get_service(user_id)

            results = service.users().messages().list(
                userId='me',
                q='in:inbox is:unread',
                maxResults=1
            ).execute()

            return results.get('resultSizeEstimate', 0)

        except Exception as e:
            print(f"[Email] Error getting unread count: {e}")
            return 0

    def search_emails(self, query: str, max_results: int = 5, user_id: str = None) -> List[Dict]:
        """
        Search emails using Gmail query syntax.

        Args:
            query: Gmail search query (e.g., "from:john subject:meeting")
            max_results: Maximum results to return
            user_id: User ID

        Returns:
            List of matching email summaries
        """
        return self.get_recent_emails(max_results=max_results, query=query, user_id=user_id)

    # ==================== Owner Email Management ====================

    def delete_email(self, message_id: str, user_id: str = None) -> Dict:
        """
        Delete an email by moving it to trash.

        Args:
            message_id: Gmail message ID
            user_id: User ID

        Returns:
            Dict with success status
        """
        try:
            service = self._get_service(user_id)

            service.users().messages().trash(
                userId='me',
                id=message_id
            ).execute()

            return {
                "success": True,
                "message": "Email moved to trash"
            }

        except HttpError as e:
            print(f"[Email] Gmail API error deleting email: {e}")
            return {
                "success": False,
                "error": f"Could not delete email: {str(e)}"
            }
        except Exception as e:
            print(f"[Email] Error deleting email: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def mark_email_read(self, message_id: str, user_id: str = None) -> Dict:
        """
        Mark an email as read.

        Args:
            message_id: Gmail message ID
            user_id: User ID

        Returns:
            Dict with success status
        """
        try:
            service = self._get_service(user_id)

            service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'removeLabelIds': ['UNREAD']}
            ).execute()

            return {
                "success": True,
                "message": "Email marked as read"
            }

        except HttpError as e:
            print(f"[Email] Gmail API error marking email read: {e}")
            return {
                "success": False,
                "error": f"Could not mark email as read: {str(e)}"
            }
        except Exception as e:
            print(f"[Email] Error marking email read: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def mark_email_unread(self, message_id: str, user_id: str = None) -> Dict:
        """
        Mark an email as unread.

        Args:
            message_id: Gmail message ID
            user_id: User ID

        Returns:
            Dict with success status
        """
        try:
            service = self._get_service(user_id)

            service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'addLabelIds': ['UNREAD']}
            ).execute()

            return {
                "success": True,
                "message": "Email marked as unread"
            }

        except HttpError as e:
            print(f"[Email] Gmail API error marking email unread: {e}")
            return {
                "success": False,
                "error": f"Could not mark email as unread: {str(e)}"
            }
        except Exception as e:
            print(f"[Email] Error marking email unread: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def archive_email(self, message_id: str, user_id: str = None) -> Dict:
        """
        Archive an email (remove from inbox but keep in All Mail).

        Args:
            message_id: Gmail message ID
            user_id: User ID

        Returns:
            Dict with success status
        """
        try:
            service = self._get_service(user_id)

            service.users().messages().modify(
                userId='me',
                id=message_id,
                body={'removeLabelIds': ['INBOX']}
            ).execute()

            return {
                "success": True,
                "message": "Email archived"
            }

        except HttpError as e:
            print(f"[Email] Gmail API error archiving email: {e}")
            return {
                "success": False,
                "error": f"Could not archive email: {str(e)}"
            }
        except Exception as e:
            print(f"[Email] Error archiving email: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Singleton instance
email_service = EmailService()
