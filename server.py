#!/usr/bin/env python3
"""
Planetfall Visual Mapper — multi-session web server for Docker deployment.
Each player gets their own dfrotz process via a session ID.
"""

import os
import re
import sys
import json
import time
import uuid
import subprocess
import threading
from collections import defaultdict
from flask import Flask, request, jsonify, send_from_directory

app = Flask(__name__, static_folder=None)

# --- Configuration ---
STORY_PATH = os.path.join(os.path.dirname(__file__), "PLANETFA.DAT")
DATA_PATH = os.path.join(os.path.dirname(__file__), "planetfall_data.json")
DFROTZ_CMD = os.environ.get("DFROTZ_CMD", "dfrotz")

# Session timeout: 30 minutes of inactivity
SESSION_TIMEOUT = 30 * 60
# Max concurrent sessions
MAX_SESSIONS = 20
# Max command length (prevent abuse)
MAX_CMD_LEN = 200
# Command sanitization: only allow game-safe characters
# dfrotz is a Z-machine interpreter, not a shell — it can't execute
# shell commands. But we still sanitize to prevent abuse and keep
# the input clean. Block shell metacharacters entirely.
ALLOWED_CMD_RE = re.compile(r'^[a-zA-Z0-9\s\'.,!?/]+$')
# Block patterns that look like shell commands (defense in depth)
BLOCKED_PATTERNS = re.compile(r'\b(rm|cat|ls|cd|cp|mv|wget|curl|bash|sh|python|perl|nc|netcat|kill|sudo)\b', re.IGNORECASE)

