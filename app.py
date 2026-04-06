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
        if sort_field:
            url += f"sort[0][field]={sort_field}&sort[0][direction]={direction}&"
        if filter_formula:
            url += f"filterByFormula={filter_formula}"
            
        res = requests.get(url, headers=HEADERS)
        data = res.json().get('records', [])
        AIRTABLE_CACHE[cache_key] = (current_time, data)
        return data
    except Exception as e:
        print(f"Error fetching {table_name}: {e}")
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
        except: pass

    master_recs = get_airtable_data("Master List", sort_field="First")
    strike_map = {str(m['fields'].get('Code')): m['fields'].get('Strikes', 0) for m in master_recs}

    signup_recs = get_airtable_data("Signups", sort_field="Created Time")
    normal_signups, penalized_signups = [], []
    for r in signup_recs:
        code = str(r['fields'].get('Player Code'))
        if strike_map.get(code, 0) >= 2: penalized_signups.append(r)
        else: normal_signups.append(r)
    
    ordered_recs = normal_signups + penalized_signups
    roster = []
    for r in ordered_recs:
        f = r['fields']; f['id'] = r['id']
        f['strikes'] = strike_map.get(str(f.get('Player Code')), 0)
        roster.append(f)

    total_signups = len(roster)
    playing_cutoff = (min(total_signups, 24) // 4) * 4
    waitlist_count = total_signups - playing_cutoff

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

    now_mdt = dt.datetime.utcnow() - dt.timedelta(hours=6)
    is_past_deadline = (now_mdt.weekday() == 4 and now_mdt.hour >= 8) or (now_mdt.weekday() == 5)
    is_saturday = (now_mdt.weekday() == 5)

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
        if target_day and start_dt: 
            s_hour = start_dt.hour
            e_hour = min(s_hour + 2, 23)
            cond = target_day['hour'][s_hour]['condition']['text']
            temp_start = int(target_day['hour'][s_hour]['temp_f'])
            temp_end = int(target_day['hour'][e_hour]['temp_f'])
            end_time_label = (start_dt + dt.timedelta(hours=2)).strftime('%I:%M %p').lstrip('0')
            weather_info = f"{cond} | {d_start}: {temp_start}°F → {end_time_label}: {temp_end}°F"
    except: pass

    applicants, guest_requests, recent_logs = [], [], []
    if curr_user and curr_user.get('is_admin'):
        all_apps = get_airtable_data("Applicants")
        applicants = [a for a in all_apps if a['fields'].get('Status') == 'Pending' and not a['fields'].get('Sponsor')]
        guest_requests = [a for a in all_apps if a['fields'].get('Status') == 'Pending' and a['fields'].get('Sponsor')]
        recent_logs = get_airtable_data("Logs", sort_field="Timestamp", direction="desc")[:10]

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end, roster=roster, 
                           applicants=applicants, guest_requests=guest_requests, master_list=master_recs, logs=recent_logs,
                           user_on_roster=user_on_roster, user_status=user_status, waitlist_pos=waitlist_pos, weather=weather_info,
                           playing_cutoff=playing_cutoff, total_signups=total_signups, waitlist_count=waitlist_count, 
                           pending_sub_offer=pending_sub_offer, is_past_deadline=is_past_deadline, is_saturday=is_saturday)

@app.route('/validate', methods=['POST'])
def validate():
    code = str(request.form.get('code', '')).strip()
    password = request.form.get('password')
    recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{code}'")
    if recs:
        f = recs[0]['fields']
        session['user'] = {
            'id': recs[0]['id'], 'first': f.get('First'), 'last': f.get('Last'), 
            'code': code, 'is_admin': (password == ADMIN_PW),
            'email': f.get('Email', ''), 'phone': f.get('Phone', '')
        }
        log_activity(f"{f.get('First')} {f.get('Last')}", "Login")
        return redirect(url_for('index'))
    flash("Invalid Code", "danger"); return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# === SECTION 5: PLAYER ACTIONS ===
@app.route('/signup', methods=['POST'])
def signup():
    if not session.get('user'): return redirect(url_for('index'))
    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{session['user']['code']}'")
    if m_recs and m_recs[0]['fields'].get('Paused'):
        flash("🚫 Your account is paused due to strikes. Please contact Jim.", "danger")
        return redirect(url_for('index'))
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, 
                  json={"fields": {"First": session['user']['first'], "Last": session['user']['last'], "Player Code": str(session['user']['code'])}})
    log_activity(f"{session['user']['first']} {session['user']['last']}", "Signed Up")
    AIRTABLE_CACHE.clear()
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
                    flash("Drop initiated. The first waitlisted player has been emailed.", "warning")
                else:
                    flash("Drop initiated, but the waitlisted player has no email on file!", "warning")
            else:
                requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[idx]['id']}", headers=HEADERS, 
                               json={"fields": {"Label": "NEEDS SUB"}})
                flash("⚠️ NO ONE is on the waitlist. You are marked NEEDS SUB.", "danger")
            AIRTABLE_CACHE.clear()
            return redirect(url_for('index'))
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[idx]['id']}", headers=HEADERS)
        log_activity(f"{session['user']['first']} {session['user']['last']}", "Cancelled")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if not session.get('user'): return redirect(url_for('index'))
    new_email, new_phone = request.form.get('email'), request.form.get('phone')
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{session['user'].get('id')}", headers=HEADERS, json={"fields": {"Email": new_email, "Phone": new_phone}, "typecast": True})
    session['user']['email'], session['user']['phone'] = new_email, new_phone
    session.modified = True
    flash("Contact info updated successfully!", "success")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/accept_sub', methods=['POST'])
