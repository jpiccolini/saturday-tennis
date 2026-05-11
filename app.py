# ==========================================
# TABLE OF CONTENTS - app.py
# 1. SETUP & CONFIG (Env Vars, Headers)
# 2. UTILITY FUNCTIONS (Email, Logging)
# 3. DATA CACHING ENGINE (With Pagination)
# 4. PRIMARY ROUTES (Index, Login/Logout)
# 5. PLAYER ACTIONS (Signup, Cancel, Subs, Profile)
# 6. ADMIN & GUEST ACTIONS
# 7. CRON / AUTOMATION ROUTES
# ==========================================

import os, requests, smtplib, uuid
from flask import Flask, render_template, request, session, redirect, url_for, flash, jsonify
import datetime as dt
from datetime import timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import time

app = Flask(__name__)
app.secret_key = os.environ.get("FLASK_SECRET", "tennis-secret-123")

# === SECTION 1: SETUP & CONFIG ===
API_KEY = os.environ.get("AIRTABLE_API_KEY")
BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "").strip()
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "jujubeE2")
W_KEY = os.environ.get("WEATHER_API_KEY") # No longer strictly needed for Open-Meteo, kept for legacy
FROM_EMAIL = os.environ.get("FROM_EMAIL") 
GMAIL_PW = os.environ.get("GMAIL_PASSWORD") 
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", FROM_EMAIL) 
SITE_URL = "https://saturday-tennis.onrender.com"

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}

# === SECTION 2: UTILITY FUNCTIONS ===
def log_activity(name, action):
    try:
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Logs", headers=HEADERS, 
                      json={"fields": {"Name": name, "Action": action}})
    except: pass

def send_email(to_emails, subject, html_content, is_multiple=False):
    if not FROM_EMAIL or not GMAIL_PW or not to_emails: return
    if isinstance(to_emails, str): to_emails = [to_emails]
    msg = MIMEMultipart()
    msg['From'] = FROM_EMAIL
    msg['Subject'] = subject
    if is_multiple:
        msg['To'] = FROM_EMAIL 
        recipients = to_emails + [FROM_EMAIL]
    else:
        msg['To'] = to_emails[0]
        recipients = to_emails
    msg.attach(MIMEText(html_content, 'html'))
    try:
        server = smtplib.SMTP('smtp.gmail.com', 587)
        server.starttls()
        server.login(FROM_EMAIL, GMAIL_PW)
        server.sendmail(FROM_EMAIL, recipients, msg.as_string())
        server.quit()
    except Exception as e: print(f"Email Error: {e}")

def find_player_matches(first, last, master_list):
    """Return (exact_matches, near_matches) from Master List.
    Exact = both first and last match (case-insensitive).
    Near  = either first OR last matches, or either starts with the typed string."""
    fl, ll = first.strip().lower(), last.strip().lower()
    exact, near = [], []
    for m in master_list:
        mf = m['fields'].get('First', '').strip().lower()
        mlv = m['fields'].get('Last', '').strip().lower()
        if mf == fl and mlv == ll:
            exact.append(m)
        elif fl and ll and (mf == fl or mlv == ll or mf.startswith(fl) or mlv.startswith(ll)):
            near.append(m)
    return exact, near

def next_player_code(master_list):
    """Return the next unused 4-digit player code as a string."""
    codes = []
    for m in master_list:
        c = str(m['fields'].get('Code', '')).strip()
        if c.endswith('.0'): c = c[:-2]
        if c.isdigit() and 1000 < int(c) < 9000:
            codes.append(int(c))
    return str(max(codes, default=1000) + 1)

def build_court_map(n_courts, group_sizes, overrides, prefix=''):
    """
    Auto-assign logical courts (1..n) to physical courts 1-6.
    Courts 3 & 6 (end of each cluster) are preferred for partial groups (<4 players).
    Courts 1-2, 4-5 (middle of each cluster) are preferred for full groups.
    overrides: {str(logical): int(physical)} from Settings JSON.
    prefix: string prepended to override keys (e.g. 'T_' for Team mode).
    Returns: {logical_int: physical_int}
    """
    if n_courts == 0:
        return {}

    end_courts   = [3, 6]
    mid_courts   = [1, 2, 4, 5]
    all_courts   = [1, 2, 3, 4, 5, 6]

    assignment = {}
    used = set()

    # Pass 1: partial groups get end courts (3, then 6)
    for i in range(n_courts):
        size = group_sizes[i] if i < len(group_sizes) else 4
        if size < 4:
            for c in end_courts:
                if c not in used:
                    assignment[i + 1] = c
                    used.add(c)
                    break

    # Pass 2: full groups fill middle courts first, then whatever's left
    for i in range(n_courts):
        if (i + 1) not in assignment:
            for c in mid_courts + end_courts + all_courts:
                if c not in used:
                    assignment[i + 1] = c
                    used.add(c)
                    break

    # Apply admin overrides (stored as prefix+str(logical) → physical)
    for k, v in overrides.items():
        if prefix:
            if not k.startswith(prefix):
                continue
            k = k[len(prefix):]
        try:
            assignment[int(k)] = int(v)
        except:
            pass

    return assignment

# === SECTION 3: DATA CACHING ENGINE (WITH PAGINATION) ===
AIRTABLE_CACHE = {}
PLAY_MODE_OVERRIDE = None   # set by admin toggle; survives cache expiry within same process
MAINTENANCE_MODE = False    # when True, only admin can sign up or create teams
CACHE_TTL = 300       # cache successful fetches for 5 min — well within Airtable Team plan quota
ERROR_CACHE_TTL = 30  # on failure, hold the empty/stale result for 30s before retrying

def get_airtable_data(table_name, sort_field=None, direction="asc", filter_formula=None):
    current_time = time.time()
    cache_key = f"{table_name}_{sort_field}_{direction}_{filter_formula}"

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

            # Timeout prevents a slow/hung Airtable call from tying up the gunicorn worker
            res = requests.get(url, headers=HEADERS, params=params, timeout=10)
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
        # CRITICAL: cache the failure too. Otherwise every page reload retries
        # immediately, which during a 429 window keeps the rate-limit alive
        # forever and the site never recovers on its own.
        # If we have prior good data, keep serving it (stale-while-error). Otherwise
        # cache an empty list briefly so we stop hammering the API.
        if cache_key in AIRTABLE_CACHE:
            _, stale_data = AIRTABLE_CACHE[cache_key]
            AIRTABLE_CACHE[cache_key] = (current_time - (CACHE_TTL - ERROR_CACHE_TTL), stale_data)
            return stale_data
        AIRTABLE_CACHE[cache_key] = (current_time - (CACHE_TTL - ERROR_CACHE_TTL), [])
        return []

# === SORT KEY for manual roster ordering ===
# Records with a "Manual Order" number sort first (ascending).
# Records without it fall back to Airtable's createdTime (original behavior).
def sort_key(r):
    manual = r.get('fields', {}).get('Manual Order')
    if manual is not None:
        return (0, float(manual), '')
    return (1, 0.0, r.get('createdTime', ''))

