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
import pty
import struct
import termios
import errno

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
        self.master_fd = None  # PTY master file descriptor
        self.status = 'idle'  # idle | connecting | waiting_input | connected | failed
        self.log_buffer = []
        self.pending_prompt = None
        self._lock = threading.Lock()
        self._read_thread = None
        self._stop_event = threading.Event()
        self.second_auth = None  # Pre-configured second auth value
        self.second_auth_used = False  # Track if auto-filled second auth was used
        self.env_override = False  # Track if environment variables override config
        self.user_provided_auth = None  # User-provided second auth value
    
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
        """Background thread: continuously read output from PTY and detect prompts."""
        # Set non-blocking on master fd
        flags = fcntl.fcntl(self.master_fd, fcntl.F_GETFL)
        fcntl.fcntl(self.master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        
        buffer = ''
        tun0_check_counter = 0
        
        while not self._stop_event.is_set() and self.process.poll() is None:
            try:
                ready, _, _ = select.select([self.master_fd], [], [], 0.1)
            except (ValueError, OSError):
                break
            
            if ready:
                try:
                    chunk = os.read(self.master_fd, 4096)
                    if not chunk:
                        # EOF reached, process ended
                        break
                    chunk_str = chunk.decode('utf-8', errors='ignore')
                    if chunk_str:
                        buffer += chunk_str
                        with self._lock:
                            self.log_buffer.append(chunk_str)
                    
                    # Detect prompt
                    if self._detect_prompt(buffer):
                        # Try auto-fill second auth if available and not used yet
                        if self.second_auth and not self.second_auth_used:
                            self.log_buffer.append('[INFO] Auto-filling second authentication...\n')
                            self.second_auth_used = True
                            os.write(self.master_fd, (self.second_auth + '\n').encode('utf-8'))
                            buffer = ''
                            # Continue reading without changing status
                            continue
                        
                        with self._lock:
                            self.status = 'waiting_input'
                            self.pending_prompt = self._extract_prompt(buffer)
                        return  # Pause reading, wait for input
                except OSError as e:
                    # PTY slave closed - this is expected when process exits
                    if hasattr(e, 'errno') and e.errno == errno.EIO:
                        break
                    # Other errors: continue trying
                    pass
            
            # Check for tun0 interface every 5 iterations (~0.5s)
            tun0_check_counter += 1
            if tun0_check_counter >= 5:
                tun0_check_counter = 0
                if self._check_tun0():
                    # Wait 0.5s to collect remaining output
                    time.sleep(0.5)
                    # Drain any remaining output
                    while True:
                        try:
                            ready, _, _ = select.select([self.master_fd], [], [], 0.1)
                            if not ready:
                                break
                            chunk = os.read(self.master_fd, 4096)
                            if not chunk:
                                break
                            chunk_str = chunk.decode('utf-8', errors='ignore')
                            if chunk_str:
                                with self._lock:
                                    self.log_buffer.append(chunk_str)
                        except OSError as e:
                            if hasattr(e, 'errno') and e.errno == errno.EIO:
                                break
                            break
                    # Update config with user-provided auth if applicable
                    self._update_config_with_auth()
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
    
    def _update_config_with_auth(self):
        """Update config file with user-provided second auth if applicable."""
        if self.user_provided_auth and not self.env_override:
            try:
                config = read_json_config()
                config['VPN_SECOND_AUTH'] = self.user_provided_auth
                write_json_config(config)
                self.log_buffer.append('[INFO] Second authentication saved to config.\n')
            except Exception as e:
                self.log_buffer.append(f'[WARN] Failed to save second auth to config: {str(e)}\n')
    
    def start(self, host, user, password, second_auth=None, env_override=False):
        """Start VPN process with PTY, non-blocking, returns immediately."""
        with self._lock:
            if self.process and self.process.poll() is None:
                return {'error': 'Already running', 'status': self.status}
            
            # Reset state
            self.log_buffer = []
            self.pending_prompt = None
            self._stop_event.clear()
            self.second_auth = second_auth
            self.second_auth_used = False
            self.env_override = env_override
            
            # Close existing master_fd if any
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except:
                    pass
                self.master_fd = None
            
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
                
                # Create PTY (pseudo-terminal)
                master_fd, slave_fd = pty.openpty()
                self.master_fd = master_fd
                
                # Set terminal size (optional, but helps some programs)
                winsize = struct.pack('HHHH', 24, 80, 0, 0)
                fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                
                # Start process with PTY as stdin/stdout/stderr
                self.process = subprocess.Popen(
                    [VPN_CMD, '-h', host, '-u', user, '-p', password],
                    stdin=slave_fd,
                    stdout=slave_fd,
                    stderr=slave_fd,
                    close_fds=True,
                    preexec_fn=os.setsid  # Create new session for proper PTY behavior
                )
                
                # Close slave fd in parent process (child has its own copy)
                os.close(slave_fd)
                
                self.status = 'connecting'
                self.log_buffer.append('[INFO] VPN client process started with PTY.\n')
                
                # Start background thread to read output
                self._read_thread = threading.Thread(target=self._read_output_loop, daemon=True)
                self._read_thread.start()
                
                return {'status': 'connecting'}
            except Exception as e:
                self.status = 'failed'
                self.log_buffer.append(f'[ERROR] Failed to start VPN: {str(e)}\n')
                return {'error': str(e), 'status': 'failed'}
    
    def send_input(self, value):
        """Send user input to VPN process via PTY and continue reading."""
        with self._lock:
            if self.status != 'waiting_input':
                return {'error': 'Not waiting for input', 'status': self.status}
            
            if not self.process or self.process.poll() is not None:
                return {'error': 'Process not running', 'status': 'failed'}
            
            if self.master_fd is None:
                return {'error': 'PTY not available', 'status': 'failed'}
            
            try:
                os.write(self.master_fd, (value + '\n').encode('utf-8'))
                # Store user-provided auth for potential config update
                self.user_provided_auth = value
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
            self._stop_event.set()
            
            if self.process and self.process.poll() is None:
                try:
                    # Kill the process group (since we used setsid)
                    os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                    self.process.wait(timeout=3)
                except:
                    try:
                        self.process.kill()
                    except:
                        pass
            
            # Close PTY master fd
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except:
                    pass
                self.master_fd = None
            
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
                            os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                            self.process.wait(timeout=3)
                        except:
                            try:
                                self.process.kill()
                            except:
                                pass
            
            # Close PTY master fd
            if self.master_fd is not None:
                try:
                    os.close(self.master_fd)
                except:
                    pass
                self.master_fd = None
            
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
    second_auth = os.environ.get('VPN_SECOND_AUTH') or config.get('VPN_SECOND_AUTH')
    
    # Check if environment variables override config
    env_override = bool(
        os.environ.get('VPN_HOST') or 
        os.environ.get('VPN_USER') or 
        os.environ.get('VPN_PASS') or 
        os.environ.get('VPN_SECOND_AUTH')
    )
    
    if not all([host, user, password]):
        return {'error': 'Missing required credentials', 'status': 'failed'}
    
    return session.start(host, user, password, second_auth=second_auth, env_override=env_override)


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
    bottle.response.content_type = 'text/event-stream'
    bottle.response.cache_control = 'no-cache'
    bottle.response.headers['Connection'] = 'keep-alive'
    
    def generate():
        last_len = 0
        last_status = None
        last_prompt = None
        
        # Send all existing logs first (don't skip startup logs)
        with session._lock:
            initial_status = session.status
            initial_logs = ''.join(session.log_buffer)
            last_len = len(session.log_buffer)
            last_status = initial_status
        
        # Send initial status
        data = {'type': 'status', 'status': initial_status}
        yield ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode('utf-8')
        
        # Send existing logs
        if initial_logs:
            data = {'type': 'log', 'log': initial_logs, 'status': initial_status}
            yield ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode('utf-8')
        
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
                    
                    yield ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode('utf-8')
                
                # Send status change
                if current_status != last_status:
                    last_status = current_status
                    data = {
                        'type': 'status',
                        'status': current_status
                    }
                    if current_prompt:
                        data['prompt'] = current_prompt
                    
                    yield ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode('utf-8')
                    
                    # If connected, start services and end stream
                    if current_status == 'connected':
                        session.start_services()
                        data = {'type': 'services_started'}
                        yield ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode('utf-8')
                        return  # End the generator
                    
                    # If failed, end stream
                    if current_status == 'failed':
                        return
                
                # Send prompt if changed
                if current_prompt and current_prompt != last_prompt:
                    last_prompt = current_prompt
                    data = {'type': 'prompt', 'prompt': current_prompt, 'status': current_status}
                    yield ('data: ' + json.dumps(data, ensure_ascii=False) + '\n\n').encode('utf-8')
            
            time.sleep(0.1)
    
    return generate()


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
    """CLI mode: synchronous blocking execution with terminal interaction using PTY."""
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
    
    # Create PTY
    try:
        master_fd, slave_fd = pty.openpty()
    except Exception as e:
        print(f"[ERROR] Failed to create PTY: {e}")
        return 1
    
    # Start VPN process with PTY
    try:
        process = subprocess.Popen(
            [VPN_CMD, '-h', host, '-u', user, '-p', password],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            close_fds=True,
            preexec_fn=os.setsid
        )
        os.close(slave_fd)
    except Exception as e:
        print(f"[ERROR] Failed to start VPN client: {e}")
        os.close(master_fd)
        return 1
    
    buffer = ''
    
    try:
        while True:
            try:
                ready, _, _ = select.select([master_fd], [], [], 0.1)
            except:
                break
            
            if ready:
                try:
                    chunk = os.read(master_fd, 4096)
                    if not chunk:
                        break
                    
                    chunk_str = chunk.decode('utf-8', errors='ignore')
                    buffer += chunk_str
                    print(chunk_str, end='', flush=True)
                    
                    # Detect prompt
                    clean = re.sub(r'\x1b\[[0-9;]*[a-zA-Z]', '', buffer)
                    for pattern in PROMPT_PATTERNS:
                        if re.search(pattern, clean, re.IGNORECASE):
                            try:
                                value = input()
                                os.write(master_fd, (value + '\n').encode('utf-8'))
                                buffer = ''
                                break
                            except EOFError:
                                process.terminate()
                                os.close(master_fd)
                                return 1
                except OSError as e:
                    # PTY slave closed - this is expected when process exits
                    if hasattr(e, 'errno') and e.errno == errno.EIO:
                        break
                    # Other errors: continue trying
                    pass
            
            if process.poll() is not None:
                break
    except KeyboardInterrupt:
        print("\n[CLI] Interrupted by user.")
        process.terminate()
        os.close(master_fd)
        return 1
    
    process.wait()
    os.close(master_fd)
    
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
