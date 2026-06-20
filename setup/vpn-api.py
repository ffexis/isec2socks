#!/usr/bin/env python3
"""
VPN API Server with interactive authentication support.
Supports both Web (SSE) and CLI modes.
"""

import bottle
import subprocess
import os
import json
import re
import signal
import threading
import time
import select
import fcntl

app = bottle.Bottle()
CONFIG_FILE = '/etc/vpn-conf.json'
VPN_CMD = '/opt/iSecSP/vpn_cmdline'
VPN_SCRIPT = '/usr/local/bin/vpn'
PID_FILE = '/var/run/vpn-api.pid'

# Prompt detection patterns
PROMPT_PATTERNS = [
    r'[:：]\s*$',                    # Ends with colon
    r'\?\s*$',                       # Ends with question mark
    r'请输入.*$',                    # Chinese input prompt
    r'Please enter.*$',              # English input prompt
    r'Password.*[:：]?\s*$',         # Password prompt
    r'Second factor.*[:：]?\s*$',    # Second factor prompt
    r'\[\d+\].*$',                   # Option list
]


class VPNSessionManager:
    """Manages VPN connection session with interactive authentication support."""
    
    def __init__(self):
        self.process = None
        self.status = 'idle'  # idle | connecting | waiting_input | connected | failed
        self.log_buffer = []
        self.pending_prompt = None
        self._lock = threading.Lock()
        self._read_thread = None
        self._stop_event = threading.Event()
    
    def _detect_prompt(self, text):
        """Detect if the text contains an input prompt."""
        # Remove ANSI escape codes
        clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', text)
        for pattern in PROMPT_PATTERNS:
            if re.search(pattern, clean, re.IGNORECASE):
                return True
        return False
    
    def _extract_prompt(self, text):
        """Extract the last line as prompt text."""
        lines = text.strip().split('\n')
        return lines[-1].strip() if lines else ''
    
    def _read_output_loop(self):
        """Background thread: continuously read output and detect prompts."""
        fd = self.process.stdout.fileno()
        fl = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, fl | os.O_NONBLOCK)
        
        buffer = ''
        tun0_check_counter = 0
        
        while not self._stop_event.is_set() and self.process.poll() is None:
            try:
                ready, _, _ = select.select([fd], [], [], 0.1)
            except (ValueError, OSError):
                break
            
            if ready:
                try:
                    chunk = os.read(fd, 1024).decode('utf-8', errors='ignore')
                    if chunk:
                        buffer += chunk
                        with self._lock:
                            self.log_buffer.append(chunk)
                            # Keep only last 1000 chunks to prevent memory overflow
                            if len(self.log_buffer) > 1000:
                                self.log_buffer = self.log_buffer[-500:]
                        
                        # Detect prompt
                        if self._detect_prompt(buffer):
                            with self._lock:
                                self.status = 'waiting_input'
                                self.pending_prompt = self._extract_prompt(buffer)
                            return  # Pause reading, wait for input
                except (BlockingIOError, OSError):
                    pass
            
            # Check for tun0 interface every 5 iterations (~0.5s)
            tun0_check_counter += 1
            if tun0_check_counter >= 5:
                tun0_check_counter = 0
                if self._check_tun0():
                    with self._lock:
                        self.status = 'connected'
                    return
        
        # Process ended
        with self._lock:
            if self.process.poll() is not None:
                if self.process.returncode == 0:
                    self.status = 'connected'
                else:
                    self.status = 'failed'
    
    def _check_tun0(self):
        """Check if tun0 interface exists."""
        try:
            result = subprocess.run(
                ['ip', 'route'],
                capture_output=True,
                text=True,
                timeout=2
            )
            return 'tun0' in result.stdout
        except:
            return False
    
    def start(self, host, user, password):
        """Start VPN process, non-blocking, returns immediately."""
        with self._lock:
            if self.process and self.process.poll() is None:
                return {'error': 'Already running', 'status': self.status}
            
            # Reset state
            self.log_buffer = []
            self.pending_prompt = None
            self._stop_event.clear()
            
            try:
                # Ensure VPN daemon is running
                daemon_check = subprocess.run(
                    ['pgrep', '-f', 'isecspdaemon'],
                    capture_output=True
                )
                if daemon_check.returncode != 0:
                    self.log_buffer.append('[INFO] Starting VPN daemon...\n')
                    subprocess.Popen(
                        ['/usr/bin/isecspdaemon'],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    time.sleep(1.5)
                
                self.process = subprocess.Popen(
                    [VPN_CMD, '-h', host, '-u', user, '-p', password],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=False,
                    bufsize=0
                )
                self.status = 'connecting'
                self.log_buffer.append('[INFO] VPN client process started.\n')
                
                # Start background thread to read output
                self._read_thread = threading.Thread(target=self._read_output_loop, daemon=True)
                self._read_thread.start()
                
                return {'status': 'connecting'}
            except Exception as e:
                self.status = 'failed'
                self.log_buffer.append(f'[ERROR] Failed to start VPN: {str(e)}\n')
                return {'error': str(e), 'status': 'failed'}
    
    def send_input(self, value):
        """Send user input to VPN process and continue reading."""
        with self._lock:
            if self.status != 'waiting_input':
                return {'error': 'Not waiting for input', 'status': self.status}
            
            if not self.process or self.process.poll() is not None:
                return {'error': 'Process not running', 'status': 'failed'}
            
            try:
                self.process.stdin.write((value + '\n').encode('utf-8'))
                self.process.stdin.flush()
                self.status = 'connecting'
                self.pending_prompt = None
                
                # Restart reading thread
                self._read_thread = threading.Thread(target=self._read_output_loop, daemon=True)
                self._read_thread.start()
                
                return {'status': 'connecting'}
            except Exception as e:
                self.status = 'failed'
                return {'error': str(e), 'status': 'failed'}
    
    def cancel(self):
        """Cancel current connection."""
        with self._lock:
            if self.process and self.process.poll() is None:
                self._stop_event.set()
                try:
                    self.process.terminate()
                    self.process.wait(timeout=3)
                except:
                    try:
                        self.process.kill()
                    except:
                        pass
            
            self.process = None
            self.status = 'idle'
            self.pending_prompt = None
            return {'status': 'idle'}
    
    def disconnect(self):
        """Disconnect VPN and stop all services."""
        with self._lock:
            self._stop_event.set()
            
            # Stop VPN process
            if self.process and self.process.poll() is None:
                try:
                    # Send stop command
                    stop_process = subprocess.Popen(
                        [VPN_CMD, '-s'],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    stop_process.wait(timeout=5)
                except:
                    if self.process.poll() is None:
                        try:
                            self.process.terminate()
                            self.process.wait(timeout=3)
                        except:
                            try:
                                self.process.kill()
                            except:
                                pass
            
            # Stop GOST and Route Guardian via bash script
            try:
                subprocess.run([VPN_SCRIPT, 'off'], timeout=10, capture_output=True)
            except:
                pass
            
            self.process = None
            self.status = 'idle'
            self.pending_prompt = None
            self.log_buffer = []
            
            return {'status': 'idle'}
    
    def get_status(self):
        """Get current status."""
        with self._lock:
            return {
                'status': self.status,
                'pending_prompt': self.pending_prompt,
                'connected': self.status == 'connected'
            }
    
    def get_log(self):
        """Get all logs."""
        with self._lock:
            return ''.join(self.log_buffer)
    
    def wait_for_tun0(self, timeout=15):
        """Wait for tun0 interface to appear."""
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                result = subprocess.run(
                    ['ip', 'route'],
                    capture_output=True,
                    text=True,
                    timeout=2
                )
                if 'tun0' in result.stdout:
                    return True
            except:
                pass
            time.sleep(0.5)
        return False
    
    def start_services(self):
        """Start GOST and Route Guardian after VPN is connected."""
        try:
            subprocess.run([VPN_SCRIPT, 'on-services'], timeout=10, capture_output=True)
            return {'success': True}
        except Exception as e:
            return {'error': str(e)}


# Global session manager
session = VPNSessionManager()


def read_json_config():
    """Read configuration from JSON file."""
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)


def write_json_config(data):
    """Write configuration to JSON file."""
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')


# ==================== API Routes ====================

@app.route('/api/vpn/connect', method='POST')
def vpn_connect():
    """Start VPN connection."""
    data = bottle.request.json or {}
    config = read_json_config()
    
    host = data.get('host') or os.environ.get('VPN_HOST') or config.get('VPN_HOST')
    user = data.get('user') or os.environ.get('VPN_USER') or config.get('VPN_USER')
    password = data.get('password') or os.environ.get('VPN_PASS') or config.get('VPN_PASS')
    
    if not all([host, user, password]):
        return {'error': 'Missing required credentials', 'status': 'failed'}
    
    return session.start(host, user, password)


@app.route('/api/vpn/input', method='POST')
def vpn_input():
    """Submit user input for authentication."""
    data = bottle.request.json or {}
    value = data.get('value', '')
    
    if not value:
        return {'error': 'No input provided', 'status': session.status}
    
    return session.send_input(value)


@app.route('/api/vpn/cancel', method='POST')
def vpn_cancel():
    """Cancel current connection."""
    return session.cancel()


@app.route('/api/vpn/off', method='POST')
def vpn_off():
    """Disconnect VPN."""
    return session.disconnect()


@app.route('/api/vpn/status')
def vpn_status():
    """Get VPN status."""
    base_status = session.get_status()
    
    # Check auxiliary services
    try:
        gost_running = subprocess.run(
            ['pgrep', '-f', 'gost -L'],
            capture_output=True
        ).returncode == 0
        guardian_running = subprocess.run(
            ['pgrep', '-f', 'vpn-route-guardian'],
            capture_output=True
        ).returncode == 0
    except:
        gost_running = False
        guardian_running = False
    
    base_status['gost'] = 'RUNNING' if gost_running else 'DOWN'
    base_status['guardian'] = 'RUNNING' if guardian_running else 'DOWN'
    
    return base_status


@app.route('/api/vpn/log')
def vpn_log():
    """Get VPN logs."""
    return {'log': session.get_log()}


@app.route('/api/vpn/log/stream')
def vpn_log_stream():
    """SSE stream for real-time logs and status."""
    response = bottle.Response()
    response.content_type = 'text/event-stream'
    response.cache_control = 'no-cache'
    response.connection = 'keep-alive'
    
    def generate():
        last_len = 0
        last_status = None
        last_prompt = None
        
        # Send initial status immediately
        with session._lock:
            initial_status = session.status
            initial_log_count = len(session.log_buffer)
            last_len = initial_log_count
            last_status = initial_status
        
        yield f'data: {json.dumps({"type": "status", "status": initial_status}, ensure_ascii=False)}\n\n'
        
        while True:
            with session._lock:
                current_status = session.status
                current_prompt = session.pending_prompt
                current_len = len(session.log_buffer)
                
                # Send new logs
                if current_len > last_len:
                    new_logs = ''.join(session.log_buffer[last_len:])
                    last_len = current_len
                    
                    data = {
                        'type': 'log',
                        'log': new_logs,
                        'status': current_status
                    }
                    if current_prompt:
                        data['prompt'] = current_prompt
                    
                    yield f'data: {json.dumps(data, ensure_ascii=False)}\n\n'
                
                # Send status change
                if current_status != last_status:
                    last_status = current_status
                    data = {
                        'type': 'status',
                        'status': current_status
                    }
                    if current_prompt:
                        data['prompt'] = current_prompt
                    
                    yield f'data: {json.dumps(data, ensure_ascii=False)}\n\n'
                    
                    # If connected, start services and end stream
                    if current_status == 'connected':
                        session.start_services()
                        yield f'data: {json.dumps({"type": "services_started"}, ensure_ascii=False)}\n\n'
                        break
                    
                    # If failed, end stream
                    if current_status == 'failed':
                        break
                
                # Send prompt if changed
                if current_prompt and current_prompt != last_prompt:
                    last_prompt = current_prompt
                    yield f'data: {json.dumps({"type": "prompt", "prompt": current_prompt, "status": current_status}, ensure_ascii=False)}\n\n'
            
            time.sleep(0.1)
    
    response.body = generate()
    return response


@app.route('/api/config')
def get_config():
    """Read config (passwords masked)."""
    try:
        config = read_json_config()
        for k in ('VPN_HOST', 'VPN_USER', 'VPN_PASS', 'VPN_SECOND_AUTH'):
            env_val = os.environ.get(k)
            if env_val is not None:
                config[k] = env_val
        masked = {}
        for k, v in config.items():
            if 'PASS' in k or 'AUTH' in k:
                masked[k] = '****' if v else v
            else:
                masked[k] = v
        return masked
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/api/config', method='PUT')
def update_config():
    """Update config."""
    data = bottle.request.json
    if not data:
        return {'error': 'No JSON body provided'}, 400
    try:
        config = read_json_config()
        for key, value in data.items():
            if key in ('VPN_HOST', 'VPN_USER', 'VPN_PASS', 'VPN_SECOND_AUTH'):
                config[key] = value
        write_json_config(config)
        return {'success': True, 'message': 'Config updated. Restart VPN to apply.'}
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/api/health')
def health():
    """Health check."""
    return {'status': 'ok'}


@app.route('/')
def index():
    """Serve Web UI."""
    return bottle.static_file('index.html', root='/etc/isec2socks')


# ==================== CLI Mode ====================

def cli_connect():
    """CLI mode: synchronous blocking execution with terminal interaction."""
    print("[CLI] Starting VPN connection...")
    
    config = read_json_config()
    host = os.environ.get('VPN_HOST') or config.get('VPN_HOST')
    user = os.environ.get('VPN_USER') or config.get('VPN_USER')
    password = os.environ.get('VPN_PASS') or config.get('VPN_PASS')
    
    if not all([host, user, password]):
        print("[ERROR] Missing required credentials in config or environment.")
        return 1
    
    # Start daemon if not running
    if subprocess.run(['pgrep', '-f', 'isecspdaemon'], capture_output=True).returncode != 0:
        print("[CLI] Starting VPN daemon...")
        subprocess.Popen(['/usr/bin/isecspdaemon'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1.5)
    
    # Start VPN process
    try:
        process = subprocess.Popen(
            [VPN_CMD, '-h', host, '-u', user, '-p', password],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1
        )
    except Exception as e:
        print(f"[ERROR] Failed to start VPN client: {e}")
        return 1
    
    buffer = ''
    
    try:
        while True:
            char = process.stdout.read(1)
            if not char:
                break
            
            buffer += char
            print(char, end='', flush=True)
            
            # Detect prompt
            clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', buffer)
            for pattern in PROMPT_PATTERNS:
                if re.search(pattern, clean, re.IGNORECASE):
                    try:
                        value = input()
                        process.stdin.write(value + '\n')
                        process.stdin.flush()
                        buffer = ''
                        break
                    except EOFError:
                        process.terminate()
                        return 1
    except KeyboardInterrupt:
        print("\n[CLI] Interrupted by user.")
        process.terminate()
        return 1
    
    process.wait()
    
    if process.returncode == 0:
        print("\n[CLI] VPN connected successfully.")
        
        # Wait for tun0
        print("[CLI] Waiting for tun0 interface...")
        if session.wait_for_tun0():
            print("[CLI] tun0 interface is ready.")
            
            # Start services
            print("[CLI] Starting GOST and Route Guardian...")
            result = session.start_services()
            if result.get('success'):
                print("[CLI] All services started.")
                return 0
            else:
                print(f"[ERROR] Failed to start services: {result.get('error')}")
                return 1
        else:
            print("[ERROR] tun0 interface not detected.")
            return 1
    else:
        print(f"\n[ERROR] VPN connection failed with code {process.returncode}.")
        return 1


if __name__ == '__main__':
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == 'connect':
        # CLI mode
        sys.exit(cli_connect())
    else:
        # API server mode
        with open(PID_FILE, 'w') as f:
            f.write(str(os.getpid()))
        bottle.run(app, host='0.0.0.0', port=31081, server='cheroot')
