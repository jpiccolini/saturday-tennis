import os, requests
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "").strip()
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
W_KEY = os.environ.get("WEATHER_API_KEY")
SG_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL")
SITE_URL = "https://saturday-tennis.onrender.com" # Update if different

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

def log_activity(name, action):
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Logs", headers=HEADERS, 
                  json={"fields": {"Name": name, "Action": action}})

def send_email(to_email, subject, html_content):
    if not SG_KEY or not FROM_EMAIL: return
    message = Mail(from_email=FROM_EMAIL, to_emails=to_email, subject=subject, html_content=html_content)
    try:
        sg = SendGridAPIClient(SG_KEY)
        sg.send(message)
    except Exception as e: print(f"Email Error: {e}")

def get_airtable_data(table_name, filter_formula=None, sort_field=None, max_records=None, direction="asc"):
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name.replace(' ', '%20')}"
    params = {}
    if table_name in ["Signups", "Master List", "Applicants"]:
        base_formula = "NOT({First} = '')"
        params['filterByFormula'] = f"AND({base_formula}, {filter_formula})" if filter_formula else base_formula
    elif filter_formula:
        params['filterByFormula'] = filter_formula
    if sort_field:
        params['sort[0][field]'] = sort_field
        params['sort[0][direction]'] = direction
    if max_records:
        params['maxRecords'] = max_records
    try:
        r = requests.get(url, headers=HEADERS, params=params)
        return r.json().get('records', []) if r.status_code == 200 else []
    except: return []

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

    applicants, master_list, recent_logs = [], [], []
    if curr_user and curr_user.get('is_admin'):
        applicants = [a for a in get_airtable_data("Applicants") if a['fields'].get('Status') == 'Pending']
        master_list = get_airtable_data("Master List", sort_field="First")
        recent_logs = get_airtable_data("Logs", sort_field="Timestamp", direction="desc", max_records=10)

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end, roster=roster, 
                           applicants=applicants, master_list=master_list, logs=recent_logs,
                           user_on_roster=user_on_roster, waitlist_pos=waitlist_pos, weather=weather_info)

@app.route('/send_invite', methods=['POST'])
def send_invite():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    email = request.form.get('invite_email')
    subject = "🎾 Invitation to join the Saturday Tennis Gang"
    body = f"Hi! You've been invited to join the Saturday Tennis Gang. Please apply here: {SITE_URL}"
    send_email(email, subject, body)
    flash(f"Invite sent to {email}", "success")
    return redirect(url_for('index'))

@app.route('/send_welcome/<app_id>', methods=['POST'])
def send_welcome(app_id):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    # Fetch applicant to get name/email
    res = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS).json()
    f = res.get('fields', {})
    email, first = f.get('Email'), f.get('First')
    
    # Check Master List for their code
    m_recs = get_airtable_data("Master List", filter_formula=f"AND({{First}}='{first}', {{Last}}='{f.get('Last')}')")
    code = m_recs[0]['fields'].get('Code', 'TBD') if m_recs else "TBD"

    subject = "🎾 You're In! Welcome to the Saturday Tennis Gang"
    content = f"""<h3>Hi {first}!</h3>
    <p>Your application is approved. Here is how to join us:</p>
    <ul>
        <li><b>Site:</b> <a href='{SITE_URL}'>{SITE_URL}</a></li>
        <li><b>Your Login Code:</b> {code}</li>
    </ul>
    <p>Log in, click sign up, and we'll see you on the courts!</p>"""
    
    send_email(email, subject, content)
    flash(f"Welcome email sent to {first}!", "success")
    return redirect(url_for('index'))

@app.route('/validate', methods=['POST'])
def validate():
    code = str(request.form.get('code', '')).strip()
    password = request.form.get('password')
    records = get_airtable_data("Master List", filter_formula=f"{{Code}}='{code}'")
    if records:
        f = records[0]['fields']
        name = f"{f.get('First')} {f.get('Last')}"
        session['user'] = {'first': f.get('First'), 'last': f.get('Last'), 'code': code, 'is_admin': (password == ADMIN_PW)}
        log_activity(name, "Login")
        return redirect(url_for('index'))
    flash("Invalid Code", "error"); return redirect(url_for('index'))

@app.route('/admin_action', methods=['POST'])
def admin_action():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    action = request.form.get('action')
    if action == 'labels':
        recs = get_airtable_data("Settings")
        if recs: requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{recs[0]['id']}", headers=HEADERS, 
                               json={"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}})
    elif action == 'reset_roster':
        for r in get_airtable_data("Signups"): requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
    elif action == 'player_update':
        pid, note = request.form.get('player_id'), request.form.get('note')
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{pid}", headers=HEADERS, json={"fields": {"Notes": note}})
    return redirect(url_for('index'))

@app.route('/attendance/<code_val>', methods=['POST'])
def attendance(code_val):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    status = request.form.get('status')
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, 
                  json={"fields": {"Player Code": str(code_val), "Attendance": status, "Date": datetime.now().strftime("%Y-%m-%d")}})
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if not session.get('user'): return redirect(url_for('index'))
    existing = get_airtable_data("Signups")
    data = {"fields": {"First": session['user']['first'], "Last": session['user']['last'], "Player Code": str(session['user']['code']), "Status": "Confirmed" if len(existing) < 24 else "Waitlist"}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=data)
    log_activity(f"{session['user']['first']} {session['user']['last']}", "Signed Up")
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if not session.get('user'): return redirect(url_for('index'))
    recs = get_airtable_data("Signups", sort_field="Created Time")
    idx = next((i for i, r in enumerate(recs) if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if idx is not None:
        if idx < 24 and len(recs) > 24:
            promo = recs[24]
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{promo['id']}", headers=HEADERS, json={"fields": {"Status": "Confirmed"}})
            send_email(promo['fields'].get('Email'), "🎾 You're IN!", "A spot opened up. You are confirmed!")
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[idx]['id']}", headers=HEADERS)
        log_activity(f"{session['user']['first']} {session['user']['last']}", "Cancelled")
    return redirect(url_for('index'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))

@app.route('/apply', methods=['POST'])
def apply():
    data = {"fields": {"First": request.form.get('first'), "Last": request.form.get('last'), "Email": request.form.get('email'), "Notes": request.form.get('note'), "Status": "Pending"}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json=data)
    flash("Application Submitted!", "success")
    return redirect(url_for('index'))

if __name__ == '__main__': app.run(debug=True)
