import pandas as pd
from flask import Flask, render_template, request, redirect, url_for, flash
import os
from datetime import datetime

app = Flask(__name__)
app.secret_key = "dawson_tennis_secret"

# --- CONFIGURATION ---
CSV_FILE = 'players.csv'
SIGNUP_FILE = 'weekly_signups.csv'
COURT_LIMIT = 24  # 6 courts * 4 people

def get_weather():
    # Placeholder for Open-Meteo API logic for Lafayette, CO
    return "Sunny, 54°F (Saturday Forecast)"

def load_players():
    return pd.read_csv(CSV_FILE, dtype={'id': str})

def save_players(df):
    df.to_csv(CSV_FILE, index=False)

@app.route('/')
def index():
    weather = get_weather()
    # Logic to load current signups and display courts 1-6
    return render_template('index.html', weather=weather)

@app.route('/login', methods=['POST'])
def login():
    user_code = request.form.get('code')
    players = load_players()
    user = players[players['id'] == user_code]
    
    if user.empty:
        flash("Code not found. Please request access if new.")
        return redirect(url_for('index'))
    
    return render_template('dashboard.html', user=user.iloc[0])

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user_id = request.form.get('id')
    new_first = request.form.get('first')
    new_last = request.form.get('last')
    new_email = request.form.get('email')
    new_backup = request.form.get('backup_email')
    new_cell = request.form.get('cell')
    
    players = load_players()
    idx = players.index[players['id'] == user_id].tolist()[0]
    
    players.at[idx, 'first'] = new_first
    players.at[idx, 'last'] = new_last
    players.at[idx, 'email'] = new_email
    players.at[idx, 'backup_email'] = new_backup
    players.at[idx, 'cell'] = new_cell
    
    save_players(players)
    flash("Profile updated successfully!")
    return redirect(url_for('index'))

@app.route('/join', methods=['POST'])
def join_week():
    user_id = request.form.get('id')
    # Logic: Check if Friday lock is active
    # Logic: Append to weekly_signups.csv if not already there
    # Jim (0001) is always auto-inserted by the Monday script
    flash("You are signed up!")
    return redirect(url_for('index'))

if __name__ == "__main__":
    app.run(debug=True)
