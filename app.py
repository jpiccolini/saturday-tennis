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
GSHEET_API_URL = "https://script.google.com/macros/s/AKfycbx_CUQsBynGKV6WvhQ3aAjjAxHR8zXnUeHXbNtIFc5TT4AsEaR3CPNaEdMRHxTsD0puLQ/exec"
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
        r = requests.get(f"{GSHEET_API_URL}?action=getRoster", timeout=10)
        current_roster = r.json()
    except: current_roster = []
    return render_template('index.html', weather=get_weather(), roster=current_roster)

@app.route('/login', methods=['POST'])
def login():
    user_code = request.form.get('code')
    r = requests.get(f"{GSHEET_API_URL}?action=validateCode&code={user_code}", timeout=10).json()
    
    if not r['found']:
        flash("Code not recognized. Please request access below.", "error")
        return redirect(url_for('index'))
    
    if user_code == ADMIN_CODE: return redirect(url_for('admin_dashboard'))
    
    user_data = {'id': user_code, 'first': r['first'], 'last': r['last'], 'email': r['email'], 'cell': r['cell']}
    return render_template('dashboard.html', user=user_data, is_admin=False)

@app.route('/request_access', methods=['POST'])
def request_access():
    payload = {
        'action': 'request_access',
        'id': request.form.get('id'),
        'first': request.form.get('first'),
        'last': request.form.get('last'),
        'email': request.form.get('email'),
        'cell': request.form.get('cell')
    }
    try:
        requests.post(GSHEET_API_URL, json=payload, timeout=10)
        flash("Request sent! Admin will notify you once approved.", "success")
    except: flash("Database Error.", "error")
    return redirect(url_for('index'))

@app.route('/signup', methods=['POST'])
def signup():
    user_id = request.form.get('id')
    r = requests.get(f"{GSHEET_API_URL}?action=validateCode&code={user_id}", timeout=10).json()
    name = f"{r['first']} {r['last']}"
    requests.post(GSHEET_API_URL, json={'action': 'signup', 'name': name}, timeout=10)
    flash(f"SUCCESS: {name} added for Saturday!", "success")
    return redirect(url_for('index'))

@app.route('/admin_dashboard')
@admin_required
def admin_dashboard():
    return render_template('dashboard.html', user={'first': 'Admin'}, is_admin=True)

@app.route('/admin_panel')
@admin_required
def admin_panel():
    try:
        p_req = requests.get(f"{GSHEET_API_URL}?action=getPending", timeout=10).json()
        hist = requests.get(f"{GSHEET_API_URL}?action=getHistory", timeout=10).json()
    except: p_req, hist = [], []
    return render_template('admin.html', pending_list=p_req, history=hist)

@app.route('/approve_player/<player_id>')
@admin_required
def approve_player(player_id):
    requests.get(f"{GSHEET_API_URL}?action=approvePlayer&id={player_id}", timeout=10)
    flash(f"Player {player_id} approved!", "success")
    return redirect(url_for('admin_panel'))

if __name__ == "__main__":
    app.run()
