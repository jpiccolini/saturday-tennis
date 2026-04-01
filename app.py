import os, requests
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

# --- CREDENTIALS ---
API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "").strip()
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
W_KEY = os.environ.get("WEATHER_API_KEY")
SG_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL")

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# --- HELPERS ---
def send_email(to_email, subject, html_content):
    if not SG_KEY or not FROM_EMAIL: return
    message = Mail(from_email=FROM_EMAIL, to_emails=to_email, subject=subject, html_content=html_content)
    try:
        sg = SendGridAPIClient(SG_KEY)
        sg.send(message)
    except Exception as e: print(f"Email Error: {e}")

def get_airtable_data(table_name, filter_formula=None, sort_field=None):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name.replace(' ', '%20')}"
    params = {}
    if filter_formula: params['filterByFormula'] = filter_formula
    if sort_field:
        params['sort[0][field]'] = sort_field
        params['sort[0][direction]'] = "asc"
    
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        return r.json().get('records', []) if r.status_code == 200 else []
    except: return []

# --- ROUTES ---

@app.route('/')
def index():
    settings = get_airtable_data("Settings")
    d_date, d_start, d_end = "TBD", "TBD", ""
    if settings:
        f = settings[0]['fields']
        d_date, d_start = f.get('Target Date', 'TBD'), f.get('Start Time', 'TBD')
        try:
            start_dt = datetime.strptime(d_start, "%I:%M %p")
            d_end = f" – {(start_dt + timedelta(hours=2, minutes=15)).strftime('%I:%M %p').lstrip('0')}"
        except: pass

    signup_recs = get_airtable_data("Signups", sort_field="Created Time")
    roster = []
    user_on_roster, waitlist_pos = False, 0
    curr_user = session.get('user')

    for i, r in enumerate(signup_recs):
        fields = r['fields']; fields['id'] = r['id']
        roster.append(fields)
        if curr_user and str(fields.get('Player Code')) == str(curr_user.get('code')):
            user_on_roster = True
            if i >= 24: waitlist_pos = i - 23

    weather_info = "Weather Unavailable"
    try:
        w_res = requests.get(f"https://api.weatherapi.com/v1/forecast.json?key={W_KEY}&q=80026&days=7").json()
        sat = next((d for d in w_res['forecast']['forecastday'] if datetime.strptime(d['date'], '%Y-%m-%d').weekday() == 5), None)
        if sat:
            t8, t11 = int(sat['hour'][8]['temp_f']), int(sat['hour'][11]['temp_f'])
            weather_info = f"Sat: {sat['hour'][8]['condition']['text']} | {t8}°F → {t11}°F"
    except: pass

    applicants, full_master = [], []
    if curr_user and curr_user.get('is_admin'):
        applicants = [a for a in get_airtable_data("Applicants") if a['fields'].get('Status') == 'Pending']
        full_master = get_airtable_data("Master List", sort_field="First")

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end, roster=roster, 
                           applicants=applicants, master_list=full_master, user_on_roster=user_on_roster, 
                           waitlist_pos=waitlist_pos, weather=weather_info)

@app.route('/admin_action', methods=['POST'])
def admin_action():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    action = request.form.get('action')
    
    if action == 'labels':
        recs = get_airtable_data("Settings")
        if recs: requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{recs[0]['id']}", headers=HEADERS, 
                               json={"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}})
    
    elif action == 'reset_roster':
        for r in get_airtable_data("Signups"): 
            requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
    
    elif action == 'player_update':
        player_id = request.form.get('player_id')
        note = request.form.get('note')
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{player_id}", headers=HEADERS, 
                       json={"fields": {"Notes": note}})
        flash("Player notes updated.", "success")

    return redirect(url_for('index'))

@app.route('/no_show/<player_code>', methods=['POST'])
def no_show(player_code):
    """Logs a strike in the Archive table."""
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, 
                  json={"fields": {"Player Code": str(player_code), "Attendance": "No Show", "Date": datetime.now().strftime("%Y-%m-%d")}})
    flash(f"Strike logged for code {player_code}.", "warning")
    return redirect(url_for('index'))

# ... (Keep /validate, /signup, /cancel, /apply, /approve from previous version) ...