# === SECTION 4: PRIMARY ROUTES ===
@app.route('/')
def index():
    settings = get_airtable_data("Settings")
    d_date, d_start, d_end = "TBD", "TBD", ""
    play_mode = "Open"
    start_dt = None
    if settings:
        f = settings[0]['fields']
        d_date, d_start = f.get('Target Date', 'TBD'), f.get('Start Time', 'TBD')
        play_mode = f.get('Play Mode', 'Open')
        try:
            start_dt = dt.datetime.strptime(d_start, "%I:%M %p")
            d_end = f" – {(start_dt + timedelta(hours=2, minutes=15)).strftime('%I:%M %p').lstrip('0')}"
        except: d_end = ""

    # Priority order: module-level override > session override > Airtable cache
    # Module-level var is set by toggle_mode_direct and survives cache expiry
    if PLAY_MODE_OVERRIDE and PLAY_MODE_OVERRIDE in ('Open', 'Split', 'Team'):
        play_mode = PLAY_MODE_OVERRIDE
    elif 'forced_play_mode' in session:
        play_mode = session.pop('forced_play_mode')
        session.modified = True

    master_recs = get_airtable_data("Master List", sort_field="First")
    strike_map = {str(m['fields'].get('Code')): m['fields'].get('Strikes', 0) for m in master_recs}
    signup_recs = sorted(get_airtable_data("Signups"), key=sort_key)
    
    roster = []
    for r in signup_recs:
        f = r['fields']; f['id'] = r['id']
        f['strikes'] = strike_map.get(str(f.get('Player Code')), 0)
        roster.append(f)

    lower_roster, upper_roster = [], []
    lower_cutoff, upper_cutoff = 12, 12
    total_signups = len(roster)
    playing_cutoff = (min(total_signups, 24) // 4) * 4
    waitlist_count = total_signups - playing_cutoff

    user_on_roster, waitlist_pos, pending_sub_offer = False, 0, False
    curr_user = session.get('user')
    my_team_id = None   # set below if user is on a team

    if play_mode == 'Split':
        lower_roster = [p for p in roster if p.get('Level') == '3.0/3.5']
        upper_roster = [p for p in roster if p.get('Level') == '4.0/4.5']
        lower_cutoff = (min(len(lower_roster), 12) // 4) * 4
        upper_cutoff = (min(len(upper_roster), 12) // 4) * 4

        if curr_user:
            for i, p in enumerate(lower_roster):
                if str(p.get('Player Code')) == str(curr_user.get('code')):
                    user_on_roster = True
                    if i >= lower_cutoff: waitlist_pos = i - lower_cutoff + 1
            for i, p in enumerate(upper_roster):
                if str(p.get('Player Code')) == str(curr_user.get('code')):
                    user_on_roster = True
                    if i >= upper_cutoff: waitlist_pos = i - upper_cutoff + 1
            for p in roster:
                if str(p.get('Sub Offer')) == str(curr_user.get('code')):
                    pending_sub_offer = True
    else:
        if curr_user:
            for i, p in enumerate(roster):
                if str(p.get('Player Code')) == str(curr_user.get('code')):
                    user_on_roster = True
                    my_team_id = p.get('Team ID')
                    if play_mode == 'Open' and i >= playing_cutoff:
                        waitlist_pos = i - playing_cutoff + 1
                if play_mode == 'Open' and str(p.get('Sub Offer')) == str(curr_user.get('code')):
                    pending_sub_offer = True

    # Build team_list for Team mode roster display
    team_list    = []
    pending_teams = []
    if play_mode == 'Team':
        teams_dict = {}
        for p in roster:
            tid = p.get('Team ID') or f"__solo_{p.get('id','')}"
            if tid not in teams_dict:
                teams_dict[tid] = []
            teams_dict[tid].append(p)
        for tid, players in teams_dict.items():
            captain  = next((p for p in players if p.get('Is Captain')), players[0] if players else None)
            status   = (captain.get('Team Status') or 'Approved') if captain else 'Approved'
            req_c    = int(captain.get('Requested Courts') or 1) if captain else 1
            app_c    = int(captain.get('Approved Courts')  or req_c) if captain else req_c
            reserves = [p for p in players if p.get('Is Reserve')]
            courts_p = [p for p in players if not p.get('Is Reserve')]
            cap_list = [captain] if captain else []
            others   = [p for p in courts_p if p is not captain]
            ordered  = cap_list + others
            courts   = [ordered[i:i+4] for i in range(0, len(ordered), 4)]
            team_data = {
                'team_id': tid, 'captain': captain, 'courts': courts,
                'reserves': reserves, 'status': status,
                'requested_courts': req_c, 'approved_courts': app_c
            }
            if status == 'Pending':
                pending_teams.append(team_data)
            else:
                team_list.append(team_data)

    weather_info = "Weather Unavailable"
    try:
        # Open-Meteo - 14-day forecast, Free, No API Key required
        lat, lon = "39.9936", "-105.0897" # Lafayette, CO Coordinates
        weather_url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&hourly=temperature_2m,weathercode&temperature_unit=fahrenheit&timezone=America%2FDenver&forecast_days=14"
        w_res = requests.get(weather_url).json()

        # Calculate the upcoming Saturday based on today
        today = dt.date.today()
        days_ahead = 5 - today.weekday()
        if days_ahead < 0: days_ahead += 7
        target_date = today + dt.timedelta(days=days_ahead)
        target_date_iso = target_date.isoformat() 
            
        s_hour = start_dt.hour if start_dt else 9
        end_dt = start_dt + timedelta(hours=2, minutes=15) if start_dt else None
        e_hour = min(end_dt.hour, 23) if end_dt else 11
        
        start_time_str = f"{target_date_iso}T{s_hour:02d}:00"
        end_time_str = f"{target_date_iso}T{e_hour:02d}:00"

        times = w_res.get('hourly', {}).get('time', [])
        temps = w_res.get('hourly', {}).get('temperature_2m', [])
        codes = w_res.get('hourly', {}).get('weathercode', [])

        if start_time_str in times:
            s_idx = times.index(start_time_str)
            e_idx = times.index(end_time_str) if end_time_str in times else s_idx + 2
            
            temp_start = int(temps[s_idx])
            temp_end = int(temps[e_idx])
            w_code = codes[s_idx]
            
            # WMO Weather code mapping
            code_map = {
                0: "Clear", 1: "Mostly Clear", 2: "Partly Cloudy", 3: "Overcast",
                45: "Fog", 48: "Fog", 51: "Drizzle", 53: "Drizzle", 55: "Drizzle",
                61: "Rain", 63: "Rain", 65: "Heavy Rain", 71: "Snow", 73: "Snow",
                75: "Heavy Snow", 95: "Thunderstorm"
            }
            cond = code_map.get(w_code, "Varied")
            
            end_label = end_dt.strftime('%I:%M %p').lstrip('0') if end_dt else "End"
            weather_info = f"{cond} | {d_start}: {temp_start}°F → {end_label}: {temp_end}°F"
        else:
            weather_info = "Saturday forecast available soon"

    except Exception as e:
        print(f"Weather Logic Error: {e}")

    applicants, guest_requests = [], []
    if curr_user and curr_user.get('is_admin'):
        all_apps = get_airtable_data("Applicants")
        applicants = [a for a in all_apps if a['fields'].get('Status') == 'Pending' and not a['fields'].get('Sponsor')]
        guest_requests = [a for a in all_apps if a['fields'].get('Status') == 'Pending' and a['fields'].get('Sponsor')]

    show_venmo = bool(settings[0]['fields'].get('Show Venmo')) if settings else False

    # --- Physical court assignment ---
    import json
    raw_map = {}
    if settings:
        try:
            raw_map = json.loads(settings[0]['fields'].get('Court Map', '{}') or '{}')
        except:
            raw_map = {}

    if play_mode == 'Open':
        # Open: up to 6 courts of 4. Overrides keyed "1".."6"
        n = playing_cutoff // 4
        court_map = build_court_map(n, [4] * n, raw_map)
        lower_court_map, upper_court_map = {}, {}

    elif play_mode == 'Split':
        # Lower = physical courts 1-3 (end court = 3), overrides keyed "L1","L2","L3"
        # Upper = physical courts 4-6 (end court = 6), overrides keyed "U1","U2","U3"
        nl = lower_cutoff // 4
        nu = upper_cutoff // 4
        lower_raw = {k[1:]: v for k, v in raw_map.items() if k.startswith('L')}
        upper_raw = {k[1:]: v for k, v in raw_map.items() if k.startswith('U')}
        lower_court_map = {i: int(lower_raw.get(str(i), i))       for i in range(1, nl + 1)}
        upper_court_map = {i: int(upper_raw.get(str(i), i + 3))   for i in range(1, nu + 1)}
        court_map = {}

    else:  # Team
        # Flatten all team-courts; auto-assign across courts 1-6; overrides keyed "T_1","T_2"…
        team_court_sizes = [len(c) for team in team_list for c in team['courts']]
        n_tc  = len(team_court_sizes)
        t_raw = {k[2:]: v for k, v in raw_map.items() if k.startswith('T_')}
        flat  = build_court_map(n_tc, team_court_sizes, t_raw)
        seq   = 1
        team_court_map: dict = {}
        for team in team_list:
            for ci in range(len(team['courts'])):
                team_court_map[(team['team_id'], ci + 1)] = flat.get(seq, seq)
                seq += 1
        court_map       = team_court_map
        lower_court_map = {}
        upper_court_map = {}

    return render_template('index.html', target_date=d_date, start_time=d_start, end_time=d_end, roster=roster,
                           applicants=applicants, guest_requests=guest_requests, master_list=master_recs,
                           user_on_roster=user_on_roster, waitlist_pos=waitlist_pos, weather=weather_info,
                           playing_cutoff=playing_cutoff, total_signups=total_signups, waitlist_count=waitlist_count,
                           pending_sub_offer=pending_sub_offer, play_mode=play_mode, lower_roster=lower_roster,
                           upper_roster=upper_roster, lower_cutoff=lower_cutoff, upper_cutoff=upper_cutoff,
                           show_venmo=show_venmo, team_list=team_list, my_team_id=my_team_id,
                           court_map=court_map, lower_court_map=lower_court_map, upper_court_map=upper_court_map,
                           pending_teams=pending_teams, maintenance_mode=MAINTENANCE_MODE)

@app.route('/validate', methods=['POST'])
def validate():
    code = str(request.form.get('code', '')).strip()
    password = request.form.get('password')
    master = get_airtable_data("Master List")
    user_rec = None
    for m in master:
        m_code = str(m['fields'].get('Code', '')).strip()
        if m_code.endswith('.0'): m_code = m_code[:-2]
        if m_code == code:
            user_rec = m
            break
    
    if user_rec:
        is_admin = (password == ADMIN_PW)
        f = user_rec['fields']
        last_confirmed_str = f.get('Last Confirmed')
        contact_confirmed = False
        if last_confirmed_str:
            try:
                last_conf_date = dt.datetime.strptime(last_confirmed_str, "%Y-%m-%d").date()
                if (dt.date.today() - last_conf_date).days < 180: contact_confirmed = True
            except: pass
        session['user'] = {
            'code': code, 'first': f.get('First'), 'last': f.get('Last'),
            'email': f.get('Email', ''), 'phone': f.get('Phone', ''), 'is_admin': is_admin,
            'contact_confirmed': contact_confirmed, 'level': f.get('Level', '')
        }
        log_activity(f.get('First'), "Logged In")
        return redirect(url_for('index'))
    else:
        log_activity(f"Failed Code Attempt: '{code}'", "Login Error")
        send_email(ADMIN_EMAIL, "⚠️ Failed Login Attempt", f"<p>A user just attempted to log in with an invalid code: <b>{code}</b>.</p>")
        flash("Invalid Player Code.", "danger")
        return redirect(url_for('index'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# === SECTION 5: PLAYER ACTIONS ===
@app.route('/signup', methods=['POST'])
def signup():
    user = session.get('user')
    if not user: return redirect(url_for('index'))

    if MAINTENANCE_MODE and not user.get('is_admin'):
        flash("Signups are temporarily paused for maintenance. Check back in a few minutes!", "warning")
        return redirect(url_for('index'))

    if not user.get('contact_confirmed') or not user.get('level'):
        flash("Action Required: Please review your profile info to unlock signups.", "danger")
        return redirect(url_for('index'))

    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{user['code']}'")
    if m_recs and m_recs[0]['fields'].get('Paused'):
        flash("🚫 Your account is paused due to strikes. Please contact Jim.", "danger")
        return redirect(url_for('index'))
    
    existing = get_airtable_data("Signups", filter_formula=f"{{Player Code}}='{user['code']}'")
    if existing:
        flash("You are already signed up!", "warning")
        return redirect(url_for('index'))

    payload = {"fields": {"First": user['first'], "Last": user['last'], "Player Code": str(user['code']), "Email": user['email'], "Level": user['level']}}
    try:
        requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json=payload).raise_for_status()
        AIRTABLE_CACHE.clear()
        log_activity(user['first'], "Signed Up")
        flash("You've been added to the list!", "success")
    except:
        flash("Error saving signup to the database. Please try again or contact Jim.", "danger")
    return redirect(url_for('index'))

@app.route('/cancel', methods=['POST'])
def cancel():
    if not session.get('user'): return redirect(url_for('index'))
    now_mdt = dt.datetime.utcnow() - dt.timedelta(hours=6)
    is_past_deadline = (now_mdt.weekday() == 4 and now_mdt.hour >= 8) or (now_mdt.weekday() == 5)
    
    settings = get_airtable_data("Settings")
    play_mode = settings[0]['fields'].get('Play Mode', 'Open') if settings else 'Open'
    recs = get_airtable_data("Signups", sort_field="Created Time")
    
    my_rec = next((r for r in recs if str(r['fields'].get('Player Code')) == str(session['user']['code'])), None)
    if my_rec:
        target_list = recs
        playing_cutoff = (min(len(target_list), 24) // 4) * 4
        
        if play_mode == 'Split':
            my_level = my_rec['fields'].get('Level')
            target_list = [r for r in recs if r['fields'].get('Level') == my_level]
            playing_cutoff = (min(len(target_list), 12) // 4) * 4

        try: idx = target_list.index(my_rec)
        except: idx = -1
            
        if idx != -1:
            is_in_complete_court = idx < playing_cutoff
            waitlist_exists = len(target_list) > playing_cutoff
            
            if is_in_complete_court and is_past_deadline:
                if waitlist_exists:
                    promo = target_list[playing_cutoff]
                    promo_code = promo['fields'].get('Player Code')
                    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{promo_code}'")
                    promo_email = m_recs[0]['fields'].get('Email') if m_recs else None

                    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{my_rec['id']}", headers=HEADERS, json={"fields": {"Label": "PENDING SUB", "Sub Offer": str(promo_code)}})
                    if promo_email:
                        send_email(promo_email, "🎾 Sub Spot Available!", f"A spot opened up! Log in to {SITE_URL} to accept it.")
                        flash("Drop initiated. Waitlisted player emailed.", "warning")
                    else:
                        flash("Drop initiated, but the waitlisted player has no email on file!", "warning")
                else:
                    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{my_rec['id']}", headers=HEADERS, json={"fields": {"Label": "NEEDS SUB"}})
                    flash("⚠️ NO ONE is on the waitlist for your level. You are marked NEEDS SUB.", "danger")
                AIRTABLE_CACHE.clear()
                return redirect(url_for('index'))

        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{my_rec['id']}", headers=HEADERS)
        log_activity(session['user']['first'], "Cancelled")
    
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/accept_sub', methods=['POST'])
def accept_sub():
    user = session.get('user')
    if not user or not user.get('contact_confirmed') or not user.get('level'): return redirect(url_for('index'))

    recs = get_airtable_data("Signups")
    dropper = next((r for r in recs if str(r['fields'].get('Sub Offer')) == str(user['code'])), None)
    me = next((r for r in recs if str(r['fields'].get('Player Code')) == str(user['code'])), None)
    if dropper and me:
        dropper_name = dropper['fields'].get('First')
        requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{dropper['id']}", headers=HEADERS)
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{me['id']}", headers=HEADERS, json={"fields": {"Label": f"SUB for {dropper_name}"}})
        AIRTABLE_CACHE.clear()
        flash("You successfully accepted the sub spot!", "success")
    return redirect(url_for('index'))

@app.route('/update_profile', methods=['POST'])
def update_profile():
    user = session.get('user')
    if not user: return redirect(url_for('index'))
    
    new_email, new_phone, new_level = request.form.get('email'), request.form.get('phone'), request.form.get('level')
    if not new_email or not new_phone:
        flash("Both Email and Phone are required.", "danger"); return redirect(url_for('index'))
    if not user.get('level') and not new_level:
        flash("Play Level is required.", "danger"); return redirect(url_for('index'))

    master = get_airtable_data("Master List")
    user_rec = next((m for m in master if str(m['fields'].get('Code')) == str(user['code'])), None)
    if user_rec:
        today_str = dt.date.today().strftime("%Y-%m-%d")
        payload = {"fields": {"Email": new_email, "Phone": new_phone, "Last Confirmed": today_str}}
        if not user.get('level') and new_level: payload["fields"]["Level"] = new_level
        try: requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{user_rec['id']}", headers=HEADERS, json=payload)
        except: pass 
        session['user'].update({'email': new_email, 'phone': new_phone, 'contact_confirmed': True, 'level': new_level or user.get('level')})
        session.modified = True
        AIRTABLE_CACHE.clear()
        flash("Profile updated! Site unlocked.", "success")
    return redirect(url_for('index'))

@app.route('/apply', methods=['POST'])
def apply():
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json={"fields": {"First": request.form.get('first'), "Last": request.form.get('last'), "Email": request.form.get('email'), "Status": "Pending"}})
    flash("Application submitted! We will email you your code once approved.", "success")
    return redirect(url_for('index'))

@app.route('/request_guest', methods=['POST'])
def request_guest():
    user = session.get('user')
    if not user or not user.get('contact_confirmed'): return redirect(url_for('index'))
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Applicants", headers=HEADERS, json={"fields": {"First": request.form.get('guest_first'), "Last": request.form.get('guest_last'), "Sponsor": f"{user['first']} {user['last']}", "Status": "Pending", "Level": user.get('level', '')}})
    flash("Guest request submitted to Admin. They will be placed in your play level.", "info")
    return redirect(url_for('index'))

# === TEAM MODE ROUTES ===

@app.route('/team/lookup', methods=['POST'])
def team_lookup():
    """AJAX endpoint: fuzzy-match a player name against Master List."""
    data = request.get_json(silent=True) or {}
    first = data.get('first', '').strip()
    last  = data.get('last',  '').strip()
    if not first or not last:
        return jsonify({'status': 'incomplete'})
    master = get_airtable_data("Master List")
    exact, near = find_player_matches(first, last, master)
    if exact:
        f = exact[0]['fields']
        code = str(f.get('Code', '')).strip()
        if code.endswith('.0'): code = code[:-2]
        return jsonify({'status': 'exact', 'player': {
            'code': code, 'first': f.get('First'), 'last': f.get('Last'),
            'email': f.get('Email', ''), 'level': f.get('Level', '')
        }})
    elif near:
        matches = []
        for m in near[:5]:
            c = str(m['fields'].get('Code', '')).strip()
            if c.endswith('.0'): c = c[:-2]
            matches.append({'code': c, 'first': m['fields'].get('First'), 'last': m['fields'].get('Last')})
        return jsonify({'status': 'near', 'matches': matches})
    return jsonify({'status': 'none'})


def _process_team_slots(user, form, court_count):
    """
    Shared logic for team create and update.
    Player dict keys: code, first, last, email, level, court_num (0=reserve), is_captain, is_reserve
    """
    master_list  = get_airtable_data("Master List")
    confirmed    = []
    new_accounts = []
    errors       = []

    # Captain: court 1, never a reserve
    confirmed.append({
        'code': user['code'], 'first': user['first'], 'last': user['last'],
        'email': user.get('email', ''), 'level': user.get('level', ''),
        'court_num': 1, 'is_captain': True, 'is_reserve': False
    })

    i = 0
    while True:
        first = form.get(f'first_{i}', None)
        if first is None and form.get(f'last_{i}') is None:
            break
        first  = (first or '').strip()
        last   = form.get(f'last_{i}',        '').strip()
        code   = form.get(f'player_code_{i}', '').strip()
        email  = form.get(f'email_{i}',       '').strip()
        phone  = form.get(f'phone_{i}',       '').strip()
        is_res = form.get(f'is_reserve_{i}',  '0') == '1'
        i += 1

        if not first and not last:
            continue

        if is_res:
            court_num = 0
        else:
            court_players = sum(1 for p in confirmed if not p['is_reserve'])
            court_num = ((court_players) // 4) + 1

        if code and code != 'new':
            m = next((m for m in master_list
                      if str(m['fields'].get('Code', '')).replace('.0', '').strip() == code), None)
            if m:
                confirmed.append({
                    'code': code,
                    'first': m['fields'].get('First', first),
                    'last':  m['fields'].get('Last',  last),
                    'email': m['fields'].get('Email', ''),
                    'level': m['fields'].get('Level', ''),
                    'court_num': court_num, 'is_captain': False, 'is_reserve': is_res
                })
        elif first and last and email:
            master_list = get_airtable_data("Master List")
            new_code    = next_player_code(master_list)
            try:
                requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List",
                    headers=HEADERS,
                    json={"fields": {"First": first, "Last": last, "Email": email,
                                     "Phone": phone, "Code": new_code,
                                     "Level": user.get('level', '')},
                          "typecast": True}, timeout=10)
                keys = [k for k in AIRTABLE_CACHE if k.startswith('Master')]
                for k in keys: del AIRTABLE_CACHE[k]
                new_accounts.append({'first': first, 'last': last, 'email': email, 'code': new_code})
                confirmed.append({
                    'code': new_code, 'first': first, 'last': last,
                    'email': email, 'level': user.get('level', ''),
                    'court_num': court_num, 'is_captain': False, 'is_reserve': is_res
                })
                send_email(email, "🎾 Welcome to Saturday Tennis Gang!",
                    f"<p>Hi {first}! <b>{user['first']} {user['last']}</b> has added you to "
                    f"the tennis roster.</p>"
                    f"<p>Your login code is: <b>{new_code}</b></p>"
                    f"<p>Visit <a href='{SITE_URL}'>{SITE_URL}</a> to view the roster, "
                    f"manage your spot, or cancel if you can't make it.</p>"
                    f"<p>See you Saturday! 🎾</p>")
            except Exception as e:
                errors.append(f"Could not create account for {first} {last}: {e}")
        elif first or last:
            errors.append(f"Skipped '{first} {last}' — no match confirmed and no email provided.")

    return confirmed, new_accounts, errors


def _send_captain_summary(user, confirmed, new_accounts, d_date, pending=False):
    courts_html = ""
    court_nums = sorted(set(p['court_num'] for p in confirmed if p['court_num'] > 0))
    for cn in court_nums:
        cp = [p for p in confirmed if p['court_num'] == cn]
        courts_html += f"<h4>Court {cn}</h4><ul>"
        for p in cp:
            tag = " <em>(Captain)</em>" if p.get('is_captain') else ""
            courts_html += f"<li>{p['first']} {p['last']}{tag}</li>"
        courts_html += "</ul>"

    reserves = [p for p in confirmed if p.get('is_reserve')]
    res_html = ""
    if reserves:
        res_html = "<h4>📋 Reserves</h4><ul>"
        for p in reserves:
            res_html += f"<li>{p['first']} {p['last']}</li>"
        res_html += "</ul>"

    new_html = ""
    if new_accounts:
        new_html = "<h4>New accounts created:</h4><ul>"
        for np in new_accounts:
            new_html += (f"<li><b>{np['first']} {np['last']}</b> — "
                         f"Code: <b>{np['code']}</b> | Email: {np['email']}</li>")
        new_html += "</ul><p><em>Each new player has been emailed their code and site link.</em></p>"

    status_note = ("<p><b>⏳ Your request is pending Jim's review.</b> "
                   "Your team will appear on the roster once approved. "
                   f"You can edit your request at <a href='{SITE_URL}'>{SITE_URL}</a>.</p>"
                   if pending else
                   f"<p>Your team is live. Edit it at <a href='{SITE_URL}'>{SITE_URL}</a>.</p>")

    send_email(user['email'], f"🎾 Team {'Request' if pending else 'Summary'} for {d_date}",
        f"<p>Hi {user['first']}! Here is your team lineup:</p>"
        f"{courts_html}{res_html}{new_html}{status_note}")


@app.route('/team/create', methods=['POST'])
def team_create():
    user = session.get('user')
    if not user or not user.get('contact_confirmed') or not user.get('level'):
        flash("Please complete your profile first.", "danger")
        return redirect(url_for('index'))

    if MAINTENANCE_MODE and not user.get('is_admin'):
        flash("Team signups are temporarily paused for maintenance. Check back in a few minutes!", "warning")
        return redirect(url_for('index'))

    existing = get_airtable_data("Signups", filter_formula=f"{{Player Code}}='{user['code']}'")
    if existing:
        flash("You are already on the roster.", "warning")
        return redirect(url_for('index'))

    settings = get_airtable_data("Settings")
    d_date = settings[0]['fields'].get('Target Date', 'Saturday') if settings else 'Saturday'

    court_count = max(1, min(2, int(request.form.get('court_count', 1))))
    confirmed, new_accounts, errors = _process_team_slots(user, request.form, court_count)

    team_id = str(uuid.uuid4())[:8].upper()
    for p in confirmed:
        try:
            fields = {
                "First": p['first'], "Last": p['last'],
                "Player Code": str(p['code']), "Email": p['email'],
                "Level": p['level'], "Team ID": team_id,
                "Is Captain": p.get('is_captain', False),
                "Is Reserve": p.get('is_reserve', False),
                "Court Num":  p['court_num']
            }
            if p.get('is_captain'):
                fields["Team Status"]     = "Pending"
                fields["Requested Courts"] = court_count
            requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups",
                headers=HEADERS, json={"fields": fields}, timeout=10)
        except Exception as e:
            errors.append(f"Error adding {p['first']} to roster: {e}")

    _send_captain_summary(user, confirmed, new_accounts, d_date, pending=True)

    AIRTABLE_CACHE.clear()
    log_activity(user['first'], f"Created Team {team_id} (pending review)")

    for e in errors: flash(e, "warning")
    n_courts  = sum(1 for p in confirmed if not p['is_reserve'] and p['court_num'] > 0)
    n_res     = sum(1 for p in confirmed if p['is_reserve'])
    res_note  = f" + {n_res} reserve(s)" if n_res else ""
    flash(f"Team request submitted — {len(confirmed)} players across {court_count} court(s){res_note}. "
          f"Jim will review and approve before it appears on the roster.", "success")
    return redirect(url_for('index'))


@app.route('/team/update/<team_id>', methods=['POST'])
def team_update(team_id):
    """Captain replaces their team's non-captain players with a new submission."""
    user = session.get('user')
    if not user: return redirect(url_for('index'))

    recs = get_airtable_data("Signups")
    my_rec = next((r for r in recs if str(r['fields'].get('Player Code')) == str(user['code'])), None)

    if not my_rec or my_rec['fields'].get('Team ID') != team_id:
        if not session['user'].get('is_admin'):
            flash("Only the team captain can edit this team.", "danger")
            return redirect(url_for('index'))

    # Delete all non-captain members of this team
    for r in recs:
        if r['fields'].get('Team ID') == team_id and r['id'] != (my_rec['id'] if my_rec else ''):
            try: requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS, timeout=10)
            except: pass

    settings = get_airtable_data("Settings")
    d_date = settings[0]['fields'].get('Target Date', 'Saturday') if settings else 'Saturday'

    court_count = max(1, min(2, int(request.form.get('court_count', 1))))
    confirmed, new_accounts, errors = _process_team_slots(user, request.form, court_count)

    for p in confirmed:
        if p.get('is_captain'): continue   # captain record already exists
        try:
            requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS,
                json={"fields": {
                    "First": p['first'], "Last": p['last'],
                    "Player Code": str(p['code']), "Email": p['email'],
                    "Level": p['level'], "Team ID": team_id,
                    "Is Captain": False, "Court Num": p['court_num']
                }}, timeout=10)
        except Exception as e:
            errors.append(f"Error adding {p['first']}: {e}")

    _send_captain_summary(user, confirmed, new_accounts, d_date)

    AIRTABLE_CACHE.clear()
    log_activity(user['first'], f"Updated Team {team_id}")
    for e in errors: flash(e, "warning")
    flash("Team updated! Summary emailed to you.", "success")
    return redirect(url_for('index'))


@app.route('/team/remove_player/<signup_id>', methods=['POST'])
def team_remove_player(signup_id):
    user = session.get('user')
    if not user: return redirect(url_for('index'))

    recs = get_airtable_data("Signups")
    my_rec     = next((r for r in recs if str(r['fields'].get('Player Code')) == str(user['code'])), None)
    target_rec = next((r for r in recs if r['id'] == signup_id), None)

    if not target_rec:
        flash("Player record not found.", "danger")
        return redirect(url_for('index'))

    is_admin   = session['user'].get('is_admin')
    is_captain = my_rec and my_rec['fields'].get('Is Captain')
    same_team  = my_rec and my_rec['fields'].get('Team ID') == target_rec['fields'].get('Team ID')

    if not is_admin and not (is_captain and same_team):
        flash("Only the team captain can remove players.", "danger")
        return redirect(url_for('index'))

    if my_rec and signup_id == my_rec['id']:
        flash("To leave the roster yourself, use Cancel Spot.", "warning")
        return redirect(url_for('index'))

    requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{signup_id}", headers=HEADERS)
    keys = [k for k in AIRTABLE_CACHE if k.startswith('Signups')]
    for k in keys: del AIRTABLE_CACHE[k]
    flash("Player removed from team.", "info")
    return redirect(url_for('index'))

@app.route('/team/approve/<team_id>', methods=['POST'])
def team_approve(team_id):
    """Admin approves a pending team, optionally with fewer courts than requested."""
    user = session.get('user')
    if not user or not user.get('is_admin'):
        return "Unauthorized", 403

    approved_courts = max(1, int(request.form.get('approved_courts', 1)))
    recs = get_airtable_data("Signups")
    team_recs = [r for r in recs if r['fields'].get('Team ID') == team_id]
    captain_rec = next((r for r in team_recs if r['fields'].get('Is Captain')), None)

    if not captain_rec:
        flash("Team not found.", "danger")
        return redirect(url_for('index'))

    requested_courts = int(captain_rec['fields'].get('Requested Courts') or 1)
    cap_fields = captain_rec['fields']

    # Approve the captain record
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{captain_rec['id']}",
        headers=HEADERS, json={"fields": {
            "Team Status":     "Approved",
            "Approved Courts": approved_courts
        }}, timeout=10)

    # If fewer courts approved, demote excess court players to reserves
    if approved_courts < requested_courts:
        capacity = approved_courts * 4 - 1  # minus captain
        non_cap = sorted(
            [r for r in team_recs if not r['fields'].get('Is Captain') and not r['fields'].get('Is Reserve')],
            key=lambda r: (r['fields'].get('Court Num', 1), r.get('createdTime', ''))
        )
        for idx, r in enumerate(non_cap):
            if idx >= capacity:
                requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}",
                    headers=HEADERS, json={"fields": {"Is Reserve": True, "Court Num": 0}}, timeout=10)

    # Email captain
    note = ""
    if approved_courts < requested_courts:
        note = (f" You requested {requested_courts} court(s), but only {approved_courts} "
                f"could be accommodated this week. Extra players have been moved to reserves.")
    send_email(cap_fields.get('Email', ''), "🎾 Your Team Has Been Approved!",
        f"<p>Hi {cap_fields.get('First', '')}! Your team request for "
        f"<b>{approved_courts} court(s)</b> has been approved.{note}</p>"
        f"<p>Your team is now visible on the roster at "
        f"<a href='{SITE_URL}'>{SITE_URL}</a>.</p>")

    AIRTABLE_CACHE.clear()
    log_activity("Admin", f"Approved Team {team_id} for {approved_courts} court(s)")
    flash(f"Team '{cap_fields.get('First','')} {cap_fields.get('Last','')}' approved "
          f"for {approved_courts} court(s).", "success")
    return redirect(url_for('index'))

