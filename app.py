import os, requests, random
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

# Credentials
API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "").strip()
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
W_KEY = os.environ.get("WEATHER_API_KEY")
SG_KEY = os.environ.get("SENDGRID_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL")

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# --- HELPERS ---

def send_email(to_email, subject, html_content):
    """Sends an email via SendGrid. Bypasses Airtable limits!"""
    if not SG_KEY or not FROM_EMAIL:
        print("SendGrid credentials missing.")
        return
    message = Mail(from_email=FROM_EMAIL, to_emails=to_email, subject=subject, html_content=html_content)
    try:
        sg = SendGridAPIClient(SG_KEY)
        sg.send(message)
    except Exception as e:
        print(f"SendGrid Error: {e}")

def get_airtable_data(table_name):
    """Fetch all records without view restrictions."""
    # Removed the ?view=Grid%20view part to be more broad
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name.replace(' ', '%20')}"
    try:
        r = requests.get(url, headers=HEADERS)
        if r.status_code == 200:
            return r.json().get('records', [])
        else:
            print(f"Airtable Error {r.status_code}: {r.text}")
    except Exception as e:
        print(f"Error fetching {table_name}: {e}")
    return []

def get_next_code():
    master = get_airtable_data("Master List")
    codes = [int(str(r['fields'].get('Code')).strip()) for r in master if str(r['fields'].get('Code', '')).strip().isdigit() and int(str(r['fields'].get('Code')).strip()) < 9999]
    return str(max(codes) + 1) if codes else "1000"

# --- ROUTES ---

@app.route('/')
def index():
    # 1. Settings & Times
    settings = get_airtable_data("Settings")
    d_date, d_start, d_end = "TBD", "TBD", ""
    if settings:
        f = settings[0]['fields']
        d_date, d_start = f.get('Target Date', 'TBD'), f.get('Start Time', 'TBD')
        try:
            start_dt = datetime.strptime(d_start, "%I:%M %p")
            d_end = f" – {(start_dt + timedelta(hours=2, minutes=15)).strftime('%I:%M %p').lstrip('0')}"
        except: pass

    # 2. Signups
    signup_recs = get_airtable_data("Signups")
    roster = []
    user_on_roster, waitlist_pos = False, 0
    curr_user = session.get('user')
    for i, r in enumerate(signup_recs):
        fields = r['fields']; fields['id'] = r['id']
        roster.append(fields)
        if curr_user and str(fields.get('Player Code')) == str(curr_user.get('code')):
            user_on_roster = True
            if i >= 24: waitlist_pos = i - 23

    # 3. Weather
    weather_info = "Weather Unavailable"
    try:
        w_res = requests.get(f"https://api.weatherapi.com/v1/forecast.json?key={W_KEY}&q=80026&days=7").json()
        sat = next((d for d in w_res['forecast']['forecastday'] if datetime.strptime(d['date'], '%Y-%m-%d').weekday() == 5), None)
        if sat:
            t8, t11 = int(sat['hour'][8]['temp_f']), int(sat['hour'][11]['temp_f'])
            weather_info = f"Sat: {sat['hour'][8]['condition']['text']} | {t8}°F → {t11}°F"
    except: pass

    # 4. Admin
    applicants, strikes = [], 0
    if curr_user:
        if curr_user.get('is_admin'):
            applicants = [a for a in get_airtable_data("Applicants") if a['fields'].get('Status') == 'Pending']
        archive = get_airtable_data("Archive")
        strikes = sum(1 for r in archive if str(r['fields'].get('Player Code')) == str(curr_user.get('code')) and r['fields'].get('Attendance') == 'No Show')

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end, roster=roster, applicants=applicants, user_on_roster=user_on_roster, waitlist_pos=waitlist_pos, strikes=strikes, weather=weather_info)

@app.route('/apply', methods=['POST'])
def apply():
    data = {"fields": {"First": request.form.get('first'), "Last": request.form.get('last'), "Email": request.form.get('email'), "Phone": request.form.get('phone'), "Note": request.form.get('note'), "Status": "Pending"}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json=data)
    flash("Application submitted!", "success")
    return redirect(url_for('index'))

