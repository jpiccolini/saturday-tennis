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

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json"
}

# --- Helper Functions ---
def get_weather_forecast(date_str, time_str):
    if not date_str or "TBD" in date_str:
        return "Weather unavailable (Date not set)."
    try:
        # Try to parse the date flexibly
        target_dt = datetime.strptime(f"{date_str} {time_str}", "%b %d, %Y %I:%M %p")
        url = f"https://api.weatherapi.com/v1/forecast.json?key={WEATHER_API_KEY}&q=Lafayette,CO&days=10"
        response = requests.get(url).json()
        target_date_string = target_dt.strftime("%Y-%m-%d")
        for day in response['forecast']['forecastday']:
            if day['date'] == target_date_string:
                for hour in day['hour']:
                    hour_dt = datetime.strptime(hour['time'], "%Y-%m-%d %H:%M")
                    if hour_dt.hour == target_dt.hour:
                        return f"{int(hour['temp_f'])}°F, {hour['condition']['text']}, Wind {int(hour['wind_mph'])} mph"
        return "Forecast not available yet."
    except: return "Weather data temporarily unavailable."

def send_email(to_email, bcc_list, subject, body):
    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg['Subject'] = subject
        msg['From'] = GMAIL_USER
        msg['To'] = to_email
        if bcc_list: msg['Bcc'] = ", ".join(bcc_list)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASSWORD)
            smtp.send_message(msg)
        return True
    except: return False

def get_player_email(player_code):
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list", headers=HEADERS).json()
    for r in master_resp.get('records', []):
        if str(r['fields'].get('Code')) == str(player_code):
            return r['fields'].get('Email')
    return None

def get_all_master_emails():
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list", headers=HEADERS).json()
    return [r['fields'].get('Email') for r in master_resp.get('records', []) if r['fields'].get('Email')]

# --- Core Web Routes ---
@app.route('/')
def index():
    # 1. Get Settings
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
    display_date = "TBD"
    display_start = "TBD"
    if 'records' in settings_resp and len(settings_resp['records']) > 0:
        f = settings_resp['records'][0]['fields']
        # Flexibility for field names with spaces
        display_date = f.get('target_date') or f.get('Target Date') or "TBD"
        display_start = f.get('start_time') or f.get('Start Time') or "TBD"

    # 2. Get Roster
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
    roster_list = []
    for r in signups_resp.get('records', []):
        fields = r['fields']
        # If Status is empty, default it to "Confirmed" so they show up!
        if not fields.get('Status'):
            fields['Status'] = 'Confirmed'
        roster_list.append(fields)

    # 3. Weather
    weather_text = get_weather_forecast(display_date, display_start)

    # 4. Deadline Check
    is_past_deadline = False
    try:
        target_dt = datetime.strptime(display_date, "%b %d, %Y")
        deadline_dt = target_dt - timedelta(days=1) + timedelta(hours=8)
        is_past_deadline = datetime.now() >= deadline_dt
    except: pass
    
    has_waitlist = len(roster_list) > 24 or (len(roster_list) % 4 != 0)

    drafts = []
    if 'user' in session and session['user'].get('is_admin'):
        messages_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/messages", headers=HEADERS).json()
        for r in messages_resp.get('records', []):
            if r['fields'].get('Status') == 'Draft':
                draft_data = r['fields']
                draft_data['id'] = r['id']
                drafts.append(draft_data)

    return render_template('index.html', weather=weather_text, start_time=display_start, target_date=display_date, roster=roster_list, drafts=drafts, is_past_deadline=is_past_deadline, has_waitlist=has_waitlist)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code')
    password = request.form.get('password')
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list", headers=HEADERS).json()
    
    for record in master_resp.get('records', []):
        fields = record.get('fields', {})
        # Compare as strings to avoid type issues
        if str(fields.get('Code')) == str(code):
            is_admin = (password == ADMIN_PW) if password else False
            if password and not is_admin:
                flash("Incorrect Admin Password.", "error")
                return redirect(url_for('index'))
            
            session['user'] = {
                'first': fields.get('First'),
                'last': fields.get('Last'),
                'code': code,
                'is_admin': is_admin
            }
            return redirect(url_for('index'))
            
    flash(f"Code {code} not found in Master List.", "error")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

# ... [KEEP ALL REMAINING ROUTES FROM THE PREVIOUS CODE BLOCK] ...
# (Signup, Cancel, Action, Update Settings, Cron Routes remain the same)
