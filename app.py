import os, requests
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

def get_airtable_data(table_name):
    """Fetches records using the default Grid view order to avoid 422 errors."""
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name.replace(' ', '%20')}?view=Grid%20view"
    try:
        r = requests.get(url, headers=HEADERS)
        if r.status_code == 200:
            return r.json().get('records', [])
        else:
            print(f"Airtable Error {r.status_code} for {table_name}: {r.text}")
            return []
    except Exception as e:
        print(f"Connection Error: {e}")
        return []

@app.route('/')
def index():
    # 1. Settings
    settings = get_airtable_data("Settings")
    d_date, d_start = "TBD", "TBD"
    if settings:
        f = settings[0]['fields']
        d_date = f.get('Target Date', 'TBD')
        d_start = f.get('Start Time', 'TBD')

    # 2. Roster & User Status
    signup_recs = get_airtable_data("Signups")
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
            if i >= 24: 
                waitlist_pos = i - 23

    # 3. Weather (Saturday morning + 3 hours)
    weather_info = "Weather Unavailable"
    try:
        w_res = requests.get(f"https://api.weatherapi.com/v1/forecast.json?key={W_KEY}&q=80026&days=7").json()
        sat = next((d for d in w_res['forecast']['forecastday'] if datetime.strptime(d['date'], '%Y-%m-%d').weekday() == 5), None)
        if sat:
            t8 = int(sat['hour'][8]['temp_f'])
            t11 = int(sat['hour'][11]['temp_f'])
            cond = sat['hour'][8]['condition']['text']
            weather_info = f"Sat: {cond}, {t8}°F → {t11}°F"
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
            is_admin = (code == '9999' and password == ADMIN_PW)
            session['user'] = {'first': f.get('First'), 'last': f.get('Last'), 'code': code, 'is_admin': is_admin}
            return redirect(url_for('index'))
    flash(f"Code {code} not found in Master List.", "error")
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if not session.get('user'): return redirect(url_for('index'))
    count = len(get_airtable_data("Signups"))
    status = "Confirmed" if count < 24 else "Waitlist"
    data = {"fields": {"First": session['user']['first'], "Last": session['user']['last'], 
                       "Player Code": str(session['user']['code']), "Status": status}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=data)
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if not session.get('user'): return redirect(url_for('index'))
    recs = get_airtable_data("Signups")
    rid = next((r['id'] for r in recs if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if rid:
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{rid}", headers=HEADERS)
    return redirect(url_for('index'))

@app.route('/update_settings', methods=['POST'])
def update_settings():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    recs = get_airtable_data("Settings")
    if recs:
        data = {"fields": {"Target Date": request.form.get('target_date'), "Start Time": request.form.get('start_time')}}
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{recs[0]['id']}", headers=HEADERS, json=data)
    return redirect(url_for('index'))

@app.route('/report_injury', methods=['POST'])
def report_injury():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    code = request.form.get('player_code')
    master = get_airtable_data("Master List")
    rid = next((r['id'] for r in master if str(r['fields'].get('Code')) == code), None)
    if rid:
        data = {"fields": {"Injury Status": "Injured", "Expected Return": request.form.get('return_date')}}
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{rid}", headers=HEADERS, json=data)
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('index'))
