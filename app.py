import os, requests, uuid, smtplib
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID")
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
WEATHER_KEY = os.environ.get("WEATHER_API_KEY")

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

def get_table_data(table_name, sort=False):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name.replace(' ', '%20')}"
    if sort:
        url += "?sort%5B0%5D%5Bfield%5D=createdTime&sort%5B0%5D%5Bdirection%5D=asc"
    r = requests.get(url, headers=HEADERS)
    return r.json().get('records', []) if r.status_code == 200 else []

@app.route('/')
def index():
    # 1. Settings
    settings_recs = get_table_data("Settings")
    display_date, display_start = "TBD", "TBD"
    is_past_deadline = False
    if settings_recs:
        f = settings_recs[0]['fields']
        display_date, display_start = f.get('Target Date', 'TBD'), f.get('Start Time', 'TBD')
        try:
            target_dt = datetime.strptime(display_date, "%b %d, %Y")
            deadline_dt = target_dt - timedelta(days=1)
            is_past_deadline = datetime.now() >= deadline_dt.replace(hour=8, minute=0)
        except: pass

    # 2. Roster & Waitlist Position
    signup_recs = get_table_data("Signups", sort=True)
    roster = []
    user_on_roster = False
    waitlist_pos = 0
    
    # Get current user from session safely
    current_user = session.get('user')
    
    for i, r in enumerate(signup_recs):
        fields = r['fields']
        fields['id'] = r['id']
        roster.append(fields)
        if current_user and str(fields.get('Player Code')) == str(current_user.get('code')):
            user_on_roster = True
            if i >= 24:
                waitlist_pos = i - 23

    # 3. Injuries & Strikes
    master_recs = get_table_data("Master List")
    injured = [r['fields'] for r in master_recs if r['fields'].get('Injury Status') == 'Injured']
    strikes = 0
    if current_user:
        archive = get_table_data("Archive")
        strikes = sum(1 for r in archive if str(r['fields'].get('Player Code')) == str(current_user.get('code')) and r['fields'].get('Attendance') == 'No Show')

    return render_template('index.html', target_date=display_date, start_time=display_start, 
                           roster=roster, injured_players=injured, strikes=strikes,
                           is_past_deadline=is_past_deadline, user_on_roster=user_on_roster, 
                           waitlist_pos=waitlist_pos, weather="Check App")

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code', '').strip()
    password = request.form.get('password')
    records = get_table_data("Master List")
    for record in records:
        f = record.get('fields', {})
        if str(f.get('Code')) == code:
            is_admin = (code == '9999' and password == ADMIN_PW)
            session['user'] = {'first': f.get('First'), 'last': f.get('Last'), 'code': code, 'is_admin': is_admin}
            return redirect(url_for('index'))
    flash("Code not found.", "error")
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session: return redirect(url_for('index'))
    count = len(get_table_data("Signups"))
    status = "Confirmed" if count < 24 else "Waitlist"
    data = {"fields": {"First": session['user']['first'], "Last": session['user']['last'], 
                       "Player Code": str(session['user']['code']), "Status": status}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=data)
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if 'user' not in session: return redirect(url_for('index'))
    signup_recs = get_table_data("Signups")
    rid = next((r['id'] for r in signup_recs if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if rid: requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{rid}", headers=HEADERS)
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('index'))
