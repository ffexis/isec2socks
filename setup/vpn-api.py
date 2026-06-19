#!/usr/bin/env python3
import bottle
import subprocess
import os
import json

app = bottle.Bottle()
CONFIG_FILE = '/etc/vpn-conf.json'
VPN_SCRIPT = '/usr/local/bin/vpn'
PID_FILE = '/var/run/vpn-api.pid'

def run_vpn_cmd(action, timeout=30):
    try:
        result = subprocess.run(
            [VPN_SCRIPT, action],
            capture_output=True, text=True, timeout=timeout
        )
        return {
            'success': result.returncode == 0,
            'output': result.stdout.strip()
        }
    except subprocess.TimeoutExpired:
        return {'success': False, 'output': 'Command timed out'}
    except Exception as e:
        return {'success': False, 'output': str(e)}

def parse_vpn_status(output):
    is_connected = 'connected' in output.lower()
    status = {
        'connected': is_connected,
        'vpn': 'Connected' if is_connected else 'DOWN',
        'gost': 'DOWN',
        'guardian': 'DOWN'
    }
    for line in output.split('\n'):
        line = line.strip()
        if 'GOST Proxy' in line:
            status['gost'] = 'RUNNING' if 'RUNNING' in line else 'DOWN'
        elif 'Route Guardian' in line:
            status['guardian'] = 'RUNNING' if 'RUNNING' in line else 'DOWN'
    return status

def read_json_config():
    with open(CONFIG_FILE, 'r') as f:
        return json.load(f)

def write_json_config(data):
    with open(CONFIG_FILE, 'w') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write('\n')

@app.route('/api/vpn/status')
def vpn_status():
    result = run_vpn_cmd('status')
    if result['output']:
        status = parse_vpn_status(result['output'])
        status['raw'] = result['output']
        return status
    return {'connected': False, 'vpn': 'DOWN', 'gost': 'DOWN', 'raw': 'No output'}

@app.route('/api/vpn/log')
def vpn_log():
    log_path = '/var/log/vpn_cli.log'
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8', errors='ignore') as f:
            return {'log': f.read()}
    return {'log': ''}

@app.route('/api/vpn/on', method='POST')
def vpn_on():
    result = run_vpn_cmd('on', timeout=20)
    if result['success']:
        return {'success': True}
    return {'success': False}

@app.route('/api/vpn/off', method='POST')
def vpn_off():
    result = run_vpn_cmd('off')
    return result

@app.route('/api/config')
def get_config():
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
    return {'status': 'ok'}

@app.route('/')
def index():
    return bottle.static_file('index.html', root='/etc/isec2socks')

if __name__ == '__main__':
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    bottle.run(app, host='0.0.0.0', port=31081, server='cheroot')
