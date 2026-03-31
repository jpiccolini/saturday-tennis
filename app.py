import os
import requests
import uuid
import smtplib
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta
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

# --- Strike Calculation ---
def get_strike_count(player_code):
    current_year = str(datetime.now().year)
    # Search Archive for 'No Show' strikes for this player
    formula = f"AND({{Player Code}}='{player_code}', {{Attendance}}='No Show')"
    url = f"https://api.airtable.com/v0/{BASE_ID}/Archive?filterByFormula={formula}"
    resp = requests.get(url, headers=HEADERS).json()
    strikes = 0
    for r in resp.get('records', []):
        date_str = r['fields'].get('Date', '')
        if current_year in date_str:
            strikes += 1
    return strikes

# --- Weather Helper ---
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

# --- Routes ---
@app.route('/')
def index():
    # 1. Get Settings
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Settings", headers=HEADERS).json()
    display_date, display_start = "TBD", "TBD"
    if 'records' in settings_resp and len(settings_resp['records']) > 0:
        f = settings_resp['records'][0]['fields']
        display_date = f.get('Target Date', 'TBD')
        display_start = f.get('Start Time', 'TBD')

    # 2. Get Roster
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS).json()
    roster_list = []
    for r in signups_resp.get('records', []):
        fields = r['fields']; fields['id'] = r['id']
        if not fields.get('Status'): fields['Status'] = 'Confirmed'
        roster_list.append(fields)

    # 3. Get Injured Players
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS).json()
    injured_players = [r['fields'] for r in master_resp.get('records', []) if r['fields'].get('Injury Status') == 'Injured']

    # 4. Strike Check
    strikes = get_strike_count(session['user']['code']) if 'user' in session else 0
    weather_text = get_weather_forecast(display_date, display_start)

    return render_template('index.html', weather=weather_text, start_time=display_start, 
                           target_date=display_date, roster=roster_list, 
                           injured_players=injured_players, strikes=strikes)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code', '').strip()
    password = request.form.get('password')
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS).json()
    for record in master_resp.get('records', []):
        f = record.get('fields', {})
        if str(f.get('Code')) == code:
            session['user'] = {
                'first': f.get('First'), 'last': f.get('Last'), 
                'code': code, 'is_admin': (password == ADMIN_PW),
                'injured': f.get('Injury Status') == 'Injured'
            }
            return redirect(url_for('index'))
    flash("Code not found.", "error")
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session: return redirect(url_for('index'))
    
    # Block if Injured
    if session['user'].get('injured'):
        flash("You are currently marked as Injured. Contact Admin to clear your status.", "error")
        return redirect(url_for('index'))

    # Check Strikes
    strikes = get_strike_count(session['user']['code'])
    if strikes >= 3:
        flash("Signup Blocked: 3+ strikes. Contact Admin.", "error")
        return redirect(url_for('index'))

    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS).json()
    count = len(signups_resp.get('records', []))
    
    status = "Waitlist" if (count >= 24 or strikes == 2) else "Confirmed"
    if strikes == 2: flash("Note: Placed on Waitlist due to attendance history.", "info")

    data = {"fields": {"First": session['user']['first'], "Last": session['user']['last'], 
                       "Player Code": session['user']['code'], "Status": status}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=data)
    return redirect(url_for('index'))

@app.route('/report_injury', methods=['POST'])
def report_injury():
    if not session.get('user', {}).get('is_admin'): return "Unauthorized", 403
    player_code = request.form.get('player_code')
    return_date = request.form.get('return_date')
    
    # Find record in Master List
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS).json()
    record_id = next((r['id'] for r in master_resp['records'] if str(r['fields'].get('Code')) == player_code), None)
    
    if record_id:
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{record_id}", headers=HEADERS, 
                       json={"fields": {"Injury Status": "Injured", "Expected Return": return_date}})
        flash("Injury status updated.", "success")
    return redirect(url_for('index'))

# --- Include existing Cancel, Action, and Cleanup routes here ---
# (Ensure Cleanup sets Attendance: "Present" by default)
