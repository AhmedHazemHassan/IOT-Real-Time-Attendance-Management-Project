import time
from datetime import datetime
import RPi.GPIO as GPIO
from mfrc522 import SimpleMFRC522
import threading
import queue
import smbus2
import requests  # NEW: Library to talk to the backend

# ==========================================
#               CONFIGURATION
# ==========================================
# CHANGE THIS to your Backend PC's IP Address!
SERVER_URL = "http://192.168.137.93:5000"

BUZZER_PIN = 29
I2C_BUS = 1
DS3231_ADDRESS = 0x68

# Ultrasonic pins
ULTRASONIC_TRIG = 31
ULTRASONIC_ECHO = 33
ULTRASONIC_THRESHOLD_CM = 50

# ==========================================
#           GLOBAL SHARED VARIABLES
# ==========================================
# NEW: This controls the device mode remotely
SERVER_MODE = "idle" 

CURRENT_DISTANCE = 999.0
LAST_SCANNED_ID = None
LAST_SCANNED_TEXT = None
CURRENT_RTC_TIME = datetime.now()

RFID_ENABLED = False
PREVIOUS_CARD_ID = None

STOP_THREADS = False
data_lock = threading.Lock()
buzzer_queue = queue.Queue()

# ==========================================
#               HARDWARE SETUP
# ==========================================
GPIO.setwarnings(False)
GPIO.setmode(GPIO.BOARD)
GPIO.setup(BUZZER_PIN, GPIO.OUT)
GPIO.setup(ULTRASONIC_TRIG, GPIO.OUT)
GPIO.setup(ULTRASONIC_ECHO, GPIO.IN)

rfid = SimpleMFRC522()

# ==========================================
#           THREAD 1: ULTRASONIC (UNCHANGED)
# ==========================================
def ultrasonic_worker():
    global CURRENT_DISTANCE
    while not STOP_THREADS:
        try:
            GPIO.output(ULTRASONIC_TRIG, False)
            time.sleep(0.05)
            GPIO.output(ULTRASONIC_TRIG, True)
            time.sleep(0.00001)
            GPIO.output(ULTRASONIC_TRIG, False)

            start_time = time.time()
            stop_time = time.time()
            timeout = time.time() + 0.1

            while GPIO.input(ULTRASONIC_ECHO) == 0:
                start_time = time.time()
                if time.time() > timeout: break

            while GPIO.input(ULTRASONIC_ECHO) == 1:
                stop_time = time.time()
                if time.time() > timeout: break

            elapsed = stop_time - start_time
            dist = (elapsed * 34300) / 2
            
            with data_lock:
                CURRENT_DISTANCE = round(dist, 1)
                
        except Exception:
            pass
        time.sleep(0.1)

# ==========================================
#           THREAD 2: RFID READER (UNCHANGED)
# ==========================================

def rfid_worker():
    global LAST_SCANNED_ID, LAST_SCANNED_TEXT, PREVIOUS_CARD_ID
    
    print("[DEBUG] RFID Thread Started") # Add this
    
    while not STOP_THREADS:
        if not RFID_ENABLED:
            time.sleep(0.1)
            continue
        
        try:
            print("[DEBUG] Waiting for card...") # Optional: Uncomment to verify it gets here
            id = rfid.read_id() # This waits for a card
            text = None 
            
            if id == PREVIOUS_CARD_ID:
                time.sleep(0.1)
                continue
            
            with data_lock:
                LAST_SCANNED_ID = id
                LAST_SCANNED_TEXT = text
            
            PREVIOUS_CARD_ID = id
            time.sleep(0.5)

        except Exception as e:
            # CHANGE THIS PART: Print the error instead of passing
            print(f"[ERROR] RFID Reader Failed: {e}")
            time.sleep(1.0) # Sleep to prevent spamming errors

