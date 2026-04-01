def get_next_code():
    master = get_airtable_data("Master List")
    codes = []
    for r in master:
        code_val = r['fields'].get('Code')
        try:
            # We only want numbers, and we definitely skip the admin 9999
            c = int(code_val)
            if c < 9999:
                codes.append(c)
        except (ValueError, TypeError):
            continue
    
    if not codes:
        return "1000" # Start here if the list is somehow empty
    
    return str(max(codes) + 1)

@app.route('/approve/<app_id>', methods=['POST'])
def approve(app_id):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    
    # 1. Get Applicant details
    app_rec = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS).json()
    f = app_rec['fields']
    
    # 2. Get the NEXT sequential code
    new_code = get_next_code()
    
    # 3. Add to Master List
    master_data = {"fields": {
        "First": f['First'], 
        "Last": f['Last'], 
        "Code": new_code, 
        "Email": f.get('Email')
    }}
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS, json=master_data)
    
    # 4. Mark applicant as Approved and save the code for Airtable/Gmail to see
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS, 
                   json={"fields": {"Status": "Approved", "Assigned Code": new_code}})
    
    flash(f"Approved {f['First']}! Assigned Code: {new_code}", "success")
    return redirect(url_for('index'))
