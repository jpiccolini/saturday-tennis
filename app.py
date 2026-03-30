import os
import requests
from flask import Flask, render_template, request, redirect, url_for, session

app = Flask(__name__)
app.secret_key = "tennis_secret_key"

# YOUR ACTUAL URL
GSHEET_API_URL = "https://script.google.com/macros/s/AKfycbwWgcJ9Ij8QJBjOLsriNqUiyjaLEec-TYV7gJ0pAdqmb1yjeqVT70lXrlG6HMJEzWpxpQ/exec"

@app.route('/')
def index():
    players = []
    try:
        response = requests.get(f"{GSHEET_API_URL}?action=getPlayers", timeout=10)
        if response.status_code == 200:
            players = [p.get('name') for p in response.json() if p.get('name')]
    except:
        pass
    return render_template('index.html', players=players, logged_in='player_id' in session)

@app.route('/login', methods=['POST'])
def login():
    code = request.form.get('player_code')
    try:
        # Handling the redirect properly
        response = requests.get(f"{GSHEET_API_URL}?action=validateCode&code={code}", timeout=10, allow_redirects=True)
        data = response.json()
        if data.get('found'):
            session['player_id'] = code
            session['player_name'] = f"{data.get('first', '')} {data.get('last', '')}".strip()
            return redirect(url_for('index'))
    except Exception as e:
        print(f"Login Error: {e}")
    return "Login Failed. Check your code.", 401

@app.route('/signup', methods=['POST'])
def signup():
    if 'player_name' in session:
        requests.post(GSHEET_API_URL, json={"action": "signup", "name": session['player_name']}, timeout=10)
    return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))
