import os
import requests
import uuid
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "supersecretkey")

# --- Environment Variables ---
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD")

HEADERS = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}

# --- ROBUST AIRTABLE FETCHING ---
def get_airtable_data(table_name):
    """Tries the table name as-is, then with spaces, then lowercase."""
    # Try exact, then with space, then lowercase underscore
    possible_names = [table_name, table_name.replace("_", " ").title(), table_name.lower()]
    for name in possible_names:
        url = f"https://api.airtable.com/v0/{BASE_ID}/{name.replace(' ', '%20')}"
        resp = requests.get(url, headers=HEADERS)
        if resp.status_code == 200:
            return resp.json().get('records', [])
    print(f"ERROR: Could not find table {table_name} after trying multiple variations.")
    return []

def get_weather_forecast(date_str, time_str):
    if not date_str or "TBD" in date_str: return "Weather unavailable."
    try:
        target_dt = datetime.strptime(f"{date_str} {time_str}", "%b %d, %Y %I:%M %p")
        url = f"https://api.weatherapi.com/v1/forecast.json?key={WEATHER_API_KEY}&q=Lafayette,CO&days=10"
        response = requests.get(url).json()
        target_date_string = target_dt.strftime("%Y-%m-%d")
        for day in response.get('forecast', {}).get('forecastday', []):
            if day['date'] == target_date_string:
                for hour in day['hour']:
                    hour_dt = datetime.strptime(hour['time'], "%Y-%m-%d %H:%M")
                    if hour_dt.hour == target_dt.hour:
                        return f"{int(hour['temp_f'])}°F, {hour['condition']['text']}, Wind {int(hour['wind_mph'])} mph"
        return "Forecast not available yet."
    except: return "Weather data temporarily unavailable."

def send_email(to_email, bcc_list, subject, body):
    try:
        msg = EmailMessage(); msg.set_content(body); msg['Subject'] = subject
        msg['From'] = GMAIL_USER; msg['To'] = to_email
        if bcc_list: msg['Bcc'] = ", ".join(bcc_list)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASSWORD)
            smtp.send_message(msg)
        return True
    except: return False

# --- Core Web Routes ---
@app.route('/')
def index():
    # 1. Get Settings
    settings_records = get_airtable_data("Settings")
    display_date = "TBD"
    display_start = "TBD"
    if settings_records:
        f = settings_records[0]['fields']
        display_date = f.get('target_date') or f.get('Target Date') or "TBD"
        display_start = f.get('start_time') or f.get('Start Time') or "TBD"

    # 2. Get Roster
    signup_records = get_airtable_data("Signups")
    roster_list = []
    for r in signup_records:
        fields = r['fields']
        if not fields.get('Status'): fields['Status'] = 'Confirmed'
        roster_list.append(fields)

    # 3. Weather & Deadline
    weather_text = get_weather_forecast(display_date, display_start)
    is_past_deadline = False
    try:
        target_dt = datetime.strptime(display_date, "%b %d, %Y")
        deadline_dt = target_dt - timedelta(days=1) + timedelta(hours=8)
        is_past_deadline = datetime.now() >= deadline_dt
    except: pass
    
    has_waitlist = len(roster_list) > 24

    drafts = []
    if 'user' in session and session['user'].get('is_admin'):
        msg_records = get_airtable_data("Messages")
        for r in msg_records:
            if r['fields'].get('Status') == 'Draft':
                d = r['fields']; d['id'] = r['id']; drafts.append(d)

    return render_template('index.html', weather=weather_text, start_time=display_start, target_date=display_date, roster=roster_list, drafts=drafts, is_past_deadline=is_past_deadline, has_waitlist=has_waitlist)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code', '').strip()
    password = request.form.get('password')
    
    master_records = get_airtable_data("Master List")
    
    if not master_records:
        flash("System Error: Could not connect to Master List. Check Table Names.", "error")
        return redirect(url_for('index'))

    for record in master_records:
        f = record.get('fields', {})
        # Check 'Code' or 'code'
        db_code = str(f.get('Code') or f.get('code') or '')
        if db_code == code:
            is_admin = (password == ADMIN_PW) if password else False
            session['user'] = {'first': f.get('First'), 'last': f.get('Last'), 'code': code, 'is_admin': is_admin}
            return redirect(url_for('index'))
            
    flash(f"Code {code} not found. We checked {len(master_records)} records in the Master List.", "error")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

# ... [Keep other functional routes from previous version] ...
