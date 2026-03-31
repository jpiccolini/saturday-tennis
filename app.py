import os
import requests
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta
import smtplib
from email.message import EmailMessage

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "supersecretkey")

# --- Environment Variables ---
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID")
WEATHER_API_KEY = os.environ.get("WEATHER_API_KEY")
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
GMAIL_USER = os.environ.get("GMAIL_USER")
GMAIL_PASSWORD = os.environ.get("GMAIL_PASSWORD")

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json"
}

# --- Helper Functions ---
def get_weather_forecast(date_str, time_str):
    try:
        # Convert "Apr 04, 2026" and "8:45 AM" into a target datetime
        target_dt = datetime.strptime(f"{date_str} {time_str}", "%b %d, %Y %I:%M %p")
        
        url = f"https://api.weatherapi.com/v1/forecast.json?key={WEATHER_API_KEY}&q=Lafayette,CO&days=10"
        response = requests.get(url).json()
        
        target_date_string = target_dt.strftime("%Y-%m-%d")
        for day in response['forecast']['forecastday']:
            if day['date'] == target_date_string:
                for hour in day['hour']:
                    # Find the closest hour
                    hour_dt = datetime.strptime(hour['time'], "%Y-%m-%d %H:%M")
                    if hour_dt.hour == target_dt.hour:
                        temp = int(hour['temp_f'])
                        condition = hour['condition']['text']
                        wind = int(hour['wind_mph'])
                        return f"{temp}°F, {condition}, Wind {wind} mph"
        return "Forecast not available yet (too far out)."
    except Exception as e:
        return "Weather data temporarily unavailable."

def send_email(to_email, bcc_list, subject, body):
    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg['Subject'] = subject
        msg['From'] = GMAIL_USER
        msg['To'] = to_email
        if bcc_list:
            msg['Bcc'] = ", ".join(bcc_list)

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASSWORD)
            smtp.send_message(msg)
        return True
    except Exception as e:
        print(f"Failed to send email: {e}")
        return False

# --- Core Web Routes ---
@app.route('/')
def index():
    # 1. Get Settings (Date & Time)
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
    display_date = "TBD"
    display_start = "TBD"
    if 'records' in settings_resp and len(settings_resp['records']) > 0:
        fields = settings_resp['records'][0]['fields']
        display_date = fields.get('target_date', 'TBD')
        display_start = fields.get('start_time', 'TBD')

    # 2. Get Roster
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
    roster_list = [r['fields'] for r in signups_resp.get('records', [])]

    # 3. Get Weather
    weather_text = get_weather_forecast(display_date, display_start)

    # 4. Calculate Friday 8 AM Deadline & Waitlist Logic
    is_past_deadline = False
    has_waitlist = False
    try:
        target_dt = datetime.strptime(display_date, "%b %d, %Y")
        # Deadline is 1 day before the target date (Friday) at 8:00 AM
        deadline_dt = target_dt - timedelta(days=1) + timedelta(hours=8)
        is_past_deadline = datetime.now() >= deadline_dt
    except: pass

    # If the roster isn't a perfect multiple of 4, someone is on the waitlist
    has_waitlist = len(roster_list) % 4 != 0

    # 5. Get Pending Message Drafts (For Admin Panel)
    drafts = []
    if 'user' in session and session['user'].get('is_admin'):
        messages_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/messages", headers=HEADERS).json()
        for r in messages_resp.get('records', []):
            if r['fields'].get('Status') == 'Draft':
                draft_data = r['fields']
                draft_data['id'] = r['id']
                drafts.append(draft_data)

    return render_template('index.html', weather=weather_text, start_time=display_start, target_date=display_date, roster=roster_list, drafts=drafts, is_past_deadline=is_past_deadline, has_waitlist=has_waitlist)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code')
    password = request.form.get('password')

    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list", headers=HEADERS).json()
    
    for record in master_resp.get('records', []):
        fields = record.get('fields', {})
        if str(fields.get('Code')) == str(code):
            is_admin = False
            if password:
                if password == ADMIN_PW:
                    is_admin = True
                else:
                    flash("Incorrect Admin Password.", "error")
                    return redirect(url_for('index'))
            
            session['user'] = {
                'first': fields.get('First'),
                'last': fields.get('Last'),
                'is_admin': is_admin
            }
            return redirect(url_for('index'))
            
    flash("Code not found. Please try again.", "error")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