# === SECTION 6: ADMIN ACTIONS ===
@app.route('/admin_action', methods=['POST'])
def admin_action():
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    action = request.form.get('action')
    settings = get_airtable_data("Settings")
    if action == "labels" and settings:
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}", headers=HEADERS, json={"fields": {"Target Date": request.form.get('date'), "Start Time": request.form.get('time')}})
        flash("Session info updated!", "success")
    elif action == "toggle_maintenance":
        global MAINTENANCE_MODE
        MAINTENANCE_MODE = not MAINTENANCE_MODE
        status = "ON — only you can sign up or create teams." if MAINTENANCE_MODE else "OFF — signups open to everyone."
        flash(f"Maintenance mode {status}", "warning" if MAINTENANCE_MODE else "success")
        return redirect(url_for('index'))
        global PLAY_MODE_OVERRIDE
        cycle = {'Open': 'Split', 'Split': 'Team', 'Team': 'Open'}
        new_mode = cycle.get(settings[0]['fields'].get('Play Mode', 'Open'), 'Open')
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}", headers=HEADERS,
            json={"fields": {"Play Mode": new_mode, "Court Map": "{}"}})
        PLAY_MODE_OVERRIDE = new_mode
        flash(f"Mode switched to {new_mode}! Court assignments reset.", "success")
    elif action == "toggle_mode_direct" and settings:
        global PLAY_MODE_OVERRIDE
        new_mode = request.form.get('new_mode', 'Open')
        if new_mode not in ('Open', 'Split', 'Team'):
            new_mode = 'Open'

        # PATCH Play Mode — check the response so we know if it actually saved
        r = requests.patch(
            f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}",
            headers=HEADERS, json={"fields": {"Play Mode": new_mode}}, timeout=10)

        if not r.ok:
            flash(f"Mode switch failed — Airtable returned {r.status_code}. "
                  f"Try again or edit the Play Mode field in Airtable directly.", "danger")
            return redirect(url_for('index'))

        # Court Map reset — best effort
        try:
            requests.patch(
                f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}",
                headers=HEADERS, json={"fields": {"Court Map": "{}"}}, timeout=10)
        except: pass

        # Three-layer persistence so mode survives cache expiry AND process restarts:
        # 1. Update the cache using Airtable's confirmed response body
        confirmed_fields = r.json().get('fields', {})
        confirmed_fields['Play Mode'] = new_mode   # ensure it's there even if Airtable omits unchanged fields
        for key in list(AIRTABLE_CACHE.keys()):
            if key.startswith('Settings'):
                _, records = AIRTABLE_CACHE[key]
                if records:
                    records[0]['fields'].update(confirmed_fields)
                    AIRTABLE_CACHE[key] = (time.time(), records)   # reset TTL from now

        # 2. Module-level var — survives cache expiry for the lifetime of this process
        PLAY_MODE_OVERRIDE = new_mode

        # 3. Session — covers the immediate redirect
        session['forced_play_mode'] = new_mode
        session.modified = True

        flash(f"Switched to {new_mode} Mode.", "success")
        return redirect(url_for('index'))
    elif action == "assign_court" and settings:
        import json
        logical  = request.form.get('logical', '').strip()
        physical = request.form.get('physical', '').strip()
        prefix   = request.form.get('prefix', '')   # '', 'L', 'U', 'T_'
        if logical and physical:
            try:
                raw = json.loads(settings[0]['fields'].get('Court Map', '{}') or '{}')
                raw[prefix + logical] = int(physical)
                requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}",
                    headers=HEADERS, json={"fields": {"Court Map": json.dumps(raw)}})
            except Exception as e:
                flash(f"Court assignment error: {e}", "danger")
    elif action == "reset_courts" and settings:
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}",
            headers=HEADERS, json={"fields": {"Court Map": "{}"}})
        flash("Court assignments reset to auto.", "success")
    elif action == "toggle_venmo" and settings:
        current = bool(settings[0]['fields'].get('Show Venmo'))
        requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}", headers=HEADERS, json={"fields": {"Show Venmo": not current}})
        flash(f"Venmo card {'hidden' if current else 'shown'}.", "success")
    elif action == "reset_roster":
        signups = get_airtable_data("Signups", sort_field="Created Time")
        _archive_and_clear_signups(settings, signups)
        log_activity("Admin", "Manual roster reset — signups archived, no emails sent")
        flash("Roster cleared and archived. Signup emails will go out via the Monday cron at 8:15 AM.", "success")
        return redirect(url_for('index'))
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/move_player/<signup_id>', methods=['POST'])
def move_player(signup_id):
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    new_level = '4.0/4.5' if request.form.get('current_level') == '3.0/3.5' else '3.0/3.5'
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{signup_id}", headers=HEADERS, json={"fields": {"Level": new_level}})
    AIRTABLE_CACHE.clear()
    flash("Player moved successfully to balance courts!", "success")
    return redirect(url_for('index'))

