import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, make_response
import os
from datetime import datetime
import requests
import functools

app = Flask(__name__)
app.secret_key = "dawson_tennis_admin_key_2026"

# --- CONFIGURATION ---
# PASTE YOUR FULL https://.../exec URL HERE
GSHEET_API_URL = "https://script.google.com/macros/s/AKfycby9j5oZGcXUt237ObFs3KXLBJBMI9l9XhpoTIQnfEmAfWjaUqBUan4UhVDkotyr4oRYlQ/exec"
CSV_FILE = 'players.csv'
ADMIN_CODE = '0001'
ADMIN_PASSWORD = 'ChangeMeSoon'

# --- SECURITY ---
def admin_required(f):
    @functools.wraps(f)
    def decorated_function(*args, **kwargs):
        auth = request.authorization
        if not auth or not (auth.username == ADMIN_CODE and auth.password == ADMIN_PASSWORD):
            return make_response('<h1>Admin Login Required</h1>', 401, {'WWW-Authenticate': 'Basic realm="Login Required"'})
        return f(*args, **kwargs)
    return decorated_function

def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=39.9936&longitude=-105.0897&hourly=temperature_2m,precipitation_probability&temperature_unit=fahrenheit&timezone=America%2FDenver"
        r = requests.get(url).json()
        return f"Sat Forecast | 8:45AM: {r['hourly']['temperature_2m'][129]}°F"
    except: return "Weather Service Offline"

# --- ROUTES ---
@app.route('/')
def index():
    try:
        r = requests.get(f"{GSHEET_API_URL}?action=getRoster", timeout=5)
        current_roster = r.json()
    except:
        current_roster = ["Error connecting to Google Sheets"]
    return render_template('index.html', weather=get_weather(), roster=current_roster)

@app.route('/login', methods=['POST'])
def login():
    user_code = request.form.get('code')
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    user_row = players[players['id'] == user_code]
    
    if user_row.empty:
        flash("Code not recognized.", "error")
        return redirect(url_for('index'))
    
    if user_code == ADMIN_CODE:
        return redirect(url_for('admin_dashboard'))
    
    return render_template('dashboard.html', user=user_row.iloc[0], is_admin=False)

@app.route('/admin_dashboard')
@admin_required
def admin_dashboard():
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    admin_data = players[players['id'] == ADMIN_CODE].iloc[0]
    return render_template('dashboard.html', user=admin_data, is_admin=True)

@app.route('/signup', methods=['POST'])
def signup():
    user_id = request.form.get('id')
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    user_row = players[players['id'] == user_id]
    
    if user_row.empty:
        flash("Code not recognized.", "error")
        return redirect(url_for('index'))

    name = f"{user_row.iloc[0]['first']} {user_row.iloc[0]['last']}"
    
    # Send to Google
    try:
        payload = {'action': 'signup', 'name': name}
        r = requests.post(GSHEET_API_URL, json=payload, timeout=5)
        if "already" in r.text:
            flash(f"{name}, you are already on the list!", "success")
        else:
            flash(f"SUCCESS: {name} added for Saturday!", "success")
    except:
        flash("Database Error: Could not connect to Google Sheets.", "error")
        
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user_id = request.form.get('id')
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    mask = players['id'] == user_id
    if mask.any():
        players.loc[mask, 'email'] = request.form.get('email')
        players.loc[mask, 'backup_email'] = request.form.get('backup_email')
        players.loc[mask, 'cell'] = request.form.get('cell')
        players.to_csv(CSV_FILE, index=False)
        flash("Profile updated!", "success")
    return redirect(url_for('index'))

@app.route('/admin_panel')
@admin_required
def admin_panel():
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    try:
        r = requests.get(f"{GSHEET_API_URL}?action=getHistory", timeout=5)
        history = r.json()
    except: history = []
    return render_template('admin.html', players=players.to_dict(orient='records'), history=history)

if __name__ == "__main__":
    app.run()
