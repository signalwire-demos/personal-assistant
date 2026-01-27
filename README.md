# Ethan Voice AI Agent

A comprehensive voice AI assistant built on the SignalWire Agents SDK. Ethan handles inbound calls for businesses, providing appointment scheduling, email/calendar integration, FAQ search, knowledge base queries, and message taking - with separate flows for business owners and customers.

## Features

### Customer Features
- **Appointment Scheduling** - Book, cancel, and check appointments via Google Calendar
- **Business Information** - Hours, location, services, and pricing
- **FAQ Search** - AI-powered search through configured FAQs
- **Knowledge Base** - RAG-powered document search for complex questions
- **Message Taking** - Leave messages for the business owner
- **Call Transfer** - Transfer to business owner's phone
- **Contact Recognition** - Identifies callers from Google Contacts

### Owner Features
- **Email Management** - Check, read, delete, and archive emails via Gmail
- **Message Review** - Review and manage messages left by callers
- **Calendar Management** - View appointments, respond to invites, update events
- **Contact Lookup** - Search contacts by name or phone number
- **Status Summary** - Get quick overview of unread emails, messages, and upcoming appointments

### Admin Features
- **Multi-tenant Support** - Multiple businesses with separate phone numbers
- **Web Dashboard** - Configure business settings, hours, FAQs, and integrations
- **Google OAuth** - Connect Gmail, Calendar, and Contacts per-user
- **Knowledge Base Management** - Upload and index documents for AI search
- **Call Logs** - Track all inbound calls with summaries

## Architecture

```
ethan-voice/
├── agent.py              # Main SignalWire AI agent (SWAIG functions, state machine)
├── app.py                # FastAPI web app (admin panel, API routes)
├── config.py             # Configuration management
├── models/
│   └── database.py       # SQLAlchemy models (User, Config, FAQs, Messages, etc.)
├── services/
│   ├── google_auth.py    # Google OAuth2 flow
│   ├── calendar_service.py  # Google Calendar integration
│   ├── email_service.py  # Gmail integration
│   ├── contacts_service.py  # Google Contacts integration
│   ├── knowledge_service.py # RAG knowledge base
│   └── auth_service.py   # User authentication
├── routes/
│   └── auth.py           # Authentication routes
├── templates/            # Jinja2 templates for admin panel
├── static/               # CSS, JS, images
└── knowledge/            # Knowledge base documents and indexes
```

## Prerequisites

- Python 3.10+
- SignalWire account with AI Agent capabilities
- Google Cloud project with OAuth credentials (for Gmail/Calendar/Contacts)
- Public URL for webhooks (ngrok for local development)

## Installation

### 1. Clone and Set Up Environment

```bash
git clone <repository-url>
cd ethan-voice

# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure Environment Variables

```bash
cp .env.example .env
```

Edit `.env` with your configuration:

```bash
# SignalWire Configuration
SIGNALWIRE_SPACE_NAME=your-space
SIGNALWIRE_PROJECT_ID=your-project-id
SIGNALWIRE_TOKEN=your-api-token

# Server Configuration
HOST=0.0.0.0
PORT=3000
SWML_PROXY_URL_BASE=https://your-domain.com  # Your public URL
SWML_BASIC_AUTH_USER=webhook-user
SWML_BASIC_AUTH_PASSWORD=secure-password-here

# Database
DATABASE_URL=sqlite:///./ethan.db

# Google OAuth
GOOGLE_CLIENT_ID=your-client-id.apps.googleusercontent.com
GOOGLE_CLIENT_SECRET=your-client-secret
GOOGLE_REDIRECT_URI=https://your-domain.com/oauth/google/callback

# Admin Panel
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your-admin-password
SECRET_KEY=your-secret-key-for-sessions

# Knowledge Base (optional)
KNOWLEDGE_INDEX_PATH=./knowledge/indexes
KNOWLEDGE_DOCS_PATH=./knowledge/docs
KNOWLEDGE_SIMILARITY_THRESHOLD=0.3
KNOWLEDGE_RESULTS_COUNT=5
```

### 3. Initialize Database

```bash
python -c "from models.database import Base, engine; Base.metadata.create_all(bind=engine)"
```

## Google OAuth Setup

### 1. Create Google Cloud Project

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a new project or select existing one
3. Enable the following APIs:
   - Gmail API
   - Google Calendar API
   - People API (for Contacts)

### 2. Configure OAuth Consent Screen

1. Go to **APIs & Services > OAuth consent screen**
2. Choose **External** user type
3. Fill in app information:
   - App name: "Ethan Voice Assistant"
   - User support email: your email
   - Developer contact: your email
4. Add scopes:
   - `openid`
   - `https://www.googleapis.com/auth/userinfo.email`
   - `https://www.googleapis.com/auth/gmail.send`
   - `https://www.googleapis.com/auth/gmail.compose`
   - `https://www.googleapis.com/auth/gmail.readonly`
   - `https://www.googleapis.com/auth/gmail.modify`
   - `https://www.googleapis.com/auth/calendar`
   - `https://www.googleapis.com/auth/calendar.events`
   - `https://www.googleapis.com/auth/contacts.readonly`