@app.route('/approve/<app_id>', methods=['POST'])
def approve(app_id):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    app_rec = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS).json()
    f = app_rec['fields']
    new_code = get_next_code()
    
    # 1. Add to Master List
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS, json={"fields": {"First": f['First'], "Last": f['Last'], "Code": new_code, "Email": f.get('Email')}})
    
    # 2. Update Applicant Status
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS, json={"fields": {"Status": "Approved", "Assigned Code": new_code}})
    
    # 3. SEND WELCOME EMAIL VIA SENDGRID
    html = f"Hi {f['First']},<br><br>Welcome to the Gang! Your login code is: <b>{new_code}</b><br><br>Sign up here: https://saturday-tennis.onrender.com"
    send_email(f.get('Email'), "Welcome to the Tennis Gang!", html)
    
    flash(f"Approved {f['First']}! Code {new_code} emailed.", "success")
    return redirect(url_for('index'))

@app.route('/reject/<app_id>', methods=['POST'])
def reject(app_id):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    app_rec = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS).json()
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS, json={"fields": {"Status": "Rejected"}})
    
    # SEND REJECTION EMAIL
    html = f"Hi {app_rec['fields']['First']}, thanks for your interest. We are currently at capacity but will keep your info on file!"
    send_email(app_rec['fields'].get('Email'), "Tennis Gang Application Update", html)
    
    flash("Application rejected.", "info")
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if not session.get('user'): return redirect(url_for('index'))
    existing = get_airtable_data("Signups")
    master = get_airtable_data("Master List")
    email = next((r['fields'].get('Email', '') for r in master if str(r['fields'].get('Code')) == str(session['user']['code'])), "")

    data = {"fields": {"First": session['user']['first'], "Last": session['user']['last'], "Player Code": str(session['user']['code']), "Email": email, "Status": "Confirmed" if len(existing) < 24 else "Waitlist", "Date": datetime.now().strftime("%Y-%m-%d")}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=data)
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if not session.get('user'): return redirect(url_for('index'))
    recs = get_airtable_data("Signups")
    
    # Find who is canceling
    cancel_idx = next((i for i, r in enumerate(recs) if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if cancel_idx is not None:
        # If they were in the top 24, the person at index 24 (Waitlist #1) gets promoted
        if cancel_idx < 24 and len(recs) > 24:
            promoted_player = recs[24]
            # UPDATE STATUS IN AIRTABLE
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{promoted_player['id']}", headers=HEADERS, json={"fields": {"Status": "Confirmed"}})
            # SEND PROMOTION EMAIL VIA SENDGRID
            html = f"Hi {promoted_player['fields']['First']},<br><br>Good news! A spot opened up. You are now <b>Confirmed</b> for this Saturday!"
            send_email(promoted_player['fields'].get('Email'), "🎾 You're OFF the Waitlist!", html)

        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[cancel_idx]['id']}", headers=HEADERS)
        
    return redirect(url_for('index'))

# --- ADMIN ACTION & LOGOUT ---
@app.route('/admin_action', methods=['POST'])
def admin_action():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    action = request.form.get('action')
    if action == 'labels':
        recs = get_airtable_data("Settings")
        if recs: requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{recs[0]['id']}", headers=HEADERS, json={"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}})
    elif action == 'reset_roster':
        for r in get_airtable_data("Signups"): requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
    return redirect(url_for('index'))

@app.route('/validate', methods=['POST'])
def validate():
    """Handles login via Player Code with extra formatting protection."""
    # .strip() removes any accidental spaces the user typed
    code = str(request.form.get('code', '')).strip()
    password = request.form.get('password')
    
    master = get_airtable_data("Master List")
    
    for r in master:
        f = r.get('fields', {})
        # Convert Airtable value to string and strip spaces to ensure a match
        airtable_code = str(f.get('Code', '')).strip()
        
        if airtable_code == code:
            # Check for admin login
            is_admin = (code == '9999' and password == ADMIN_PW)
            
            session['user'] = {
                'first': f.get('First'), 
                'last': f.get('Last'), 
                'code': code, 
                'is_admin': is_admin
            }
            return redirect(url_for('index'))
            
    flash(f"Code {code} not found. Please check your email or contact Jim.", "error")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear(); return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
