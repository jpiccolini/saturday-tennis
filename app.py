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
GMAIL_PW = os.environ.get("GMAIL_PASSWORD") # Your 16-character App Password
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", FROM_EMAIL) 
SITE_URL = "https://saturday-tennis.onrender.com"

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

def log_activity(name, action):
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Logs", headers=HEADERS, 
                  json={"fields": {"Name": name, "Action": action}})

# UPDATED: Replaced SendGrid with Gmail (smtplib). Uses BCC for bulk emails.
def send_email(to_emails, subject, html_content, is_multiple=False):
    if not FROM_EMAIL or not GMAIL_PW or not to_emails: return
    
    if isinstance(to_emails, str): 
        to_emails = [to_emails]
        
    msg = MIMEMultipart()
    msg['From'] = FROM_EMAIL
    msg['Subject'] = subject
    
    if is_multiple:
        msg['To'] = FROM_EMAIL # Addressed to self, others BCC'd
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
    except Exception as e: 
        print(f"Email Error: {e}")

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
        
    records = []
    try:
        while True:
            r = requests.get(url, headers=HEADERS, params=params)
            if r.status_code != 200: break
            data = r.json()
            records.extend(data.get('records', []))
            if 'offset' in data and not max_records:
                params['offset'] = data['offset']
            else:
                break
        return records
    except: return records

@app.route('/')
def index():
    settings = get_airtable_data("Settings")
    d_date, d_start, d_end = "TBD", "TBD", ""
    if settings:
        f = settings[0]['fields']
        d_date, d_start = f.get('Target Date', 'TBD'), f.get('Start Time', 'TBD')
        try:
            start_dt = dt.datetime.strptime(d_start, "%I:%M %p")
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

    # UPDATED: Calculate Complete Courts
    confirmed_players = min(len(roster), 24)
    complete_courts = confirmed_players // 4

    weather_info = "Weather Unavailable"
    try:
        w_res = requests.get(f"https://api.weatherapi.com/v1/forecast.json?key={W_KEY}&q=80026&days=7").json()
        sat = next((d for d in w_res['forecast']['forecastday'] if dt.datetime.strptime(d['date'], '%Y-%m-%d').weekday() == 5), None)
        if sat:
            t8, t11 = int(sat['hour'][8]['temp_f']), int(sat['hour'][11]['temp_f'])
            weather_info = f"Sat: {sat['hour'][8]['condition']['text']} | {t8}°F → {t11}°F"
    except: pass

    applicants, recent_logs, master_list = [], [], []
    show_emergency_btn = False

    if curr_user:
        master_list = get_airtable_data("Master List", sort_field="First")
        if curr_user.get('is_admin'):
            show_emergency_btn = True
            applicants = [a for a in get_airtable_data("Applicants") if a['fields'].get('Status') == 'Pending']
            recent_logs = get_airtable_data("Logs", sort_field="Timestamp", direction="desc", max_records=10)
        else:
            now_utc = dt.datetime.utcnow()
            if now_utc.weekday() == 5 and now_utc.hour >= 12:
                show_emergency_btn = True

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end, roster=roster, 
                           applicants=applicants, master_list=master_list, logs=recent_logs,
                           user_on_roster=user_on_roster, waitlist_pos=waitlist_pos, weather=weather_info,
                           show_emergency_btn=show_emergency_btn, complete_courts=complete_courts)

@app.route('/validate', methods=['POST'])
def validate():
    code = str(request.form.get('code', '')).strip()
    password = request.form.get('password')
    
    if password and password != ADMIN_PW:
        flash("Incorrect Admin Password. <a href='/reset_admin_pw'><strong>Click here to email password reminder</strong></a>.", "danger")
        return redirect(url_for('index'))
        
    records = get_airtable_data("Master List", filter_formula=f"{{Code}}='{code}'")
    if records:
        f = records[0]['fields']
        name = f"{f.get('First')} {f.get('Last')}"
        session['user'] = {
            'id': records[0]['id'],
            'first': f.get('First'), 
            'last': f.get('Last'), 
            'code': code, 
            'is_admin': (password == ADMIN_PW),
            'email': f.get('Email', ''),
            'phone': f.get('Phone', '')
        }
        log_activity(name, "Login")
        return redirect(url_for('index'))
    flash("Invalid Code", "danger")
    return redirect(url_for('index'))

