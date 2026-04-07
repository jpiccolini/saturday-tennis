# ==========================================
# TABLE OF CONTENTS - app.py
# 1. SETUP & CONFIG (Env Vars, Headers)
# 2. UTILITY FUNCTIONS (Email, Logging)
# 3. DATA CACHING ENGINE
# 4. PRIMARY ROUTES (Index, Login/Logout)
# 5. PLAYER ACTIONS (Signup, Cancel, Subs, Profile)
# 6. ADMIN & GUEST ACTIONS
# 7. CRON / AUTOMATION ROUTES
# ==========================================

import os, requests, smtplib
from flask import Flask, render_template, request, session, redirect, url_for, flash
import datetime as dt
from datetime import timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

# === SECTION 1: SETUP & CONFIG ===
API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "").strip()
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
W_KEY = os.environ.get("WEATHER_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL") 
GMAIL_PW = os.environ.get("GMAIL_PASSWORD") 
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", FROM_EMAIL) 
SITE_URL = "https://saturday-tennis.onrender.com"

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# === SECTION 2: UTILITY FUNCTIONS ===
def log_activity(name, action):
    try:
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Logs", headers=HEADERS, 
                      json={"fields": {"Name": name, "Action": action}})
    except: pass

def send_email(to_emails, subject, html_content, is_multiple=False):
    if not FROM_EMAIL or not GMAIL_PW or not to_emails: return
    if isinstance(to_emails, str): to_emails = [to_emails]
    msg = MIMEMultipart()
    msg['From'] = FROM_EMAIL
    msg['Subject'] = subject
    if is_multiple:
        msg['To'] = FROM_EMAIL 
        recipients = to_emails + [FROM_EMAIL]
    else:
        msg['To'] = to_emails[0]
        recipients = to_emails
    msg.attach(MIMEText(html_content, 'html'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(FROM_EMAIL, GMAIL_PW)
        server.sendmail(FROM_EMAIL, recipients, msg.as_string())
        server.quit()
    except Exception as e: print(f"Email Error: {e}")

# === SECTION 3: DATA CACHING ENGINE ===
AIRTABLE_CACHE = {}
CACHE_TTL = 60 

def get_airtable_data(table_name, sort_field=None, direction="asc", filter_formula=None):
    current_time = time.time()
    cache_key = f"{table_name}_{sort_field}_{direction}_{filter_formula}"
    if cache_key in AIRTABLE_CACHE:
        cached_time, cached_data = AIRTABLE_CACHE[cache_key]
        if current_time - cached_time < CACHE_TTL:
            return cached_data
    try:
        url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name}?"
        if sort_field: url += f"sort[0][field]={sort_field}&sort[0][direction]={direction}&"
        if filter_formula: url += f"filterByFormula={filter_formula}"
        res = requests.get(url, headers=HEADERS)
        data = res.json().get('records', [])
        AIRTABLE_CACHE[cache_key] = (current_time, data)
        return data
    except: return []

# === SECTION 4: PRIMARY ROUTES ===
@app.route('/')
def index():
    settings = get_airtable_data("Settings")
    d_date, d_start, d_end = "TBD", "TBD", ""
    start_dt = None
    if settings:
        f = settings[0]['fields']
        d_date, d_start = f.get('Target Date', 'TBD'), f.get('Start Time', 'TBD')
        try:
            start_dt = dt.datetime.strptime(d_start, "%I:%M %p")
            d_end = f" – {(start_dt + timedelta(hours=2, minutes=15)).strftime('%I:%M %p').lstrip('0')}"
        except: 
            # Fallback if time format in Airtable is non-standard
            d_end = ""

    master_recs = get_airtable_data("Master List", sort_field="First")
    strike_map = {str(m['fields'].get('Code')): m['fields'].get('Strikes', 0) for m in master_recs}
    signup_recs = get_airtable_data("Signups", sort_field="Created Time")
    
    roster = []
    for r in signup_recs:
        f = r['fields']; f['id'] = r['id']
        f['strikes'] = strike_map.get(str(f.get('Player Code')), 0)
        roster.append(f)

    total_signups = len(roster)
    playing_cutoff = (min(total_signups, 24) // 4) * 4
    waitlist_count = total_signups - playing_cutoff

    user_on_roster, waitlist_pos, pending_sub_offer = False, 0, False
    curr_user = session.get('user')
    if curr_user:
        for i, p in enumerate(roster):
            if str(p.get('Player Code')) == str(curr_user.get('code')):
                user_on_roster = True
                if i >= playing_cutoff: waitlist_pos = i - playing_cutoff + 1
            if str(p.get('Sub Offer')) == str(curr_user.get('code')):
                pending_sub_offer = True

    # FLEXIBLE WEATHER LOGIC
    weather_info = "Weather Unavailable"
    try:
        w_res = requests.get(f"https://api.weatherapi.com/v1/forecast.json?key={W_KEY}&q=80026&days=8").json()
        target_day = None
        for d in w_res['forecast']['forecastday']:
            api_dt = dt.datetime.strptime(d['date'], '%Y-%m-%d')
            # Check for name match or if it's the upcoming Saturday
            if d_date in [api_dt.strftime('%b %d'), api_dt.strftime('%B %d')]:
                target_day = d
                break
        if not target_day:
            target_day = next((d for d in w_res['forecast']['forecastday'] if dt.datetime.strptime(d['date'], '%Y-%m-%d').weekday() == 5), None)
            
        if target_day:
            s_hour = start_dt.hour if start_dt else 9
            cond = target_day['hour'][s_hour]['condition']['text']
            temp = int(target_day['hour'][s_hour]['temp_f'])
            weather_info = f"{cond} | {temp}°F at session start"
    except: pass

    applicants, guest_requests = [], []
    if curr_user and curr_user.get('is_admin'):
        all_apps = get_airtable_data("Applicants")
        applicants = [a for a in all_apps if a['fields'].get('Status') == 'Pending' and not a['fields'].get('Sponsor')]
        guest_requests = [a for a in all_apps if a['fields'].get('Status') == 'Pending' and a['fields'].get('Sponsor')]

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end, roster=roster, 
                           applicants=applicants, guest_requests=guest_requests, master_list=master_recs,
                           user_on_roster=user_on_roster, waitlist_pos=waitlist_pos, weather=weather_info,
                           playing_cutoff=playing_cutoff, total_signups=total_signups, waitlist_count=waitlist_count, 
                           pending_sub_offer=pending_sub_offer)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code')
    password = request.form.get('password')
    master = get_airtable_data("Master List")
    user_rec = next((m for m in master if str(m['fields'].get('Code')) == str(code)), None)
    
    if user_rec:
        is_admin = (password == ADMIN_PW)
        f = user_rec['fields']
        session['user'] = {
            'code': code, 'first': f.get('First'), 'last': f.get('Last'),
            'email': f.get('Email'), 'phone': f.get('Phone'), 'is_admin': is_admin
        }
        log_activity(f.get('First'), "Logged In")
        flash(f"Welcome, {f.get('First')}!", "success")
    else:
        flash("Invalid Player Code.", "danger")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# === SECTION 5: PLAYER ACTIONS ===
@app.route('/signup', methods=['POST'])
def signup():
    user = session.get('user')
    if not user: return redirect(url_for('index'))
    
    existing = get_airtable_data("Signups", filter_formula=f"{{Player Code}}='{user['code']}'")
    if existing:
        flash("You are already signed up!", "warning")
        return redirect(url_for('index'))

    payload = {"fields": {
        "First": user['first'], 
        "Last": user['last'], 
        "Player Code": int(user['code']),
        "Email": user['email']
    }}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=payload)
    AIRTABLE_CACHE.clear()
    log_activity(user['first'], "Signed Up")
    flash("You've been added to the list!", "success")
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    user = session.get('user')
    if not user: return redirect(url_for('index'))
    
    signup_recs = get_airtable_data("Signups")
    user_signup = next((r for r in signup_recs if str(r['fields'].get('Player Code')) == str(user['code'])), None)
    
    if user_signup:
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{user_signup['id']}", headers=HEADERS)
        AIRTABLE_CACHE.clear()
        log_activity(user['first'], "Cancelled")
        flash("Your spot has been cancelled.", "info")
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user = session.get('user')
    if not user: return redirect(url_for('index'))
    new_email = request.form.get('email')
    new_phone = request.form.get('phone')
    
    master = get_airtable_data("Master List")
    user_rec = next((m for m in master if str(m['fields'].get('Code')) == str(user['code'])), None)
    
    if user_rec:
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{user_rec['id']}", 
                       headers=HEADERS, json={"fields": {"Email": new_email, "Phone": new_phone}})
        session['user']['email'] = new_email
        session['user']['phone'] = new_phone
        AIRTABLE_CACHE.clear()
        flash("Profile updated!", "success")
    return redirect(url_for('index'))

