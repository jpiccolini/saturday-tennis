import requests
from flask import Flask, render_template, request, jsonify, session, flash, redirect, url_for
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "tennis_roster_secure_key"

GAS_URL = "https://script.google.com/macros/s/AKfycbxho8NgMSpmKA8afOw43TBsgx46EO79_okFu1H9t76mES2ZSrNBddcCydTXtktQjX2stQ/exec"

def get_next_saturday():
    today = datetime.now()
    days_ahead = (5 - today.weekday() + 7) % 7
    if days_ahead == 0: days_ahead = 7
    return (today + timedelta(days_ahead)).strftime('%Y-%m-%d')

def get_weather_forecast(hour):
    """
    Fetches weather for Lafayette, CO. 
    Flexes based on the 'hour' provided by Google Sheets.
    """
    try:
        # Using wttr.in for a quick, no-auth weather string
        # format: %C=Condition, %t=Temp
        city = "Lafayette,CO"
        response = requests.get(f"https://wttr.in/{city}?format=%C+%t")
        condition_temp = response.text
        
        # Calculate the "Arrival" and "Mid-Play" times
        arrival_time = f"{hour-1}:45 AM" if hour <= 12 else f"{hour-13}:45 PM"
        peak_time = f"{hour+1}:00 AM" if (hour+1) < 12 else f"{(hour+1)-12 if hour+1 > 12 else 12}:00 PM"
        
        return f"Forecast for {arrival_time}: {condition_temp} (Expected peak at {peak_time})"
    except:
        return "Weather currently unavailable"

@app.route('/')
def index():
    # 1. Get the Schedule/Hour from Google
    try:
        sched_resp = requests.get(f"{GAS_URL}?action=getSchedule").json()
        start_hour = int(sched_resp.get('hour', 9))
        display_start = sched_resp.get('startTime', '8:45 AM')
    except:
        start_hour = 9
        display_start = "8:45 AM"

    # 2. Get the Roster
    try:
        roster_resp = requests.get(f"{GAS_URL}?action=getPlayers").json()
    except:
        roster_resp = []

    weather_info = get_weather_forecast(start_hour)
    target_date = get_next_saturday()

    return render_template('index.html', 
                           weather=weather_info, 
                           start_time=display_start,
                           target_date=target_date,
                           roster=roster_resp)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code')
    try:
        response = requests.get(f"{GAS_URL}?action=validateCode&code={code}")
        data = response.json()

        if data.get('found'):
            # Security: Explicitly check for 0001 for Admin rights
            session['user'] = {
                'first': data['first'],
                'last': data['last'],
                'is_admin': (str(code) == "0001")
            }
            return redirect(url_for('index'))
        else:
            flash("Invalid code.", "error")
    except Exception as e:
        flash(f"Server Error: {str(e)}", "error")
    
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session:
        flash("Please log in first.", "error")
        return redirect(url_for('index'))

    payload = {
        "action": "signup",
        "date": get_next_saturday(),
        "first": session['user']['first'],
        "last": session['user']['last']
    }

    try:
        requests.post(GAS_URL, json=payload)
        flash("You are signed up!", "success")
    except:
        flash("Signup failed. Try again.", "error")

    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
