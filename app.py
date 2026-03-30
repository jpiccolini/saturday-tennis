import requests
from flask import Flask, render_template, request, jsonify, session
from datetime import datetime, timedelta

app = Flask(__name__)
app.secret_key = "tennis_roster_secure_key" # Keeps your session data safe

# Use your provided deployment URL
GAS_URL = "https://script.google.com/macros/s/AKfycbxho8NgMSpmKA8afOw43TBsgx46EO79_okFu1H9t76mES2ZSrNBddcCydTXtktQjX2stQ/exec"

def get_next_saturday():
    """Calculates the date of the upcoming Saturday."""
    today = datetime.now()
    # Saturday is weekday 5. (5 - current_weekday) % 7 
    # If today is Saturday, it finds the NEXT Saturday.
    days_ahead = (5 - today.weekday() + 7) % 7
    if days_ahead == 0: days_ahead = 7 
    next_sat = today + timedelta(days_ahead)
    return next_sat.strftime('%Y-%m-%d')

@app.route('/')
def index():
    # We'll calculate the date here so the front-end knows which Saturday we are targeting
    target_date = get_next_saturday()
    return render_template('index.html', target_date=target_date)

@app.route('/validate', methods=['POST'])
def validate():
    code = request.form.get('code')
    if not code:
        return jsonify({"success": False, "message": "No code provided"}), 400

    try:
        # Step 1: Ask Google Script to validate the code
        # We use a GET request for validation as defined in our doGet
        response = requests.get(f"{GAS_URL}?action=validateCode&code={code}")
        data = response.json()

        if data.get('found'):
            # Store player info in the session so they don't have to re-log
            session['user'] = {
                'first': data['first'],
                'last': data['last'],
                'isAdmin': data.get('isAdmin', False) # Logic from our Step 1 script
            }
            return jsonify({"success": True, "user": session['user']})
        
        return jsonify({"success": False, "message": "Invalid Player Code"}), 401

    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/signup', methods=['POST'])
def signup():
    if 'user' not in session:
        return jsonify({"success": False, "message": "Please validate your code first"}), 403

    # The date is passed from the front-end or calculated here
    target_date = get_next_saturday()
    
    # Prepare the payload for Google Script's doPost
    payload = {
        "action": "signup",
        "date": target_date,
        "first": session['user']['first'],
        "last": session['user']['last']
    }

    try:
        # Step 2: POST the data to Google Sheets
        # requests.post handles the 302 redirect automatically
        response = requests.post(GAS_URL, json=payload)
        return jsonify(response.json())
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 500

@app.route('/logout')
def logout():
    session.clear()
    return jsonify({"success": True})

if __name__ == '__main__':
    app.run(debug=True)
