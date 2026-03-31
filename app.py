import os
import requests
import re
from flask import Flask, render_template, request, session, flash, redirect, url_for
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "tennis_roster_secure_key"

# --- AIRTABLE CONFIGURATION ---
# We use os.environ.get so GitHub doesn't see the password!
AIRTABLE_PAT = os.environ.get("AIRTABLE_PAT")
BASE_ID = "appEC9INt2PRYewNj"

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_PAT}",
    "Content-Type": "application/json"
}
ADMIN_PASSWORD = "jujubeE2" 

def get_next_saturday():
    today = datetime.now()
    days_ahead = (5 - today.weekday() + 7) % 7
    if days_ahead == 0: days_ahead = 7
    return (today + timedelta(days_ahead)).strftime('%b %d, %Y')

def get_weather_forecast(time_str):
    print("-> Fetching Weather...")
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=39.9936&longitude=-105.0897&current_weather=true"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"} 
        response = requests.get(url, headers=headers, timeout=10).json()
        temp_c = response['current_weather']['temperature']
        temp_f = int((temp_c * 9/5) + 32)
        return f" | Current Temp: {temp_f}°F"
    except Exception as e:
        print(f"-> Weather failed: {e}")
        return ""

@app.route('/')
def index():
    print("=== LOADING INDEX PAGE ===")
    
    # 1. Fetch Schedule from Airtable
    print("-> Fetching Schedule from Airtable...")
    display_start = "8:45 AM"
    try:
        url = f"https://api.airtable.com/v0/{BASE_ID}/settings?filterByFormula=Setting='StartTime'"
        response = requests.get(url, headers=HEADERS, timeout=10).json()
        if 'records' in response and len(response['records']) > 0:
            raw_time = response['records'][0]['fields'].get('Value', '8:45 AM')
            time_match = re.search(r'(\d{1,2}:\d{2}\s*(?:AM|PM)?)', str(raw_time), re.IGNORECASE)
            display_start = time_match.group(1).upper() if time_match else "8:45 AM"
    except Exception as e:
        print(f"-> Schedule fetch failed: {e}")

    # 2. Fetch Roster from Airtable
    print("-> Fetching Roster from Airtable...")
    roster_list = []
    try:
        url = f"https://api.airtable.com/v0/{BASE_ID}/signups"
        response = requests.get(url, headers=HEADERS, timeout=10).json()
        if 'records' in response:
            for record in response['records']:
                fields = record.get('fields', {})
                if 'First' in fields and 'Last' in fields:
                    roster_list.append({
                        "first": fields['First'],
                        "last": fields['Last']
                    })
    except Exception as e:
        print(f"-> Roster fetch failed: {e}")

    weather_text = get_weather_forecast(display_start)

    return render_template('index.html', 
                           weather=weather_text, 
                           start_time=display_start,
                           target_date=get_next_saturday(),
                           roster=roster_list)

@app.route('/validate', methods=['GET', 'POST'])
def validate():
    if request.method == 'GET':
        return redirect(url_for('index'))

    code = request.form.get('code', '').strip()
    password = request.form.get('password', '')

    print(f"-> Validating code: {code} via Airtable")
    try:
        url = f"https://api.airtable.com/v0/{BASE_ID}/master_list?filterByFormula=Code='{code}'"
        response = requests.get(url, headers=HEADERS, timeout=10).json()
        
        if 'records' in response and len(response['records']) > 0:
            user_data = response['records'][0]['fields']
            
            if str(code) == "9999":
                if password == ADMIN_PASSWORD:
                    session['user'] = {'first': user_data.get('First', 'Admin'), 'last': user_data.get('Last', 'User'), 'is_admin': True}
                    flash("Admin Access Granted", "success")
                else:
                    flash("Incorrect Admin Password", "error")
            else:
                session['user'] = {'first': user_data.get('First', 'Unknown'), 'last': user_data.get('Last', 'Unknown'), 'is_admin': False}
        else:
            flash("Invalid Player Code", "error")
            
    except Exception as e:
        print(f"-> EXCEPTION in validate: {e}")
        flash("Database connection error. Please try again.", "error")
    
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session: 
        return redirect(url_for('index'))
    try:
        url = f"https://api.airtable.com/v0/{BASE_ID}/signups"
        payload = {
            "records": [{
                "fields": {
                    "Date": get_next_saturday(),
                    "First": session['user']['first'],
                    "Last": session['user']['last']
                }
            }]
        }
        requests.post(url, headers=HEADERS, json=payload, timeout=10)
        flash("Successfully signed up!", "success")
    except Exception as e:
        print(f"-> Signup failed: {e}")
        flash("Signup failed.", "error")
    return redirect(url_for('index'))

@app.route('/update_time', methods=['POST'])
def update_time():
    if not session.get('user', {}).get('is_admin'): 
        return redirect(url_for('index'))
    
    new_time = request.form.get('time_string')
    try:
        # Step 1: Find the specific record ID for the StartTime setting
        get_url = f"https://api.airtable.com/v0/{BASE_ID}/settings?filterByFormula=Setting='StartTime'"
        get_resp = requests.get(get_url, headers=HEADERS, timeout=10).json()
        
        if 'records' in get_resp and len(get_resp['records']) > 0:
            record_id = get_resp['records'][0]['id']
            
            # Step 2: Update that specific record
            patch_url = f"https://api.airtable.com/v0/{BASE_ID}/settings/{record_id}"
            payload = {"fields": {"Value": new_time}}
            requests.patch(patch_url, headers=HEADERS, json=payload, timeout=10)
            flash("Start time updated!", "success")
        else:
            flash("Settings row not found in Airtable.", "error")
    except Exception as e:
        print(f"-> Update time failed: {e}")
        flash("Failed to update.", "error")
        
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
