from flask import Flask, request, jsonify
import sqlite3
from datetime import datetime
import os

app = Flask(__name__, static_url_path='', static_folder='.')
DB_FILE = 'attendance.db'

# ==========================================
#        GLOBAL VARIABLES (Crucial Fix)
# ==========================================
# This determines the mode the Pi should be in.
CURRENT_MODE = "idle" 

# --- DATABASE SETUP ---
def init_db():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Table for Users
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (card_id TEXT PRIMARY KEY, name TEXT)''')
    
    # Table for Logs
    c.execute('''CREATE TABLE IF NOT EXISTS attendance 
                 (id INTEGER PRIMARY KEY AUTOINCREMENT, 
                  card_id TEXT, 
                  check_in TEXT, 
                  check_out TEXT, 
                  duration TEXT)''')
    conn.commit()
    conn.close()

# --- HELPER: Get active session ---
def get_active_session(card_id):
    """Finds if a user has checked in but not checked out."""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''SELECT id, check_in FROM attendance 
                 WHERE card_id = ? AND check_out IS NULL''', (card_id,))
    data = c.fetchone()
    conn.close()
    return data # Returns (id, check_in_time) or None
    
@app.route('/')
def index():
    # Serves the Single Page Dashboard
    return app.send_static_file('dashboard.html') 

# --- API ROUTE 1: ENROLL NEW CARD ---

@app.route('/api/enroll', methods=['POST'])
def enroll_user():
    data = request.json
    card_id = str(data.get('card_id'))
    name = data.get('name')

    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    try:
        # Try to insert new user
        c.execute("INSERT INTO users (card_id, name) VALUES (?, ?)", (card_id, name))
        conn.commit()
        msg = f"Successfully enrolled {name}"
        status = "success"
        # REMOVED THE LATEST_UNKNOWN_CARD LOGIC HERE
    except sqlite3.IntegrityError:
        # If card_id already exists
        msg = "Card already registered!"
        status = "error"
    finally:
        conn.close()

    return jsonify({"status": status, "message": msg})
# --- API ROUTE 2: CHECK IN / CHECK OUT ---
@app.route('/api/scan', methods=['POST'])
def scan_card():
    data = request.json
    card_id = str(data.get('card_id'))
    
    # Look for specific type ('checkin', 'checkout', or None)
    action_type = data.get('type') 
    
    # Get time from the Pi, default to server time if missing
    client_time = data.get('timestamp')
    if client_time:
        timestamp = client_time
    else:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    
    # 1. Check if user exists
    c.execute("SELECT name FROM users WHERE card_id = ?", (card_id,))
    user = c.fetchone()
    
    if not user:
        if CURRENT_MODE == 'enroll':
            placeholder_name = f"Unknown Card {card_id[-4:]}" # Use last 4 digits
            try:
                c.execute("INSERT INTO users (card_id, name) VALUES (?, ?)", (card_id, placeholder_name))
                conn.commit()
                conn.close()
                # Return 'enrolled' status so Client knows to beep successfully
                return jsonify({"status": "enrolled", "message": "Card Saved. Next!"})
            except sqlite3.IntegrityError:
                 conn.close()
                 return jsonify({"status": "error", "message": "Card already exists."})
                 
        conn.close()
        return jsonify({"status": "unknown", "message": "Unknown Card"})
    name = user[0]
    # 2. Check for active session
    active_session = get_active_session(card_id)
    
    # === SAFETY SETTING: MINIMUM TIME BEFORE CHECKOUT ===
    MINUTES_BEFORE_CHECKOUT = 1 # Set to 1 minute for testing

    # ==========================================
    #           STRICT LOGIC HANDLER
    # ==========================================
    
    # CASE A: User wants to CHECK IN (Force IN)
    if action_type == 'checkin':
        if active_session:
            conn.close()
            return jsonify({"status": "error", "message": f"{name} is already checked in!"})
        
        c.execute("INSERT INTO attendance (card_id, check_in) VALUES (?, ?)", (card_id, timestamp))
        conn.commit()
        response = {"status": "success", "message": f"Welcome, {name}!"}

    # CASE B: User wants to CHECK OUT (Force OUT)
    elif action_type == 'checkout':
        if not active_session:
            conn.close()
            return jsonify({"status": "error", "message": f"Cannot check out: {name} never checked in!"})
        
        session_id, check_in_time = active_session
        t1 = datetime.strptime(check_in_time, "%Y-%m-%d %H:%M:%S")
        t2 = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
        duration = str(t2 - t1)

        c.execute("UPDATE attendance SET check_out = ?, duration = ? WHERE id = ?", (timestamp, duration, session_id))
        conn.commit()
        response = {"status": "success", "message": f"Goodbye, {name}!"}

    # CASE C: AUTO MODE (Smart Toggle - For Pi & Default)
    else:
        if active_session:
            # User is IN -> Try to Check OUT
            session_id, check_in_time = active_session
            
            # Anti-Bounce Check
            t_in = datetime.strptime(check_in_time, "%Y-%m-%d %H:%M:%S")
            t_now = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            diff_minutes = (t_now - t_in).total_seconds() / 60
            
            if diff_minutes < MINUTES_BEFORE_CHECKOUT:
                remaining = int(MINUTES_BEFORE_CHECKOUT - diff_minutes)
                conn.close()
                return jsonify({
                    "status": "warning",
                    "message": f"Too soon! Wait {remaining} min to check out."
                })
            
            # Valid Checkout
            duration = str(t_now - t_in)
            c.execute('''UPDATE attendance SET check_out = ?, duration = ? 
                         WHERE id = ?''', (timestamp, duration, session_id))
            conn.commit()
            
            response = {
                "status": "checkout",
                "name": name,
                "message": f"Goodbye, {name}!"
            }
        else:
            # User is OUT -> Check IN
            c.execute("INSERT INTO attendance (card_id, check_in) VALUES (?, ?)", 
                      (card_id, timestamp))
            conn.commit()
            
            response = {
                "status": "checkin",
                "name": name,
                "message": f"Welcome, {name}!"
            }
        
    conn.close()
    return jsonify(response)