@app.route('/reset_admin_pw')
def reset_admin_pw():
    send_email(ADMIN_EMAIL, "Tennis Admin Password", f"Your admin password is: {ADMIN_PW}")
    flash("Password reminder sent to the admin email.", "success")
    return redirect(url_for('index'))

@app.route('/emergency_sub', methods=['POST'])
def emergency_sub():
    if not session.get('user'): return redirect(url_for('index'))
    
    master_list = get_airtable_data("Master List")
    sender_name = f"{session['user']['first']} {session['user']['last']}"
    
    subject = "🚨 URGENT: Tennis Sub Needed!"
    body = f"""<h3>Emergency Sub Needed!</h3>
    <p><b>{sender_name}</b> just broadcasted an urgent need for a sub for this Saturday's tennis rotation.</p>
    <p>If you can play, please sign up immediately at <a href='{SITE_URL}'>{SITE_URL}</a> or log in to check the directory and text them directly.</p>
    """
    emails = [m['fields'].get('Email') for m in master_list if m['fields'].get('Email')]
    send_email(emails, subject, body, is_multiple=True)
            
    log_activity(sender_name, "Broadcasted Emergency Sub")
    flash("Emergency sub broadcast sent to all players!", "success")
    return redirect(url_for('index'))

@app.route('/send_invite', methods=['POST'])
def send_invite():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    email = request.form.get('invite_email')
    subject = "🎾 Invitation: Saturday Tennis Gang"
    body = f"""<h3>You're invited!</h3>
    <p>Apply to join our Saturday tennis rotation here: <a href='{SITE_URL}'>{SITE_URL}</a></p>
    <p><b>Important:</b> Please add {FROM_EMAIL} to your contacts!</p>"""
    send_email(email, subject, body)
    flash(f"Invite sent to {email}", "success")
    return redirect(url_for('index'))

@app.route('/approve_player/<app_id>', methods=['POST'])
def approve_player(app_id):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    res = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS).json()
    f = res.get('fields', {})
    email, first, last, phone = f.get('Email'), f.get('First'), f.get('Last'), f.get('Phone', '')
    
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
    
    master_data = {"fields": {"First": first, "Last": last, "Email": email, "Phone": phone, "Code": new_code, "Notes": f.get('Notes', '')}, "typecast": True}
    m_res = requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS, json=master_data)
    
    if m_res.status_code != 200:
        flash(f"Error adding to Master List.", "danger")
        return redirect(url_for('index'))

    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS, 
                   json={"fields": {"Status": "Approved", "Assigned Code": new_code}, "typecast": True})
    
    send_email(email, "🎾 Welcome to the Gang!", f"<h3>Hi {first}!</h3><p>Your application is approved. Login code: <b>{new_code}</b></p><p>Sign up here: <a href='{SITE_URL}'>{SITE_URL}</a></p>")
    flash(f"Success! {first} approved with Code {new_code}.", "success")
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    if not session.get('user'): return redirect(url_for('index'))
    record_id, new_email, new_phone = session['user'].get('id'), request.form.get('email'), request.form.get('phone')
    res = requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{record_id}", headers=HEADERS, 
                         json={"fields": {"Email": new_email, "Phone": new_phone}, "typecast": True})
    if res.status_code == 200:
        session['user']['email'], session['user']['phone'] = new_email, new_phone
        session.modified = True
        flash("Contact info updated successfully!", "success")
        log_activity(f"{session['user']['first']} {session['user']['last']}", "Updated Contact Info")
    else: flash("Failed to update profile.", "danger")
    return redirect(url_for('index'))

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
                  json={"fields": {"Player Code": str(code_val), "Attendance": status, "Date": dt.datetime.now().strftime("%Y-%m-%d")}})
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if not session.get('user'): return redirect(url_for('index'))
    existing = get_airtable_data("Signups")
    data = {"fields": {"First": session['user']['first'], "Last": session['user']['last'], "Player Code": str(session['user']['code']), "Status": "Confirmed" if len(existing) < 24 else "Waitlist"}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=data)
    log_activity(f"{session['user']['first']} {session['user']['last']}", "Signed Up")
    return redirect(url_for('index'))

