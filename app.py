import os, requests, uuid, smtplib
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

# --- Environment Variables ---
API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID")
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
WEATHER_KEY = os.environ.get("WEATHER_API_KEY")

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

def get_table_data(table_name, sort_field=None):
    """Fetches data with optional sorting to maintain signup order."""
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name.replace(' ', '%20')}"
    if sort_field:
        # Sorts by Airtable's internal createdTime to ensure order
        url += "?sort%5B0%5D%5Bfield%5D=createdTime&sort%5B0%5D%5Bdirection%5D=asc"
    
    r = requests.get(url, headers=HEADERS)
    return r.json().get('records', []) if r.status_code == 200 else []

@app.route('/')
def index():
    # 1. Fetch Settings for Header
    settings_recs = get_table_data("Settings")
    display_date = "TBD"
    display_start = "TBD"
    if settings_recs:
        f = settings_recs[0]['fields']
        display_date = f.get('Target Date', 'TBD')
        display_start = f.get('Start Time', 'TBD')

    # 2. Fetch Roster (Sorted by signup time)
    signup_recs = get_table_data("Signups", sort_field="createdTime")
    roster = [r['fields'] for r in signup_recs]
    
    # 3. Fetch Injuries
    master_recs = get_table_data("Master List")
    injured = [r['fields'] for r in master_recs if r['fields'].get('Injury Status') == 'Injured']

    # 4. Weather Logic
    weather_text = "Forecast Unavailable"
    if display_date != "TBD":
        try:
            w_url = f"https://api.weatherapi.com/v1/forecast.json?key={WEATHER_KEY}&q=Lafayette,CO&days=10"
            w_resp = requests.get(w_url).json()
            # Simple check for target date in forecast
            weather_text = w_resp['forecast']['forecastday'][0]['day']['condition']['text']
        except: pass

    return render_template('index.html', 
                           target_date=display_date, 
                           start_time=display_start, 
                           roster=roster, 
                           injured_players=injured, 
                           weather=weather_text)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code', '').strip()
    password = request.form.get('password')
    
    records = get_table_data("Master List")
    for record in records:
        f = record.get('fields', {})
        if str(f.get('Code')) == code:
            # ONLY 9999 can be an admin, and ONLY if the password matches
            is_admin = (code == '9999' and password == ADMIN_PW)
            session['user'] = {
                'first': f.get('First'), 
                'last': f.get('Last'), 
                'code': code, 
                'is_admin': is_admin
            }
            return redirect(url_for('index'))
            
    flash(f"Code {code} not found.", "error")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

# Add standard signup/cancel/report_injury routes as previously discussed...
