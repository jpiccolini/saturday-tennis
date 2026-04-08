# ==========================================
# TABLE OF CONTENTS - app.py
# 1. SETUP & CONFIG (Env Vars, Headers)
# 2. UTILITY FUNCTIONS (Email, Logging)
# 3. DATA CACHING ENGINE (With Pagination)
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

# === SECTION 3: DATA CACHING ENGINE (WITH PAGINATION) ===
AIRTABLE_CACHE = {}
CACHE_TTL = 30 

def get_airtable_data(table_name, sort_field=None, direction="asc", filter_formula=None):
    current_time = time.time()
    cache_key = f"{table_name}_{sort_field}_{direction}_{filter_formula}"
    
    if cache_key in AIRTABLE_CACHE:
        cached_time, cached_data = AIRTABLE_CACHE[cache_key]
        if current_time - cached_time < CACHE_TTL:
            return cached_data
            
    records = []
    offset = None
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name}"
    
    try:
        # Loop handles Airtable's 100-record limit so nobody gets dropped
        while True:
            params = {}
            if sort_field:
                params["sort[0][field]"] = sort_field
                params["sort[0][direction]"] = direction
            if filter_formula:
                params["filterByFormula"] = filter_formula
            if offset:
                params["offset"] = offset
                
            res = requests.get(url, headers=HEADERS, params=params)
            res.raise_for_status()
            data = res.json()
            records.extend(data.get('records', []))
            
            offset = data.get('offset')
            if not offset:
                break
                
        AIRTABLE_CACHE[cache_key] = (current_time, records)
        return records
    except Exception as e: 
        print(f"Airtable Fetch Error ({table_name}): {e}")
        return []

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
        except: d_end = ""

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

    weather_info = "Weather Unavailable"
    try:
        w_res = requests.get(f"https://api.weatherapi.com/v1/forecast.json?key={W_KEY}&q=80026&days=8").json()
        target_day = None
        for d in w_res['forecast']['forecastday']:
            api_dt = dt.datetime.strptime(d['date'], '%Y-%m-%d')
            d1, d2 = api_dt.strftime('%b %d'), api_dt.strftime('%b %d').replace(' 0', ' ')
            d3, d4 = api_dt.strftime('%B %d'), api_dt.strftime('%B %d').replace(' 0', ' ')
            if d_date in [d1, d2, d3, d4]:
                target_day = d
                break
                
        if not target_day:
            target_day = next((d for d in w_res['forecast']['forecastday'] if dt.datetime.strptime(d['date'], '%Y-%m-%d').weekday() == 5), None)
            
        if target_day:
            s_hour = start_dt.hour if start_dt else 9
            e_hour = min(s_hour + 2, 23) 
            
            cond = target_day['hour'][s_hour]['condition']['text']
            temp_start = int(target_day['hour'][s_hour]['temp_f'])
            temp_end = int(target_day['hour'][e_hour]['temp_f'])
            
            if start_dt:
                end_time_label = (start_dt + dt.timedelta(hours=2)).strftime('%I:%M %p').lstrip('0')
                weather_info = f"{cond} | {d_start}: {temp_start}°F → {end_time_label}: {temp_end}°F"
            else:
                weather_info = f"{cond} | Start: {temp_start}°F → End: {temp_end}°F"
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
    code = str(request.form.get('code', '')).strip()
    password = request.form.get('password')
    
    # 1. Fetch entire Master List to do safe matching in Python
    master = get_airtable_data("Master List")
    
    # 2. Robust search: strips spaces and handles rogue ".0" floats from CSV imports
    user_rec = None
    for m in master:
        m_code = str(m['fields'].get('Code', '')).strip()
        if m_code.endswith('.0'): 
            m_code = m_code[:-2] # Turn "1060.0" into "1060"
        
        if m_code == code:
            user_rec = m
            break
    
    if user_rec:
        is_admin = (password == ADMIN_PW)
        f = user_rec['fields']
        
        # --- 6-MONTH RE-VERIFICATION LOGIC ---
        last_confirmed_str = f.get('Last Confirmed')
        contact_confirmed = False
        
        if last_confirmed_str:
            try:
                last_conf_date = dt.datetime.strptime(last_confirmed_str, "%Y-%m-%d").date()
                days_since = (dt.date.today() - last_conf_date).days
                if days_since < 180:
                    contact_confirmed = True
            except: pass
        
        session['user'] = {
            'code': code, 'first': f.get('First'), 'last': f.get('Last'),
            'email': f.get('Email', ''), 'phone': f.get('Phone', ''), 'is_admin': is_admin,
            'contact_confirmed': contact_confirmed
        }
        log_activity(f.get('First'), "Logged In")
        return redirect(url_for('index'))
    else:
        # LOG FAILED ATTEMPTS TO AIRTABLE
        log_activity(f"Failed Code Attempt: '{code}'", "Login Error")
        
        # EMAIL ALERT TO ADMIN
        alert_msg = f"<p>A user just attempted to log in to the Tennis site with an invalid code: <b>{code}</b>.</p>"
        send_email(ADMIN_EMAIL, "⚠️ Failed Login Attempt", alert_msg)
        
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

    # ENFORCEMENT: Check for 6-month contact confirmation
    if not user.get('contact_confirmed'):
        flash("Action Required: Please update your contact info to unlock signups.", "danger")
        return redirect(url_for('index'))

    # ENFORCEMENT: Check for Strikes/Paused
    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{user['code']}'")
    if m_recs and m_recs[0]['fields'].get('Paused'):
        flash("🚫 Your account is paused due to strikes. Please contact Jim.", "danger")
        return redirect(url_for('index'))
    
    existing = get_airtable_data("Signups", filter_formula=f"{{Player Code}}='{user['code']}'")
    if existing:
        flash("You are already signed up!", "warning")
        return redirect(url_for('index'))

    # Ensure Player Code is a string to match Airtable structure perfectly
    payload = {"fields": {"First": user['first'], "Last": user['last'], "Player Code": str(user['code']), "Email": user['email']}}
    
    try:
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=payload).raise_for_status()
        AIRTABLE_CACHE.clear()
        log_activity(user['first'], "Signed Up")
        flash("You've been added to the list!", "success")
    except Exception as e:
        print(f"Signup failed: {e}")
        flash("Error saving signup to the database. Please try again or contact Jim.", "danger")

    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if not session.get('user'): return redirect(url_for('index'))
    now_mdt = dt.datetime.utcnow() - dt.timedelta(hours=6)
    is_past_deadline = (now_mdt.weekday() == 4 and now_mdt.hour >= 8) or (now_mdt.weekday() == 5)
    recs = get_airtable_data("Signups", sort_field="Created Time")
    
    idx = next((i for i, r in enumerate(recs) if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if idx is not None:
        playing_cutoff = (min(len(recs), 24) // 4) * 4
        is_in_complete_court = idx < playing_cutoff
        waitlist_exists = len(recs) > playing_cutoff
        
        if is_in_complete_court and is_past_deadline:
            if waitlist_exists:
                promo = recs[playing_cutoff]
                promo_code = promo['fields'].get('Player Code')
                m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{promo_code}'")
                promo_email = m_recs[0]['fields'].get('Email') if m_recs else None

                requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[idx]['id']}", headers=HEADERS, 
                               json={"fields": {"Label": "PENDING SUB", "Sub Offer": str(promo_code)}})
                if promo_email:
                    send_email(promo_email, "🎾 Sub Spot Available!", f"A spot opened up! Log in to {SITE_URL} to accept it.")
                    flash("Drop initiated. Waitlisted player emailed.", "warning")
                else:
                    flash("Drop initiated, but the waitlisted player has no email on file!", "warning")
            else:
                requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[idx]['id']}", headers=HEADERS, json={"fields": {"Label": "NEEDS SUB"}})
                flash("⚠️ NO ONE is on the waitlist. You are marked NEEDS SUB.", "danger")
            AIRTABLE_CACHE.clear()
            return redirect(url_for('index'))

        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[idx]['id']}", headers=HEADERS)
        log_activity(session['user']['first'], "Cancelled")
    
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/accept_sub', methods=['POST'])
def accept_sub():
    user = session.get('user')
    if not user: return redirect(url_for('index'))
    if not user.get('contact_confirmed'): return redirect(url_for('index'))

    recs = get_airtable_data("Signups")
    dropper = next((r for r in recs if str(r['fields'].get('Sub Offer')) == str(user['code'])), None)
    me = next((r for r in recs if str(r['fields'].get('Player Code')) == str(user['code'])), None)
    if dropper and me:
        dropper_name = dropper['fields'].get('First')
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{dropper['id']}", headers=HEADERS)
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{me['id']}", headers=HEADERS, json={"fields": {"Label": f"SUB for {dropper_name}"}})
        AIRTABLE_CACHE.clear()
        flash("You successfully accepted the sub spot!", "success")
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user = session.get('user')
    if not user: return redirect(url_for('index'))
    
    new_email, new_phone = request.form.get('email'), request.form.get('phone')
    
    if not new_email or not new_phone:
        flash("Both Email and Phone are required.", "danger")
        return redirect(url_for('index'))

    master = get_airtable_data("Master List")
    user_rec = next((m for m in master if str(m['fields'].get('Code')) == str(user['code'])), None)
    
    if user_rec:
        today_str = dt.date.today().strftime("%Y-%m-%d")
        payload = {"fields": {"Email": new_email, "Phone": new_phone, "Last Confirmed": today_str}}
        try:
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{user_rec['id']}", headers=HEADERS, json=payload).raise_for_status()
        except:
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{user_rec['id']}", headers=HEADERS, json={"fields": {"Email": new_email, "Phone": new_phone}})

        session['user']['email'] = new_email
        session['user']['phone'] = new_phone
        session['user']['contact_confirmed'] = True 
        session.modified = True
        AIRTABLE_CACHE.clear()
        flash("Profile confirmed and updated! Site unlocked.", "success")
    return redirect(url_for('index'))

