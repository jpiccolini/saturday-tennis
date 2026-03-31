import os
import requests
import re
import smtplib
from email.mime.text import MIMEText
from flask import Flask, render_template, request, session, flash, redirect, url_for
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "tennis_roster_secure_key"

# --- CONFIGURATION ---
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT")
BASE_ID = "appEC9INt2PRYewNj"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_PAT}", "Content-Type": "application/json"}
ADMIN_PASSWORD = "jujubeE2" 

GMAIL_USER = os.environ.get("GMAIL_ADDRESS")
GMAIL_PASS = os.environ.get("GMAIL_APP_PASSWORD")

# --- HELPER FUNCTIONS ---
def get_weather_icon(code):
    if code == 0: return "☀️", "Clear"
    elif code in [1, 2, 3]: return "⛅", "Partly Cloudy"
    elif code in [45, 48]: return "🌫️", "Foggy"
    elif code in [51, 53, 55, 56, 57]: return "🌧️", "Drizzle"
    elif code in [61, 63, 65, 66, 67, 80, 81, 82]: return "🌧️", "Rain"
    elif code in [71, 73, 75, 77, 85, 86]: return "❄️", "Snow"
    elif code in [95, 96, 99]: return "⛈️", "Thunderstorm"
    return "🌡️", "Unknown"

def get_weather_forecast(date_str, time_str):
    try:
        dt_str = f"{date_str} {time_str}"
        target_dt = datetime.strptime(dt_str, "%b %d, %Y %I:%M %p")
        start_hour_str = target_dt.strftime("%Y-%m-%dT%H:00")
        end_dt = target_dt + timedelta(hours=2)
        end_hour_str = end_dt.strftime("%Y-%m-%dT%H:00")

        url = "https://api.open-meteo.com/v1/forecast?latitude=39.9936&longitude=-105.0897&hourly=temperature_2m,weathercode&temperature_unit=fahrenheit&timezone=America%2FDenver"
        headers = {"User-Agent": "Mozilla/5.0"} 
        data = requests.get(url, headers=headers, timeout=10).json()
        times = data['hourly']['time']
        
        if start_hour_str in times:
            s_idx = times.index(start_hour_str)
            e_idx = times.index(end_hour_str) if end_hour_str in times else s_idx + 2
            
            s_temp, s_code = int(data['hourly']['temperature_2m'][s_idx]), data['hourly']['weathercode'][s_idx]
            e_temp, e_code = int(data['hourly']['temperature_2m'][e_idx]), data['hourly']['weathercode'][e_idx]
            s_icon, s_cond = get_weather_icon(s_code)
            e_icon, e_cond = get_weather_icon(e_code)
            
            return f"{s_icon} Start: {s_temp}°F ({s_cond})  |  {e_icon} End: {e_temp}°F ({e_cond})"
        return "Weather forecast unavailable yet."
    except: return "Weather currently unavailable."

def send_email(bcc_list, subject, body):
    if not GMAIL_USER or not GMAIL_PASS or not bcc_list: return False
    try:
        msg = MIMEText(body)
        msg['Subject'] = subject
        msg['From'] = GMAIL_USER
        msg['To'] = GMAIL_USER # Send to self, BCC everyone else for privacy
        msg['Bcc'] = ", ".join(bcc_list)

        server = smtplib.SMTP_SSL('smtp.gmail.com', 465)
        server.login(GMAIL_USER, GMAIL_PASS)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"-> Email error: {e}")
        return False

# --- MAIN ROUTES ---
@app.route('/')
def index():
    display_start, display_date = "8:45 AM", "Apr 04, 2026" 
    try:
        settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
        for r in settings_resp.get('records', []):
            if r['fields'].get('Setting') == 'StartTime':
                match = re.search(r'(\d{1,2}:\d{2}\s*(?:AM|PM)?)', str(r['fields'].get('Value')), re.IGNORECASE)
                display_start = match.group(1).upper() if match else "8:45 AM"
            elif r['fields'].get('Setting') == 'TargetDate': display_date = r['fields'].get('Value')
    except: pass

    roster_list = []
    try:
        signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
        for r in signups_resp.get('records', []):
            if 'First' in r.get('fields', {}): roster_list.append(r['fields'])
    except: pass

    # Fetch Draft Messages for Admin
    drafts = []
    if session.get('user', {}).get('is_admin'):
        try:
            msgs_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/messages?filterByFormula=Status='Draft'", headers=HEADERS).json()
            drafts = [{"id": r['id'], **r['fields']} for r in msgs_resp.get('records', [])]
        except: pass
    
    # Calculate Friday 8 AM Deadline
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
    
    weather_text = get_weather_forecast(display_date, display_start)
    return render_template('index.html', weather=weather_text, start_time=display_start, target_date=display_date, roster=roster_list, drafts=drafts)