@app.route('/info_blast', methods=['POST'])
def info_blast():
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    msg, group = request.form.get('message'), request.form.get('target_group')
    emails = [r['fields'].get('Email') for r in get_airtable_data("Signups" if group == "roster" else "Master List") if r['fields'].get('Email')]
    send_email(emails, "🎾 Tennis Gang Announcement", f"<p>{msg}</p>", is_multiple=True)
    flash("Announcement sent!", "success")
    return redirect(url_for('index'))

@app.route('/approve_player/<id>', methods=['POST'])
def approve_player(id):
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    res = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{id}", headers=HEADERS).json()
    f = res.get('fields', {})
    m_list = get_airtable_data("Master List")
    highest_code = max([int(str(m['fields'].get('Code')).strip()) for m in m_list if str(m['fields'].get('Code')).isdigit() and 1000 < int(str(m['fields'].get('Code')).strip()) < 9000], default=1000)
    new_code = str(highest_code + 1)
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List", headers=HEADERS, json={"fields": {"First": f.get('First'), "Last": f.get('Last'), "Email": f.get('Email'), "Code": new_code}, "typecast": True})
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{id}", headers=HEADERS, json={"fields": {"Status": "Approved", "Assigned Code": new_code}, "typecast": True})
    send_email(f.get('Email'), "🎾 Welcome to the Gang!", f"<p>Approved. Login code: <b>{new_code}</b></p>")
    flash(f"Approved with Code {new_code}.", "success")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/approve_guest/<app_id>', methods=['POST'])
