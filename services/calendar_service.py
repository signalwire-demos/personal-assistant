"""
Google Calendar Service

Handles appointment scheduling via Google Calendar API.
Supports multi-tenant with per-user credentials and configuration.
"""
from datetime import datetime, timedelta, time
from typing import List, Dict, Optional, Tuple
import pytz

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

import config
from models.database import SessionLocal, BusinessHours, AppointmentType, Holiday
from services.google_auth import google_auth


class CalendarService:
    """Google Calendar integration for appointment scheduling (multi-user)"""

    def __init__(self):
        self.default_timezone = config.DEFAULT_TIMEZONE

    def _get_service(self, user_id: str = None):
        """Get Google Calendar API service for a user"""
        creds = google_auth.get_credentials(user_id)
        if not creds:
            raise ValueError("Google Calendar not connected. Please connect your Google account.")
        return build('calendar', 'v3', credentials=creds)

    def _get_calendar_id(self, user_id: str = None) -> str:
        """Get the configured calendar ID for a user"""
        return google_auth.get_selected_calendar(user_id)

    def _get_timezone(self, user_id: str = None) -> str:
        """Get timezone for a user"""
        from models.database import Config as ConfigModel
        db = SessionLocal()
        try:
            return ConfigModel.get(db, "timezone", self.default_timezone, user_id=user_id)
        finally:
            db.close()

    def _get_business_hours(self, day_of_week: int, user_id: str = None) -> Optional[Tuple[time, time]]:
        """Get business hours for a specific day for a user"""
        db = SessionLocal()
        try:
            query = db.query(BusinessHours).filter(BusinessHours.day_of_week == day_of_week)
            if user_id:
                query = query.filter(BusinessHours.user_id == user_id)
            else:
                query = query.filter(BusinessHours.user_id.is_(None))
            hours = query.first()

            if hours and not hours.is_closed and hours.open_time and hours.close_time:
                return (hours.open_time, hours.close_time)
            return None
        finally:
            db.close()

    def _is_holiday(self, date: datetime, user_id: str = None) -> bool:
        """Check if a date is a holiday for a user"""
        db = SessionLocal()
        try:
            query = db.query(Holiday).filter(Holiday.date == date.date())
            if user_id:
                query = query.filter(Holiday.user_id == user_id)
            else:
                query = query.filter(Holiday.user_id.is_(None))
            holiday = query.first()
            return holiday is not None
        finally:
            db.close()

    def _get_appointment_type(self, type_name: str, user_id: str = None) -> Optional[AppointmentType]:
        """Get appointment type by name for a user"""
        db = SessionLocal()
        try:
            # Try exact match first
            query = db.query(AppointmentType).filter(
                AppointmentType.name.ilike(type_name),
                AppointmentType.is_active == True
            )
            if user_id:
                query = query.filter(AppointmentType.user_id == user_id)
            else:
                query = query.filter(AppointmentType.user_id.is_(None))
            apt_type = query.first()

            if not apt_type:
                # Try partial match
                query = db.query(AppointmentType).filter(
                    AppointmentType.name.ilike(f"%{type_name}%"),
                    AppointmentType.is_active == True
                )
                if user_id:
                    query = query.filter(AppointmentType.user_id == user_id)
                else:
                    query = query.filter(AppointmentType.user_id.is_(None))
                apt_type = query.first()

            return apt_type
        finally:
            db.close()

    def get_busy_times(
        self,
        user_id: str,
        start_date: datetime,
        end_date: datetime,
        calendar_id: str = None
    ) -> List[Dict]:
        """Get busy time slots from calendar for a user"""
        calendar_id = calendar_id or self._get_calendar_id(user_id)
        timezone = self._get_timezone(user_id)
        try:
            service = self._get_service(user_id)

            # Use freebusy query
            body = {
                "timeMin": start_date.isoformat(),
                "timeMax": end_date.isoformat(),
                "timeZone": timezone,
                "items": [{"id": calendar_id}]
            }

            result = service.freebusy().query(body=body).execute()
            busy_times = result.get('calendars', {}).get(calendar_id, {}).get('busy', [])

            return [
                {
                    "start": slot["start"],
                    "end": slot["end"]
                }
                for slot in busy_times
            ]

        except HttpError as e:
            print(f"[Calendar] API error: {e}")
            return []

    def get_available_slots(
        self,
        date_str: str,
        duration_minutes: int = 60,
        calendar_id: str = None,
        user_id: str = None
    ) -> List[Dict]:
        """
        Get available time slots for a specific date.

        Args:
            user_id: User ID
            date_str: Date in YYYY-MM-DD format or natural language
            duration_minutes: Appointment duration in minutes
            calendar_id: Google Calendar ID

        Returns:
            List of available slots with start and end times
        """
        calendar_id = calendar_id or self._get_calendar_id(user_id)
        timezone = self._get_timezone(user_id)
        try:
            # Parse the date
            tz = pytz.timezone(timezone)

            # Try to parse various date formats
            target_date = self._parse_date(date_str, timezone)
            if not target_date:
                print(f"[Calendar DEBUG] Failed to parse date: {date_str}")
                return []

            # Check if date is in the past
            now = datetime.now(tz)
            print(f"[Calendar DEBUG] date_str={date_str}, target_date={target_date.date()}, now={now.date()}")
            if target_date.date() < now.date():
                print(f"[Calendar DEBUG] Date is in the past")
                return []  # No slots for past dates

            # Check if it's a holiday
            if self._is_holiday(target_date, user_id):
                print(f"[Calendar DEBUG] Date is a holiday")
                return []

            # Get business hours for this day
            day_of_week = target_date.weekday()
            print(f"[Calendar DEBUG] day_of_week={day_of_week} (0=Mon, 6=Sun)")
            hours = self._get_business_hours(day_of_week, user_id)
            if not hours:
                print(f"[Calendar DEBUG] No business hours for day {day_of_week}")
                return []  # Closed on this day
            print(f"[Calendar DEBUG] Business hours: {hours}")

            open_time, close_time = hours

            # Create datetime range for the day
            day_start = tz.localize(datetime.combine(target_date.date(), open_time))
            day_end = tz.localize(datetime.combine(target_date.date(), close_time))

            # Get busy times
            busy_times = self.get_busy_times(user_id, day_start, day_end, calendar_id)

            # Parse busy times
            busy_slots = []
            for busy in busy_times:
                busy_start = datetime.fromisoformat(busy["start"].replace('Z', '+00:00'))
                busy_end = datetime.fromisoformat(busy["end"].replace('Z', '+00:00'))
                busy_slots.append((busy_start.astimezone(tz), busy_end.astimezone(tz)))

            # Generate available slots
            available_slots = []
            slot_duration = timedelta(minutes=duration_minutes)
            current_time = day_start

            # If date is today, start from current time (rounded up to next 30 min)
            now = datetime.now(tz)
            if target_date.date() == now.date():
                # Round up to next 30-minute mark
                minutes = now.minute
                if minutes < 30:
                    current_time = now.replace(minute=30, second=0, microsecond=0)
                else:
                    current_time = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)

                if current_time < day_start:
                    current_time = day_start

            while current_time + slot_duration <= day_end:
                slot_end = current_time + slot_duration

                # Check if slot overlaps with any busy time
                is_available = True
                for busy_start, busy_end in busy_slots:
                    if not (slot_end <= busy_start or current_time >= busy_end):
                        is_available = False
                        break

                if is_available:
                    available_slots.append({
                        "start": current_time.strftime("%I:%M %p"),
                        "end": slot_end.strftime("%I:%M %p"),
                        "start_iso": current_time.isoformat(),
                        "end_iso": slot_end.isoformat(),
                    })

                # Move to next slot (30-minute intervals)
                current_time += timedelta(minutes=30)

            return available_slots

        except Exception as e:
            print(f"[Calendar] Error getting available slots: {e}")
            return []

    def _parse_date(self, date_str: str, timezone: str = None) -> Optional[datetime]:
        """Parse date string in various formats"""
        timezone = timezone or self.default_timezone
        tz = pytz.timezone(timezone)
        now = datetime.now(tz)

        date_str_lower = date_str.lower().strip()

        # Handle relative dates
        if date_str_lower in ['today', 'now']:
            return now
        elif date_str_lower == 'tomorrow':
            return now + timedelta(days=1)
        elif date_str_lower == 'next week':
            return now + timedelta(weeks=1)

        # Handle day names
        days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
        for i, day in enumerate(days):
            if day in date_str_lower:
                days_ahead = i - now.weekday()
                if days_ahead <= 0:
                    days_ahead += 7
                if 'next' in date_str_lower:
                    days_ahead += 7
                return now + timedelta(days=days_ahead)

        # Try standard date formats
        formats = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%m/%d/%y",
            "%B %d, %Y",
            "%B %d",
            "%b %d, %Y",
            "%b %d",
        ]

        for fmt in formats:
            try:
                parsed = datetime.strptime(date_str, fmt)
                # If no year, assume current or next year
                if parsed.year == 1900:
                    parsed = parsed.replace(year=now.year)
                    if parsed < now:
                        parsed = parsed.replace(year=now.year + 1)
                return tz.localize(parsed)
            except ValueError:
                continue

        return None

    def book_appointment(
        self,
        start_time: str,
        end_time: str,
        appointment_type: str,
        attendee_name: str,
        attendee_email: str,
        attendee_phone: str = "",
        notes: str = "",
        calendar_id: str = None,
        user_id: str = None
    ) -> Dict:
        """
        Book an appointment on Google Calendar.

        Args:
            user_id: User ID
            start_time: ISO format start time
            end_time: ISO format end time
            appointment_type: Type of appointment
            attendee_name: Customer's name
            attendee_email: Customer's email
            attendee_phone: Customer's phone (optional)
            notes: Additional notes (optional)
            calendar_id: Google Calendar ID

        Returns:
            Dict with event details or error
        """
        calendar_id = calendar_id or self._get_calendar_id(user_id)
        timezone = self._get_timezone(user_id)
        try:
            service = self._get_service(user_id)

            # Get appointment type details
            apt_type = self._get_appointment_type(appointment_type, user_id)
            type_name = apt_type.name if apt_type else appointment_type

            # Build description
            description_parts = [
                f"Appointment Type: {type_name}",
                f"Client: {attendee_name}",
            ]
            if attendee_phone:
                description_parts.append(f"Phone: {attendee_phone}")
            if notes:
                description_parts.append(f"\nNotes: {notes}")

            description = "\n".join(description_parts)

            # Get business name
            db = SessionLocal()
            try:
                from models.database import Config as ConfigModel
                business_name = ConfigModel.get(db, "business_name", config.DEFAULT_BUSINESS_NAME, user_id=user_id)
            finally:
                db.close()

            event = {
                'summary': f"{type_name} - {attendee_name}",
                'description': description,
                'start': {
                    'dateTime': start_time,
                    'timeZone': timezone,
                },
                'end': {
                    'dateTime': end_time,
                    'timeZone': timezone,
                },
                'attendees': [
                    {'email': attendee_email, 'displayName': attendee_name},
                ],
                'reminders': {
                    'useDefault': False,
                    'overrides': [
                        {'method': 'email', 'minutes': 24 * 60},  # 1 day before
                        {'method': 'popup', 'minutes': 60},       # 1 hour before
                    ],
                },
                'conferenceData': None,
            }

            # Create the event
            created_event = service.events().insert(
                calendarId=calendar_id,
                body=event,
                sendUpdates='all'  # Send email notifications
            ).execute()

            # Parse the start time for display
            start_dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            tz = pytz.timezone(timezone)
            start_local = start_dt.astimezone(tz)

            return {
                "success": True,
                "event_id": created_event.get('id'),
                "event_link": created_event.get('htmlLink'),
                "start_time": start_local.strftime("%B %d, %Y at %I:%M %p"),
                "appointment_type": type_name,
                "attendee_name": attendee_name,
                "attendee_email": attendee_email,
            }

        except HttpError as e:
            print(f"[Calendar] API error: {e}")
            return {
                "success": False,
                "error": f"Could not create appointment: {str(e)}"
            }
        except Exception as e:
            print(f"[Calendar] Error booking appointment: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def cancel_appointment(self, event_id: str, calendar_id: str = None, user_id: str = None) -> Dict:
        """Cancel an appointment"""
        calendar_id = calendar_id or self._get_calendar_id(user_id)
        try:
            service = self._get_service(user_id)

            service.events().delete(
                calendarId=calendar_id,
                eventId=event_id,
                sendUpdates='all'
            ).execute()

            return {
                "success": True,
                "message": "Appointment cancelled successfully"
            }

        except HttpError as e:
            return {
                "success": False,
                "error": f"Could not cancel appointment: {str(e)}"
            }

    def reschedule_appointment(
        self,
        event_id: str,
        new_start_time: str,
        new_end_time: str,
        calendar_id: str = None,
        user_id: str = None
    ) -> Dict:
        """Reschedule an existing appointment"""
        calendar_id = calendar_id or self._get_calendar_id(user_id)
        timezone = self._get_timezone(user_id)
        try:
            service = self._get_service(user_id)

            # Get existing event
            event = service.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()

            # Update times
            event['start'] = {
                'dateTime': new_start_time,
                'timeZone': timezone,
            }
            event['end'] = {
                'dateTime': new_end_time,
                'timeZone': timezone,
            }

            # Update the event
            updated_event = service.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=event,
                sendUpdates='all'
            ).execute()

            # Parse the new start time for display
            start_dt = datetime.fromisoformat(new_start_time.replace('Z', '+00:00'))
            tz = pytz.timezone(timezone)
            start_local = start_dt.astimezone(tz)

            return {
                "success": True,
                "event_id": updated_event.get('id'),
                "new_time": start_local.strftime("%B %d, %Y at %I:%M %p"),
            }

        except HttpError as e:
            return {
                "success": False,
                "error": f"Could not reschedule appointment: {str(e)}"
            }

    def get_upcoming_appointments(
        self,
        max_results: int = 10,
        calendar_id: str = None,
        user_id: str = None
    ) -> List[Dict]:
        """Get upcoming appointments for a user"""
        calendar_id = calendar_id or self._get_calendar_id(user_id)
        timezone = self._get_timezone(user_id)
        try:
            service = self._get_service(user_id)
            tz = pytz.timezone(timezone)
            now = datetime.now(tz).isoformat()

            events_result = service.events().list(
                calendarId=calendar_id,
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy='startTime'
            ).execute()

            events = events_result.get('items', [])

            return [
                {
                    "id": event.get('id'),
                    "summary": event.get('summary'),
                    "description": event.get('description', ''),
                    "start": event.get('start', {}).get('dateTime'),
                    "end": event.get('end', {}).get('dateTime'),
                    "attendees": [
                        a.get('email') for a in event.get('attendees', [])
                    ],
                }
                for event in events
            ]

        except HttpError as e:
            print(f"[Calendar] Error getting appointments: {e}")
            return []

    # ==================== Owner Calendar Management ====================

    def respond_to_invite(
        self,
        event_id: str,
        response: str,
        calendar_id: str = None,
        user_id: str = None
    ) -> Dict:
        """
        Respond to a calendar invitation with Yes, No, or Maybe.

        Args:
            event_id: Google Calendar event ID
            response: 'yes', 'no', or 'maybe' (maps to 'accepted', 'declined', 'tentative')
            calendar_id: Google Calendar ID
            user_id: User ID

        Returns:
            Dict with success status
        """
        calendar_id = calendar_id or self._get_calendar_id(user_id)
        try:
            service = self._get_service(user_id)

            # Map response to Google Calendar response status
            response_map = {
                'yes': 'accepted',
                'no': 'declined',
                'maybe': 'tentative',
                'accepted': 'accepted',
                'declined': 'declined',
                'tentative': 'tentative',
            }
            response_status = response_map.get(response.lower(), 'tentative')

            # Get the event first
            event = service.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()

            # Get user's email to find their attendee entry
            user_info = service.calendarList().get(calendarId='primary').execute()
            user_email = user_info.get('id', '')

            # Update the attendee response
            attendees = event.get('attendees', [])
            for attendee in attendees:
                if attendee.get('self') or attendee.get('email', '').lower() == user_email.lower():
                    attendee['responseStatus'] = response_status
                    break
            else:
                # If user not in attendees, they might be the organizer
                # Just update the event with the response
                pass

            event['attendees'] = attendees

            # Update the event
            updated_event = service.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=event,
                sendUpdates='all'
            ).execute()

            response_text = {'accepted': 'Yes', 'declined': 'No', 'tentative': 'Maybe'}
            return {
                "success": True,
                "message": f"Responded '{response_text.get(response_status, response)}' to the invitation",
                "event_summary": updated_event.get('summary', 'Event'),
            }

        except HttpError as e:
            print(f"[Calendar] API error responding to invite: {e}")
            return {
                "success": False,
                "error": f"Could not respond to invitation: {str(e)}"
            }
        except Exception as e:
            print(f"[Calendar] Error responding to invite: {e}")
            return {
                "success": False,
                "error": str(e)
            }

    def update_appointment(
        self,
        event_id: str,
        new_start_time: str = None,
        new_end_time: str = None,
        new_summary: str = None,
        new_description: str = None,
        calendar_id: str = None,
        user_id: str = None
    ) -> Dict:
        """
        Update an existing appointment (time, title, notes).

        Args:
            event_id: Google Calendar event ID
            new_start_time: New start time in ISO format (optional)
            new_end_time: New end time in ISO format (optional)
            new_summary: New title/summary (optional)
            new_description: New description/notes (optional)
            calendar_id: Google Calendar ID
            user_id: User ID

        Returns:
            Dict with success status and updated event details
        """
        calendar_id = calendar_id or self._get_calendar_id(user_id)
        timezone = self._get_timezone(user_id)
        try:
            service = self._get_service(user_id)

            # Get existing event
            event = service.events().get(
                calendarId=calendar_id,
                eventId=event_id
            ).execute()

            # Update fields if provided
            if new_start_time:
                event['start'] = {
                    'dateTime': new_start_time,
                    'timeZone': timezone,
                }
            if new_end_time:
                event['end'] = {
                    'dateTime': new_end_time,
                    'timeZone': timezone,
                }
            if new_summary:
                event['summary'] = new_summary
            if new_description:
                event['description'] = new_description

            # Update the event
            updated_event = service.events().update(
                calendarId=calendar_id,
                eventId=event_id,
                body=event,
                sendUpdates='all'
            ).execute()

            # Format response
            result = {
                "success": True,
                "event_id": updated_event.get('id'),
                "summary": updated_event.get('summary'),
            }

            if new_start_time:
                start_dt = datetime.fromisoformat(new_start_time.replace('Z', '+00:00'))
                tz = pytz.timezone(timezone)
                start_local = start_dt.astimezone(tz)
                result["new_time"] = start_local.strftime("%B %d, %Y at %I:%M %p")

            return result

        except HttpError as e:
            print(f"[Calendar] API error updating appointment: {e}")
            return {
                "success": False,
                "error": f"Could not update appointment: {str(e)}"
            }
        except Exception as e:
            print(f"[Calendar] Error updating appointment: {e}")
            return {
                "success": False,
                "error": str(e)
            }


# Singleton instance
calendar_service = CalendarService()
