import os, requests, smtplib
from flask import Flask, render_template, request, session, redirect, url_for, flash
import datetime as dt
from datetime import timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

# Environment Variables
API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "").strip()
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
W_KEY = os.environ.get("WEATHER_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL") 
GMAIL_PW = os.environ.get("GMAIL_PASSWORD") 
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", FROM_EMAIL) 
SITE_URL = "https://saturday-tennis.onrender.com"

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

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

def get_airtable_data(table_name, filter_formula=None, sort_field=None, direction="asc"):
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
        
    records = []
    try:
        while True:
            r = requests.get(url, headers=HEADERS, params=params)
            if r.status_code != 200: break
            data = r.json()
            records.extend(data.get('records', []))
            if 'offset' in data: params['offset'] = data['offset']
            else: break
        return records
    except: return []

@app.route('/')
def index():
    # 1. Fetch Settings
    settings = get_airtable_data("Settings")
    d_date, d_start, d_end = "TBD", "TBD", ""
    if settings:
        f = settings[0]['fields']
        d_date, d_start = f.get('Target Date', 'TBD'), f.get('Start Time', 'TBD')
        try:
            start_dt = dt.datetime.strptime(d_start, "%I:%M %p")
            d_end = f" – {(start_dt + timedelta(hours=2, minutes=15)).strftime('%I:%M %p').lstrip('0')}"
        except: pass

    # 2. Fetch Master List for Strike/Paused Status
    master_recs = get_airtable_data("Master List", sort_field="First")
    strike_map = {str(m['fields'].get('Code')): m['fields'].get('Strikes', 0) for m in master_recs}

    # 3. Fetch Roster & Sort by Penalties
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

    # 4. Math & State Variables
    playing_cutoff = (min(len(roster), 24) // 4) * 4
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

    now_utc = dt.datetime.utcnow()
    is_past_deadline = (now_utc.weekday() == 4 and now_utc.hour >= 14) or (now_utc.weekday() == 5)

    # 5. Weather (Updated with explicit start and end times)
    weather_info = "Weather Unavailable"
    try:
        w_res = requests.get(f"https://api.weatherapi.com/v1/forecast.json?key={W_KEY}&q=80026&days=7").json()
        sat = next((d for d in w_res['forecast']['forecastday'] if dt.datetime.strptime(d['date'], '%Y-%m-%d').weekday() == 5), None)
        if sat: 
            cond = sat['hour'][8]['condition']['text']
            temp_8 = int(sat['hour'][8]['temp_f'])
            temp_11 = int(sat['hour'][11]['temp_f'])
            weather_info = f"{cond} | 8:00 AM: {temp_8}°F → 11:00 AM: {temp_11}°F"
    except: pass

    # 6. Admin Data
    applicants, guest_requests, recent_logs = [], [], []
    if curr_user and curr_user.get('is_admin'):
        all_apps = get_airtable_data("Applicants")
        applicants = [a for a in all_apps if a['fields'].get('Status') == 'Pending' and not a['fields'].get('Sponsor')]
        guest_requests = [a for a in all_apps if a['fields'].get('Status') == 'Pending' and a['fields'].get('Sponsor')]
        recent_logs = get_airtable_data("Logs", sort_field="Timestamp", direction="desc")[:10]

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end, roster=roster, 
                           applicants=applicants, guest_requests=guest_requests, master_list=master_recs, logs=recent_logs,
                           user_on_roster=user_on_roster, user_status=user_status, waitlist_pos=waitlist_pos, weather=weather_info,
                           playing_cutoff=playing_cutoff, pending_sub_offer=pending_sub_offer, is_past_deadline=is_past_deadline)

@app.route('/validate', methods=['POST'])
def validate():
    code = str(request.form.get('code', '')).strip()
    password = request.form.get('password')
    
    recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{code}'")
    if recs:
        f = recs[0]['fields']
        session['user'] = {
            'id': recs[0]['id'], 
            'first': f.get('First'), 
            'last': f.get('Last'), 
            'code': code, 
            'is_admin': (password == ADMIN_PW),
            'email': f.get('Email', ''), 
            'phone': f.get('Phone', '')
        }
        log_activity(f"{f.get('First')} {f.get('Last')}", "Login")
        return redirect(url_for('index'))
    flash("Invalid Code", "danger")
    return redirect(url_for('index'))

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
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if not session.get('user'): return redirect(url_for('index'))
    now_utc = dt.datetime.utcnow()
    is_past_deadline = (now_utc.weekday() == 4 and now_utc.hour >= 14) or (now_utc.weekday() == 5)
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
                requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[idx]['id']}", headers=HEADERS, 
                               json={"fields": {"Label": "PENDING SUB", "Sub Offer": str(promo_code)}})
                send_email(promo['fields'].get('Email'), "🎾 Sub Spot Available!", f"A spot opened up! Log in to {SITE_URL} to accept it.")
                flash("Drop initiated. The first waitlisted player has been emailed.", "warning")
            else:
                requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[idx]['id']}", headers=HEADERS, 
                               json={"fields": {"Label": "NEEDS SUB"}})
                flash("⚠️ NO ONE is on the waitlist. You are marked NEEDS SUB. Find a sub.", "danger")
            return redirect(url_for('index'))

        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[idx]['id']}", headers=HEADERS)
        log_activity(f"{session['user']['first']} {session['user']['last']}", "Cancelled")
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
    return redirect(url_for('index'))