# ==========================================
#           THREAD 3: BUZZER MANAGER (UNCHANGED)
# ==========================================
def buzzer_worker():
    while not STOP_THREADS:
        try:
            duration = buzzer_queue.get(timeout=0.5)
            GPIO.output(BUZZER_PIN, True)
            time.sleep(duration)
            GPIO.output(BUZZER_PIN, False)
            time.sleep(0.1)
            buzzer_queue.task_done()
        except queue.Empty:
            continue
        except Exception as e:
            print(f"Buzzer Error: {e}")

# ==========================================
#           THREAD 4: RTC UPDATER (UNCHANGED)
# ==========================================
def rtc_worker():
    global CURRENT_RTC_TIME
    def bcd_to_dec(b): return (b // 16) * 10 + (b % 16)

    try:
        bus = smbus2.SMBus(I2C_BUS)
    except:
        return

    while not STOP_THREADS:
        try:
            data = bus.read_i2c_block_data(DS3231_ADDRESS, 0x00, 7)
            second = bcd_to_dec(data[0])
            minute = bcd_to_dec(data[1])
            hour = bcd_to_dec(data[2])
            day = bcd_to_dec(data[4])
            month = bcd_to_dec(data[5])
            year = 2025 + bcd_to_dec(data[6])
            
            new_time = datetime(year, month, day, hour, minute, second)
            with data_lock:
                CURRENT_RTC_TIME = new_time
        except Exception:
            with data_lock:
                CURRENT_RTC_TIME = datetime.now()
        
        time.sleep(1.0)

# ==========================================
#      THREAD 5: MODE CHECKER (NEW)
# ==========================================
def mode_checker_worker():
    """Polls the server to check what mode we should be in."""
    global SERVER_MODE
    while not STOP_THREADS:
        try:
            # Short timeout so it doesn't hang
            response = requests.get(f"{SERVER_URL}/api/mode", timeout=2)
            if response.status_code == 200:
                new_mode = response.json().get("mode", "idle")
                if new_mode != SERVER_MODE:
                    print(f"\n[COMMAND RECEIVED] Switching to: {new_mode.upper()}")
                    with data_lock:
                        SERVER_MODE = new_mode
        except Exception:
            pass
        time.sleep(2.0) # Check every 2 seconds

# ==========================================
#           HELPER FUNCTIONS
# ==========================================

def beep(duration=0.5):
    buzzer_queue.put(duration)

def get_current_time():
    with data_lock:
        return CURRENT_RTC_TIME

def get_rtc_time_string():
    """Formats the current RTC time for the backend"""
    with data_lock:
        return CURRENT_RTC_TIME.strftime("%Y-%m-%d %H:%M:%S")

def consume_rfid_data():
    global LAST_SCANNED_ID
    with data_lock:
        if LAST_SCANNED_ID is not None:
            found_id = LAST_SCANNED_ID
            LAST_SCANNED_ID = None
            return found_id
    return None

def get_current_distance():
    with data_lock:
        return CURRENT_DISTANCE

# ==========================================
#           BACKEND COMMUNICATION
# ==========================================

def api_enroll(card_id, name):
    """Sends new card data to the server"""
    try:
        payload = {"card_id": str(card_id), "name": name}
        response = requests.post(f"{SERVER_URL}/api/enroll", json=payload, timeout=5)
        return response.json()
    except Exception as e:
        print(f"Network Error: {e}")
        return {"status": "error", "message": "Server Offline"}

def api_scan(card_id):
    """Sends scan data + RTC Timestamp to the server"""
    try:
        timestamp = get_rtc_time_string()
        # Note: We do NOT send 'type' here. 
        # The Server's Smart Logic decides if it is Check-in or Check-out.
        payload = {
            "card_id": str(card_id), 
            "timestamp": timestamp
        }
        response = requests.post(f"{SERVER_URL}/api/scan", json=payload, timeout=5)
        return response.json()
    except Exception as e:
        print(f"Network Error: {e}")
        return {"status": "error", "message": "Server Offline"}

# ==========================================
#           MAIN LOGIC (MODIFIED)
# ==========================================

def run_enroll_logic():
    global RFID_ENABLED, PREVIOUS_CARD_ID
    # REPLACED: Non-blocking logic for remote control
    
    dist = get_current_distance()
    person_detected = dist < ULTRASONIC_THRESHOLD_CM
    
    if person_detected:
        if not RFID_ENABLED:
            print(">> ENROLL MODE: Person detected. Scan card now.")
            beep(0.2)
        RFID_ENABLED = True
        
        card_id = consume_rfid_data()
        
        if card_id:
            beep(0.1) # Short beep on scan
            print(f"--------------------------------")
            print(f"    SCANNED NEW CARD ID: {card_id}")
            print("Sending to Dashboard...")
            res = api_scan(card_id)
            
            # --- NEW HANDLER ---
            status = res.get('status')
            message = res.get('message', 'No msg')
            print(f"Server: {message}")

            if status == 'enrolled':
                print(">> SUCCESS: Card Auto-Saved.")
                beep(0.1); time.sleep(0.1); beep(0.1) # Success Double Beep
            elif status == 'error':
                 print(">> IGNORED: Already exists.")
                 beep(0.5) # Long Error Beep
            # -------------------
            
            time.sleep(1.0) # Fast debounce so you can scan the next one quickly
            
    else:
        RFID_ENABLED = False
        PREVIOUS_CARD_ID = None

def run_attendance_logic():
    global RFID_ENABLED, PREVIOUS_CARD_ID
    # REPLACED: Non-blocking logic for remote control
    
    dist = get_current_distance()
    person_detected = dist < ULTRASONIC_THRESHOLD_CM
    
    if person_detected:
        if not RFID_ENABLED:
            print(">> ATTENDANCE: Ready to scan...")
            beep(0.2)
        RFID_ENABLED = True
        
        card_id = consume_rfid_data()
        
        if card_id:
            print(f"Scanning Card: {card_id}...")
            
            # --- COMMUNICATE WITH BACKEND ---
            result = api_scan(card_id)
            status = result.get('status')
            message = result.get('message', 'No response')
            
            print(f"SERVER: {message}")
            
            # --- HANDLE RESPONSES ---
            if status == 'checkin':
                beep(0.5) 
            elif status == 'checkout':
                beep(0.2); time.sleep(0.1); beep(0.2)
            elif status == 'warning':
                print("(!) Ignored: Scan too soon.")
                beep(0.1); time.sleep(0.1); beep(0.1)
            elif status == 'unknown':
                print("(!) Unknown Card.")
                beep(0.1); beep(0.1); beep(0.1)
            else:
                beep(1.0)
            
            time.sleep(1.0)
    else:
        RFID_ENABLED = False
        PREVIOUS_CARD_ID = None

if __name__ == "__main__":
    try:
        print("Starting System...")
        
        t1 = threading.Thread(target=ultrasonic_worker, daemon=True)
        t1.start()
        
        t2 = threading.Thread(target=rfid_worker, daemon=True)
        t2.start()
        
        t3 = threading.Thread(target=buzzer_worker, daemon=True)
        t3.start()
        
        t4 = threading.Thread(target=rtc_worker, daemon=True)
        t4.start()

        # NEW: Start the mode checker
        t5 = threading.Thread(target=mode_checker_worker, daemon=True)
        t5.start()
        
        print(f"Connected to {SERVER_URL}")
        print("Threads Running. Waiting for Dashboard commands...")
        beep(0.2); beep(0.2)
        
        while True:
            # Check the global mode variable (updated by the thread)
            current_mode = SERVER_MODE
            
            if current_mode == 'attendance':
                run_attendance_logic()
            elif current_mode == 'enroll':
                run_enroll_logic()
            else:
                # Idle mode
                RFID_ENABLED = False
                time.sleep(0.5)
            
            time.sleep(0.1)
            
    except KeyboardInterrupt:
        RFID_ENABLED = False
        print("Stopping System...")
    finally:
        STOP_THREADS = True
        GPIO.cleanup()
        print("System Shutdown.")