# --- Player Actions ---
@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session: return redirect(url_for('index'))
    
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
    target_date = "TBD"
    if 'records' in settings_resp and len(settings_resp['records']) > 0:
        target_date = settings_resp['records'][0]['fields'].get('target_date', 'TBD')

    payload = {
        "records": [{
            "fields": {
                "Date": target_date,
                "First": session['user']['first'],
                "Last": session['user']['last']
            }
        }]
    }
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS, json=payload)
    flash("You are on the roster!", "success")
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if 'user' not in session: return redirect(url_for('index'))
    try:
        signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
        for r in signups_resp.get('records', []):
            if r['fields'].get('First') == session['user']['first'] and r['fields'].get('Last') == session['user']['last']:
                requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/signups/{r['id']}", headers=HEADERS)
                break 
    except: pass
    return redirect(url_for('index'))

@app.route('/provide_sub', methods=['POST'])
def provide_sub():
    if 'user' not in session: return redirect(url_for('index'))
    sub_name = request.form.get('sub_name')
    if not sub_name: return redirect(url_for('index'))
    
    try:
        signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
        for r in signups_resp.get('records', []):
            if r['fields'].get('First') == session['user']['first'] and r['fields'].get('Last') == session['user']['last']:
                payload = {"fields": {"First": f"{sub_name} (Sub)", "Last": f"for {session['user']['first']}"}}
                requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/signups/{r['id']}", headers=HEADERS, json=payload)
                break
    except: pass
    return redirect(url_for('index'))

# --- Admin Controls ---
@app.route('/update_settings', methods=['POST'])
def update_settings():
    if 'user' not in session or not session['user'].get('is_admin'):
        return redirect(url_for('index'))

    new_date = request.form.get('date_string')
    new_time = request.form.get('time_string')

    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
    if 'records' in settings_resp and len(settings_resp['records']) > 0:
        record_id = settings_resp['records'][0]['id']
        payload = {
            "fields": {
                "target_date": new_date,
                "start_time": new_time
            }
        }
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/settings/{record_id}", headers=HEADERS, json=payload)
        flash("Schedule updated successfully!", "success")
        
    return redirect(url_for('index'))

@app.route('/approve_message', methods=['POST'])
def approve_message():
    if 'user' not in session or not session['user'].get('is_admin'):
        return redirect(url_for('index'))

    msg_id = request.form.get('msg_id')
    new_subject = request.form.get('subject')
    new_body = request.form.get('body')

    payload = {
        "fields": {
            "Status": "Approved",
            "Subject": new_subject,
            "Body": new_body
        }
    }
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/messages/{msg_id}", headers=HEADERS, json=payload)
    flash("Message approved and queued for sending!", "success")
    
    return redirect(url_for('index'))


# --- CRON JOB AUTOMATIONS ---