@app.route('/validate', methods=['POST'])
def validate():
    code, password = request.form.get('code', '').strip(), request.form.get('password', '')
    try:
        resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list?filterByFormula=Code='{code}'", headers=HEADERS).json()
        if resp.get('records'):
            user_data = resp['records'][0]['fields']
            if str(code) == "9999":
                if password == ADMIN_PASSWORD:
                    session['user'] = {'first': 'Admin', 'last': 'User', 'is_admin': True}
                else: flash("Incorrect Admin Password", "error")
            else: session['user'] = {'first': user_data.get('First'), 'last': user_data.get('Last'), 'is_admin': False}
        else: flash("Invalid Player Code", "error")
    except: flash("Database error.", "error")
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session: return redirect(url_for('index'))
    try:
        date_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings?filterByFormula=Setting='TargetDate'", headers=HEADERS).json()
        target_date = date_resp['records'][0]['fields']['Value'] if date_resp.get('records') else "Upcoming Saturday"
        
        payload = {"records": [{"fields": {"Date": target_date, "First": session['user']['first'], "Last": session['user']['last']}}]}
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS, json=payload)
    except: pass
    return redirect(url_for('index'))

@app.route('/approve_message', methods=['POST'])
def approve_message():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    msg_id = request.form.get('msg_id')
    payload = {"fields": {"Subject": request.form.get('subject'), "Body": request.form.get('body'), "Status": "Approved"}}
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/messages/{msg_id}", headers=HEADERS, json=payload)
    flash("Message approved and queued!", "success")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- AUTOMATION (CRON) ROUTING ---
@app.route('/cron/sunday')
def cron_sunday():
    # 1. Archive current signups & delete them
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
    records = signups_resp.get('records', [])
    if records:
        archive_payload = {"records": [{"fields": {"Date": r['fields'].get('Date'), "First": r['fields'].get('First'), "Last": r['fields'].get('Last')}} for r in records]}
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/history", headers=HEADERS, json=archive_payload)
        for r in records: requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/signups/{r['id']}", headers=HEADERS)

    # 2. Advance TargetDate to next Saturday
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings?filterByFormula=Setting='TargetDate'", headers=HEADERS).json()
    if settings_resp.get('records'):
        rec_id = settings_resp['records'][0]['id']
        next_sat = (datetime.now() + timedelta((5 - datetime.now().weekday() + 7) % 7 or 7)).strftime('%b %d, %Y')
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/settings/{rec_id}", headers=HEADERS, json={"fields": {"Value": next_sat}})

    # 3. Draft Monday Promo
    draft_payload = {"records": [{"fields": {"Type": "Monday Promo", "Status": "Draft", "Subject": "Tennis This Saturday?", "Body": f"Signups are open for {next_sat}!\n\nLog in to RSVP."}}]}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/messages", headers=HEADERS, json=draft_payload)
    return "Sunday Routine Complete", 200

@app.route('/cron/thursday')
def cron_thursday():
    # Fetch current target date/time for weather
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
    date_val, time_val = "Upcoming", "8:45 AM"
    for r in settings_resp.get('records', []):
        if r['fields'].get('Setting') == 'TargetDate': date_val = r['fields'].get('Value')
        if r['fields'].get('Setting') == 'StartTime': time_val = r['fields'].get('Value')
    
    weather = get_weather_forecast(date_val, time_val)
    body = f"Hello Tennis Gang,\n\nHere is the update for {date_val} at {time_val}.\n\nForecast: {weather}\n\nSee you on the courts!"
    
    draft_payload = {"records": [{"fields": {"Type": "Friday Confirmation", "Status": "Draft", "Subject": "Saturday Tennis Confirmation", "Body": body}}]}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/messages", headers=HEADERS, json=draft_payload)
    return "Thursday Routine Complete", 200

@app.route('/cron/send_approved')
def cron_send_approved():
    # Sends emails marked "Approved" to the Master List
    msgs_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/messages?filterByFormula=Status='Approved'", headers=HEADERS).json()
    if not msgs_resp.get('records'): return "No approved messages", 200
    
    # Get all emails
    emails = []
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list", headers=HEADERS).json()
    for r in master_resp.get('records', []):
        if 'Email' in r['fields']: emails.append(r['fields']['Email'])

    for msg in msgs_resp['records']:
        send_email(emails, msg['fields'].get('Subject', ''), msg['fields'].get('Body', ''))
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/messages/{msg['id']}", headers=HEADERS, json={"fields": {"Status": "Sent"}})
    
    return "Sent Approved Messages", 200

@app.route('/cron/saturday_reminder')
def cron_saturday():
    # Gets emails ONLY for scheduled players
    signups = [r['fields'].get('First') for r in requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json().get('records', [])]
    if not signups: return "No players", 200

    emails = []
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list", headers=HEADERS).json()
    for r in master_resp.get('records', []):
        if r['fields'].get('First') in signups and 'Email' in r['fields']:
            emails.append(r['fields']['Email'])
    
    send_email(emails, "Tennis Reminder", "Friendly reminder: Tennis starts in 90 minutes! Drive safe.")
    return "Saturday Reminder Sent", 200

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
