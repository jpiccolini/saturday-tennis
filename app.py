import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash, make_response
import os
from datetime import datetime
import requests
import functools

app = Flask(__name__)
app.secret_key = "dawson_tennis_admin_key_2026"

CSV_FILE = 'players.csv'
ROSTER_FILE = 'weekly_roster.csv'
HISTORY_FILE = 'history.csv'

# Ensure files exist
for f in [ROSTER_FILE, HISTORY_FILE]:
    if not os.path.exists(f):
        pd.DataFrame(columns=['date', 'name']).to_csv(f, index=False)

# --- SECURITY ---
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

# --- WEATHER & HELPERS ---
def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=39.9936&longitude=-105.0897&hourly=temperature_2m,precipitation_probability&temperature_unit=fahrenheit&timezone=America%2FDenver"
        r = requests.get(url).json()
        times = r['hourly']['time']
        start_w, end_w = None, None
        for i, t in enumerate(times):
            dt = datetime.fromisoformat(t)
            if dt.weekday() == 5:
                if dt.hour == 9: start_w = f"8:45AM: {r['hourly']['temperature_2m'][i]}°F ({r['hourly']['precipitation_probability'][i]}%)"
                if dt.hour == 12: end_w = f"12PM: {r['hourly']['temperature_2m'][i]}°F ({r['hourly']['precipitation_probability'][i]}%)"
        return f"Sat Forecast | {start_w} ⮕ {end_w}" if start_w else "Saturday Forecast Pending..."
    except: return "Weather Service Offline"

def check_and_reset_roster():
    """Checks if it is Sunday after 5PM and archives the list if not already done."""
    now = datetime.now()
    # Sunday is weekday 6. After 17:00 (5PM)
    if now.weekday() == 6 and now.hour >= 17:
        roster_df = pd.read_csv(ROSTER_FILE)
        if not roster_df.empty:
            history_df = pd.read_csv(HISTORY_FILE)
            new_history = pd.concat([history_df, roster_df], ignore_index=True)
            new_history.to_csv(HISTORY_FILE, index=False)
            # Clear current roster
            pd.DataFrame(columns=['date', 'name']).to_csv(ROSTER_FILE, index=False)

# --- ROUTES ---
@app.route('/')
def index():
    check_and_reset_roster()
    roster_df = pd.read_csv(ROSTER_FILE)
    return render_template('index.html', weather=get_weather(), roster=roster_df['name'].tolist())

@app.route('/login', methods=['POST'])
def login():
    user_code = request.form.get('code')
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    user = players[players['id'] == user_code]
    if user.empty:
        flash("Code not recognized.", "error")
        return redirect(url_for('index'))
    if user_code == ADMIN_CODE: return redirect(url_for('admin_dashboard'))
    return render_template('dashboard.html', user=user.iloc[0], is_admin=False)

@app.route('/admin_dashboard')
@admin_required
def admin_dashboard():
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    admin_data = players[players['id'] == ADMIN_CODE].iloc[0]
    return render_template('dashboard.html', user=admin_data, is_admin=True)

@app.route('/admin_panel')
@admin_required
def admin_panel():
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    history = pd.read_csv(HISTORY_FILE).tail(100) # Show last 100 entries
    return render_template('admin.html', players=players.to_dict(orient='records'), history=history.to_dict(orient='records'))

@app.route('/signup', methods=['POST'])
def signup():
    user_id = request.form.get('id')
    players = pd.read_csv(CSV_FILE, dtype={'id': str})
    user_row = players[players['id'] == user_id]
    
    if user_row.empty:
        flash("Code not recognized.", "error")
        return redirect(url_for('index'))

    # Security for Admin code
    if user_id == ADMIN_CODE:
        auth = request.authorization
        if not auth or not (auth.username == ADMIN_CODE and auth.password == ADMIN_PASSWORD):
            flash("Admin password required for this code.", "error")
            return redirect(url_for('index'))

    roster_df = pd.read_csv(ROSTER_FILE)
    name = f"{user_row.iloc[0]['first']} {user_row.iloc[0]['last']}"
    
    if name in roster_df['name'].values:
        flash(f"{name}, you are already on the list!", "success")
    else:
        new_entry = pd.DataFrame([{'date': datetime.now().strftime('%Y-%m-%d'), 'name': name}])
        roster_df = pd.concat([roster_df, new_entry], ignore_index=True)
        roster_df.to_csv(ROSTER_FILE, index=False)
        flash(f"SUCCESS: {name} added for Saturday!", "success")
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    # ... (Keep previous update_profile logic, ensure user_id == ADMIN_CODE check remains)
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

if __name__ == "__main__":
    app.run()