def approve_guest(app_id):
    if not session.get('user', {}).get('is_admin'): return redirect(url_for('index'))
    res = requests.get(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS).json()
    f = res.get('fields', {})
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Signups", headers=HEADERS, json={"fields": {"First": f.get('First'), "Last": f.get('Last'), "Player Code": "GUEST", "Label": f"GUEST of {f.get('Sponsor')}", "Level": f.get('Level')}})
    requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Applicants/{app_id}", headers=HEADERS, json={"fields": {"Status": "Approved"}})
    flash(f"Guest added to roster!", "success")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/attendance/<code_str>', methods=['POST'])
def attendance(code_str):
    if not session.get('user') or not session['user'].get('is_admin'): return "Unauthorized", 403
    status = request.form.get('status')
    strike_inc = 1 if status == 'Late' else 2 if status == 'No Show' else 0
    m_recs = get_airtable_data("Master List", filter_formula=f"{{Code}}='{code_str}'")
    
    player_level = ""
    if m_recs:
        player_level = m_recs[0]['fields'].get('Level', '')
        if strike_inc > 0:
            new_strikes = m_recs[0]['fields'].get('Strikes', 0) + strike_inc
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Master%20List/{m_recs[0]['id']}", headers=HEADERS, json={"fields": {"Strikes": new_strikes, "Paused": (new_strikes >= 3)}})
            
    requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS, json={"fields": {"Player Code": str(code_str), "Attendance": status, "Date": dt.datetime.now().strftime("%Y-%m-%d"), "Level": player_level}, "typecast": True})
    flash(f"Updated attendance for {code_str}", "info")
    AIRTABLE_CACHE.clear()
    return redirect(url_for('index'))