@app.route('/apply', methods=['POST'])
def apply():
    payload = {"fields": {"First": request.form.get('first'), "Last": request.form.get('last'), "Email": request.form.get('email'), "Status": "Pending"}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json=payload)
    flash("Application submitted! We will email you your code once approved.", "success")
    return redirect(url_for('index'))

@app.route('/request_guest', methods=['POST'])
def request_guest():
    user = session.get('user')
    if not user: return redirect(url_for('index'))
    if not user.get('contact_confirmed'): return redirect(url_for('index'))

    payload = {"fields": {"First": request.form.get('guest_first'), "Last": request.form.get('guest_last'), "Sponsor": f"{user['first']} {user['last']}", "Status": "Pending"}}
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
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}", headers=HEADERS, json={"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}})
            flash("Session info updated!", "success")
    elif action == "reset_roster":
        return cron_monday() # Clear and notify  
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/info_blast', methods=['POST'])
def info_blast():
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    msg, group = request.form.get('message'), request.form.get('target_group')
    emails = [r['fields'].get('Email') for r in get_airtable_data("Signups" if group == "roster" else "Master List") if r['fields'].get('Email')]
    send_email(emails, "🎾 Tennis Gang Announcement", f"<p>{msg}</p>", is_multiple=True)
    flash("Announcement sent!", "success")
    return redirect(url_for('index'))

