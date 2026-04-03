import os, requests, smtplib
from flask import Flask, render_template, request, session, redirect, url_for, flash
import datetime as dt
from datetime import timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

# Environment Variables
API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "").strip()
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
W_KEY = os.environ.get("WEATHER_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL") 
GMAIL_PW = os.environ.get("GMAIL_PASSWORD") 
SITE_URL = "https://saturday-tennis.onrender.com"

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

def log_activity(name, action):
    try:
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Logs", headers=HEADERS, 
                      json={"fields": {"Name": name, "Action": action}})
    except: pass

def get_airtable_data(table_name, filter_formula=None, sort_field=None, direction="asc"):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name.replace(' ', '%20')}"
    params = {}
    if filter_formula: params['filterByFormula'] = filter_formula
    if sort_field:
        params['sort[0][field]'] = sort_field
        params['sort[0][direction]'] = direction
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        return r.json().get('records', [])
    except: return []

@app.route('/')
def index():
    # 1. Fetch Settings
    settings = get_airtable_data("Settings")
    d_date, d_start, d_end = "TBD", "TBD", ""
    if settings:
        f = settings[0]['fields']
        d_date, d_start = f.get('Target Date', 'TBD'), f.get('Start Time', 'TBD')
        try:
            start_dt = dt.datetime.strptime(d_start, "%I:%M %p")
            d_end = f" – {(start_dt + timedelta(hours=2, minutes=15)).strftime('%I:%M %p').lstrip('0')}"
        except: pass

    # 2. Fetch Master Data (Strikes/Paused)
    master_recs = get_airtable_data("Master List", sort_field="First")
    strike_map = {str(m['fields'].get('Code')): m['fields'].get('Strikes', 0) for m in master_recs}
    
    # 3. Fetch Signups and Apply Penalty Sorting
    signup_recs = get_airtable_data("Signups", sort_field="Created Time")
    normal_signups, penalized_signups = [], []
    for r in signup_recs:
        code = str(r['fields'].get('Player Code'))
        if strike_map.get(code, 0) >= 2: penalized_signups.append(r)
        else: normal_signups.append(r)
    
    ordered_recs = normal_signups + penalized_signups
    roster = []
    for i, r in enumerate(ordered_recs):
        f = r['fields']; f['id'] = r['id']
        f['strikes'] = strike_map.get(str(f.get('Player Code')), 0)
        roster.append(f)

    playing_cutoff = (min(len(roster), 24) // 4) * 4
    
    # 4. User Status Checks
    user_on_roster, waitlist_pos, user_status, pending_sub_offer = False, 0, None, False
    curr_user = session.get('user')
    
    if curr_user:
        for i, p in enumerate(roster):
            if str(p.get('Player Code')) == str(curr_user.get('code')):
                user_on_roster = True
                user_status = p.get('Label')
                if i >= playing_cutoff: waitlist_pos = i - playing_cutoff + 1
            if str(p.get('Sub Offer')) == str(curr_user.get('code')):
                pending_sub_offer = True

    # 5. Admin Specific Data
    applicants, recent_logs = [], []
    if curr_user and curr_user.get('is_admin'):
        all_apps = get_airtable_data("Applicants")
        applicants = [a for a in all_apps if a['fields'].get('Status') == 'Pending']
        recent_logs = get_airtable_data("Logs", sort_field="Timestamp", direction="desc")[:10]

    # Weather
    weather_info = "Weather Unavailable"
    try:
        w_res = requests.get(f"https://api.weatherapi.com/v1/forecast.json?key={W_KEY}&q=80026&days=7").json()
        sat = next((d for d in w_res['forecast']['forecastday'] if dt.datetime.strptime(d['date'], '%Y-%m-%d').weekday() == 5), None)
        if sat: weather_info = f"Sat: {sat['hour'][8]['condition']['text']} | {int(sat['hour'][8]['temp_f'])}°F"
    except: pass

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end, roster=roster, 
                           applicants=applicants, master_list=master_recs, logs=recent_logs,
                           user_on_roster=user_on_roster, user_status=user_status, waitlist_pos=waitlist_pos, weather=weather_info,
                           playing_cutoff=playing_cutoff, pending_sub_offer=pending_sub_offer)

@app.route('/validate', methods=['POST'])
def validate():
    code = str(request.form.get('code', '')).strip()
    password = request.form.get('password')
    
    recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{code}'")
    if recs:
        f = recs[0]['fields']
        is_admin = (password == ADMIN_PW)
        session['user'] = {
            'id': recs[0]['id'], 
            'first': f.get('First'), 
            'last': f.get('Last'), 
            'code': code, 
            'is_admin': is_admin
        }
        log_activity(f"{f.get('First')} {f.get('Last')}", "Login")
        return redirect(url_for('index'))
    flash("Invalid Code", "danger")
    return redirect(url_for('index'))

@app.route('/attendance/<code_val>', methods=['POST'])
def attendance(code_val):
    if not session.get('user') or not session['user'].get('is_admin'): 
        return redirect(url_for('index'))
    
    status = request.form.get('status')
    note = request.form.get('note', '')
    strike_inc = 1 if status == 'Late' else 2 if status == 'No Show' else 0
    
    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{code_val}'")
    if m_recs:
        new_strikes = m_recs[0]['fields'].get('Strikes', 0) + strike_inc
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{m_recs[0]['id']}", headers=HEADERS, 
                       json={"fields": {"Strikes": new_strikes, "Paused": (new_strikes >= 3)}})
    
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, 
                  json={"fields": {"Player Code": str(code_val), "Attendance": status, "Notes": note, "Date": dt.datetime.now().strftime("%Y-%m-%d")}})
    flash(f"Recorded {status} for {code_val}.", "info")
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if not session.get('user'): return redirect(url_for('index'))
    # Strike check
    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{session['user']['code']}'")
    if m_recs and m_recs[0]['fields'].get('Paused'):
        flash("🚫 Your account is paused due to strikes.", "danger")
        return redirect(url_for('index'))
    
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, 
                  json={"fields": {"First": session['user']['first'], "Last": session['user']['last'], "Player Code": str(session['user']['code'])}})
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