@app.route('/reorder', methods=['POST'])
def reorder():
    """Admin-only: move a signup record up or down in the manual roster order."""
    if not session.get('user') or not session['user'].get('is_admin'):
        return "Unauthorized", 403

    record_id = request.form.get('record_id')
    direction = request.form.get('direction')  # 'up' or 'down'

    # Use the same sort used by index() so positions match what admin sees
    signups = sorted(get_airtable_data("Signups"), key=sort_key)
    ids = [r['id'] for r in signups]

    if record_id not in ids:
        flash("Record not found — try refreshing.", "danger")
        return redirect(url_for('index'))

    idx = ids.index(record_id)
    if direction == 'up' and idx == 0:
        return redirect(url_for('index'))   # already at top
    if direction == 'down' and idx == len(ids) - 1:
        return redirect(url_for('index'))   # already at bottom

    swap_idx = idx - 1 if direction == 'up' else idx + 1

    # Assign 1-based Manual Order values to the two swapped records
    new_order_this = swap_idx + 1
    new_order_swap = idx + 1

    base_url = f"https://api.airtable.com/v0/{BASE_ID}/Signups"
    try:
        requests.patch(f"{base_url}/{signups[idx]['id']}", headers=HEADERS,
                       json={"fields": {"Manual Order": new_order_this}}, timeout=10)
        requests.patch(f"{base_url}/{signups[swap_idx]['id']}", headers=HEADERS,
                       json={"fields": {"Manual Order": new_order_swap}}, timeout=10)
    except Exception as e:
        flash(f"Reorder failed: {e}", "danger")
        return redirect(url_for('index'))

    # Bust Signups cache so the next page load shows the updated order
    keys_to_clear = [k for k in AIRTABLE_CACHE if k.startswith('Signups')]
    for k in keys_to_clear:
        del AIRTABLE_CACHE[k]

    return redirect(url_for('index'))

