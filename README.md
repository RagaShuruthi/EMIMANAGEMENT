# EMI Management System

A modular Django web application for managing mobile phone EMI financing.

## Project Structure

The project is organised into nine Django applications:

| App              | Purpose                                    |
|------------------|--------------------------------------------|
| `accounts`       | Authentication, user accounts and roles    |
| `shops`          | Mobile shop management                     |
| `customers`      | Customer registration and profiles         |
| `applications`   | EMI application processing                 |
| `payments`       | EMI schedules, payment tracking, collections |
| `followups`      | Employee assignments and customer follow-ups |
| `devices`        | Device records and restriction workflows   |
| `notifications`  | Notifications and reminders                |
| `reports`        | Business reports and analytics             |

## Quick Start

```bash
# 1. Create and activate a virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
# source venv/bin/activate   # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Copy and edit the environment file
copy .env.example .env       # Windows
# cp .env.example .env       # macOS / Linux

# 4. Run migrations
python manage.py migrate

# 5. (Optional) Load sample/initial data (shops, users, roles)
python manage.py loaddata initial_data.json

# 6. Start the development server
python manage.py runserver
```

The application will be available at `http://127.0.0.1:8000/`.

## Configuration

All secrets and environment-specific values are loaded from a `.env` file (see `.env.example`).

To use **PostgreSQL** instead of SQLite, set `DB_ENGINE=postgresql` in your `.env` and provide the connection details.

