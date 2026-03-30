import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, make_response
import os
from datetime import datetime
import requests
import functools

app = Flask(__name__)
app.secret_key = "dawson_tennis_admin_key_2026"

CSV_FILE = 'players.csv'
weekly_roster = [] 

# --- SECURITY CONFIGURATION ---
ADMIN_CODE = '0001'
ADMIN_PASSWORD = 'ChangeMeSoon' # <--- BE SURE TO CHANGE THIS

def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=39.9936&longitude=-105.0897&hourly=temperature_2m,precipitation_probability&temperature_unit=fahrenheit&timezone=America%2FDenver"
        r = requests.get(url).json()
        times = r['hourly']['time']
        start_w, end_w = None, None
        for i, t in enumerate(times):
            dt = datetime.fromisoformat(t)
            if dt.weekday() == 5:
                if dt.hour == 9:
                    start_w = f"8:45AM: {r['hourly']['temperature_2m'][i]}°F ({r['hourly']['precipitation_probability'][i]}%)"
                if dt.hour == 12:
                    end_w = f"12PM: {r['hourly']['temperature_2m'][i]}°F ({r['hourly']['precipitation_probability'][i]}%)"
        if start_w and end_w:
            return f"Sat Forecast | {start_w} ⮕ {end_w}"
        return "Saturday Forecast Pending..."
    except:
        return "Weather Service Offline"

def load_players():
    return pd.read_csv(CSV_FILE, dtype={'id': str})

# THE SECURITY GUARD
def admin_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        if not auth or not (auth.username == ADMIN_CODE and auth.password == ADMIN_PASSWORD):
            return make_response('<h1>Admin Login Required</h1>', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    return render_template('index.html', weather=get_weather(), roster=weekly_roster)

@app.route('/login', methods=['POST'])
def login():
    user_code = request.form.get('code')
    players = load_players()
    user_row = players[players['id'] == user_code]
    
    if user_row.empty:
        flash("Code not recognized.", "error")
        return redirect(url_for('index'))
    
    user_data = user_row.iloc[0]
    
    # If someone tries to log in as 0001, we force them to the password-protected route
    if user_code == ADMIN_CODE:
        return redirect(url_for('admin_dashboard'))
    
    return render_template('dashboard.html', user=user_data, is_admin=False)

@app.route('/admin_dashboard')
@admin_required
def admin_dashboard():
    players = load_players()
    admin_data = players[players['id'] == ADMIN_CODE].iloc[0]
    return render_template('dashboard.html', user=admin_data, is_admin=True)

@app.route('/admin_panel')
@admin_required
def admin_panel():
    players = load_players()
    return render_template('admin.html', players=players.to_dict(orient='records'), roster=weekly_roster)

@app.route('/signup', methods=['POST'])
def signup():
    user_id = request.form.get('id')
    # Extra protection: Don't let anyone sign up code 0001 without being the admin
    if user_id == ADMIN_CODE:
        auth = request.authorization
        if not auth or not (auth.username == ADMIN_CODE and auth.password == ADMIN_PASSWORD):
             flash("Only the admin can sign up for this code.", "error")
             return redirect(url_for('index'))

    players = load_players()
    user_row = players[players['id'] == user_id]
    if user_row.empty:
        flash("Code not recognized.", "error")
        return redirect(url_for('index'))
    
    player_name = f"{user_row.iloc[0]['first']} {user_row.iloc[0]['last']}"
    if player_name in weekly_roster:
        flash(f"Note: {player_name}, you are already on the roster!", "success")
    else:
        weekly_roster.append(player_name)
        flash(f"SUCCESS: {player_name} added for 8:45 AM Saturday!", "success")
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user_id = request.form.get('id')
    # Stop people from editing your profile info!
    if user_id == ADMIN_CODE:
        auth = request.authorization
        if not auth or not (auth.username == ADMIN_CODE and auth.password == ADMIN_PASSWORD):
            return make_response('Unauthorized', 401)

    players = load_players()
    mask = players['id'] == user_id
    if mask.any():
        players.loc[mask, 'email'] = request.form.get('email')
        players.loc[mask, 'backup_email'] = request.form.get('backup_email')
        players.loc[mask, 'cell'] = request.form.get('cell')
        players.to_csv(CSV_FILE, index=False)
        flash("Profile updated successfully!", "success")
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run()