@app.route('/approve_player/<id>', methods=['POST'])
def approve_player(id):
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    res = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{id}", headers=HEADERS).json()
    f = res.get('fields', {})
    m_list = get_airtable_data("Master List")
    highest_code = max([int(str(m['fields'].get('Code')).strip()) for m in m_list if str(m['fields'].get('Code')).isdigit() and 1000 < int(str(m['fields'].get('Code')).strip()) < 9000], default=1000)
    new_code = str(highest_code + 1)
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS, json={"fields": {"First": f.get('First'), "Last": f.get('Last'), "Email": f.get('Email'), "Code": new_code}, "typecast": True})
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{id}", headers=HEADERS, json={"fields": {"Status": "Approved", "Assigned Code": new_code}, "typecast": True})
    send_email(f.get('Email'), "🎾 Welcome to the Gang!", f"<p>Approved. Login code: <b>{new_code}</b></p>")
    flash(f"Approved with Code {new_code}.", "success")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/approve_guest/<app_id>', methods=['POST'])
def approve_guest(app_id):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    res = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS).json()
    f = res.get('fields', {})
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json={"fields": {"First": f.get('First'), "Last": f.get('Last'), "Player Code": "GUEST", "Label": f"GUEST of {f.get('Sponsor')}"}})
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS, json={"fields": {"Status": "Approved"}})
    flash(f"Guest added to roster!", "success")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/attendance/<code_str>', methods=['POST'])
def attendance(code_str):
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    
    status = request.form.get('status')
    strike_inc = 1 if status == 'Late' else 2 if status == 'No Show' else 0
    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{code_str}'")
    
    if m_recs and strike_inc > 0:
        new_strikes = m_recs[0]['fields'].get('Strikes', 0) + strike_inc
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{m_recs[0]['id']}", headers=HEADERS, json={"fields": {"Strikes": new_strikes, "Paused": (new_strikes >= 3)}})

    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json={"fields": {"Player Code": str(code_str), "Attendance": status, "Date": dt.datetime.now().strftime("%Y-%m-%d")}})
    flash(f"Updated attendance for {code_str}", "info")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

# === SECTION 7: CRON / AUTOMATION ===
@app.route('/cron/monday')
def cron_monday():
    settings = get_airtable_data("Settings")
    d_date = settings[0]['fields'].get('Target Date', 'TBD') if settings else 'TBD'
    d_start = settings[0]['fields'].get('Start Time', 'TBD') if settings else 'TBD'
    
    signups = get_airtable_data("Signups")
    for r in signups:
        archive_payload = {"fields": {"First": r['fields'].get('First'), "Last": r['fields'].get('Last'), "Player Code": str(r['fields'].get('Player Code','')), "Date": d_date, "Attendance": r['fields'].get('Label', 'Signed Up')}}
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json=archive_payload)
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
    
    emails = [m['fields'].get('Email') for m in get_airtable_data("Master List") if m['fields'].get('Email')]
    send_email(emails, f"🎾 Signups OPEN for {d_date}!", f"<h3>Signups are open!</h3><p>Time: {d_start}</p><p><a href='{SITE_URL}'>Claim your spot!</a></p>", is_multiple=True)
    
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
