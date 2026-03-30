import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
import os
from datetime import datetime
import requests

app = Flask(__name__)
app.secret_key = "dawson_tennis_admin_key_2026"

# --- SETTINGS ---
CSV_FILE = 'players.csv'

def get_weather():
    try:
        # Coordinates for Lafayette, CO
        url = "https://api.open-meteo.com/v1/forecast?latitude=39.9936&longitude=-105.0897&hourly=temperature_2m,precipitation_probability&temperature_unit=fahrenheit&timezone=America%2FDenver"
        r = requests.get(url).json()
        
        # Hunt for the next Saturday at 9:00 AM
        times = r['hourly']['time']
        for i, t in enumerate(times):
            dt = datetime.fromisoformat(t)
            # weekday() 5 is Saturday, hour 9 is 9 AM
            if dt.weekday() == 5 and dt.hour == 9:
                temp = r['hourly']['temperature_2m'][i]
                prob = r['hourly']['precipitation_probability'][i]
                return f"Sat 9AM: {temp}°F | {prob}% Rain"
        return "Saturday forecast pending..."
    except:
        return "Weather Service Offline"

def load_players():
    return pd.read_csv(CSV_FILE, dtype={'id': str})

def save_players(df):
    df.to_csv(CSV_FILE, index=False)

@app.route('/')
def index():
    weather = get_weather()
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
    is_admin = (user_data['id'] == '0001')
    return render_template('dashboard.html', user=user_data, is_admin=is_admin)

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user_id = request.form.get('id')
    players = load_players()
    
    # Update the row where ID matches
    mask = players['id'] == user_id
    if mask.any():
        players.loc[mask, 'first'] = request.form.get('first')
        players.loc[mask, 'last'] = request.form.get('last')
        players.loc[mask, 'email'] = request.form.get('email')
        players.loc[mask, 'backup_email'] = request.form.get('backup_email')
        players.loc[mask, 'cell'] = request.form.get('cell')
        save_players(players)
        flash("Profile updated successfully!")
    
    # After saving, we need to show the dashboard again
    user_data = players[players['id'] == user_id].iloc[0]
    return render_template('dashboard.html', user=user_data, is_admin=(user_id == '0001'))

if __name__ == "__main__":
    app.run()