@app.route('/cron/sunday')
def cron_sunday():
    # 1. Archive Roster & Clear Signups
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
    records = signups_resp.get('records', [])
    
    if records:
        archive_payload = {"records": [{"fields": {"Date": r['fields'].get('Date', 'Unknown'), "First": r['fields'].get('First', ''), "Last": r['fields'].get('Last', '')}} for r in records]}
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/roster_history", headers=HEADERS, json=archive_payload)
        
        for r in records:
            requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/signups/{r['id']}", headers=HEADERS)

    # 2. Advance the Date by 7 days
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
    if 'records' in settings_resp and len(settings_resp['records']) > 0:
        record_id = settings_resp['records'][0]['id']
        current_date_str = settings_resp['records'][0]['fields'].get('target_date')
        try:
            current_dt = datetime.strptime(current_date_str, "%b %d, %Y")
            new_dt = current_dt + timedelta(days=7)
            new_date_str = new_dt.strftime("%b %d, %Y")
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/settings/{record_id}", headers=HEADERS, json={"fields": {"target_date": new_date_str}})
        except: pass

    # 3. Create Monday Invite Draft
    draft_payload = {
        "records": [{
            "fields": {
                "Type": "Monday Invite",
                "Status": "Draft",
                "Subject": f"Tennis Signups Open for {new_date_str}",
                "Body": "Happy Sunday!\n\nSignups are now open for this coming Saturday. Click the link below to add your name to the roster:\n\nhttps://YOUR-APP-NAME.onrender.com\n\nCheers!"
            }
        }]
    }
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/messages", headers=HEADERS, json=draft_payload)

    return "Sunday Routine Complete", 200

@app.route('/cron/thursday')
def cron_thursday():
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
    target_date = "TBD"
    start_time = "TBD"
    if 'records' in settings_resp and len(settings_resp['records']) > 0:
        target_date = settings_resp['records'][0]['fields'].get('target_date', 'TBD')
        start_time = settings_resp['records'][0]['fields'].get('start_time', 'TBD')

    weather = get_weather_forecast(target_date, start_time)

    draft_payload = {
        "records": [{
            "fields": {
                "Type": "Friday Confirmation",
                "Status": "Draft",
                "Subject": f"Tennis Tomorrow ({target_date}) - Details & Weather",
                "Body": f"Hello everyone,\n\nTennis is on for tomorrow at {start_time}.\n\nExpected Weather: {weather}\n\nPlease check the live roster to confirm your spot. If you are past the deadline to drop out, please ensure you use the app to provide a sub if you can no longer make it.\n\nhttps://YOUR-APP-NAME.onrender.com\n\nSee you on the courts!"
            }
        }]
    }
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/messages", headers=HEADERS, json=draft_payload)
    
    return "Thursday Routine Complete", 200

@app.route('/cron/send_approved')
def cron_send_approved():
    messages_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/messages", headers=HEADERS).json()
    approved_messages = [m for m in messages_resp.get('records', []) if m['fields'].get('Status') == 'Approved']
    
    if not approved_messages:
        return "No approved messages to send.", 200

    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list", headers=HEADERS).json()
    bcc_emails = [r['fields'].get('Email') for r in master_resp.get('records', []) if r['fields'].get('Email')]

    for msg in approved_messages:
        subject = msg['fields'].get('Subject', 'Tennis Update')
        body = msg['fields'].get('Body', '')
        
        success = send_email(to_email=GMAIL_USER, bcc_list=bcc_emails, subject=subject, body=body)
        
        if success:
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/messages/{msg['id']}", headers=HEADERS, json={"fields": {"Status": "Sent"}})

    return "Sent approved messages.", 200

@app.route('/cron/saturday_reminder')
def cron_saturday_reminder():
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
    active_players = [r['fields'] for r in signups_resp.get('records', [])]
    
    if not active_players:
        return "No players, no reminder sent.", 200

    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list", headers=HEADERS).json()
    master_dict = {f"{r['fields'].get('First')} {r['fields'].get('Last')}": r['fields'].get('Email') for r in master_resp.get('records', [])}

    bcc_emails = []
    for player in active_players:
        full_name = f"{player.get('First')} {player.get('Last')}"
        if full_name in master_dict and master_dict[full_name]:
            bcc_emails.append(master_dict[full_name])

    if bcc_emails:
        send_email(to_email=GMAIL_USER, bcc_list=bcc_emails, subject="Tennis Reminder - See you soon!", body="Just a friendly reminder that you are on the roster for tennis this morning! See you on the courts shortly.")

    return "Saturday reminder sent.", 200

if __name__ == '__main__':
    app.run(debug=True)
