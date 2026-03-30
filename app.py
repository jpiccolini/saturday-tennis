import os
import requests
import re
from flask import Flask, render_template, request, session, flash, redirect, url_for
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "tennis_roster_secure_key"

# --- CONFIGURATION ---
GAS_URL = "https://script.google.com/macros/s/AKfycbzFo0KsTgzrGrCYVJ5-A9ymvm_OSdNecEgy9a3cu4MraKUtrTNF_3YQstOp4tYGWu84SQ/exec"
ADMIN_PASSWORD = "jujubeE2" 

def get_next_saturday():
    """Calculates the date of the upcoming Saturday."""
    today = datetime.now()
    days_ahead = (5 - today.weekday() + 7) % 7
    if days_ahead == 0: days_ahead = 7
    return (today + timedelta(days_ahead)).strftime('%b %d, %Y')

def get_weather_forecast(time_str):
    """Fetches weather using Open-Meteo with a User-Agent so Render isn't blocked."""
    print("-> Fetching Weather...")
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=39.9936&longitude=-105.0897&current_weather=true"
        # The fake ID so the API doesn't block us
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"} 
        
        response = requests.get(url, headers=headers, timeout=10)
        data = response.json()
        
        temp_c = data['current_weather']['temperature']
        temp_f = int((temp_c * 9/5) + 32)
        print("-> Weather fetched successfully.")
        return f" | Current Temp: {temp_f}°F"
    except Exception as e:
        print(f"-> Weather failed: {e}")
        return "" # Fails silently so it doesn't break the header

@app.route('/')
def index():
    print("=== LOADING INDEX PAGE ===")
    
    print("-> Fetching Schedule from Google...")
    try:
        sched_resp = requests.get(f"{GAS_URL}?action=getSchedule", timeout=15).json()
        raw_time = sched_resp.get('startTime', '8:45 AM')
        time_match = re.search(r'(\d{1,2}:\d{2}\s*(?:AM|PM)?)', str(raw_time), re.IGNORECASE)
        display_start = time_match.group(1).upper() if time_match else "8:45 AM"
    except Exception as e:
        print(f"-> Schedule fetch failed: {e}")
        display_start = "8:45 AM"

    print("-> Fetching Roster from Google...")
    try:
        roster_resp = requests.get(f"{GAS_URL}?action=getPlayers", timeout=15).json()
    except Exception as e:
        print(f"-> Roster fetch failed: {e}")
        roster_resp = []

    weather_text = get_weather_forecast(display_start)

    return render_template('index.html', 
                           weather=weather_text, 
                           start_time=display_start,
                           target_date=get_next_saturday(),
                           roster=roster_resp)

@app.route('/validate', methods=['GET', 'POST'])
def validate():
    if request.method == 'GET':
        return redirect(url_for('index'))

    code = request.form.get('code')
    password = request.form.get('password')

    try:
        response = requests.get(f"{GAS_URL}?action=validateCode&code={code}", timeout=15)
        
        # If Google returns an HTML login page instead of data, catch it here:
        if "text/html" in response.headers.get('content-type', '').lower():
            flash("Google blocked the connection. Set 'Who has access' to 'Anyone'.", "error")
            return redirect(url_for('index'))
            
        data = response.json()

        if data.get('found'):
            if str(code) == "9999":
                if password == ADMIN_PASSWORD:
                    session['user'] = {'first': data['first'], 'last': data['last'], 'is_admin': True}
                    flash("Admin Access Granted", "success")
                else:
                    flash("Incorrect Admin Password", "error")
            else:
                session['user'] = {'first': data['first'], 'last': data['last'], 'is_admin': False}
        else:
            flash("Invalid Player Code", "error")
            
    except Exception as e:
        print(f"-> EXCEPTION in validate: {str(e)}")
        flash("Google connection timed out. Please try again.", "error")
    
    return redirect(url_for('index'))

# ... (keep your signup, update_time, and logout routes the exact same) ...
@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session: return redirect(url_for('index'))
    try: requests.post(GAS_URL, json={"action": "signup", "date": get_next_saturday(), "first": session['user']['first'], "last": session['user']['last']}, timeout=15)
    except: pass
    return redirect(url_for('index'))

@app.route('/update_time', methods=['POST'])
def update_time():
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    try: requests.post(GAS_URL, json={"action": "updateSchedule", "hour": request.form.get('time_string')}, timeout=15)
    except: pass
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)
