import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, make_response
import os
from datetime import datetime
import requests
import functools

app = Flask(__name__)
app.secret_key = "dawson_tennis_admin_key_2026"

# --- CONFIGURATION ---
# Replace this with the "Web App URL" we get in Step 3
GSHEET_API_URL = "https://script.google.com/macros/s/AKfycby9j5oZGcXUt237ObFs3KXLBJBMI9l9XhpoTIQnfEmAfWjaUqBUan4UhVDkotyr4oRYlQ/exec"
CSV_FILE = 'players.csv'
ADMIN_CODE = '0001'
ADMIN_PASSWORD = 'ChangeMeSoon'

def admin_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        if not auth or not (auth.username == ADMIN_CODE and auth.password == ADMIN_PASSWORD):
            return make_response('Admin Login Required', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated_function

def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=39.9936&longitude=-105.0897&hourly=temperature_2m,precipitation_probability&temperature_unit=fahrenheit&timezone=America%2FDenver"
        r = requests.get(url).json()
        temp = r['hourly']['temperature_2m'][129] # Saturday 9am approx
        prob = r['hourly']['precipitation_probability'][129]
        return f"Sat Forecast | 8:45AM: {temp}°F ({prob}% Precip)"
    except: return "Weather Service Offline"

@app.route('/')
def index():
    # Fetch the live roster from Google Sheets
    try:
        r = requests.get(f"{GSHEET_API_URL}?action=getRoster")
        current_roster = r.json()
    except:
        current_roster = []
    return render_template('index.html', weather=get_weather(), roster=current_roster)

@app.route('/signup', methods=['POST'])
def signup():
    user_id = request.form.get('id')
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    user_row = players[players['id'] == user_id]
    
    if user_row.empty:
        flash("Code not recognized.", "error")
        return redirect(url_for('index'))

    name = f"{user_row.iloc[0]['first']} {user_row.iloc[0]['last']}"
    
    # Send signup to Google Sheets
    payload = {'action': 'signup', 'name': name}
    r = requests.post(GSHEET_API_URL, json=payload)
    
    if "already" in r.text:
        flash(f"{name}, you are already on the list!", "success")
    else:
        flash(f"SUCCESS: {name} added for Saturday!", "success")
    return redirect(url_for('index'))

@app.route('/admin_panel')
@admin_required
def admin_panel():
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    r = requests.get(f"{GSHEET_API_URL}?action=getHistory")
    history = r.json()
    return render_template('admin.html', players=players.to_dict(orient='records'), history=history)

# ... (Keep /login and /update_profile from previous version)