def accept_sub():
    if not session.get('user'): return redirect(url_for('index'))
    recs = get_airtable_data("Signups")
    dropper = next((r for r in recs if str(r['fields'].get('Sub Offer')) == str(session['user']['code'])), None)
    me = next((r for r in recs if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if dropper and me:
        dropper_name = dropper['fields'].get('First')
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{dropper['id']}", headers=HEADERS)
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{me['id']}", headers=HEADERS, json={"fields": {"Label": f"SUB for {dropper_name}"}})
        flash("You successfully accepted the sub spot!", "success")
        AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/provide_sub', methods=['POST'])
def provide_sub():
    if not session.get('user'): return redirect(url_for('index'))
    sub_first, sub_last, sub_email = request.form.get('sub_first'), request.form.get('sub_last'), request.form.get('sub_email')
    recs = get_airtable_data("Signups")
    me = next((r for r in recs if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if me:
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{me['id']}", headers=HEADERS, json={"fields": {"First": sub_first, "Last": sub_last, "Label": f"SUB for {session['user']['first']}"}})
        send_email([sub_email, session['user']['email'], ADMIN_EMAIL], "🎾 Tennis Sub Confirmed", f"<p>You are confirmed to sub for {session['user']['first']}!</p>")
        flash("Sub confirmed and added to the roster.", "success")
        AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/emergency_sub', methods=['POST'])
def emergency_sub():
    if not session.get('user'): return redirect(url_for('index'))
    master_list = get_airtable_data("Master List")
    emails = [m['fields'].get('Email') for m in master_list if m['fields'].get('Email')]
    send_email(emails, "🚨 URGENT: Tennis Sub Needed!", f"<p><b>{session['user']['first']} {session['user']['last']}</b> needs an emergency sub. <a href='{SITE_URL}'>Claim it!</a></p>", is_multiple=True)
    flash("Emergency sub broadcast sent!", "success")
    return redirect(url_for('index'))

@app.route('/apply', methods=['POST'])
def apply():
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json={"fields": {"First": request.form.get('first'), "Last": request.form.get('last'), "Email": request.form.get('email'), "Phone": request.form.get('phone'), "Notes": request.form.get('note'), "Status": "Pending"}})
    flash("Application Submitted!", "success")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/request_guest', methods=['POST'])
def request_guest():
    if not session.get('user'): return redirect(url_for('index'))
    data = {"fields": {"First": request.form.get('guest_first'), "Last": request.form.get('guest_last'), "Email": request.form.get('guest_email'), "Phone": request.form.get('guest_phone'), "Sponsor": f"{session['user']['first']} {session['user']['last']}", "Status": "Pending"}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json=data)
    send_email(ADMIN_EMAIL, "🎾 New Guest Request", "You have a new guest request. Log in to approve.")
    flash("Guest request sent to Jim for approval!", "success")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

# === SECTION 6: ADMIN & GUEST ACTIONS ===
@app.route('/admin_action', methods=['POST'])
def admin_action():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    action = request.form.get('action')
    if action == 'labels':
        recs = get_airtable_data("Settings")
        if recs: requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{recs[0]['id']}", headers=HEADERS, json={"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}})
        flash("Date and time updated!", "success")
    elif action == 'reset_roster':
        signups = get_airtable_data("Signups")
        settings = get_airtable_data("Settings")
        current_date = settings[0]['fields'].get('Target Date', 'Unknown') if settings else 'Unknown'
        for r in signups:
            archive_payload = {"fields": {"First": r['fields'].get('First', ''), "Last": r['fields'].get('Last', ''), "Player Code": str(r['fields'].get('Player Code', '')), "Attendance": r['fields'].get('Label', 'Signed Up'), "Date": current_date, "Notes": r['fields'].get('Sub Offer', '')}}
            requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json=archive_payload)
            requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
        flash("Roster archived and cleared!", "success")
    elif action == 'player_update':
        pid, note = request.form.get('player_id'), request.form.get('note')
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{pid}", headers=HEADERS, json={"fields": {"Notes": note}})
        flash("Admin note saved.", "success")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/attendance/<code_val>', methods=['POST'])
