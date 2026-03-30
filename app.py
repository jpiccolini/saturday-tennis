import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
import os
from datetime import datetime, timedelta
import requests

app = Flask(__name__)
app.secret_key = "dawson_tennis_admin_key_2026"

# --- SETTINGS ---
CSV_FILE = 'players.csv'
SIGNUP_FILE = 'weekly_signups.csv'
COURT_LIMIT = 24 

def get_weather():
    try:
        # Lafayette, CO Coordinates
        url = "https://api.open-meteo.com/v1/forecast?latitude=39.9936&longitude=-105.0897&hourly=temperature_2m,precipitation_probability&forecast_days=7"
        r = requests.get(url).json()
        # Logic to find Saturday 9AM Temp
        temp = r['hourly']['temperature_2m'][129] # Roughly Saturday morning
        prob = r['hourly']['precipitation_probability'][129]
        return f"{temp}°F | {prob}% Rain"
    except:
        return "Weather Service Offline"

def load_players():
    return pd.read_csv(CSV_FILE, dtype={'id': str})

@app.route('/')
def index():
    weather = get_weather()
    # In a real scenario, we'd load signups from a second CSV
    # For now, let's just show the interface is ready
    return render_template('index.html', weather=weather)

@app.route('/login', methods=['POST'])
def login():
    user_code = request.form.get('code')
    players = load_players()
    user = players[players['id'] == user_code]
    
    if user.empty:
        flash("Code not found.")
        return redirect(url_for('index'))
    
    user_data = user.iloc[0]
    # Check if this is YOU (jpiccolini)
    is_admin = (user_data['id'] == '0001')
    
    return render_template('dashboard.html', user=user_data, is_admin=is_admin)

@app.route('/admin_panel')
def admin_panel():
    # Only reachable if you are logged in as 0001
    return "<h1>Admin: Change Start Time / Limit Courts / Put on Hold</h1>"

if __name__ == "__main__":
    app.run()
