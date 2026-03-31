import os
import requests
import uuid
import smtplib
from flask import Flask, render_template, request, session, redirect, url_for, flash
from datetime import datetime, timedelta
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
    if not date_str or "TBD" in date_str:
        return "Weather unavailable (Date not set)."
    try:
        target_dt = datetime.strptime(f"{date_str} {time_str}", "%b %d, %Y %I:%M %p")
        url = f"https://api.weatherapi.com/v1/forecast.json?key={WEATHER_API_KEY}&q=Lafayette,CO&days=10"
        response = requests.get(url).json()
        target_date_string = target_dt.strftime("%Y-%m-%d")
        for day in response['forecast']['forecastday']:
            if day['date'] == target_date_string:
                for hour in day['hour']:
                    hour_dt = datetime.strptime(hour['time'], "%Y-%m-%d %H:%M")
                    if hour_dt.hour == target_dt.hour:
                        return f"{int(hour['temp_f'])}°F, {hour['condition']['text']}, Wind {int(hour['wind_mph'])} mph"
        return "Forecast not available yet."
    except: return "Weather data temporarily unavailable."

def send_email(to_email, subject, body, bcc_list=None):
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
        print(f"Email Error: {e}")
        return False

# --- Core Web Routes ---
@app.route('/')
def index():
    # 1. Get Settings
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Settings", headers=HEADERS).json()
    display_date, display_start = "TBD", "TBD"
    if 'records' in settings_resp and len(settings_resp['records']) > 0:
        f = settings_resp['records'][0]['fields']
        display_date = f.get('Target Date', 'TBD')
        display_start = f.get('Start Time', 'TBD')

    # 2. Get Roster
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS).json()
    roster_list = []
    for r in signups_resp.get('records', []):
        fields = r['fields']
        fields['id'] = r['id']
        if not fields.get('Status'): fields['Status'] = 'Confirmed'
        roster_list.append(fields)

    # 3. Weather & Deadline
    weather_text = get_weather_forecast(display_date, display_start)
    is_past_deadline = False
    try:
        target_dt = datetime.strptime(display_date, "%b %d, %Y")
        deadline_dt = target_dt - timedelta(days=1) + timedelta(hours=8) # Friday 8AM
        is_past_deadline = datetime.now() >= deadline_dt
    except: pass
    
    has_waitlist = len(roster_list) > 24

    drafts = []
    if 'user' in session and session['user'].get('is_admin'):
        msg_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Messages", headers=HEADERS).json()
        for r in msg_resp.get('records', []):
            if r['fields'].get('Status') == 'Draft':
                d = r['fields']; d['id'] = r['id']; drafts.append(d)

    return render_template('index.html', weather=weather_text, start_time=display_start, 
                           target_date=display_date, roster=roster_list, drafts=drafts, 
                           is_past_deadline=is_past_deadline, has_waitlist=has_waitlist)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code', '').strip()
    password = request.form.get('password')
    # URL encode the space for the API call
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS).json()
    records = master_resp.get('records', [])

    for record in records:
        f = record.get('fields', {})
        if str(f.get('Code')) == code:
            is_admin = (password == ADMIN_PW) if password else False
            session['user'] = {
                'first': f.get('First'), 
                'last': f.get('Last'), 
                'code': code, 
                'email': f.get('Email'),
                'is_admin': is_admin
            }
            return redirect(url_for('index'))
            
    flash(f"Code {code} not found. Checked {len(records)} entries in Master List.", "error")
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session: return redirect(url_for('index'))
    
    # Check current roster size
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS).json()
    count = len(signups_resp.get('records', []))
    
    new_status = "Confirmed" if count < 24 else "Waitlist"
    
    data = {
        "fields": {
            "First": session['user']['first'],
            "Last": session['user']['last'],
            "Player Code": session['user']['code'],
            "Status": new_status
        }
    }
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=data)
    flash(f"Successfully signed up as {new_status}!", "success")
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if 'user' not in session: return redirect(url_for('index'))
    
    # 1. Find user's record
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS).json()
    user_record = None
    for r in signups_resp.get('records', []):
        if str(r['fields'].get('Player Code')) == str(session['user']['code']):
            user_record = r
            break
    
    if user_record:
        was_active = user_record['fields'].get('Status') in ['Confirmed', 'Pending']
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{user_record['id']}", headers=HEADERS)
        
        # 2. If an active player dropped, promote the first person on Waitlist
        if was_active:
            waitlist = [r for r in signups_resp.get('records', []) if r['fields'].get('Status') == 'Waitlist']
            if waitlist:
                next_up = waitlist[0]
                token = str(uuid.uuid4())[:8]
                
                # Update Waitlist person to Pending
                update_data = {"fields": {"Status": "Pending", "Token": token}}
                requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{next_up['id']}", headers=HEADERS, json=update_data)
                
                # Get their email from Master List
                master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS).json()
                next_email = next((r['fields'].get('Email') for r in master_resp.get('records', []) 
                                  if str(r['fields'].get('Code')) == str(next_up['fields'].get('Player Code'))), None)
                
                if next_email:
                    subject = "A spot opened up for Saturday Tennis!"
                    body = f"Hi {next_up['fields'].get('First')},\n\nA spot is now available. Click below to claim it or decline.\n\n" \
                           f"CONFIRM: {request.host_url}action?token={token}&choice=confirm\n" \
                           f"DECLINE: {request.host_url}action?token={token}&choice=decline"
                    send_email(next_email, subject, body)

    flash("You have been removed from the roster.", "success")
    return redirect(url_for('index'))

@app.route('/action')
def action():
    token = request.args.get('token')
    choice = request.args.get('choice')
    
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS).json()
    target = next((r for r in signups_resp.get('records', []) if r['fields'].get('Token') == token), None)
    
    if not target:
        return "Invalid or expired link.", 400
        
    if choice == 'confirm':
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{target['id']}", headers=HEADERS, 
                       json={"fields": {"Status": "Confirmed", "Token": ""}})
        flash("You are confirmed! See you on the court.", "success")
    else:
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{target['id']}", headers=HEADERS)
        # Re-trigger logic to find the next waitlist person
        return redirect(url_for('cancel')) # Using cancel logic to find next sub

    return redirect(url_for('index'))

@app.route('/cron/cleanup')
def cleanup():
    # Sunday Brain Wipe: Move to Archive and Clear
    signups = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS).json().get('records', [])
    settings = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Settings", headers=HEADERS).json().get('records', [])
    game_date = settings[0]['fields'].get('Target Date', 'Unknown') if settings else "Unknown"

    for record in signups:
        f = record['fields']
        archive_data = {
            "fields": {
                "First": f.get("First"),
                "Last": f.get("Last"),
                "Date": game_date,
                "Status": f.get("Status"),
                "Player Code": f.get("Player Code"),
                "Attendance": "Attended"
            }
        }
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json=archive_data)
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{record['id']}", headers=HEADERS)

    return "Cleanup Complete.", 200

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
