import os, requests, random
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

# Setup Credentials
API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "").strip()
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
W_KEY = os.environ.get("WEATHER_API_KEY")

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# --- HELPERS ---

def get_airtable_data(table_name):
    """Fetch all records from a specific Airtable table."""
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name.replace(' ', '%20')}?view=Grid%20view"
    try:
        r = requests.get(url, headers=HEADERS)
        if r.status_code == 200:
            return r.json().get('records', [])
    except Exception as e:
        print(f"Error fetching {table_name}: {e}")
    return []

def get_next_code():
    """Finds the highest numeric code in Master List and returns the next one."""
    master = get_airtable_data("Master List")
    codes = []
    for r in master:
        val = str(r['fields'].get('Code', '')).strip()
        if val.isdigit():
            c = int(val)
            if c < 9999: # Skip the admin code
                codes.append(c)
    
    if not codes:
        return "1000"
    return str(max(codes) + 1)

# --- ROUTES ---

@app.route('/')
def index():
    # 1. Fetch Settings
    settings = get_airtable_data("Settings")
    d_date, d_start, d_end = "TBD", "TBD", ""
    if settings:
        f = settings[0]['fields']
        d_date = f.get('Target Date', 'TBD')
        d_start = f.get('Start Time', 'TBD')
        try:
            start_dt = datetime.strptime(d_start, "%I:%M %p")
            end_dt = start_dt + timedelta(hours=2, minutes=15)
            d_end = f" – {end_dt.strftime('%I:%M %p').lstrip('0')}"
        except: d_end = ""

    # 2. Fetch Signups & Determine User Status
    signup_recs = get_airtable_data("Signups")
    roster = []
    user_on_roster = False
    waitlist_pos = 0
    curr_user = session.get('user')

    for i, r in enumerate(signup_recs):
        fields = r['fields']; fields['id'] = r['id']
        roster.append(fields)
        if curr_user and str(fields.get('Player Code')) == str(curr_user.get('code')):
            user_on_roster = True
            if i >= 24: waitlist_pos = i - 23

    # 3. Weather API
    weather_info = "Weather Unavailable"
    try:
        w_res = requests.get(f"https://api.weatherapi.com/v1/forecast.json?key={W_KEY}&q=80026&days=7").json()
        sat = next((d for d in w_res['forecast']['forecastday'] if datetime.strptime(d['date'], '%Y-%m-%d').weekday() == 5), None)
        if sat:
            t8 = int(sat['hour'][8]['temp_f'])
            t11 = int(sat['hour'][11]['temp_f'])
            cond = sat['hour'][8]['condition']['text']
            weather_info = f"Sat: {cond} | {t8}°F → {t11}°F"
    except: pass

    # 4. Admin Only: Applicants & Master List
    applicants = []
    strikes = 0
    if curr_user:
        if curr_user.get('is_admin'):
            all_apps = get_airtable_data("Applicants")
            applicants = [a for a in all_apps if a['fields'].get('Status') == 'Pending']
        
        # Check for Strikes/No-shows in Archive
        archive = get_airtable_data("Archive")
        strikes = sum(1 for r in archive if str(r['fields'].get('Player Code')) == str(curr_user.get('code')) and r['fields'].get('Attendance') == 'No Show')

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end,
                           roster=roster, applicants=applicants,
                           user_on_roster=user_on_roster, waitlist_pos=waitlist_pos, 
                           strikes=strikes, weather=weather_info)

@app.route('/apply', methods=['POST'])
def apply():
    """Handles new player application submission."""
    data = {"fields": {
        "First": request.form.get('first'),
        "Last": request.form.get('last'),
        "Email": request.form.get('email'),
        "Phone": request.form.get('phone'),
        "Note": request.form.get('note'),
        "Status": "Pending"
    }}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json=data)
    flash("Application submitted! We will email you once approved.", "success")
    return redirect(url_for('index'))

@app.route('/approve/<app_id>', methods=['POST'])
def approve(app_id):
    """Admin approves a player, moves them to Master List, and triggers Email via Airtable."""
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    
    # Get Applicant details
    app_rec = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS).json()
    f = app_rec['fields']
    
    new_code = get_next_code()
    
    # 1. Add to Master List
    master_payload = {"fields": {
        "First": f['First'], 
        "Last": f['Last'], 
        "Code": new_code, 
        "Email": f.get('Email')
    }}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS, json=master_payload)
    
    # 2. Mark applicant as Approved (This triggers your Gmail Automation)
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS, 
                   json={"fields": {"Status": "Approved", "Assigned Code": new_code}})
    
    flash(f"Approved {f['First']}! Code {new_code} is being emailed.", "success")
    return redirect(url_for('index'))

@app.route('/reject/<app_id>', methods=['POST'])
def reject(app_id):
    """Admin rejects an applicant."""
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS, json={"fields": {"Status": "Rejected"}})
    flash("Application rejected.", "info")
    return redirect(url_for('index'))

@app.route('/validate', methods=['POST'])
def validate():
    """Handles login via Player Code."""
    code = request.form.get('code', '').strip()
    password = request.form.get('password')
    master = get_airtable_data("Master List")
    
    for r in master:
        f = r.get('fields', {})
        if str(f.get('Code')) == code:
            is_admin = (code == '9999' and password == ADMIN_PW)
            session['user'] = {'first': f.get('First'), 'last': f.get('Last'), 'code': code, 'is_admin': is_admin}
            return redirect(url_for('index'))
            
    flash(f"Code {code} not found. Please request access if you are new.", "error")
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    """Adds user to the weekly Signups table."""
    if not session.get('user'): return redirect(url_for('index'))
    
    existing = get_airtable_data("Signups")
    
    # Find user's email from Master List to enable Waitlist-to-Confirmed automations
    master = get_airtable_data("Master List")
    email = next((r['fields'].get('Email', '') for r in master if str(r['fields'].get('Code')) == str(session['user']['code'])), "")

    data = {"fields": {
        "First": session['user']['first'], 
        "Last": session['user']['last'], 
        "Player Code": str(session['user']['code']), 
        "Email": email,
        "Status": "Confirmed" if len(existing) < 24 else "Waitlist",
        "Date": datetime.now().strftime("%Y-%m-%d")
    }}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=data)
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    """Removes user from the weekly Signups table."""
    if not session.get('user'): return redirect(url_for('index'))
    recs = get_airtable_data("Signups")
    rid = next((r['id'] for r in recs if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if rid:
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{rid}", headers=HEADERS)
    return redirect(url_for('index'))

@app.route('/admin_action', methods=['POST'])
def admin_action():
    """Handles global admin tasks like clearing roster or updating date."""
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    action = request.form.get('action')
    
    if action == 'labels':
        recs = get_airtable_data("Settings")
        if recs:
            payload = {"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}}
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{recs[0]['id']}", headers=HEADERS, json=payload)
    
    elif action == 'reset_roster':
        recs = get_airtable_data("Signups")
        for r in recs:
            requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
        flash("Roster cleared for the new week.", "success")
        
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
