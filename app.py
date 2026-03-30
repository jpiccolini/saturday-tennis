import os
import requests
from flask import Flask, render_template, request, redirect, url_for, session, jsonify
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "tennis_secret_key"  # You can change this to any random string

# --- CONFIGURATION ---
# REPLACE THE URL BELOW WITH YOUR ACTUAL GOOGLE SCRIPT URL
GSHEET_API_URL = "https://script.google.com/macros/s/AKfycbx_CUQsBynGKV6WvhQ3aAjjAxHR8zXnUeHXbNtIFc5TT4AsEaR3CPNaEdMRHxTsD0puLQ/exec"

def get_next_saturday():
    today = datetime.now()
    days_ahead = 5 - today.weekday()
    if days_ahead <= 0: # Target next Saturday if today is Sat/Sun
        days_ahead += 7
    return (today + timedelta(days=days_ahead)).strftime('%m/%d/%Y')

@app.route('/')
def index():
    next_sat = get_next_saturday()
    # Fetch players from Google Sheet
    try:
        response = requests.get(f"{GSHEET_API_URL}?action=getPlayers")
        all_players = response.json()
        # Filter only for the upcoming Saturday
        players = [p['name'] for p in all_players if p['date'] == next_sat]
    except:
        players = []

    return render_template('index.html', 
                           players=players, 
                           next_saturday=next_sat,
                           logged_in='player_id' in session,
                           player_name=session.get('player_name', ''),
                           is_admin=session.get('is_admin', False))

@app.route('/login', methods=['POST'])
def login():
    code = request.form.get('player_code')
    if not code:
        return redirect(url_for('index'))

    try:
        response = requests.get(f"{GSHEET_API_URL}?action=validateCode&code={code}")
        data = response.json()

        if data.get('found'):
            session['player_id'] = code
            session['player_name'] = f"{data.get('first')} {data.get('last')}"
            session['is_admin'] = (str(code) == "0001")
            return redirect(url_for('index'))
        else:
            return "Invalid Player Code. Please check your ID and try again.", 401
    except Exception as e:
        return f"Connection Error: {str(e)}", 500

@app.route('/signup', methods=['POST'])
def signup():
    if 'player_id' not in session:
        return redirect(url_for('index'))

    payload = {
        "action": "signup",
        "name": session['player_name'],
        "date": get_next_saturday()
    }
    
    try:
        requests.post(GSHEET_API_URL, json=payload)
    except:
        pass

    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    app.run(debug=True)
