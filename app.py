# ==========================================
# TABLE OF CONTENTS - app.py
# 1. SETUP & CONFIG (Env Vars, Headers)
# 2. UTILITY FUNCTIONS (Email, Logging)
# 3. DATA CACHING ENGINE (With Pagination & Rate Limiting)
# 4. PRIMARY ROUTES (Index, Login/Logout, Bot Shield)
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
# Hardcoding Base ID to prevent the 'None' URL errors seen in Render logs
BASE_ID = "appEC9INt2PRYewNj" 

ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
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
                
            # SPEED BUMP: Respect Airtable 5 req/sec limit
            time.sleep(0.25)
            
            res = requests.get(url, headers=HEADERS, params=params)
            
            # Handle 429 specifically
            if res.status_code == 429:
                time.sleep(31)
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
    # BOT SHIELD: Instantly satisfy Render pings without using Airtable credits
    ua = request.headers.get('User-Agent', '')
    if request.method == 'HEAD' or 'Go-http-client' in ua:
        return "OK", 200

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
    else:
        if curr_user:
            for i, p in enumerate(roster):
                if str(p.get('Player Code')) == str(curr_user.get('code')):
                    user_on_roster = True
                    if i >= playing_cutoff: waitlist_pos = i - playing_cutoff + 1
    
    # Weather Logic
    weather_info = "Weather Unavailable"
    try:
        lat, lon = "39.9936", "-105.0897"
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,weathercode&temperature_unit=fahrenheit&timezone=America%2FDenver&forecast_days=14"
        w_res = requests.get(weather_url).json()
        today = dt.date.today()
        days_ahead = 5 - today.weekday()
        if days_ahead < 0: days_ahead += 7
        target_date_iso = (today + dt.timedelta(days=days_ahead)).isoformat()
        s_hour = start_dt.hour if start_dt else 9
        start_time_str = f"{target_date_iso}T{s_hour:02d}:00"
        times = w_res.get('hourly', {}).get('time', [])
        if start_time_str in times:
            s_idx = times.index(start_time_str)
            temp_start = int(w_res['hourly']['temperature_2m'][s_idx])
            cond = {0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast"}.get(w_res['hourly']['weathercode'][s_idx], "Varied")
            weather_info = f"{cond} | {d_start}: {temp_start}°F"
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
            'email': f.get('Email', ''), 'phone': f.get('Phone', ''), 'is_admin': (password == ADMIN_PW),
            'contact_confirmed': contact_confirmed, 'level': f.get('Level', '')
        }
        log_activity(f.get('First'), "Logged In")
        return redirect(url_for('index'))
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
    if not user.get('contact_confirmed') or not user.get('level'):
        flash("Action Required: Please review your profile info to unlock signups.", "danger")
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
        flash("Error saving signup.", "danger")
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if not session.get('user'): return redirect(url_for('index'))
    recs = get_airtable_data("Signups")
    my_rec = next((r for r in recs if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if my_rec:
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{my_rec['id']}", headers=HEADERS)
        AIRTABLE_CACHE.clear()
        log_activity(session['user']['first'], "Cancelled")
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user = session.get('user')
    if not user: return redirect(url_for('index'))
    new_email, new_phone, new_level = request.form.get('email'), request.form.get('phone'), request.form.get('level')
    master = get_airtable_data("Master List")
    user_rec = next((m for m in master if str(m['fields'].get('Code')) == str(user['code'])), None)
    if user_rec:
        today_str = dt.date.today().strftime("%Y-%m-%d")
        payload = {"fields": {"Email": new_email, "Phone": new_phone, "Last Confirmed": today_str, "Level": new_level}}
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{user_rec['id']}", headers=HEADERS, json=payload)
        session['user'].update({'email': new_email, 'phone': new_phone, 'contact_confirmed': True, 'level': new_level})
        AIRTABLE_CACHE.clear()
        flash("Profile updated!", "success")
    return redirect(url_for('index'))

@app.route('/apply', methods=['POST'])
def apply():
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json={"fields": {"First": request.form.get('first'), "Last": request.form.get('last'), "Email": request.form.get('email'), "Status": "Pending"}})
    flash("Application submitted!", "success")
    return redirect(url_for('index'))

@app.route('/request_guest', methods=['POST'])
def request_guest():
    user = session.get('user')
    if not user: return redirect(url_for('index'))
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json={"fields": {"First": request.form.get('guest_first'), "Last": request.form.get('guest_last'), "Sponsor": f"{user['first']} {user['last']}", "Status": "Pending", "Level": user.get('level', '')}})
    flash("Guest request submitted!", "info")
    return redirect(url_for('index'))

# === SECTION 6: ADMIN ACTIONS ===
@app.route('/admin_action', methods=['POST'])
def admin_action():
    if not session.get('user', {}).get('is_admin'): return "Unauthorized", 403
    action = request.form.get('action')
    settings = get_airtable_data("Settings")
    if action == "labels" and settings:
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}", headers=HEADERS, json={"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}})
    elif action == "toggle_mode" and settings:
        new_mode = 'Split' if settings[0]['fields'].get('Play Mode', 'Open') == 'Open' else 'Open'
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}", headers=HEADERS, json={"fields": {"Play Mode": new_mode}})
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/attendance/<code_str>', methods=['POST'])
def attendance(code_str):
    if not session.get('user', {}).get('is_admin'): return "Unauthorized", 403
    status = request.form.get('status')
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json={"fields": {"Player Code": str(code_str), "Attendance": status, "Date": dt.datetime.now().strftime("%Y-%m-%d")}, "typecast": True})
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

# === SECTION 7: CRON ROUTES ===
@app.route('/cron/monday')
def cron_monday():
    signups = get_airtable_data("Signups")
    for r in signups:
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json={"fields": {"First": r['fields'].get('First'), "Last": r['fields'].get('Last'), "Player Code": str(r['fields'].get('Player Code')), "Attendance": "Signed Up"}})
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
        time.sleep(0.2)
    AIRTABLE_CACHE.clear()
    return "Monday reset complete", 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