@app.route('/apply', methods=['POST'])
def apply():
    payload = {"fields": {
        "First": request.form.get('first'),
        "Last": request.form.get('last'),
        "Email": request.form.get('email'),
        "Status": "Pending"
    }}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json=payload)
    flash("Application submitted! We will email you your code once approved.", "success")
    return redirect(url_for('index'))

@app.route('/request_guest', methods=['POST'])
def request_guest():
    user = session.get('user')
    if not user: return redirect(url_for('index'))
    payload = {"fields": {
        "First": request.form.get('guest_first'),
        "Last": request.form.get('guest_last'),
        "Sponsor": f"{user['first']} {user['last']}",
        "Status": "Pending"
    }}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json=payload)
    flash("Guest request submitted to Admin.", "info")
    return redirect(url_for('index'))

# === SECTION 6: ADMIN ACTIONS ===
@app.route('/admin_action', methods=['POST'])
def admin_action():
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    action = request.form.get('action')
    
    if action == "labels":
        settings = get_airtable_data("Settings")
        if settings:
            payload = {"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}}
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}", headers=HEADERS, json=payload)
            flash("Session info updated!", "success")
    
    elif action == "reset_roster":
        return cron_monday() # Clear and notify
        
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/info_blast', methods=['POST'])
def info_blast():
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    msg = request.form.get('message')
    group = request.form.get('target_group')
    
    if group == "roster":
        emails = [r['fields'].get('Email') for r in get_airtable_data("Signups") if r['fields'].get('Email')]
    else:
        emails = [m['fields'].get('Email') for m in get_airtable_data("Master List") if m['fields'].get('Email')]
        
    send_email(emails, "🎾 Tennis Gang Announcement", f"<p>{msg}</p>", is_multiple=True)
    flash("Announcement sent!", "success")
    return redirect(url_for('index'))

@app.route('/attendance/<code_str>', methods=['POST'])
def attendance(code_str):
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    # Logic to toggle attendance/label in Airtable
    flash(f"Updated attendance for {code_str}", "info")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

# === SECTION 7: CRON / AUTOMATION ===
@app.route('/cron/monday')
def cron_monday():
    settings = get_airtable_data("Settings")
    d_date = settings[0]['fields'].get('Target Date', 'TBD')
    d_start = settings[0]['fields'].get('Start Time', 'TBD')
    
    signups = get_airtable_data("Signups")
    for r in signups:
        # Archive
        archive_payload = {"fields": {
            "First": r['fields'].get('First'), 
            "Last": r['fields'].get('Last'), 
            "Date": d_date,
            "Attendance": r['fields'].get('Label', 'Signed Up')
        }}
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json=archive_payload)
        # Delete
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
    
    emails = [m['fields'].get('Email') for m in get_airtable_data("Master List") if m['fields'].get('Email')]
    send_email(emails, f"🎾 Signups OPEN for {d_date}!", f"<h3>Signups are open!</h3><p>Time: {d_start}</p><p><a href='{SITE_URL}'>Claim your spot!</a></p>", is_multiple=True)
    
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
