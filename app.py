# --- UPDATE IN app.py ---

@app.route('/')
def index():
    # ... (Keep existing settings/weather logic) ...

    # Fetch Master List for lookup (Strikes and Paused status)
    master_recs = get_airtable_data("Master List")
    strike_map = {str(m['fields'].get('Code')): m['fields'].get('Strikes', 0) for m in master_recs}
    paused_map = {str(m['fields'].get('Code')): m['fields'].get('Paused', False) for m in master_recs}

    signup_recs = get_airtable_data("Signups", sort_field="Created Time")
    
    # NEW SORT LOGIC: 2 Strikes moves you to the bottom
    # We create two lists: Normal players and "Back of the line" (2+ strikes)
    normal_signups = []
    penalized_signups = []

    for r in signup_recs:
        p_code = str(r['fields'].get('Player Code'))
        strikes = strike_map.get(p_code, 0)
        
        if strikes >= 2:
            penalized_signups.append(r)
        else:
            normal_signups.append(r)

    # Recombine: Normal first, then penalized
    ordered_recs = normal_signups + penalized_signups
    
    roster = []
    total_signups = len(ordered_recs)
    complete_courts = min(total_signups, 24) // 4
    playing_cutoff = complete_courts * 4
    waitlist_count = total_signups - playing_cutoff

    user_on_roster, waitlist_pos = False, 0
    curr_user = session.get('user')
    user_status = None
    pending_sub_offer = False

    for i, r in enumerate(ordered_recs):
        fields = r['fields']; fields['id'] = r['id']
        # Add strike info to the roster object for the Admin to see
        fields['strikes'] = strike_map.get(str(fields.get('Player Code')), 0)
        roster.append(fields)
        
        if curr_user:
            if str(fields.get('Player Code')) == str(curr_user.get('code')):
                user_on_roster = True
                user_status = fields.get('Label')
                if i >= playing_cutoff: waitlist_pos = i - playing_cutoff + 1
            if str(fields.get('Sub Offer')) == str(curr_user.get('code')):
                pending_sub_offer = True

    # ... (Keep existing applicants/logs logic) ...

    return render_template('index.html', roster=roster, ...) # Pass all variables as before

@app.route('/signup', methods=['POST'])
def signup():
    if not session.get('user'): return redirect(url_for('index'))
    
    # Check if Paused
    player_code = str(session['user']['code'])
    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{player_code}'")
    if m_recs and m_recs[0]['fields'].get('Paused'):
        flash("🚫 Your account is currently paused due to strikes. Please contact Jim to resolve.", "danger")
        return redirect(url_for('index'))

    data = {"fields": {"First": session['user']['first'], "Last": session['user']['last'], "Player Code": player_code}}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=data)
    log_activity(f"{session['user']['first']} {session['user']['last']}", "Signed Up")
    return redirect(url_for('index'))

@app.route('/attendance/<code_val>', methods=['POST'])
def attendance(code_val):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    
    status = request.form.get('status') # 'Played', 'Late', or 'No Show'
    note = request.form.get('note', '')
    strike_inc = 0
    
    if status == 'Late': strike_inc = 1
    elif status == 'No Show': strike_inc = 2
    
    # 1. Update Master List Strikes
    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{code_val}'")
    if m_recs:
        m_id = m_recs[0]['id']
        curr_strikes = m_recs[0]['fields'].get('Strikes', 0)
        new_strikes = curr_strikes + strike_inc
        
        update_fields = {"Strikes": new_strikes}
        if new_strikes >= 3:
            update_fields["Paused"] = True
            
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{m_id}", headers=HEADERS, json={"fields": update_fields})

    # 2. Log to Archive with Date and Note
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, 
                  json={"fields": {
                      "Player Code": str(code_val), 
                      "Attendance": status, 
                      "Date": dt.datetime.now().strftime("%Y-%m-%d"),
                      "Notes": note
                  }})
    
    flash(f"Recorded {status} for player {code_val}. Notes added.", "info")
    return redirect(url_for('index'))