@app.route('/provide_sub', methods=['POST'])
def provide_sub():
    if not session.get('user'): return redirect(url_for('index'))
    sub_first, sub_last, sub_email = request.form.get('sub_first'), request.form.get('sub_last'), request.form.get('sub_email')
    recs = get_airtable_data("Signups")
    me = next((r for r in recs if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if me:
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{me['id']}", headers=HEADERS, json={"fields": {"First": sub_first, "Last": sub_last, "Label": f"SUB for {session['user']['first']}"}})
        send_email([sub_email, session['user']['email'], ADMIN_EMAIL], "🎾 Tennis Sub Confirmed", f"<p>You are confirmed to sub for {session['user']['first']}!</p>", is_multiple=False)
        flash("Sub confirmed and added to the roster.", "success")
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
    return redirect(url_for('index'))

@app.route('/request_guest', methods=['POST'])
def request_guest():
    if not session.get('user'): return redirect(url_for('index'))
    data = {"fields": {"First": request.form.get('guest_first'), "Last": request.form.get('guest_last'), "Email": request.form.get('guest_email'), "Phone": request.form.get('guest_phone'), "Sponsor": f"{session['user']['first']} {session['user']['last']}", "Status": "Pending"}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json=data)
    send_email(ADMIN_EMAIL, "🎾 New Guest Request", "You have a new guest request. Log in to approve.")
    flash("Guest request sent to Jim for approval!", "success")
    return redirect(url_for('index'))

@app.route('/approve_guest/<app_id>', methods=['POST'])
def approve_guest(app_id):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    res = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS).json()
    f = res.get('fields', {})
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json={"fields": {"First": f.get('First'), "Last": f.get('Last'), "Player Code": "GUEST", "Label": f"GUEST of {f.get('Sponsor')}"}})
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS, json={"fields": {"Status": "Approved"}})
    flash(f"Guest added to roster!", "success")
    return redirect(url_for('index'))

@app.route('/emergency_sub', methods=['POST'])
def emergency_sub():
    if not session.get('user'): return redirect(url_for('index'))
    master_list = get_airtable_data("Master List")
    emails = [m['fields'].get('Email') for m in master_list if m['fields'].get('Email')]
    send_email(emails, "🚨 URGENT: Tennis Sub Needed!", f"<p><b>{session['user']['first']} {session['user']['last']}</b> needs an emergency sub immediately. Log in to claim the spot!</p>", is_multiple=True)
    flash("Emergency sub broadcast sent!", "success")
    return redirect(url_for('index'))

@app.route('/send_invite', methods=['POST'])
def send_invite():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    email = request.form.get('invite_email')
    send_email(email, "🎾 Invitation: Saturday Tennis Gang", f"<p>Apply to join our Saturday tennis rotation here: <a href='{SITE_URL}'>{SITE_URL}</a></p>")
    flash(f"Invite sent to {email}", "success")
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
    send_email(f.get('Email'), "🎾 Welcome to the Gang!", f"<p>Your application is approved. Login code: <b>{new_code}</b></p>")
    flash(f"Success! {f.get('First')} approved with Code {new_code}.", "success")
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if not session.get('user'): return redirect(url_for('index'))
    new_email, new_phone = request.form.get('email'), request.form.get('phone')
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{session['user'].get('id')}", headers=HEADERS, json={"fields": {"Email": new_email, "Phone": new_phone}, "typecast": True})
    session['user']['email'], session['user']['phone'] = new_email, new_phone
    session.modified = True
    flash("Contact info updated successfully!", "success")
    return redirect(url_for('index'))

@app.route('/admin_action', methods=['POST'])
def admin_action():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    action = request.form.get('action')
    if action == 'labels':
        recs = get_airtable_data("Settings")
        if recs: requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{recs[0]['id']}", headers=HEADERS, json={"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}})
        flash("Date and time updated!", "success")
    elif action == 'reset_roster':
        for r in get_airtable_data("Signups"): requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
        flash("Roster cleared!", "success")
    elif action == 'player_update':
        pid, note = request.form.get('player_id'), request.form.get('note')
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{pid}", headers=HEADERS, json={"fields": {"Notes": note}})
        flash("Admin note saved.", "success")
    return redirect(url_for('index'))

@app.route('/apply', methods=['POST'])
def apply():
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json={"fields": {"First": request.form.get('first'), "Last": request.form.get('last'), "Email": request.form.get('email'), "Phone": request.form.get('phone'), "Notes": request.form.get('note'), "Status": "Pending"}})
    flash("Application Submitted!", "success")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/cron/thursday')
def cron_thursday(): return "Executed", 200

@app.route('/cron/monday')
def cron_monday():
    for r in get_airtable_data("Signups"): requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
    emails = [m['fields'].get('Email') for m in get_airtable_data("Master List") if m['fields'].get('Email')]
    send_email(emails, "🎾 Signups are OPEN!", f"<p>Signups for this Saturday are open. <a href='{SITE_URL}'>Claim your spot!</a></p>", is_multiple=True)
    return "Executed", 200

@app.route('/cron/friday')
def cron_friday():
    emails = [m['fields'].get('Email') for m in get_airtable_data("Master List") if m['fields'].get('Email')]
    send_email(emails, "🎾 Tomorrow's Tennis Roster & Reminders", f"<p>Check the live roster here: <a href='{SITE_URL}'>{SITE_URL}</a></p>", is_multiple=True)
    return "Executed", 200

if __name__ == '__main__':
    app.run(debug=True)
