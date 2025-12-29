import subprocess
import sys
import time
import os
import signal

def start_process(script_name: str):
    python_exe = sys.executable or "python3"
    # Note: On Raspberry Pi (Linux), we don't use CREATE_NEW_CONSOLE
    return subprocess.Popen([python_exe, script_name])

def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(base_dir)

    print("Starting backend server (backend.py)...")
    backend_proc = start_process("backend.py")

    time.sleep(5)

    print("Starting client (client.py)...")
    client_proc = start_process("client.py")

    # This function runs when the Pi tells the script to stop
    def shutdown_handler(signum, frame):
        print("Shutdown signal received. Killing sub-processes...")
        client_proc.terminate()
        backend_proc.terminate()
        sys.exit(0)

    # Register the shutdown signals
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    print("Processes started. Monitoring... (Press Ctrl+C to stop manually)")
    
    # Keep the launcher running so systemd can manage it
    while True:
        time.sleep(1)

if __name__ == "__main__":
    main()