# --- API ROUTE 3: VIEW DATA (For Dashboard History) ---
@app.route('/api/history', methods=['GET'])
def get_history():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Join tables to get Name + Time
    c.execute('''SELECT users.name, attendance.check_in, attendance.check_out, attendance.duration 
                 FROM attendance 
                 JOIN users ON attendance.card_id = users.card_id
                 ORDER BY attendance.id DESC''')
    rows = c.fetchall()
    conn.close()
    return jsonify(rows)

# --- API ROUTE 4: LIST ALL USERS (For Dashboard User List) ---
@app.route('/api/users', methods=['GET'])
def get_users():
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # Get user info and their latest check-in status
    query = '''
        SELECT u.card_id, u.name, 
        (SELECT check_in FROM attendance a WHERE a.card_id = u.card_id AND a.check_out IS NULL) as status
        FROM users u
    '''
    c.execute(query)
    rows = c.fetchall()
    conn.close()
    
    # Format as a list of dictionaries
    users_list = []
    for r in rows:
        users_list.append({
            "card_id": r[0],
            "name": r[1],
            "active_checkin": r[2] # Will be None if not checked in
        })
    return jsonify(users_list)

# --- API ROUTE 5: GET/SET DEVICE MODE ---
@app.route('/api/mode', methods=['GET', 'POST'])
def handle_mode():
    global CURRENT_MODE
    
    if request.method == 'POST':
        data = request.json
        new_mode = data.get('mode')
        if new_mode in ['idle', 'attendance', 'enroll']:
            CURRENT_MODE = new_mode
            print(f"Mode changed to: {CURRENT_MODE}")
            return jsonify({"status": "success", "mode": CURRENT_MODE})
        return jsonify({"status": "error", "message": "Invalid mode"})
    
    # GET request (Pi polling)
    return jsonify({"mode": CURRENT_MODE})

@app.route('/api/rename', methods=['POST'])
def rename_user():
    data = request.json
    card_id = data.get('card_id')
    new_name = data.get('name')
    
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute("UPDATE users SET name = ? WHERE card_id = ?", (new_name, card_id))
    conn.commit()
    conn.close()
    
    return jsonify({"status": "success", "message": "User registered successfully"})
if __name__ == '__main__':
    init_db()
    # host='0.0.0.0' allows the Pi to connect to this computer
    app.run(debug=True, host='0.0.0.0', port=5000)