def get_saturday_weather(d_start='9:00 AM'):
    """Fetch Saturday's weather from Open-Meteo (free, no API key).
    Returns a short HTML string suitable for email, or empty string on failure."""
    try:
        lat, lon = "39.9936", "-105.0897"
        url = (f"https://api.open-meteo.com/v1/forecast"
               f"?latitude={lat}&longitude={lon}"
               f"&daily=weathercode,temperature_2m_max,temperature_2m_min,precipitation_probability_max"
               f"&temperature_unit=fahrenheit&timezone=America%2FDenver&forecast_days=16")
        data = requests.get(url, timeout=10).json()

        today = dt.date.today()
        days_ahead = (5 - today.weekday()) % 7 or 7   # next Saturday
        target = today + dt.timedelta(days=days_ahead)
        target_iso = target.isoformat()

        dates = data.get('daily', {}).get('time', [])
        if target_iso not in dates:
            return ""

        idx      = dates.index(target_iso)
        hi       = int(data['daily']['temperature_2m_max'][idx])
        lo       = int(data['daily']['temperature_2m_min'][idx])
        precip   = int(data['daily'].get('precipitation_probability_max', [0]*16)[idx] or 0)
        wcode    = data['daily']['weathercode'][idx]
        code_map = {
            0: "☀️ Clear", 1: "🌤 Mostly Clear", 2: "⛅ Partly Cloudy", 3: "☁️ Overcast",
            45: "🌫 Fog", 48: "🌫 Fog",
            51: "🌦 Drizzle", 53: "🌦 Drizzle", 55: "🌧 Drizzle",
            61: "🌧 Rain", 63: "🌧 Rain", 65: "🌧 Heavy Rain",
            71: "🌨 Snow", 73: "🌨 Snow", 75: "❄️ Heavy Snow",
            95: "⛈ Thunderstorm"
        }
        cond = code_map.get(wcode, "🌡 Varied")
        rain_note = f", {precip}% chance of rain" if precip >= 20 else ""
        return (f"<p>📅 <b>Saturday forecast</b> ({target.strftime('%b %d')}): "
                f"{cond} | High {hi}°F, Low {lo}°F{rain_note}. "
                f"<small><i>(via Open-Meteo, updated daily)</i></small></p>")
    except:
        return ""

# === SECTION 7: CRON / AUTOMATION ===

def _archive_and_clear_signups(settings, signups):
    """Archive all current signups then delete them from the Signups table.
    Called by both cron_monday (with emails) and the manual admin reset (no emails)."""
    d_date = settings[0]['fields'].get('Target Date', 'TBD') if settings else 'TBD'
    for r in signups:
        try:
            requests.post(f"https://api.airtable.com/v0/{BASE_ID}/Archive", headers=HEADERS,
                json={"fields": {
                    "First":       r['fields'].get('First'),
                    "Last":        r['fields'].get('Last'),
                    "Player Code": str(r['fields'].get('Player Code', '')),
                    "Date":        d_date,
                    "Attendance":  r['fields'].get('Label', 'Signed Up'),
                    "Level":       r['fields'].get('Level', '')
                }, "typecast": True})
        except: pass
        try:
            requests.delete(f"https://api.airtable.com/v0/{BASE_ID}/Signups/{r['id']}", headers=HEADERS)
        except: pass
    AIRTABLE_CACHE.clear()