# --- Session management ---
class DfrotzSession:
    def __init__(self, story_path):
        self.story_path = story_path
        self.proc = None
        self.buffer = []
        self.lock = threading.Lock()
        self.resolve_callback = None
        self.response_timer = None
        self.last_activity = time.time()
        self._start()

    def _start(self):
        self.proc = subprocess.Popen(
            [DFROTZ_CMD, self.story_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        self.buffer = []
        self.resolve_callback = None
        self.response_timer = None

        def reader():
            try:
                for line in self.proc.stdout:
                    with self.lock:
                        self.buffer.append(line)
                        if self.resolve_callback:
                            if self.response_timer:
                                self.response_timer.cancel()
                            self.response_timer = threading.Timer(0.4, self._fire_callback)
                            self.response_timer.start()
            except (ValueError, OSError):
                pass

        t = threading.Thread(target=reader, daemon=True)
        t.start()

    def _fire_callback(self):
        with self.lock:
            if self.resolve_callback:
                cb = self.resolve_callback
                self.resolve_callback = None
                self.response_timer = None
                output = ''.join(self.buffer)
                self.buffer = []
                cb(output)

    def send(self, command):
        self.last_activity = time.time()
        with self.lock:
            self.buffer = []
            if self.response_timer:
                self.response_timer.cancel()
                self.response_timer = None

        result_holder = {'result': None}

        def callback(output):
            result_holder['result'] = output

        with self.lock:
            self.resolve_callback = callback

        try:
            self.proc.stdin.write(command + '\n')
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self._start()
            self.proc.stdin.write(command + '\n')
            self.proc.stdin.flush()

        # Wait for response (max 15s)
        for _ in range(150):
            if result_holder['result'] is not None:
                break
            time.sleep(0.1)

        if result_holder['result'] is None:
            with self.lock:
                self.resolve_callback = None
                if self.response_timer:
                    self.response_timer.cancel()
                    self.response_timer = None
            return {'command': command, 'room': '', 'score': 0, 'moves': 0, 'text': '(timeout)'}

        return self._parse(result_holder['result'], command)

    def _parse(self, raw, command):
        lines = raw.split('\n')
        lines = [l.replace('\r', '') for l in lines]
        room_name = ''
        score = 0
        moves = 0
        status_idx = -1

        for i, line in enumerate(lines):
            if 'Score:' in line and 'Moves:' in line:
                status_idx = i
                m1 = re.search(r'Score:\s*(\d+)', line)
                m2 = re.search(r'Moves:\s*(\d+)', line)
                if m1: score = int(m1.group(1))
                if m2: moves = int(m2.group(1))
                clean = re.sub(r'^>\s*', '', line)
                clean = re.sub(r'\s*Score:.*$', '', clean).strip()
                if clean: room_name = clean
                break

        body_lines = []
        in_body = False
        for i in range(status_idx + 1, len(lines)):
            stripped = lines[i].strip()
            if stripped == '>' or stripped == '> ':
                break
            if not in_body and stripped == '':
                continue
            in_body = True
            body_lines.append(lines[i])

        while body_lines and body_lines[-1].strip() == '':
            body_lines.pop()

        return {
            'command': command,
            'room': room_name,
            'score': score,
            'moves': moves,
            'text': '\n'.join(body_lines).strip(),
        }

    def is_alive(self):
        return self.proc and self.proc.poll() is None

    def kill(self):
        try:
            if self.proc:
                self.proc.stdin.close()
                self.proc.terminate()
                self.proc.wait(timeout=3)
        except Exception:
            try:
                self.proc.kill()
            except Exception:
                pass


# --- Session registry ---
sessions = {}
sessions_lock = threading.Lock()

def get_session(session_id):
    with sessions_lock:
        s = sessions.get(session_id)
        if s and s.is_alive():
            return s
        return None

def create_session():
    with sessions_lock:
        # Clean up expired sessions
        now = time.time()
        expired = [sid for sid, s in sessions.items() if now - s.last_activity > SESSION_TIMEOUT]
        for sid in expired:
            sessions[sid].kill()
            del sessions[sid]

        if len(sessions) >= MAX_SESSIONS:
            # Evict oldest
            oldest = min(sessions, key=lambda sid: sessions[sid].last_activity)
            sessions[oldest].kill()
            del sessions[oldest]

        session_id = str(uuid.uuid4())
        session = DfrotzSession(STORY_PATH)
        sessions[session_id] = session
        return session_id

def sanitize_command(cmd):
    if len(cmd) > MAX_CMD_LEN:
        return None
    if not ALLOWED_CMD_RE.match(cmd):
        return None
    if BLOCKED_PATTERNS.search(cmd):
        return None
    return cmd

# --- Map data ---
_map_data = None
_desc_to_zil = {}
def get_map_data():
    global _map_data, _desc_to_zil
    if _map_data is None:
        with open(DATA_PATH, 'r') as f:
            _map_data = json.load(f)
        # Build desc → ZIL room name lookup
        for name, info in _map_data.get('rooms', {}).items():
            _desc_to_zil[info['desc'].lower()] = name
    return _map_data

# --- Routes ---
@app.route('/')
def index():
    return send_from_directory(os.path.dirname(__file__), 'index.html')

@app.route('/api/map')
def api_map():
    return jsonify(get_map_data())

@app.route('/api/session', methods=['POST'])
def api_create_session():
    session_id = create_session()
    return jsonify({'session_id': session_id})

@app.route('/api/command', methods=['POST'])
def api_command():
    data = request.get_json()
    command = data.get('command', '').strip()
    session_id = data.get('session_id', '')

    if not command:
        return jsonify({'error': 'No command'}), 400

    cmd = sanitize_command(command)
    if cmd is None:
        return jsonify({'error': 'Invalid command'}), 400

    session = get_session(session_id)
    if not session:
        # Create a new session if one doesn't exist
        session_id = create_session()
        session = get_session(session_id)
        if not session:
            return jsonify({'error': 'Server full'}), 503

    result = session.send(cmd)
    result['session_id'] = session_id
    # Add room_zil for the client to update the map
    if result.get('room'):
        result['room_zil'] = _desc_to_zil.get(result['room'].lower())
    return jsonify(result)

@app.route('/api/restart', methods=['POST'])
def api_restart():
    data = request.get_json() or {}
    session_id = data.get('session_id', '')
    session = get_session(session_id)
    if session:
        session.kill()
    with sessions_lock:
        if session_id in sessions:
            del sessions[session_id]
    session_id = create_session()
    return jsonify({'session_id': session_id, 'status': 'restarted'})

if __name__ == '__main__':
    if not os.path.isfile(STORY_PATH):
        print(f"ERROR: PLANETFA.DAT not found at {STORY_PATH}")
        sys.exit(1)

    # Pre-load map data to populate _desc_to_zil
    get_map_data()
    print(f"Loaded {len(_desc_to_zil)} room name mappings")

    print(f"Planetfall Visual Mapper starting on port 5000")
    print(f"  dfrotz: {DFROTZ_CMD}")
    print(f"  story:  {STORY_PATH}")
    app.run(host='0.0.0.0', port=5000, debug=False)