# UPDATED: Enforces Friday 8 AM rule
@app.route('/cancel', methods=['POST'])
def cancel():
    if not session.get('user'): return redirect(url_for('index'))
    
    now_utc = dt.datetime.utcnow()
    # 14:00 UTC is 8:00 AM MDT in Colorado
    is_past_deadline = (now_utc.weekday() == 4 and now_utc.hour >= 14) or (now_utc.weekday() == 5)

    recs = get_airtable_data("Signups", sort_field="Created Time")
    idx = next((i for i, r in enumerate(recs) if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    
    if idx is not None:
        is_confirmed = idx < 24
        waitlist_exists = len(recs) > 24
        
        if is_confirmed and is_past_deadline and not waitlist_exists:
            flash("⚠️ It is past Friday 8 AM! You cannot cancel unless someone is on the waitlist to take your spot. Please find a sub and contact Jim.", "danger")
            return redirect(url_for('index'))

        if idx < 24 and len(recs) > 24:
            promo = recs[24]
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{promo['id']}", headers=HEADERS, json={"fields": {"Status": "Confirmed"}})
            send_email(promo['fields'].get('Email'), "🎾 You're IN!", "A spot opened up. You are confirmed!", is_multiple=False)
            
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{recs[idx]['id']}", headers=HEADERS)
        log_activity(f"{session['user']['first']} {session['user']['last']}", "Cancelled")
        
    return redirect(url_for('index'))

@app.route('/logout')
def logout(): session.clear(); return redirect(url_for('index'))

@app.route('/apply', methods=['POST'])
def apply():
    data = {"fields": {"First": request.form.get('first'), "Last": request.form.get('last'), "Email": request.form.get('email'), "Phone": request.form.get('phone'), "Notes": request.form.get('note'), "Status": "Pending"}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json=data)
    flash("Application Submitted!", "success")
    return redirect(url_for('index'))

@app.route('/cron/thursday')
def cron_thursday():
    return "Thursday cron executed successfully", 200

@app.route('/cron/monday')
def cron_monday():
    for r in get_airtable_data("Signups"): 
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
    
    master_list = get_airtable_data("Master List")
    subject = "🎾 Signups are OPEN for Saturday Tennis!"
    body = f"<h3>Happy Monday, Gang!</h3><p>Signups for this Saturday are open. <a href='{SITE_URL}'>Claim your spot!</a></p>"
    emails = [m['fields'].get('Email') for m in master_list if m['fields'].get('Email')]
    send_email(emails, subject, body, is_multiple=True)
    return "Monday cron executed", 200

@app.route('/cron/friday')
def cron_friday():
    master_list = get_airtable_data("Master List")
    subject = "🎾 Tomorrow's Tennis Roster & Reminders"
    body = f"<h3>Happy Friday!</h3><p>Check the live roster here: <a href='{SITE_URL}'>{SITE_URL}</a></p>"
    emails = [m['fields'].get('Email') for m in master_list if m['fields'].get('Email')]
    send_email(emails, subject, body, is_multiple=True)
    return "Friday cron executed", 200

if __name__ == '__main__': app.run(debug=True)
