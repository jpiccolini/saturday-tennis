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
W_KEY = os.environ.get("WEATHER_API_KEY") # No longer strictly needed for Open-Meteo, kept for legacy
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
    play_mode = "Open"
    start_dt = None
    if settings:
        f = settings[0]['fields']
        d_date, d_start = f.get('Target Date', 'TBD'), f.get('Start Time', 'TBD')
        play_mode = f.get('Play Mode', 'Open')
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

    lower_roster, upper_roster = [], []
    lower_cutoff, upper_cutoff = 12, 12
    total_signups = len(roster)
    playing_cutoff = (min(total_signups, 24) // 4) * 4
    waitlist_count = total_signups - playing_cutoff

    user_on_roster, waitlist_pos, pending_sub_offer = False, 0, False
    curr_user = session.get('user')

    if play_mode == 'Split':
        lower_roster = [p for p in roster if p.get('Level') == '3.0/3.5']
        upper_roster = [p for p in roster if p.get('Level') == '4.0/4.5']
        lower_cutoff = (min(len(lower_roster), 12) // 4) * 4
        upper_cutoff = (min(len(upper_roster), 12) // 4) * 4

        if curr_user:
            for i, p in enumerate(lower_roster):
                if str(p.get('Player Code')) == str(curr_user.get('code')):
                    user_on_roster = True
                    if i >= lower_cutoff: waitlist_pos = i - lower_cutoff + 1
            for i, p in enumerate(upper_roster):
                if str(p.get('Player Code')) == str(curr_user.get('code')):
                    user_on_roster = True
                    if i >= upper_cutoff: waitlist_pos = i - upper_cutoff + 1
            for p in roster:
                if str(p.get('Sub Offer')) == str(curr_user.get('code')):
                    pending_sub_offer = True
    else:
        if curr_user:
            for i, p in enumerate(roster):
                if str(p.get('Player Code')) == str(curr_user.get('code')):
                    user_on_roster = True
                    if i >= playing_cutoff: waitlist_pos = i - playing_cutoff + 1
                if str(p.get('Sub Offer')) == str(curr_user.get('code')):
                    pending_sub_offer = True

    weather_info = "Weather Unavailable"
    try:
        # Open-Meteo - 14-day forecast, Free, No API Key required
        lat, lon = "39.9936", "-105.0897" # Lafayette, CO Coordinates
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,weathercode&temperature_unit=fahrenheit&timezone=America%2FDenver&forecast_days=14"
        w_res = requests.get(weather_url).json()

        # Calculate the upcoming Saturday based on today
        today = dt.date.today()
        days_ahead = 5 - today.weekday()
        if days_ahead < 0: days_ahead += 7
        target_date = today + dt.timedelta(days=days_ahead)
        target_date_iso = target_date.isoformat() 
            
        s_hour = start_dt.hour if start_dt else 9
        end_dt = start_dt + timedelta(hours=2, minutes=15) if start_dt else None
        e_hour = min(end_dt.hour, 23) if end_dt else 11
        
        start_time_str = f"{target_date_iso}T{s_hour:02d}:00"
        end_time_str = f"{target_date_iso}T{e_hour:02d}:00"

        times = w_res.get('hourly', {}).get('time', [])
        temps = w_res.get('hourly', {}).get('temperature_2m', [])
        codes = w_res.get('hourly', {}).get('weathercode', [])

        if start_time_str in times:
            s_idx = times.index(start_time_str)
            e_idx = times.index(end_time_str) if end_time_str in times else s_idx + 2
            
            temp_start = int(temps[s_idx])
            temp_end = int(temps[e_idx])
            w_code = codes[s_idx]
            
            # WMO Weather code mapping
            code_map = {
                0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
                45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
                61: "Rain", 63: "Rain", 65: "Heavy Rain", 71: "Snow", 73: "Snow",
                75: "Heavy Snow", 95: "Thunderstorm"
            }
            cond = code_map.get(w_code, "Varied")
            
            end_label = end_dt.strftime('%I:%M %p').lstrip('0') if end_dt else "End"
            weather_info = f"{cond} | {d_start}: {temp_start}°F → {end_label}: {temp_end}°F"
        else:
            weather_info = "Saturday forecast available soon"

    except Exception as e:
        print(f"Weather Logic Error: {e}")

    applicants, guest_requests = [], []
    if curr_user and curr_user.get('is_admin'):
        all_apps = get_airtable_data("Applicants")
        applicants = [a for a in all_apps if a['fields'].get('Status') == 'Pending' and not a['fields'].get('Sponsor')]
        guest_requests = [a for a in all_apps if a['fields'].get('Status') == 'Pending' and a['fields'].get('Sponsor')]

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end, roster=roster, 
                           applicants=applicants, guest_requests=guest_requests, master_list=master_recs,
                           user_on_roster=user_on_roster, waitlist_pos=waitlist_pos, weather=weather_info,
                           playing_cutoff=playing_cutoff, total_signups=total_signups, waitlist_count=waitlist_count, 
                           pending_sub_offer=pending_sub_offer, play_mode=play_mode, lower_roster=lower_roster,
                           upper_roster=upper_roster, lower_cutoff=lower_cutoff, upper_cutoff=upper_cutoff)

@app.route('/validate', methods=['POST'])
def validate():
    code = str(request.form.get('code', '')).strip()
    password = request.form.get('password')
    master = get_airtable_data("Master List")
    user_rec = None
    for m in master:
        m_code = str(m['fields'].get('Code', '')).strip()
        if m_code.endswith('.0'): m_code = m_code[:-2]
        if m_code == code:
            user_rec = m
            break
    
    if user_rec:
        is_admin = (password == ADMIN_PW)
        f = user_rec['fields']
        last_confirmed_str = f.get('Last Confirmed')
        contact_confirmed = False
        if last_confirmed_str:
            try:
                last_conf_date = dt.datetime.strptime(last_confirmed_str, "%Y-%m-%d").date()
                if (dt.date.today() - last_conf_date).days < 180: contact_confirmed = True
            except: pass
        session['user'] = {
            'code': code, 'first': f.get('First'), 'last': f.get('Last'),
            'email': f.get('Email', ''), 'phone': f.get('Phone', ''), 'is_admin': is_admin,
            'contact_confirmed': contact_confirmed, 'level': f.get('Level', '')
        }
        log_activity(f.get('First'), "Logged In")
        return redirect(url_for('index'))
    else:
        log_activity(f"Failed Code Attempt: '{code}'", "Login Error")
        send_email(ADMIN_EMAIL, "⚠️ Failed Login Attempt", f"<p>A user just attempted to log in with an invalid code: <b>{code}</b>.</p>")
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

    if not user.get('contact_confirmed') or not user.get('level'):
        flash("Action Required: Please review your profile info to unlock signups.", "danger")
        return redirect(url_for('index'))

    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{user['code']}'")
    if m_recs and m_recs[0]['fields'].get('Paused'):
        flash("🚫 Your account is paused due to strikes. Please contact Jim.", "danger")
        return redirect(url_for('index'))
    
    existing = get_airtable_data("Signups", filter_formula=f"{{Player Code}}='{user['code']}'")
    if existing:
        flash("You are already signed up!", "warning")
        return redirect(url_for('index'))

    payload = {"fields": {"First": user['first'], "Last": user['last'], "Player Code": str(user['code']), "Email": user['email'], "Level": user['level']}}
    try:
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=payload).raise_for_status()
        AIRTABLE_CACHE.clear()
        log_activity(user['first'], "Signed Up")
        flash("You've been added to the list!", "success")
    except:
        flash("Error saving signup to the database. Please try again or contact Jim.", "danger")
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if not session.get('user'): return redirect(url_for('index'))
    now_mdt = dt.datetime.utcnow() - dt.timedelta(hours=6)
    is_past_deadline = (now_mdt.weekday() == 4 and now_mdt.hour >= 8) or (now_mdt.weekday() == 5)
    
    settings = get_airtable_data("Settings")
    play_mode = settings[0]['fields'].get('Play Mode', 'Open') if settings else 'Open'
    recs = get_airtable_data("Signups", sort_field="Created Time")
    
    my_rec = next((r for r in recs if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if my_rec:
        target_list = recs
        playing_cutoff = (min(len(target_list), 24) // 4) * 4
        
        if play_mode == 'Split':
            my_level = my_rec['fields'].get('Level')
            target_list = [r for r in recs if r['fields'].get('Level') == my_level]
            playing_cutoff = (min(len(target_list), 12) // 4) * 4

        try: idx = target_list.index(my_rec)
        except: idx = -1
            
        if idx != -1:
            is_in_complete_court = idx < playing_cutoff
            waitlist_exists = len(target_list) > playing_cutoff
            
            if is_in_complete_court and is_past_deadline:
                if waitlist_exists:
                    promo = target_list[playing_cutoff]
                    promo_code = promo['fields'].get('Player Code')
                    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{promo_code}'")
                    promo_email = m_recs[0]['fields'].get('Email') if m_recs else None

                    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{my_rec['id']}", headers=HEADERS, json={"fields": {"Label": "PENDING SUB", "Sub Offer": str(promo_code)}})
                    if promo_email:
                        send_email(promo_email, "🎾 Sub Spot Available!", f"A spot opened up! Log in to {SITE_URL} to accept it.")
                        flash("Drop initiated. Waitlisted player emailed.", "warning")
                    else:
                        flash("Drop initiated, but the waitlisted player has no email on file!", "warning")
                else:
                    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{my_rec['id']}", headers=HEADERS, json={"fields": {"Label": "NEEDS SUB"}})
                    flash("⚠️ NO ONE is on the waitlist for your level. You are marked NEEDS SUB.", "danger")
                AIRTABLE_CACHE.clear()
                return redirect(url_for('index'))

        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{my_rec['id']}", headers=HEADERS)
        log_activity(session['user']['first'], "Cancelled")
    
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/accept_sub', methods=['POST'])
def accept_sub():
    user = session.get('user')
    if not user or not user.get('contact_confirmed') or not user.get('level'): return redirect(url_for('index'))

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
    
    new_email, new_phone, new_level = request.form.get('email'), request.form.get('phone'), request.form.get('level')
    if not new_email or not new_phone:
        flash("Both Email and Phone are required.", "danger"); return redirect(url_for('index'))
    if not user.get('level') and not new_level:
        flash("Play Level is required.", "danger"); return redirect(url_for('index'))

    master = get_airtable_data("Master List")
    user_rec = next((m for m in master if str(m['fields'].get('Code')) == str(user['code'])), None)
    if user_rec:
        today_str = dt.date.today().strftime("%Y-%m-%d")
        payload = {"fields": {"Email": new_email, "Phone": new_phone, "Last Confirmed": today_str}}
        if not user.get('level') and new_level: payload["fields"]["Level"] = new_level
        try: requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{user_rec['id']}", headers=HEADERS, json=payload)
        except: pass 
        session['user'].update({'email': new_email, 'phone': new_phone, 'contact_confirmed': True, 'level': new_level or user.get('level')})
        session.modified = True
        AIRTABLE_CACHE.clear()
        flash("Profile updated! Site unlocked.", "success")
    return redirect(url_for('index'))

@app.route('/apply', methods=['POST'])
def apply():
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json={"fields": {"First": request.form.get('first'), "Last": request.form.get('last'), "Email": request.form.get('email'), "Status": "Pending"}})
    flash("Application submitted! We will email you your code once approved.", "success")
    return redirect(url_for('index'))

@app.route('/request_guest', methods=['POST'])
def request_guest():
    user = session.get('user')
    if not user or not user.get('contact_confirmed'): return redirect(url_for('index'))
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json={"fields": {"First": request.form.get('guest_first'), "Last": request.form.get('guest_last'), "Sponsor": f"{user['first']} {user['last']}", "Status": "Pending", "Level": user.get('level', '')}})
    flash("Guest request submitted to Admin. They will be placed in your play level.", "info")
    return redirect(url_for('index'))

# === SECTION 6: ADMIN ACTIONS ===
@app.route('/admin_action', methods=['POST'])
def admin_action():
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    action = request.form.get('action')
    settings = get_airtable_data("Settings")
    if action == "labels" and settings:
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}", headers=HEADERS, json={"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}})
        flash("Session info updated!", "success")
    elif action == "toggle_mode" and settings:
        new_mode = 'Split' if settings[0]['fields'].get('Play Mode', 'Open') == 'Open' else 'Open'
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}", headers=HEADERS, json={"fields": {"Play Mode": new_mode}})
        flash(f"Mode switched to {new_mode}!", "success")
    elif action == "reset_roster":
        return cron_monday() 
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/move_player/<signup_id>', methods=['POST'])
def move_player(signup_id):
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    new_level = '4.0/4.5' if request.form.get('current_level') == '3.0/3.5' else '3.0/3.5'
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{signup_id}", headers=HEADERS, json={"fields": {"Level": new_level}})
    AIRTABLE_CACHE.clear()
    flash("Player moved successfully to balance courts!", "success")
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
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json={"fields": {"First": f.get('First'), "Last": f.get('Last'), "Player Code": "GUEST", "Label": f"GUEST of {f.get('Sponsor')}", "Level": f.get('Level')}})
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
    
    player_level = ""
    if m_recs:
        player_level = m_recs[0]['fields'].get('Level', '')
        if strike_inc > 0:
            new_strikes = m_recs[0]['fields'].get('Strikes', 0) + strike_inc
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{m_recs[0]['id']}", headers=HEADERS, json={"fields": {"Strikes": new_strikes, "Paused": (new_strikes >= 3)}})
            
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json={"fields": {"Player Code": str(code_str), "Attendance": status, "Date": dt.datetime.now().strftime("%Y-%m-%d"), "Level": player_level}, "typecast": True})
    flash(f"Updated attendance for {code_str}", "info")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

