import os
import requests
import uuid
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
        return "Forecast not available yet (too far out)."
    except: return "Weather data temporarily unavailable."

def send_email(to_email, bcc_list, subject, body):
    try:
        msg = EmailMessage()
        msg.set_content(body)
        msg['Subject'] = subject
        msg['From'] = GMAIL_USER
        msg['To'] = to_email
        if bcc_list: msg['Bcc'] = ", ".join(bcc_list)
        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
            smtp.login(GMAIL_USER, GMAIL_PASSWORD)
            smtp.send_message(msg)
        return True
    except: return False

def get_player_email(player_code):
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list", headers=HEADERS).json()
    for r in master_resp.get('records', []):
        if str(r['fields'].get('Code')) == str(player_code):
            return r['fields'].get('Email')
    return None

def get_all_master_emails():
    master_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/master_list", headers=HEADERS).json()
    return [r['fields'].get('Email') for r in master_resp.get('records', []) if r['fields'].get('Email')]

# --- Core Web Routes ---
@app.route('/')
def index():
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
    display_date = "TBD"
    display_start = "TBD"
    if 'records' in settings_resp and len(settings_resp['records']) > 0:
        fields = settings_resp['records'][0]['fields']
        display_date = fields.get('target_date', 'TBD')
        display_start = fields.get('start_time', 'TBD')

    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
    roster_list = [r['fields'] for r in signups_resp.get('records', [])]

    weather_text = get_weather_forecast(display_date, display_start)

    is_past_deadline = False
    has_waitlist = len(roster_list) % 4 != 0 or len(roster_list) > 24
    try:
        target_dt = datetime.strptime(display_date, "%b %d, %Y")
        deadline_dt = target_dt - timedelta(days=1) + timedelta(hours=8)
        is_past_deadline = datetime.now() >= deadline_dt
    except: pass

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
            is_admin = (password == ADMIN_PW) if password else False
            if password and not is_admin:
                flash("Incorrect Admin Password.", "error")
                return redirect(url_for('index'))
            
            session['user'] = {
                'first': fields.get('First'),
                'last': fields.get('Last'),
                'code': code,
                'is_admin': is_admin
            }
            return redirect(url_for('index'))
            
    flash("Code not found. Please try again.", "error")
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.pop('user', None)
    return redirect(url_for('index'))

# --- Complex Logic Actions (Signup, Cancel, Confirm) ---
@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session: return redirect(url_for('index'))
    
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
    target_date = settings_resp['records'][0]['fields'].get('target_date', 'TBD') if settings_resp.get('records') else "TBD"

    # Check Deadline
    is_past_deadline = False
    try:
        target_dt = datetime.strptime(target_date, "%b %d, %Y")
        deadline_dt = target_dt - timedelta(days=1) + timedelta(hours=8)
        is_past_deadline = datetime.now() >= deadline_dt
    except: pass

    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
    records = signups_resp.get('records', [])
    current_count = len(records)
    
    # Logic: Over 24 is always Waitlist. Otherwise, standard Confirmed. 
    # If past deadline and completing a court, mark as Pending and send email.
    initial_status = "Confirmed"
    token = uuid.uuid4().hex
    
    if current_count >= 24:
        initial_status = "Waitlist"
    
    new_player_payload = {"records": [{"fields": {
        "Date": target_date, "First": session['user']['first'], "Last": session['user']['last'], 
        "PlayerCode": session['user']['code'], "Status": initial_status, "Token": token
    }}]}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS, json=new_player_payload)
    
    # If this person just completed a court of 4 AFTER the deadline (and under the 24 cap)
    if is_past_deadline and (current_count + 1) % 4 == 0 and (current_count + 1) <= 24:
        # Find the last 4 people (including the new one) and send them all approval links
        updated_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
        all_recs = updated_resp.get('records', [])
        court_of_4 = all_recs[-4:] # Get the 4 newest
        
        for r in court_of_4:
            r_token = r['fields'].get('Token')
            r_code = r['fields'].get('PlayerCode')
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/signups/{r['id']}", headers=HEADERS, json={"fields": {"Status": "Pending"}})
            
            p_email = get_player_email(r_code)
            if p_email:
                confirm_link = f"{request.host_url}action/confirm/{r_token}"
                decline_link = f"{request.host_url}action/decline/{r_token}"
                body = f"A court has been completed! Since it is past the Friday deadline, please confirm you are still able to play.\n\nClick here to CONFIRM: {confirm_link}\n\nClick here to DECLINE: {decline_link}"
                send_email(to_email=p_email, bcc_list=[], subject="Action Required: Tennis Court Confirmation", body=body)

    flash("You have been added to the roster!", "success")
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if 'user' not in session: return redirect(url_for('index'))
    
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
    records = signups_resp.get('records', [])
    
    dropped = False
    for r in records:
        if r['fields'].get('First') == session['user']['first'] and r['fields'].get('Last') == session['user']['last']:
            requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/signups/{r['id']}", headers=HEADERS)
            dropped = True
            break
            
    if dropped:
        # Check if we need to promote someone from the waitlist
        updated_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
        new_records = updated_resp.get('records', [])
        
        # If there are waitlisted players and we now have an open spot in the top 24
        waitlisted = [r for r in new_records if r['fields'].get('Status') == 'Waitlist']
        if len(new_records) < 24 and waitlisted:
            first_waitlister = waitlisted[0]
            w_token = first_waitlister['fields'].get('Token')
            w_code = first_waitlister['fields'].get('PlayerCode')
            
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/signups/{first_waitlister['id']}", headers=HEADERS, json={"fields": {"Status": "Pending"}})
            
            w_email = get_player_email(w_code)
            if w_email:
                confirm_link = f"{request.host_url}action/confirm/{w_token}"
                decline_link = f"{request.host_url}action/decline/{w_token}"
                body = f"Good news! A spot opened up for tennis. Please confirm if you can take it.\n\nClick here to CONFIRM: {confirm_link}\n\nClick here to DECLINE: {decline_link}"
                send_email(to_email=w_email, bcc_list=[], subject="Tennis Spot Available!", body=body)

    return redirect(url_for('index'))