def attendance(code_val):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    status, note = request.form.get('status'), request.form.get('note', '')
    strike_inc = 1 if status == 'Late' else 2 if status == 'No Show' else 0
    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{code_val}'")
    if m_recs:
        new_strikes = m_recs[0]['fields'].get('Strikes', 0) + strike_inc
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{m_recs[0]['id']}", headers=HEADERS, json={"fields": {"Strikes": new_strikes, "Paused": (new_strikes >= 3)}})
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json={"fields": {"Player Code": str(code_val), "Attendance": status, "Notes": note, "Date": dt.datetime.now().strftime("%Y-%m-%d")}})
    flash(f"Recorded {status} for {code_val}.", "info")
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

@app.route('/approve_player/<app_id>', methods=['POST'])
def approve_player(app_id):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    res = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS).json()
    f = res.get('fields', {})
    m_list = get_airtable_data("Master List")
    highest_code = 1000
    for m in m_list:
        c = m['fields'].get('Code')
        if c:
            try:
                num = int(str(c).strip())
                if highest_code < num < 9000: highest_code = num
            except ValueError: pass 
    new_code = str(highest_code + 1)
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS, json={"fields": {"First": f.get('First'), "Last": f.get('Last'), "Email": f.get('Email'), "Phone": f.get('Phone', ''), "Code": new_code}, "typecast": True})
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS, json={"fields": {"Status": "Approved", "Assigned Code": new_code}, "typecast": True})
    send_email(f.get('Email'), "🎾 Welcome to the Gang!", f"<p>Approved. Login code: <b>{new_code}</b></p>")
    flash(f"Approved with Code {new_code}.", "success")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/info_blast', methods=['POST'])
def info_blast():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    target, message = request.form.get('target_group'), request.form.get('message')
    master_list = get_airtable_data("Master List")
    emails = []
    if target == 'roster':
        signups = get_airtable_data("Signups")
        roster_codes = [str(s['fields'].get('Player Code')) for s in signups]
        emails = [m['fields'].get('Email') for m in master_list if str(m['fields'].get('Code')) in roster_codes and m['fields'].get('Email')]
    else:
        emails = [m['fields'].get('Email') for m in master_list if m['fields'].get('Email')]
    if emails:
        send_email(emails, "Saturday Tennis Update", f"<h3>Update:</h3><p>{message}</p><p><a href='{SITE_URL}'>Roster here.</a></p>", is_multiple=True)
        flash(f"Blast sent to {len(emails)} players!", "success")
    return redirect(url_for('index'))

@app.route('/send_invite', methods=['POST'])
def send_invite():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    email = request.form.get('invite_email')
    send_email(email, "🎾 Invitation: Saturday Tennis Gang", f"<p>Apply here: <a href='{SITE_URL}'>{SITE_URL}</a></p>")
    flash(f"Invite sent to {email}", "success")
    return redirect(url_for('index'))

# === SECTION 7: CRON / AUTOMATION ROUTES ===
@app.route('/cron/thursday')
def cron_thursday(): return "Executed", 200

@app.route('/cron/monday')
def cron_monday():
    signups = get_airtable_data("Signups")
    settings = get_airtable_data("Settings")
    d_date = settings[0]['fields'].get('Target Date', 'Next Session') if settings else 'Next Session'
    d_start = settings[0]['fields'].get('Start Time', 'TBD') if settings else 'TBD'
    for r in signups:
        archive_payload = {"fields": {"First": r['fields'].get('First', ''), "Last": r['fields'].get('Last', ''), "Player Code": str(r['fields'].get('Player Code', '')), "Attendance": r['fields'].get('Label', 'Signed Up'), "Date": d_date}}
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json=archive_payload)
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
    emails = [m['fields'].get('Email') for m in get_airtable_data("Master List") if m['fields'].get('Email')]
    body = f"<h3>🎾 Signups OPEN for {d_date}!</h3><p>Time: {d_start}</p><p><a href='{SITE_URL}'>Claim your spot!</a></p>"
    send_email(emails, f"🎾 Signups OPEN for {d_date}!", body, is_multiple=True)
    AIRTABLE_CACHE.clear()
    return "Executed", 200

@app.route('/cron/friday')
def cron_friday():
    settings = get_airtable_data("Settings")
    d_date = settings[0]['fields'].get('Target Date', 'Tomorrow') if settings else 'Tomorrow'
    d_start = settings[0]['fields'].get('Start Time', 'TBD') if settings else 'TBD'
    emails = [m['fields'].get('Email') for m in get_airtable_data("Master List") if m['fields'].get('Email')]
    body = f"<p>Reminder for upcoming tennis!</p><p>Date: {d_date}<br>Time: {d_start}</p><p><a href='{SITE_URL}'>Final Roster here.</a></p>"
    send_email(emails, f"🎾 Tennis Roster & Reminders for {d_date}", body, is_multiple=True)
    return "Executed", 200

if __name__ == '__main__':
    app.run(debug=True)
