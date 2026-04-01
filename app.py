import os, requests, uuid
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

# --- Environment Variables ---
API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "").strip()
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
W_KEY = os.environ.get("WEATHER_API_KEY")

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

def get_airtable_data(table_name, sort=False):
    """Tries to find the table in Airtable; handles potential naming mismatches."""
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name.replace(' ', '%20')}"
    if sort:
        url += "?sort%5B0%5D%5Bfield%5D=createdTime&sort%5B0%5D%5Bdirection%5D=asc"
    try:
        r = requests.get(url, headers=HEADERS)
        if r.status_code == 200:
            return r.json().get('records', [])
    except: pass
    return []

@app.route('/')
def index():
    # 1. Fetch Settings
    settings = get_airtable_data("Settings")
    d_date, d_start = "TBD", "TBD"
    if settings:
        f = settings[0]['fields']
        d_date = f.get('Target Date', 'TBD')
        d_start = f.get('Start Time', 'TBD')

    # 2. Fetch Roster & Waitlist
    signup_recs = get_airtable_data("Signups", sort=True)
    roster = []
    user_on_roster = False
    waitlist_pos = 0
    curr_user = session.get('user')
    
    for i, r in enumerate(signup_recs):
        fields = r['fields']
        fields['id'] = r['id']
        roster.append(fields)
        if curr_user and str(fields.get('Player Code')) == str(curr_user.get('code')):
            user_on_roster = True
            if i >= 24: waitlist_pos = i - 23

    # 3. Weather (Saturday & 3 hours later)
    weather_info = "Weather Unavailable"
    try:
        w_res = requests.get(f"https://api.weatherapi.com/v1/forecast.json?key={W_KEY}&q=Lafayette,CO&days=7").json()
        # Find Saturday in the forecast
        sat_forecast = next((d for d in w_res['forecast']['forecastday'] if datetime.strptime(d['date'], '%Y-%m-%d').weekday() == 5), None)
        if sat_forecast:
            # Get 8 AM and 11 AM temps (adjust index if start time varies)
            temp_start = sat_forecast['hour'][8]['temp_f']
            cond_start = sat_forecast['hour'][8]['condition']['text']
            temp_end = sat_forecast['hour'][11]['temp_f']
            weather_info = f"Sat: {cond_start}, {int(temp_start)}°F → {int(temp_end)}°F"
    except: pass

    # 4. Injuries & Strikes
    master = get_airtable_data("Master List")
    injured = [r['fields'] for r in master if r['fields'].get('Injury Status') == 'Injured']
    strikes = 0
    if curr_user:
        archive = get_airtable_data("Archive")
        strikes = sum(1 for r in archive if str(r['fields'].get('Player Code')) == str(curr_user.get('code')) and r['fields'].get('Attendance') == 'No Show')

    return render_template('index.html', target_date=d_date, start_time=d_start, 
                           roster=roster, injured_players=injured, strikes=strikes,
                           user_on_roster=user_on_roster, waitlist_pos=waitlist_pos, weather=weather_info)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code', '').strip()
    password = request.form.get('password')
    master = get_airtable_data("Master List")
    for r in master:
        f = r.get('fields', {})
        if str(f.get('Code')) == code:
            session['user'] = {'first': f.get('First'), 'last': f.get('Last'), 'code': code, 'is_admin': (code == '9999' and password == ADMIN_PW)}
            return redirect(url_for('index'))
    flash("Code Not Found", "error")
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if not session.get('user'): return redirect(url_for('index'))
    count = len(get_airtable_data("Signups"))
    data = {"fields": {"First": session['user']['first'], "Last": session['user']['last'], 
                       "Player Code": str(session['user']['code']), "Status": "Confirmed" if count < 24 else "Waitlist"}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=data)
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if not session.get('user'): return redirect(url_for('index'))
    recs = get_airtable_data("Signups")
    rid = next((r['id'] for r in recs if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if rid: requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{rid}", headers=HEADERS)
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('index'))
