import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
import os
from datetime import datetime
import requests

app = Flask(__name__)
app.secret_key = "dawson_tennis_admin_key_2026"

CSV_FILE = 'players.csv'

def get_weather():
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=39.9936&longitude=-105.0897&hourly=temperature_2m,precipitation_probability&temperature_unit=fahrenheit&timezone=America%2FDenver"
        r = requests.get(url).json()
        times = r['hourly']['time']
        start_weather, end_weather = None, None
        
        for i, t in enumerate(times):
            dt = datetime.fromisoformat(t)
            # Find Saturday (weekday 5)
            if dt.weekday() == 5:
                if dt.hour == 9:
                    start_weather = f"9AM: {r['hourly']['temperature_2m'][i]}°F ({r['hourly']['precipitation_probability'][i]}%)"
                if dt.hour == 12:
                    end_weather = f"12PM: {r['hourly']['temperature_2m'][i]}°F ({r['hourly']['precipitation_probability'][i]}%)"
        
        if start_weather and end_weather:
            return f"Sat Forecast | {start_weather} ⮕ {end_weather}"
        return "Saturday Forecast Pending..."
    except:
        return "Weather Service Offline"

def load_players():
    return pd.read_csv(CSV_FILE, dtype={'id': str})

@app.route('/')
def index():
    return render_template('index.html', weather=get_weather())

@app.route('/login', methods=['POST'])
def login():
    user_code = request.form.get('code')
    players = load_players()
    user = players[players['id'] == user_code]
    if user.empty:
        flash("Code not found.", "error")
        return redirect(url_for('index'))
    return render_template('dashboard.html', user=user.iloc[0], is_admin=(user_code == '0001'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    # This matches the 'action' in your HTML form
    user_id = request.form.get('id')
    players = load_players()
    mask = players['id'] == user_id
    if mask.any():
        players.loc[mask, 'email'] = request.form.get('email')
        players.loc[mask, 'backup_email'] = request.form.get('backup_email')
        players.loc[mask, 'cell'] = request.form.get('cell')
        players.to_csv(CSV_FILE, index=False)
        flash("Profile updated successfully!", "success")
    
    user_data = players[players['id'] == user_id].iloc[0]
    return render_template('dashboard.html', user=user_data, is_admin=(user_id == '0001'))

if __name__ == "__main__":
    app.run()
