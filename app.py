import requests
import re
from flask import Flask, render_template, request, jsonify, session, flash, redirect, url_for
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "tennis_roster_secure_key"

# --- CONFIGURATION ---
GAS_URL = "https://script.google.com/macros/s/AKfycbyCzNjCIIkWhJlwU-LlOCq1bZhg-B5HW7B6ketU-E8b87OUQjeKKSp5_OrQHa0F5MWPSw/exec"
ADMIN_PASSWORD = "jujubeE2" 

def get_next_saturday():
    """Calculates the date of the upcoming Saturday."""
    today = datetime.now()
    days_ahead = (5 - today.weekday() + 7) % 7
    if days_ahead == 0: days_ahead = 7
    return (today + timedelta(days_ahead)).strftime('%Y-%m-%d')

def get_weather_forecast(time_str):
    """Parses a string like '8:45 AM' to get a weather window."""
    try:
        # Extract the hour from the string (e.g., '8' from '8:45 AM')
        match = re.search(r'(\d+)', str(time_str))
        hour = int(match.group(1)) if match else 9
        
        # Adjust for PM (simple logic for display)
        if "PM" in str(time_str).upper() and hour != 12:
            hour += 12
        
        city = "Lafayette,CO"
        # wttr.in format: %C=Condition, %t=Temperature
        response = requests.get(f"https://wttr.in/{city}?format=%C+%t", timeout=5)
        condition_temp = response.text
        
        # Calculate approximate end time (3 hours later)
        end_hour = (hour + 3)
        end_display = f"{end_hour-12}:00 PM" if end_hour > 12 else f"{end_hour}:00 AM"
        
        return f"Forecast for {time_str}: {condition_temp} (Ends approx {end_display})"
    except:
        return "Weather currently unavailable"

@app.route('/')
def index():
    # 1. Fetch Schedule Time from Google Settings
    try:
        sched_resp = requests.get(f"{GAS_URL}?action=getSchedule", timeout=8).json()
        display_start = sched_resp.get('startTime', '8:45 AM')
    except:
        display_start = "8:45 AM"

    # 2. Fetch Current Roster
    try:
        roster_resp = requests.get(f"{GAS_URL}?action=getPlayers", timeout=8).json()
    except:
        roster_resp = []

    return render_template('index.html', 
                           weather=get_weather_forecast(display_start), 
                           start_time=display_start,
                           target_date=get_next_saturday(),
                           roster=roster_resp)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code')
    password = request.form.get('password')

    try:
        # Request validation from Google Script
        response = requests.get(f"{GAS_URL}?action=validateCode&code={code}", timeout=8)
        data = response.json()

        if data.get('found'):
            # Admin Logic (Code 0001 + Password)
            if str(code) == "0001":
                if password == ADMIN_PASSWORD:
                    session['user'] = {
                        'first': data['first'], 
                        'last': data['last'], 
                        'is_admin': True
                    }
                    flash("Admin Access Granted", "success")
                else:
                    flash("Incorrect Admin Password", "error")
                    return redirect(url_for('index'))
            else:
                # Regular Player Logic (e.g., 1001)
                session['user'] = {
                    'first': data['first'], 
                    'last': data['last'], 
                    'is_admin': False
                }
            
            return redirect(url_for('index'))
        else:
            flash("Invalid Player Code", "error")
            
    except Exception as e:
        print(f"Log Error: {e}")
        flash("Communication with Google failed. Please try again.", "error")
    
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session:
        flash("Please log in first", "error")
        return redirect(url_for('index'))

    payload = {
        "action": "signup",
        "date": get_next_saturday(),
        "first": session['user']['first'],
        "last": session['user']['last']
    }

    try:
        requests.post(GAS_URL, json=payload, timeout=8)
        flash(f"Successfully signed up for {get_next_saturday()}!", "success")
    except:
        flash("Signup failed. Check connection.", "error")

    return redirect(url_for('index'))

@app.route('/update_time', methods=['POST'])
def update_time():
    if not session.get('user', {}).get('is_admin'):
        flash("Unauthorized", "error")
        return redirect(url_for('index'))
    
    new_time = request.form.get('time_string') 
    try:
        requests.post(GAS_URL, json={"action": "updateSchedule", "hour": new_time}, timeout=8)
        flash(f"Start time updated to {new_time}", "success")
    except:
        flash("Failed to update Google Settings", "error")
    
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