# === SECTION 7: CRON / AUTOMATION ===

# Helper for friendly numbers (1st, 2nd, 3rd)
def get_ordinal(n):
    if 11 <= (n % 100) <= 13: return str(n) + 'th'
    return str(n) + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')

@app.route('/cron/monday')
def cron_monday():
    settings = get_airtable_data("Settings")
    d_date = settings[0]['fields'].get('Target Date', 'TBD') if settings else 'TBD'
    d_start = settings[0]['fields'].get('Start Time', 'TBD') if settings else 'TBD'
    play_mode = settings[0]['fields'].get('Play Mode', 'Open') if settings else 'Open'
    
    mode_explanation = "This week we are in <b>Open</b> mode, all members in one list."
    if play_mode == 'Split':
        mode_explanation = "This week we are in <b>Split</b> mode, with 3 courts reserved for each group. <br><i>(I may shift numbers on Friday to a 4 court/2 court arrangement if numbers support it.)</i>"

    signups = get_airtable_data("Signups", sort_field="Created Time")
    
    # 1. Calculate the number of each rating that actually PLAYED (ignores waitlist)
    played_levels = {}
    if play_mode == 'Split':
        lower = [s for s in signups if s['fields'].get('Level') == '3.0/3.5']
        upper = [s for s in signups if s['fields'].get('Level') == '4.0/4.5']
        l_cutoff = (min(len(lower), 12) // 4) * 4
        u_cutoff = (min(len(upper), 12) // 4) * 4
        playing_recs = lower[:l_cutoff] + upper[:u_cutoff]
    else:
        playing_cutoff = (min(len(signups), 24) // 4) * 4
        playing_recs = signups[:playing_cutoff]

    for r in playing_recs:
        lvl = r['fields'].get('Level', 'Unrated')
        played_levels[lvl] = played_levels.get(lvl, 0) + 1

    # 2. Log stats and Email Admin
    stats_msg = " | ".join([f"{k}: {v} players" for k, v in played_levels.items()])
    if stats_msg:
        log_activity("Weekly Play Stats", f"Played on {d_date} -> {stats_msg}")
        send_email(ADMIN_EMAIL, f"📊 Weekly Stats for {d_date}", f"<p>Here is the breakdown of ratings that made the cutoff and played this past Saturday:</p><h3>{stats_msg}</h3>")

    # 3. Archive everyone (with their Level) and clear signups
    for r in signups:
        try:
            requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json={"fields": {"First": r['fields'].get('First'), "Last": r['fields'].get('Last'), "Player Code": str(r['fields'].get('Player Code','')), "Date": d_date, "Attendance": r['fields'].get('Label', 'Signed Up'), "Level": r['fields'].get('Level', '')}, "typecast": True})
        except: pass
        
        try:
            requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
        except: pass
            
    # 4. Open signups for the new week
    try:
        emails = [m['fields'].get('Email') for m in get_airtable_data("Master List") if m['fields'].get('Email')]
        send_email(emails, f"🎾 Signups OPEN for {d_date}!", f"<h3>Signups are open!</h3><p><b>Time:</b> {d_start}</p><p>{mode_explanation}</p><p><a href='{SITE_URL}'>Claim your spot!</a></p>", is_multiple=True)
    except: pass
        
    AIRTABLE_CACHE.clear()
    return "Monday reset, stats calculated, and emails sent successfully.", 200

@app.route('/cron/friday')
def cron_friday():
    settings = get_airtable_data("Settings")
    d_date = settings[0]['fields'].get('Target Date', 'TBD') if settings else 'TBD'
    d_start = settings[0]['fields'].get('Start Time', 'TBD') if settings else 'TBD'
    play_mode = settings[0]['fields'].get('Play Mode', 'Open') if settings else 'Open'
    
    signups = get_airtable_data("Signups", sort_field="Created Time")
    master_list = get_airtable_data("Master List")
    all_emails = [m['fields'].get('Email') for m in master_list if m['fields'].get('Email')]
    
    if play_mode == 'Open':
        total = len(signups)
        playing_cutoff = (min(total, 24) // 4) * 4
        playing_recs = signups[:playing_cutoff]
        waitlist_recs = signups[playing_cutoff:]
        
        C = total // 4
        W = total - playing_cutoff 
        needed = 4 - (total % 4) if total % 4 != 0 else 4
        
        big_picture = f"We have {W} on the waitlist, so if {needed} more join us, we will add a {get_ordinal(C + 1)} court!"
        
        # 1. Email Playing
        playing_emails = [r['fields'].get('Email') for r in playing_recs if r['fields'].get('Email')]
        if playing_emails:
            send_email(playing_emails, f"🎾 Roster Locked for {d_date}", f"<h3>You are on the board for tomorrow!</h3><p>Start Time: {d_start}</p><p>Check the live roster here: <a href='{SITE_URL}'>{SITE_URL}</a></p><p><i>Note: If you must drop, the late-cancel rules are now in effect.</i></p>", is_multiple=True)
            
        # 2. Email Waitlist Individually
        for idx, r in enumerate(waitlist_recs):
            em = r['fields'].get('Email')
            if em:
                send_email([em], f"🎾 Waitlist Status for {d_date}", f"<h3>You are on the waitlist!</h3><p>Just a heads up, the roster is locked and you are currently <b>{get_ordinal(idx+1)} of {len(waitlist_recs)}</b> on the Open waitlist.</p><p>Keep an eye out for sub requests! {C} courts are currently reserved.</p>")
                
        # 3. Email Big Picture Blast
        send_email(all_emails, "🎾 Friday Update: Player slot roundup for this week!", f"<h3>Friday Court Status</h3><p>Here is the big picture for this weekend: <b>{big_picture}</b></p><p>If you can play, jump in and help us fill the next court: <a href='{SITE_URL}'>{SITE_URL}</a></p>", is_multiple=True)

    else: 
        # SPLIT MODE LOGIC
        lower = [s for s in signups if s['fields'].get('Level') == '3.0/3.5']
        upper = [s for s in signups if s['fields'].get('Level') == '4.0/4.5']
        
        l_cutoff = (min(len(lower), 12) // 4) * 4
        u_cutoff = (min(len(upper), 12) // 4) * 4
        
        l_play, l_wait = lower[:l_cutoff], lower[l_cutoff:]
        u_play, u_wait = upper[:u_cutoff], upper[u_cutoff:]
        
        l_C, u_C = len(lower) // 4, len(upper) // 4
        l_needed = 4 - (len(lower) % 4) if len(lower) % 4 != 0 else 4
        u_needed = 4 - (len(upper) % 4) if len(upper) % 4 != 0 else 4
        
        l_status = f"we are full on 3.0/3.5 with {len(l_wait)} on the waitlist, so if {l_needed} more join, we will add a {get_ordinal(l_C + 1)} court for 3.0/3.5."
        if len(l_wait) == 0 and len(lower) < 12:
            l_status = f"we have {len(lower)} players for 3.0/3.5. If {l_needed} more join, we will add a {get_ordinal(l_C + 1)} court."
            
        u_status = f"we have {len(u_wait)} on the 4.0/4.5 waitlist, so if {u_needed} more join, we will add a {get_ordinal(u_C + 1)} court."
        if len(u_wait) == 0 and len(upper) < 12:
            u_status = f"we have {len(upper)} players for 4.0/4.5. If {u_needed} more join, we will add a {get_ordinal(u_C + 1)} court."
        
        big_picture = f"This week we are in Split mode. For the big picture: {l_status} And {u_status} <i>(We may shift to a 4 court / 2 court arrangement if the numbers support it!)</i>"

        # 1. Email Playing
        playing_emails = [r['fields'].get('Email') for r in (l_play + u_play) if r['fields'].get('Email')]
        if playing_emails:
            send_email(playing_emails, f"🎾 Roster Locked for {d_date}", f"<h3>You are on the board for tomorrow!</h3><p>Start Time: {d_start}</p><p>Check the live roster here: <a href='{SITE_URL}'>{SITE_URL}</a></p><p><i>Note: If you must drop, the late-cancel rules are now in effect.</i></p>", is_multiple=True)
            
        # 2. Email Waitlists Individually
        for idx, r in enumerate(l_wait):
            em = r['fields'].get('Email')
            if em: send_email([em], f"🎾 Waitlist Status for {d_date}", f"<h3>You are on the waitlist!</h3><p>Just a heads up, the roster is locked and you are currently <b>{get_ordinal(idx+1)} of {len(l_wait)}</b> on the 3.0/3.5 waitlist.</p><p>Keep an eye out for sub requests! {l_cutoff//4} courts are currently reserved for your level.</p>")
        for idx, r in enumerate(u_wait):
            em = r['fields'].get('Email')
            if em: send_email([em], f"🎾 Waitlist Status for {d_date}", f"<h3>You are on the waitlist!</h3><p>Just a heads up, the roster is locked and you are currently <b>{get_ordinal(idx+1)} of {len(u_wait)}</b> on the 4.0/4.5 waitlist.</p><p>Keep an eye out for sub requests! {u_cutoff//4} courts are currently reserved for your level.</p>")

        # 3. Email Big Picture Blast
        send_email(all_emails, "🎾 Friday Update: Player slot roundup for this week!", f"<h3>Friday Court Status</h3><p>Here is the big picture for this weekend:</p><p><b>{big_picture}</b></p><p>If you can play, jump in and help us fill out the next court: <a href='{SITE_URL}'>{SITE_URL}</a></p>", is_multiple=True)
        
    return "Friday reminder emails sent successfully.", 200

if __name__ == '__main__':
    app.run(debug=True)
