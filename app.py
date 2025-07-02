#!/usr/bin/env python3
"""
Remote Desktop Relay Server for Render.com
Handles WebSocket connections between web browsers and client PCs
"""

import eventlet
eventlet.monkey_patch()

import os
import json
import time
import logging
import uuid
from datetime import datetime
from flask import Flask, request, session, redirect, render_template_string
from flask_socketio import SocketIO, emit, join_room, leave_room

# Configuration
DEFAULT_PASSWORD = os.environ.get('ACCESS_PASSWORD', 'remote123')
PORT = int(os.environ.get('PORT', 5000))

# Logging setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global state
clients = {}  # client_id -> {socket_id, last_seen, info}
browsers = {}  # browser_socket_id -> {client_id, authenticated}

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY', 'render_remote_desktop_2024')
socketio = SocketIO(app, 
                   async_mode='eventlet', 
                   cors_allowed_origins="*",
                   ping_timeout=60,
                   ping_interval=25,
                   max_http_buffer_size=10*1024*1024)  # 10MB for images

# HTML Templates
LOGIN_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Desktop Control</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; margin: 0; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); height: 100vh; display: flex; justify-content: center; align-items: center; }
        .container { background: white; padding: 2rem; border-radius: 15px; box-shadow: 0 20px 40px rgba(0,0,0,0.1); max-width: 500px; width: 90%; }
        .logo { text-align: center; margin-bottom: 2rem; }
        .logo h1 { color: #333; margin: 0; font-size: 2rem; }
        .logo p { color: #666; margin: 0.5rem 0 0 0; }
        .client-list { margin-bottom: 2rem; }
        .client-item { background: #f8f9fa; padding: 1rem; margin: 0.5rem 0; border-radius: 8px; border-left: 4px solid #4CAF50; }
        .client-item.offline { border-left-color: #f44336; }
        .client-info { display: flex; justify-content: space-between; align-items: center; }
        .client-status { font-size: 0.9rem; color: #666; }
        .btn { padding: 0.5rem 1rem; background: #4CAF50; color: white; border: none; border-radius: 5px; cursor: pointer; text-decoration: none; display: inline-block; }
        .btn:hover { background: #45a049; }
        .btn.disabled { background: #ccc; cursor: not-allowed; }
        .form-group { margin-bottom: 1rem; }
        label { display: block; margin-bottom: 0.5rem; font-weight: 500; }
        input { width: 100%; padding: 0.75rem; border: 2px solid #ddd; border-radius: 5px; box-sizing: border-box; }
        .error { background: #fee; color: #c33; padding: 0.75rem; border-radius: 5px; margin-bottom: 1rem; }
        .info { background: #e8f4fd; color: #1976d2; padding: 0.75rem; border-radius: 5px; margin-bottom: 1rem; }
    </style>
</head>
<body>
    <div class="container">
        <div class="logo">
            <h1>🌐 Remote Desktop</h1>
            <p>Cloud-based PC Control</p>
        </div>
        
        {% if not session.get('authenticated') %}
        <form method="POST">
            {% if error %}<div class="error">{{ error }}</div>{% endif %}
            <div class="form-group">
                <label>Access Password</label>
                <input type="password" name="password" required>
            </div>
            <button type="submit" class="btn" style="width: 100%;">Login</button>
        </form>
        {% else %}
        <div class="client-list">
            <h3>Available Computers</h3>
            <div id="clientList">Loading...</div>
        </div>
        <div style="text-align: center;">
            <a href="/logout" class="btn">Logout</a>
        </div>
        {% endif %}
    </div>
    
    {% if session.get('authenticated') %}
    <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
    <script>
        const socket = io();
        socket.on('clients_update', (data) => {
            const list = document.getElementById('clientList');
            if (data.clients.length === 0) {
                list.innerHTML = '<div class="info">No computers connected. Install and run client.py on your PC.</div>';
                return;
            }
            list.innerHTML = data.clients.map(client => `
                <div class="client-item ${client.online ? '' : 'offline'}">
                    <div class="client-info">
                        <div>
                            <strong>${client.name}</strong><br>
                            <small>${client.os} • Last seen: ${client.last_seen}</small>
                        </div>
                        <a href="/control/${client.id}" class="btn ${client.online ? '' : 'disabled'}">
                            ${client.online ? 'Connect' : 'Offline'}
                        </a>
                    </div>
                </div>
            `).join('');
        });
        socket.emit('get_clients');
    </script>
    {% endif %}
</body>
</html>
"""

CONTROL_HTML = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Control - {{client_name}}</title>
    <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: #000; overflow: hidden; height: 100vh; }
        .header { background: #1a1a1a; color: white; padding: 0.5rem 1rem; display: flex; justify-content: space-between; align-items: center; }
        .header h1 { font-size: 1.2rem; }
        .status { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; }
        .status-dot { width: 10px; height: 10px; border-radius: 50%; }
        .status-connected { background: #4CAF50; }
        .status-disconnected { background: #f44336; }
        .controls { background: #2a2a2a; padding: 0.5rem; display: flex; gap: 0.5rem; flex-wrap: wrap; }
        .btn { padding: 0.4rem 0.8rem; background: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.85rem; }
        .btn:hover { background: #45a049; }
        .btn.danger { background: #f44336; }
        .screen-container { position: relative; height: calc(100vh - 100px); display: flex; justify-content: center; align-items: center; background: #000; }
        .screen-image { max-width: 100%; max-height: 100%; cursor: crosshair; border: 2px solid #333; }
        .loading { color: #4CAF50; font-size: 1.2rem; text-align: center; }
        .text-panel { position: fixed; bottom: 0; left: 0; right: 0; background: #1a1a1a; padding: 1rem; transform: translateY(100%); transition: transform 0.3s; }
        .text-panel.active { transform: translateY(0); }
        .text-panel textarea { width: 100%; height: 100px; background: #333; color: white; border: 1px solid #555; padding: 0.5rem; font-family: monospace; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🖥️ {{client_name}}</h1>
        <div class="status">
            <div class="status-dot status-disconnected" id="statusDot"></div>
            <span id="statusText">Connecting...</span>
        </div>
    </div>
    
    <div class="controls">
        <button class="btn" id="textBtn">📝 Text</button>
        <button class="btn" onclick="sendKey('ctrl+c')">Copy</button>
        <button class="btn" onclick="sendKey('ctrl+v')">Paste</button>
        <button class="btn danger" onclick="window.location.href='/'">Exit</button>
    </div>
    
    <div class="screen-container">
        <div class="loading" id="loading">Connecting to {{client_name}}...</div>
        <img class="screen-image" id="screenImage" style="display: none;">
    </div>
    
    <div class="text-panel" id="textPanel">
        <textarea id="textInput" placeholder="Type text here..."></textarea>
        <div style="margin-top: 0.5rem;">
            <button class="btn" id="sendTextBtn">Send Text</button>
            <button class="btn" id="closeTextBtn">Close</button>
        </div>
    </div>
    
    <script>
        const socket = io();
        const clientId = '{{client_id}}';
        const screenImage = document.getElementById('screenImage');
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        const loading = document.getElementById('loading');
        const textPanel = document.getElementById('textPanel');
        const textBtn = document.getElementById('textBtn');
        const textInput = document.getElementById('textInput');
        let isConnected = false;
        
        socket.on('connect', () => {
            socket.emit('browser_join', {client_id: clientId});
        });
        
        socket.on('client_status', (data) => {
            isConnected = data.connected;
            if (isConnected) {
                statusDot.className = 'status-dot status-connected';
                statusText.textContent = 'Connected';
                loading.style.display = 'none';
            } else {
                statusDot.className = 'status-dot status-disconnected';
                statusText.textContent = 'Disconnected';
                loading.style.display = 'block';
                screenImage.style.display = 'none';
            }
        });
        
        socket.on('screen_frame', (data) => {
            if (data.image) {
                const blob = new Blob([new Uint8Array(data.image)], {type: 'image/jpeg'});
                const url = URL.createObjectURL(blob);
                screenImage.src = url;
                screenImage.style.display = 'block';
                loading.style.display = 'none';
            }
        });
        
        screenImage.addEventListener('click', (e) => {
            if (!isConnected) return;
            const rect = screenImage.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width;
            const y = (e.clientY - rect.top) / rect.height;
            socket.emit('mouse_click', {client_id: clientId, x, y, button: e.button === 2 ? 'right' : 'left'});
        });
        
        screenImage.addEventListener('contextmenu', e => e.preventDefault());
        
        screenImage.addEventListener('mousemove', (e) => {
            if (!isConnected) return;
            const rect = screenImage.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width;
            const y = (e.clientY - rect.top) / rect.height;
            socket.emit('mouse_move', {client_id: clientId, x, y});
        });
        
        document.addEventListener('keydown', (e) => {
            if (e.target === textInput || !isConnected) return;
            e.preventDefault();
            socket.emit('key_press', {client_id: clientId, key: e.key, code: e.code});
        });
        
        function sendKey(combo) {
            if (isConnected) socket.emit('key_combo', {client_id: clientId, combo});
        }
        
        textBtn.addEventListener('click', () => {
            textPanel.classList.toggle('active');
            if (textPanel.classList.contains('active')) textInput.focus();
        });
        
        document.getElementById('closeTextBtn').addEventListener('click', () => {
            textPanel.classList.remove('active');
        });
        
        document.getElementById('sendTextBtn').addEventListener('click', () => {
            if (isConnected && textInput.value) {
                socket.emit('type_text', {client_id: clientId, text: textInput.value});
                textInput.value = '';
                textPanel.classList.remove('active');
            }
        });
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    return render_template_string(LOGIN_HTML)

@app.route('/', methods=['POST'])
def login():
    if request.form.get('password') == DEFAULT_PASSWORD:
        session['authenticated'] = True
        return redirect('/')
    return render_template_string(LOGIN_HTML, error="Invalid password")

@app.route('/control/<client_id>')
def control(client_id):
    if not session.get('authenticated'):
        return redirect('/')
    
    if client_id not in clients:
        return redirect('/')
    
    client_info = clients[client_id]
    return render_template_string(CONTROL_HTML, 
                                client_id=client_id,
                                client_name=client_info.get('name', 'Unknown PC'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# SocketIO Events
@socketio.on('connect')
def handle_connect():
    logger.info(f"Connection: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    logger.info(f"Disconnection: {request.sid}")
    
    # Remove from browsers
    if request.sid in browsers:
        del browsers[request.sid]
    
    # Remove from clients
    for client_id, client_info in list(clients.items()):
        if client_info['socket_id'] == request.sid:
            del clients[client_id]
            broadcast_clients_update()
            break

@socketio.on('client_register')
def handle_client_register(data):
    """Client PC registering itself"""
    client_id = data.get('client_id') or str(uuid.uuid4())
    clients[client_id] = {
        'socket_id': request.sid,
        'name': data.get('name', 'Unknown PC'),
        'os': data.get('os', 'Unknown'),
        'last_seen': datetime.now().strftime('%H:%M:%S'),
        'online': True
    }
    
    join_room(f"client_{client_id}")
    emit('client_registered', {'client_id': client_id})
    broadcast_clients_update()
    logger.info(f"Client registered: {client_id}")

@socketio.on('browser_join')
def handle_browser_join(data):
    """Browser joining to control a client"""
    client_id = data['client_id']
    browsers[request.sid] = {'client_id': client_id}
    
    join_room(f"browser_{client_id}")
    
    # Check if client is online
    client_online = client_id in clients
    emit('client_status', {'connected': client_online})
    
    if client_online:
        # Request initial screen from client
        socketio.emit('get_screen', room=f"client_{client_id}")
    
    logger.info(f"Browser joined for client: {client_id}")

@socketio.on('get_clients')
def handle_get_clients():
    """Send list of available clients to browser"""
    if not session.get('authenticated'):
        return
    
    client_list = []
    for client_id, client_info in clients.items():
        client_list.append({
            'id': client_id,
            'name': client_info['name'],
            'os': client_info['os'],
            'last_seen': client_info['last_seen'],
            'online': client_info['online']
        })
    
    emit('clients_update', {'clients': client_list})

def broadcast_clients_update():
    """Broadcast updated client list to all authenticated browsers"""
    client_list = []
    for client_id, client_info in clients.items():
        client_list.append({
            'id': client_id,
            'name': client_info['name'],
            'os': client_info['os'],
            'last_seen': client_info['last_seen'],
            'online': client_info['online']
        })
    
    socketio.emit('clients_update', {'clients': client_list}, broadcast=True)

# Relay events from browser to client
@socketio.on('mouse_click')
def relay_mouse_click(data):
    client_id = data['client_id']
    if client_id in clients:
        socketio.emit('mouse_click', data, room=f"client_{client_id}")

@socketio.on('mouse_move')
def relay_mouse_move(data):
    client_id = data['client_id']
    if client_id in clients:
        socketio.emit('mouse_move', data, room=f"client_{client_id}")

@socketio.on('key_press')
def relay_key_press(data):
    client_id = data['client_id']
    if client_id in clients:
        socketio.emit('key_press', data, room=f"client_{client_id}")

@socketio.on('key_combo')
def relay_key_combo(data):
    client_id = data['client_id']
    if client_id in clients:
        socketio.emit('key_combo', data, room=f"client_{client_id}")

@socketio.on('type_text')
def relay_type_text(data):
    client_id = data['client_id']
    if client_id in clients:
        socketio.emit('type_text', data, room=f"client_{client_id}")

# Relay events from client to browser
@socketio.on('screen_data')
def relay_screen_data(data):
    """Relay screen data from client to browsers"""
    client_id = None
    # Find which client sent this
    for cid, cinfo in clients.items():
        if cinfo['socket_id'] == request.sid:
            client_id = cid
            break
    
    if client_id:
        socketio.emit('screen_frame', data, room=f"browser_{client_id}")

if __name__ == '__main__':
    logger.info(f"Starting server on port {PORT}")
    socketio.run(app, host='0.0.0.0', port=PORT, debug=False)