### 3. Create OAuth Credentials

1. Go to **APIs & Services > Credentials**
2. Click **Create Credentials > OAuth client ID**
3. Choose **Web application**
4. Add authorized redirect URI: `https://your-domain.com/oauth/google/callback`
5. Copy Client ID and Client Secret to your `.env`

## SignalWire Setup

### 1. Get API Credentials

1. Log into your [SignalWire Dashboard](https://signalwire.com/)
2. Go to **API > API Tokens**
3. Create or copy your Project ID and API Token
4. Note your Space Name (e.g., `your-space.signalwire.com`)

### 2. Configure Phone Number

1. Purchase or port a phone number in SignalWire
2. In the admin panel, go to **Settings > Phone Numbers**
3. Add your SignalWire phone number
4. The system will generate a SWML webhook URL

### 3. Point Phone Number to Webhook

1. In SignalWire Dashboard, go to **Phone Numbers**
2. Click on your number
3. Set **Handle Calls Using**: SWML Script
4. Set **SWML Script URL** to your webhook URL (shown in admin panel)
5. If using basic auth, include credentials in the URL or configure in SignalWire

## Running the Application

### Development (Local)

```bash
# Activate virtual environment
source venv/bin/activate

# Start the server
python app.py

# Or use uvicorn directly
uvicorn app:app --host 0.0.0.0 --port 3000 --reload
```

For local development, use ngrok to expose your server:

```bash
ngrok http 3000
```

Update `SWML_PROXY_URL_BASE` and `GOOGLE_REDIRECT_URI` with your ngrok URL.

### Production

```bash
# Using Procfile (Heroku-compatible)
uvicorn app:app --host 0.0.0.0 --port ${PORT:-3000}

# Or with gunicorn
gunicorn app:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:3000
```

## Deployment Options

### Docker

Create a `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 3000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "3000"]
```

Build and run:

```bash
docker build -t ethan-voice .
docker run -p 3000:3000 --env-file .env ethan-voice
```

### Docker Compose

Create a `docker-compose.yml`:

```yaml
version: '3.8'

services:
  ethan-voice:
    build: .
    ports:
      - "3000:3000"
    env_file:
      - .env
    volumes:
      - ./ethan.db:/app/ethan.db
      - ./knowledge:/app/knowledge
    restart: unless-stopped
```

Run with:

```bash
docker-compose up -d
```

### Heroku

```bash
# Login to Heroku
heroku login

# Create app
heroku create your-app-name

# Set environment variables
heroku config:set SIGNALWIRE_SPACE_NAME=your-space
heroku config:set SIGNALWIRE_PROJECT_ID=your-project-id
heroku config:set SIGNALWIRE_TOKEN=your-token
heroku config:set SWML_PROXY_URL_BASE=https://your-app-name.herokuapp.com
heroku config:set GOOGLE_CLIENT_ID=your-client-id
heroku config:set GOOGLE_CLIENT_SECRET=your-client-secret
heroku config:set GOOGLE_REDIRECT_URI=https://your-app-name.herokuapp.com/oauth/google/callback
heroku config:set SECRET_KEY=$(openssl rand -hex 32)
# ... set remaining env vars

# Deploy
git push heroku main
```

### Railway / Render / Fly.io

These platforms auto-detect the `Procfile` and Python requirements. Set environment variables in their respective dashboards.

### AWS / GCP / Azure

Use container deployment services (ECS, Cloud Run, App Service) with the Docker image, or deploy to a VM with systemd:

```ini
# /etc/systemd/system/ethan-voice.service
[Unit]
Description=Ethan Voice AI Agent
After=network.target

[Service]
User=www-data
WorkingDirectory=/opt/ethan-voice
Environment="PATH=/opt/ethan-voice/venv/bin"
EnvironmentFile=/opt/ethan-voice/.env
ExecStart=/opt/ethan-voice/venv/bin/uvicorn app:app --host 0.0.0.0 --port 3000
Restart=always

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable ethan-voice
sudo systemctl start ethan-voice
```

## Admin Panel Usage

### Initial Setup

1. Navigate to `https://your-domain.com/signup`
2. Create your admin account
3. Log in at `https://your-domain.com/login`

### Configure Your Business

1. **Settings** - Set business name, timezone, owner email/phone
2. **Hours** - Configure business hours for each day
3. **FAQs** - Add frequently asked questions
4. **Knowledge** - Upload documents for AI search
5. **Integrations** - Connect Google account

### Phone Number Setup

1. Go to **Settings > Phone Numbers**
2. Click **Add Phone Number**
3. Enter your SignalWire phone number (E.164 format: `+15551234567`)
4. Copy the generated SWML URL
5. Configure this URL in SignalWire Dashboard

### Owner Mode

When the business owner calls their own number:
- The system recognizes them by caller ID (must match configured owner phone)
- Owner gets a personalized greeting with status summary
- Owner can check emails, messages, calendar, and contacts
- Customer functions (leave message, transfer) are disabled

## API Reference

### Health Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/health` | GET | Basic health check |
| `/ready` | GET | Readiness check (database connection) |

### SWML Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/swml/{token}/` | GET/POST | Main SWML webhook for SignalWire |
| `/swaig/` | POST | SWAIG function callbacks |
| `/post_prompt/` | POST | Post-call summary webhook |

### Admin API

All require authentication via session cookie.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/config` | GET | Get business configuration |
| `/api/config` | POST | Update business configuration |
| `/api/profile` | POST | Update user profile |
| `/api/phone-numbers` | GET | List phone numbers |
| `/api/phone-numbers` | POST | Add phone number |
| `/api/phone-numbers/{id}` | DELETE | Remove phone number |
| `/api/messages` | GET | List caller messages |
| `/api/messages/{id}/read` | POST | Mark message as read |
| `/api/messages/{id}` | DELETE | Delete message |
| `/api/faqs` | GET | List FAQs |
| `/api/faqs` | POST | Create FAQ |
| `/api/faqs/{id}` | PUT | Update FAQ |
| `/api/faqs/{id}` | DELETE | Delete FAQ |
| `/api/faqs/search` | POST | Search FAQs |
| `/api/appointment-types` | GET | List appointment types |
| `/api/appointment-types` | POST | Create appointment type |
| `/api/appointment-types/{id}` | PUT | Update appointment type |
| `/api/appointment-types/{id}` | DELETE | Delete appointment type |
| `/api/hours` | GET | Get business hours |
| `/api/hours` | POST | Update business hours |
| `/api/knowledge/upload` | POST | Upload knowledge document |
| `/api/knowledge/reindex` | POST | Reindex knowledge base |

### OAuth Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/oauth/google/connect` | GET | Start Google OAuth flow |
| `/oauth/google/callback` | GET | OAuth callback handler |
| `/oauth/google/disconnect` | POST | Disconnect Google account |

## Testing

### Test SWAIG Functions

```bash
# Start the agent
python agent.py

# In another terminal, run tests
python test_swaig_http.py --check              # Check if agent is running
python test_swaig_http.py --all                # Run all function tests
python test_swaig_http.py -c calendar          # Test calendar functions
python test_swaig_http.py -f check_calendar_availability -a '{"date": "tomorrow"}'
python test_swaig_http.py --owner -c owner_only  # Test owner-only functions
```

### Test via Phone

1. Ensure your phone number is configured in SignalWire
2. Call your SignalWire number
3. Test various flows:
   - "What are your hours?"
   - "I'd like to book an appointment"
   - "Can I leave a message?"

For owner testing, call from the phone number configured as owner phone.

## Troubleshooting

### Common Issues

**"Google not connected"**
- User needs to complete OAuth flow in admin panel (Integrations page)
- Check that `GOOGLE_REDIRECT_URI` matches exactly in Google Console and `.env`

**"Tool not in request.tools"**
- Function not available in current step
- Check `FUNCTION_AUDIT.md` for function-to-step mapping

**"Insufficient permissions" (Gmail)**
- Re-authenticate Google account to grant new scopes
- Disconnect and reconnect in admin panel Integrations

**SWML webhook not responding**
- Verify `SWML_PROXY_URL_BASE` is accessible from internet
- Check basic auth credentials match in SignalWire and `.env`
- Review logs for errors

**Owner not recognized**
- Verify owner phone in Settings matches caller ID exactly (E.164 format)
- Check `is_owner_calling` in logs

**State bleeding between calls**
- Fixed in latest version - contexts are no longer modified between calls
- If issues persist, restart the agent

### Debug Logging

Set `DEBUG_SWAIG = True` in `agent.py` (enabled by default) to see detailed SWAIG function calls and results in stderr.

### Logs

```bash
# View real-time logs
uvicorn app:app --host 0.0.0.0 --port 3000 2>&1 | tee app.log

# SWML output is logged to stderr
python agent.py 2>&1 | grep "SWML\|SWAIG"
```

## Environment Variables Reference

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `SIGNALWIRE_SPACE_NAME` | Yes | - | SignalWire space name |
| `SIGNALWIRE_PROJECT_ID` | Yes | - | SignalWire project ID |
| `SIGNALWIRE_TOKEN` | Yes | - | SignalWire API token |
| `HOST` | No | `0.0.0.0` | Server bind host |
| `PORT` | No | `3000` | Server port |
| `SWML_PROXY_URL_BASE` | Yes | - | Public URL for webhooks |
| `SWML_BASIC_AUTH_USER` | No | - | Basic auth username for webhooks |
| `SWML_BASIC_AUTH_PASSWORD` | No | - | Basic auth password for webhooks |
| `DATABASE_URL` | No | `sqlite:///./ethan.db` | Database connection string |
| `GOOGLE_CLIENT_ID` | Yes* | - | Google OAuth client ID |
| `GOOGLE_CLIENT_SECRET` | Yes* | - | Google OAuth client secret |
| `GOOGLE_REDIRECT_URI` | Yes* | - | Google OAuth redirect URI |
| `ADMIN_USERNAME` | No | `admin` | Legacy admin username |
| `ADMIN_PASSWORD` | No | `changeme` | Legacy admin password |
| `SECRET_KEY` | Yes | - | Session encryption key |
| `AI_MODEL` | No | `gpt-oss-120b` | AI model for SignalWire |
| `KNOWLEDGE_BACKEND` | No | `sqlite` | Knowledge base backend (`sqlite` or `pgvector`) |
| `KNOWLEDGE_DB_URL` | No | - | PostgreSQL URL for pgvector backend |
| `KNOWLEDGE_INDEX_PATH` | No | `./knowledge/indexes` | Path for knowledge indexes |
| `KNOWLEDGE_DOCS_PATH` | No | `./knowledge/docs` | Path for knowledge documents |
| `KNOWLEDGE_SIMILARITY_THRESHOLD` | No | `0.3` | Minimum similarity for search results |
| `KNOWLEDGE_RESULTS_COUNT` | No | `5` | Max results from knowledge search |

*Required for Google integrations (Gmail, Calendar, Contacts)

## Database Models

| Model | Description |
|-------|-------------|
| `User` | Admin users with authentication |
| `UserSession` | Active login sessions |
| `UserGoogleToken` | Google OAuth tokens per user |
| `UserPhoneNumber` | Phone numbers linked to users |
| `Config` | Business configuration per user |
| `BusinessHours` | Operating hours per day |
| `Holiday` | Holiday closures |
| `AppointmentType` | Available appointment types |
| `Service` | Business services and pricing |
| `FAQ` | Frequently asked questions |
| `Message` | Messages left by callers |
| `CallLog` | Call history with summaries |
| `KnowledgeDocument` | Indexed documents for RAG |

## SWAIG Functions

See `FUNCTION_AUDIT.md` for complete function documentation.

### Core Functions
- `check_business_hours` - Check if currently open
- `get_business_hours` - Get full hours schedule
- `get_business_info` - Get business information
- `get_services` - Get services and pricing
- `get_location` - Get address and directions
- `search_faqs` - Search FAQs
- `save_message` - Save message for owner
- `transfer_to_owner` - Transfer call to owner

### Calendar Functions
- `check_calendar_availability` - Check available time slots
- `find_my_appointments` - Find appointments
- `book_appointment` - Book new appointment
- `cancel_appointment` - Cancel appointment
- `update_appointment` - Update appointment (owner only)
- `respond_to_invite` - RSVP to calendar invite (owner only)

### Email Functions
- `send_email` - Send email
- `email_owner` - Email the business owner
- `get_recent_emails` - Get recent emails (owner only)
- `check_unread_emails` - Get unread count (owner only)
- `read_email` - Read email content (owner only)
- `delete_email` - Delete email (owner only)
- `mark_email_read` - Mark as read (owner only)
- `archive_email` - Archive email (owner only)

### Contact Functions
- `lookup_contact` - Lookup contact by phone
- `search_contacts` - Search contacts by name

### Owner Functions
- `get_messages` - Get caller messages
- `mark_message_read` - Mark message as read
- `delete_message` - Delete message

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## Contributing

Contributions are welcome! Please submit a Pull Request with your changes.

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request