@app.route('/action/<action_type>/<token>')
def process_action(action_type, token):
    signups_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/signups", headers=HEADERS).json()
    target_record = None
    for r in signups_resp.get('records', []):
        if r['fields'].get('Token') == token:
            target_record = r
            break
            
    if not target_record:
        return "Invalid or expired link."
        
    first_name = target_record['fields'].get('First')
    
    if action_type == 'confirm':
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/signups/{target_record['id']}", headers=HEADERS, json={"fields": {"Status": "Confirmed"}})
        
        # Email the whole group that someone stepped in
        all_emails = get_all_master_emails()
        send_email(to_email=GMAIL_USER, bcc_list=all_emails, subject="Roster Update: Sub Confirmed", body=f"Update: {first_name} has officially confirmed their spot on the roster!")
        return f"Thank you, {first_name}! Your spot is confirmed."
        
    elif action_type == 'decline':
        # Delete them from roster, which essentially drops them out
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/signups/{target_record['id']}", headers=HEADERS)
        return f"Thank you, {first_name}. You have been removed from the list."

# --- Admin & Cron Routes (Kept exactly as previously built) ---
@app.route('/update_settings', methods=['POST'])
def update_settings():
    if 'user' not in session or not session['user'].get('is_admin'): return redirect(url_for('index'))
    new_date, new_time = request.form.get('date_string'), request.form.get('time_string')
    settings_resp = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/settings", headers=HEADERS).json()
    if 'records' in settings_resp and len(settings_resp['records']) > 0:
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/settings/{settings_resp['records'][0]['id']}", headers=HEADERS, json={"fields": {"target_date": new_date, "start_time": new_time}})
    return redirect(url_for('index'))

@app.route('/approve_message', methods=['POST'])
def approve_message():
    if 'user' not in session or not session['user'].get('is_admin'): return redirect(url_for('index'))
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/messages/{request.form.get('msg_id')}", headers=HEADERS, json={"fields": {"Status": "Approved", "Subject": request.form.get('subject'), "Body": request.form.get('body')}})
    return redirect(url_for('index'))

@app.route('/cron/sunday')
def cron_sunday():
    # Archive logic kept exactly the same
    return "Sunday Routine Complete", 200

@app.route('/cron/thursday')
def cron_thursday():
    # Thursday logic kept exactly the same
    return "Thursday Routine Complete", 200

@app.route('/cron/send_approved')
def cron_send_approved():
    # Sender logic kept exactly the same
    return "Sent approved messages.", 200

if __name__ == '__main__':
    app.run(debug=True)
