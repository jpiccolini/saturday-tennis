import os
import time
import requests
from flask import Flask, render_template, request

# === SECTION 1: APP INITIALIZATION ===
app = Flask(__name__)

# === SECTION 2: AIRTABLE CONFIGURATION ===
# Grabbing credentials securely from Render's Environment Variables
AIRTABLE_API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("BASE_ID")

HEADERS = {
    "Authorization": f"Bearer {AIRTABLE_API_KEY}",
    "Content-Type": "application/json"
}

# === SECTION 3: DATA CACHING ENGINE (BULLETPROOF) ===
AIRTABLE_CACHE = {}
CACHE_TTL = 30 

def get_airtable_data(table_name, sort_field=None, direction="asc", filter_formula=None):
    current_time = time.time()
    cache_key = f"{table_name}_{sort_field}_{direction}_{filter_formula}"
    
    # Check if we have fresh data in the cache to avoid hitting Airtable entirely
    if cache_key in AIRTABLE_CACHE:
        cached_time, cached_data = AIRTABLE_CACHE[cache_key]
        if current_time - cached_time < CACHE_TTL:
            return cached_data
            
    records = []
    offset = None
    url = f"https://api.airtable.com/v0/{BASE_ID}/{table_name}"
    
    try:
        while True:
            params = {}
            if sort_field:
                params["sort[0][field]"] = sort_field
                params["sort[0][direction]"] = direction
            if filter_formula:
                params["filterByFormula"] = filter_formula
            if offset:
                params["offset"] = offset
                
            # STRICT SPEED LIMIT: 0.33s pause (Max 3 req/sec to stay safely under 5 req/sec limit)
            time.sleep(0.33)
            
            res = requests.get(url, headers=HEADERS, params=params)
            
            # IF WE HIT THE 30-SECOND PENALTY BOX, ACTUALLY WAIT IT OUT
            if res.status_code == 429:
                print(f"🛑 429 Penalty Box on {table_name}. Waiting 31 seconds for lock to clear...")
                time.sleep(31) 
                # Try one more time now that the lock is gone
                res = requests.get(url, headers=HEADERS, params=params)
                
            res.raise_for_status()
            data = res.json()
            records.extend(data.get('records', []))
            
            offset = data.get('offset')
            if not offset:
                break
                
        AIRTABLE_CACHE[cache_key] = (current_time, records)
        return records
    except Exception as e: 
        print(f"Airtable Fetch Error ({table_name}): {e}")
        return []

# === SECTION 4: PRIMARY ROUTES ===
@app.route('/')
def index():
    # Instantly satisfy Render's health checks (both HEAD and GET bots) without hitting Airtable!
    if request.method == 'HEAD' or 'Go-http-client' in request.headers.get('User-Agent', ''):
        return "OK", 200

    # Fetch data using the cached/rate-limited engine
    settings = get_airtable_data("Settings")
    master_list = get_airtable_data("Master List", sort_field="First", direction="asc")
    signups = get_airtable_data("Signups", sort_field="Created Time", direction="asc")
    
    return render_template('index.html', settings=settings, master_list=master_list, signups=signups)

# === SECTION 5: ADDITIONAL ROUTES ===
# (If you have any POST routes for form submissions, signups, or emails, paste them here!)



# === SECTION 6: APP RUNNER ===
if __name__ == '__main__':
    # Bind to Render's dynamic port, or default to 10000
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)