# Helper for friendly numbers (1st, 2nd, 3rd)
def get_ordinal(n):
    if 11 <= (n % 100) <= 13: return str(n) + 'th'
    return str(n) + {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')

@app.route('/cron/monday')
def cron_monday():
    settings = get_airtable_data("Settings")
    d_date = settings[0]['fields'].get('Target Date', 'TBD') if settings else 'TBD'
    d_start = settings[0]['fields'].get('Start Time', 'TBD') if settings else 'TBD'
    play_mode = settings[0]['fields'].get('Play Mode', 'Open') if settings else 'Open'
    
    mode_descriptions = {
        'Open':  "This week we are in <b>Open</b> mode — sign up individually, first come first served across all available courts.",
        'Split': "This week we are in <b>Split</b> mode, with 3 courts reserved for each skill group. "
                 "<br><i>(I may shift numbers on Friday to a 4/2 arrangement if sign-ups support it.)</i>",
        'Team':  "This week we are in <b>Team</b> mode — captains sign up a full court (4 players) and can list reserves. "
                 "Log in, click <b>Start a Team</b>, and submit your court request. "
                 "I'll review and approve court assignments before the roster goes live.<br><br>"
                 "<b>After submitting your team:</b> you should receive a confirmation email within a few minutes. "
                 "If you don't, something may have gone wrong — contact Jim rather than submitting again.",
    }
    mode_explanation = mode_descriptions.get(play_mode, mode_descriptions['Open'])

    # Week Note: if set in Settings, prepend it (for special introductions like Team Mode launch)
    week_note = settings[0]['fields'].get('Week Note', '').strip() if settings else ''
    if week_note:
        mode_explanation = f"{week_note}<br><br>{mode_explanation}"
        # Clear the note so it doesn't repeat next week
        try:
            requests.patch(f"https://api.airtable.com/v0/{BASE_ID}/Settings/{settings[0]['id']}",
                headers=HEADERS, json={"fields": {"Week Note": ""}})
        except: pass

    signups = get_airtable_data("Signups", sort_field="Created Time")
    
    # 1. Calculate the number of each rating that actually PLAYED (ignores waitlist)
    played_levels = {}
    if play_mode == 'Split':
        lower = [s for s in signups if s['fields'].get('Level') == '3.0/3.5']
        upper = [s for s in signups if s['fields'].get('Level') == '4.0/4.5']
        l_cutoff = (min(len(lower), 12) // 4) * 4
        u_cutoff = (min(len(upper), 12) // 4) * 4
        playing_recs = lower[:l_cutoff] + upper[:u_cutoff]
    else:
        playing_cutoff = (min(len(signups), 24) // 4) * 4
        playing_recs = signups[:playing_cutoff]

    for r in playing_recs:
        lvl = r['fields'].get('Level', 'Unrated')
        played_levels[lvl] = played_levels.get(lvl, 0) + 1

    # 2. Log stats and Email Admin
    stats_msg = " | ".join([f"{k}: {v} players" for k, v in played_levels.items()])
    if stats_msg:
        log_activity("Weekly Play Stats", f"Played on {d_date} -> {stats_msg}")
        send_email(ADMIN_EMAIL, f"📊 Weekly Stats for {d_date}", f"<p>Here is the breakdown of ratings that made the cutoff and played this past Saturday:</p><h3>{stats_msg}</h3>")

    # 3. Archive everyone (with their Level) and clear signups
    _archive_and_clear_signups(settings, signups)
            
    # 4. Open signups for the new week (include Saturday weather forecast)
    try:
        weather_html = get_saturday_weather(d_start)
        emails = [m['fields'].get('Email') for m in get_airtable_data("Master List") if m['fields'].get('Email')]
        send_email(emails, f"🎾 Signups OPEN for {d_date}!",
            f"<h3>Signups are open!</h3>"
            f"<p><b>Time:</b> {d_start}</p>"
            f"{weather_html}"
            f"<p>{mode_explanation}</p>"
            f"<p><a href='{SITE_URL}'>Claim your spot!</a></p>",
            is_multiple=True)
    except: pass
        
    AIRTABLE_CACHE.clear()
    return "Monday reset, stats calculated, and emails sent successfully.", 200

@app.route('/cron/friday')
def cron_friday():
    settings = get_airtable_data("Settings")
    d_date = settings[0]['fields'].get('Target Date', 'TBD') if settings else 'TBD'
    d_start = settings[0]['fields'].get('Start Time', 'TBD') if settings else 'TBD'
    play_mode = settings[0]['fields'].get('Play Mode', 'Open') if settings else 'Open'
    
    signups = get_airtable_data("Signups", sort_field="Created Time")
    master_list = get_airtable_data("Master List")
    all_emails = [m['fields'].get('Email') for m in master_list if m['fields'].get('Email')]
    
    if play_mode == 'Open':
        total = len(signups)
        playing_cutoff = (min(total, 24) // 4) * 4
        playing_recs = signups[:playing_cutoff]
        waitlist_recs = signups[playing_cutoff:]
        
        C = total // 4
        W = total - playing_cutoff 
        needed = 4 - (total % 4) if total % 4 != 0 else 4
        
        big_picture = f"We have {W} on the waitlist, so if {needed} more join us, we will add a {get_ordinal(C + 1)} court!"
        
        # 1. Email Playing
        playing_emails = [r['fields'].get('Email') for r in playing_recs if r['fields'].get('Email')]
        if playing_emails:
            send_email(playing_emails, f"🎾 Roster Locked for {d_date}", f"<h3>You are on the board for tomorrow!</h3><p>Start Time: {d_start}</p><p>Check the live roster here: <a href='{SITE_URL}'>{SITE_URL}</a></p><p><i>Note: If you must drop, the late-cancel rules are now in effect.</i></p>", is_multiple=True)
            
        # 2. Email Waitlist Individually
        for idx, r in enumerate(waitlist_recs):
            em = r['fields'].get('Email')
            if em:
                send_email([em], f"🎾 Waitlist Status for {d_date}", f"<h3>You are on the waitlist!</h3><p>Just a heads up, the roster is locked and you are currently <b>{get_ordinal(idx+1)} of {len(waitlist_recs)}</b> on the Open waitlist.</p><p>Keep an eye out for sub requests! {C} courts are currently reserved.</p>")
                
        # 3. Email Big Picture Blast
        send_email(all_emails, "🎾 Friday Update: Player slot roundup for this week!", f"<h3>Friday Court Status</h3><p>Here is the big picture for this weekend: <b>{big_picture}</b></p><p>If you can play, jump in and help us fill the next court: <a href='{SITE_URL}'>{SITE_URL}</a></p>", is_multiple=True)

    else: 
        # SPLIT MODE LOGIC
        lower = [s for s in signups if s['fields'].get('Level') == '3.0/3.5']
        upper = [s for s in signups if s['fields'].get('Level') == '4.0/4.5']
        
        l_cutoff = (min(len(lower), 12) // 4) * 4
        u_cutoff = (min(len(upper), 12) // 4) * 4
        
        l_play, l_wait = lower[:l_cutoff], lower[l_cutoff:]
        u_play, u_wait = upper[:u_cutoff], upper[u_cutoff:]
        
        l_C, u_C = len(lower) // 4, len(upper) // 4
        l_needed = 4 - (len(lower) % 4) if len(lower) % 4 != 0 else 4
        u_needed = 4 - (len(upper) % 4) if len(upper) % 4 != 0 else 4
        
        l_status = f"we are full on 3.0/3.5 with {len(l_wait)} on the waitlist, so if {l_needed} more join, we will add a {get_ordinal(l_C + 1)} court for 3.0/3.5."
        if len(l_wait) == 0 and len(lower) < 12:
            l_status = f"we have {len(lower)} players for 3.0/3.5. If {l_needed} more join, we will add a {get_ordinal(l_C + 1)} court."
            
        u_status = f"we have {len(u_wait)} on the 4.0/4.5 waitlist, so if {u_needed} more join, we will add a {get_ordinal(u_C + 1)} court."
        if len(u_wait) == 0 and len(upper) < 12:
            u_status = f"we have {len(upper)} players for 4.0/4.5. If {u_needed} more join, we will add a {get_ordinal(u_C + 1)} court."
        
        big_picture = f"This week we are in Split mode. For the big picture: {l_status} And {u_status} <i>(We may shift to a 4 court / 2 court arrangement if the numbers support it!)</i>"

        # 1. Email Playing
        playing_emails = [r['fields'].get('Email') for r in (l_play + u_play) if r['fields'].get('Email')]
        if playing_emails:
            send_email(playing_emails, f"🎾 Roster Locked for {d_date}", f"<h3>You are on the board for tomorrow!</h3><p>Start Time: {d_start}</p><p>Check the live roster here: <a href='{SITE_URL}'>{SITE_URL}</a></p><p><i>Note: If you must drop, the late-cancel rules are now in effect.</i></p>", is_multiple=True)
            
        # 2. Email Waitlists Individually
        for idx, r in enumerate(l_wait):
            em = r['fields'].get('Email')
            if em: send_email([em], f"🎾 Waitlist Status for {d_date}", f"<h3>You are on the waitlist!</h3><p>Just a heads up, the roster is locked and you are currently <b>{get_ordinal(idx+1)} of {len(l_wait)}</b> on the 3.0/3.5 waitlist.</p><p>Keep an eye out for sub requests! {l_cutoff//4} courts are currently reserved for your level.</p>")
        for idx, r in enumerate(u_wait):
            em = r['fields'].get('Email')
            if em: send_email([em], f"🎾 Waitlist Status for {d_date}", f"<h3>You are on the waitlist!</h3><p>Just a heads up, the roster is locked and you are currently <b>{get_ordinal(idx+1)} of {len(u_wait)}</b> on the 4.0/4.5 waitlist.</p><p>Keep an eye out for sub requests! {u_cutoff//4} courts are currently reserved for your level.</p>")

        # 3. Email Big Picture Blast
        send_email(all_emails, "🎾 Friday Update: Player slot roundup for this week!", f"<h3>Friday Court Status</h3><p>Here is the big picture for this weekend:</p><p><b>{big_picture}</b></p><p>If you can play, jump in and help us fill out the next court: <a href='{SITE_URL}'>{SITE_URL}</a></p>", is_multiple=True)
        
    return "Friday reminder emails sent successfully.", 200

if __name__ == '__main__':
    app.run(debug=True)
