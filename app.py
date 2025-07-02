#!/usr/bin/env python3
"""
Render.com Server - Remote Desktop Control Relay
Optimized for low latency and high performance
"""

import eventlet
eventlet.monkey_patch()

import os
import time
import json
import logging
import base64
from datetime import datetime
from flask import Flask, render_template_string, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import hashlib

# Configuration
PORT = int(os.environ.get('PORT', 5000))
SECRET_KEY = os.environ.get('SECRET_KEY', 'remote_desktop_relay_2024')

# Global state
connected_clients = {}  # client_id -> {socket_id, last_seen, info}
web_viewers = {}        # viewer_id -> {socket_id, watching_client}
client_screens = {}     # client_id -> latest_screen_data

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Flask App
app = Flask(__name__)
app.config['SECRET_KEY'] = SECRET_KEY
socketio = SocketIO(app, 
                   async_mode='eventlet', 
                   cors_allowed_origins="*",
                   ping_timeout=60,
                   ping_interval=25,
                   max_http_buffer_size=10*1024*1024)  # 10MB for large images

# HTML Templates
INDEX_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Desktop Control - Select Client</title>
    <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); min-height: 100vh; padding: 2rem; }
        .container { max-width: 1200px; margin: 0 auto; }
        .header { text-align: center; color: white; margin-bottom: 3rem; }
        .header h1 { font-size: 3rem; margin-bottom: 1rem; text-shadow: 2px 2px 4px rgba(0,0,0,0.3); }
        .header p { font-size: 1.2rem; opacity: 0.9; }
        .clients-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 2rem; margin-bottom: 2rem; }
        .client-card { background: white; border-radius: 15px; padding: 2rem; box-shadow: 0 10px 30px rgba(0,0,0,0.2); transition: transform 0.3s ease; cursor: pointer; }
        .client-card:hover { transform: translateY(-5px); }
        .client-card.offline { opacity: 0.6; cursor: not-allowed; }
        .client-status { display: flex; align-items: center; gap: 0.5rem; margin-bottom: 1rem; }
        .status-dot { width: 12px; height: 12px; border-radius: 50%; }
        .status-online { background: #4CAF50; animation: pulse 2s infinite; }
        .status-offline { background: #f44336; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
        .client-info h3 { color: #333; margin-bottom: 0.5rem; }
        .client-info p { color: #666; font-size: 0.9rem; margin-bottom: 0.3rem; }
        .client-preview { width: 100%; height: 150px; background: #f5f5f5; border-radius: 8px; margin: 1rem 0; display: flex; align-items: center; justify-content: center; overflow: hidden; }
        .client-preview img { max-width: 100%; max-height: 100%; object-fit: contain; }
        .no-clients { text-align: center; color: white; font-size: 1.2rem; background: rgba(255,255,255,0.1); padding: 3rem; border-radius: 15px; }
        .stats { background: rgba(255,255,255,0.1); color: white; padding: 1rem; border-radius: 10px; text-align: center; }
        .btn { display: inline-block; padding: 0.8rem 2rem; background: #4CAF50; color: white; text-decoration: none; border-radius: 8px; font-weight: 500; transition: background 0.3s; }
        .btn:hover { background: #45a049; }
        .btn.disabled { background: #ccc; cursor: not-allowed; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>🖥️ Remote Desktop Control</h1>
            <p>Select a client computer to control</p>
        </div>
        
        <div class="stats">
            <strong>Connected Clients: <span id="clientCount">0</span> | Active Viewers: <span id="viewerCount">0</span></strong>
        </div>
        
        <div class="clients-grid" id="clientsGrid">
            <div class="no-clients">
                <h3>No clients connected</h3>
                <p>Start a client with: python client.py</p>
            </div>
        </div>
    </div>

    <script>
        const socket = io();
        let clients = {};
        
        socket.on('connect', () => {
            console.log('Connected to server');
            socket.emit('request_client_list');
        });
        
        socket.on('client_list', (data) => {
            clients = data.clients;
            updateClientsDisplay();
            document.getElementById('clientCount').textContent = Object.keys(clients).length;
        });
        
        socket.on('client_connected', (data) => {
            clients[data.client_id] = data.info;
            updateClientsDisplay();
            document.getElementById('clientCount').textContent = Object.keys(clients).length;
        });
        
        socket.on('client_disconnected', (data) => {
            delete clients[data.client_id];
            updateClientsDisplay();
            document.getElementById('clientCount').textContent = Object.keys(clients).length;
        });
        
        socket.on('client_screen_preview', (data) => {
            if (clients[data.client_id]) {
                clients[data.client_id].preview = data.image;
                updateClientsDisplay();
            }
        });
        
        function updateClientsDisplay() {
            const grid = document.getElementById('clientsGrid');
            
            if (Object.keys(clients).length === 0) {
                grid.innerHTML = '<div class="no-clients"><h3>No clients connected</h3><p>Start a client with: python client.py</p></div>';
                return;
            }
            
            grid.innerHTML = Object.entries(clients).map(([clientId, info]) => `
                <div class="client-card ${info.online ? '' : 'offline'}" onclick="connectToClient('${clientId}')">
                    <div class="client-status">
                        <div class="status-dot ${info.online ? 'status-online' : 'status-offline'}"></div>
                        <strong>${info.online ? 'Online' : 'Offline'}</strong>
                    </div>
                    <div class="client-info">
                        <h3>${info.hostname || 'Unknown Computer'}</h3>
                        <p>OS: ${info.os || 'Unknown'}</p>
                        <p>Resolution: ${info.screen_size || 'Unknown'}</p>
                        <p>Last seen: ${new Date(info.last_seen).toLocaleString()}</p>
                    </div>
                    <div class="client-preview">
                        ${info.preview ? `<img src="data:image/jpeg;base64,${info.preview}" alt="Screen preview">` : '<p>No preview available</p>'}
                    </div>
                    <a href="/control/${clientId}" class="btn ${info.online ? '' : 'disabled'}">
                        ${info.online ? 'Connect' : 'Offline'}
                    </a>
                </div>
            `).join('');
        }
        
        function connectToClient(clientId) {
            if (clients[clientId] && clients[clientId].online) {
                window.location.href = `/control/${clientId}`;
            }
        }
        
        // Request updates every 5 seconds
        setInterval(() => {
            socket.emit('request_client_list');
        }, 5000);
    </script>
</body>
</html>
"""

CONTROL_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Remote Desktop Control - {{ client_id }}</title>
    <script src="https://cdn.socket.io/4.5.0/socket.io.min.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #000; overflow: hidden; height: 100vh; }
        .header { background: #1a1a1a; color: white; padding: 0.5rem 1rem; display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid #333; }
        .header h1 { font-size: 1.2rem; }
        .status { display: flex; align-items: center; gap: 0.5rem; font-size: 0.9rem; }
        .status-dot { width: 10px; height: 10px; border-radius: 50%; animation: pulse 2s infinite; }
        .status-connected { background: #4CAF50; }
        .status-disconnected { background: #f44336; }
        .status-connecting { background: #ff9800; }
        .status-typing { background: #9c27b0; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.5; } 100% { opacity: 1; } }
        .fps-counter { background: rgba(0,255,0,0.1); color: #4CAF50; padding: 0.2rem 0.5rem; border-radius: 3px; font-size: 0.8rem; font-family: monospace; }
        .controls { background: #2a2a2a; padding: 0.5rem; display: flex; gap: 0.5rem; flex-wrap: wrap; border-bottom: 1px solid #333; }
        .btn { padding: 0.4rem 0.8rem; background: #4CAF50; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.85rem; transition: all 0.2s; }
        .btn:hover { background: #45a049; transform: translateY(-1px); }
        .btn.active { background: #2196F3; }
        .btn.danger { background: #f44336; }
        .btn.danger:hover { background: #da190b; }
        .btn.typing { background: #9c27b0; }
        .btn.typing:hover { background: #7b1fa2; }
        .screen-container { position: relative; height: calc(100vh - 120px); display: flex; justify-content: center; align-items: center; background: #000; }
        .screen-image { max-width: 100%; max-height: 100%; cursor: crosshair; border: 2px solid #333; border-radius: 4px; }
        .text-input-panel { position: fixed; bottom: 0; left: 0; right: 0; background: #1a1a1a; border-top: 1px solid #333; padding: 1rem; transform: translateY(100%); transition: transform 0.3s ease; }
        .text-input-panel.active { transform: translateY(0); }
        .text-input-panel textarea { width: 100%; height: 120px; background: #333; color: white; border: 1px solid #555; border-radius: 4px; padding: 0.5rem; font-family: monospace; resize: vertical; font-size: 14px; }
        .text-input-panel .controls { background: transparent; padding: 0.5rem 0 0 0; }
        .typing-status { position: fixed; top: 50%; left: 50%; transform: translate(-50%, -50%); background: rgba(156, 39, 176, 0.95); color: white; padding: 1rem 2rem; border-radius: 8px; font-size: 1.1rem; z-index: 1000; display: none; }
        .typing-status.active { display: block; }
        .loading { position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: #4CAF50; font-size: 1.2rem; text-align: center; }
        .loading-spinner { border: 3px solid #333; border-top: 3px solid #4CAF50; border-radius: 50%; width: 40px; height: 40px; animation: spin 1s linear infinite; margin: 0 auto 1rem; }
        @keyframes spin { 0% { transform: rotate(0deg); } 100% { transform: rotate(360deg); } }
        .click-feedback { position: absolute; width: 30px; height: 30px; border: 3px solid #ff4444; border-radius: 50%; pointer-events: none; animation: click-pulse 0.5s ease-out; }
        @keyframes click-pulse { from { transform: translate(-50%, -50%) scale(0.3); opacity: 1; } to { transform: translate(-50%, -50%) scale(1.5); opacity: 0; } }
        .quality-controls { display: flex; gap: 0.5rem; align-items: center; }
        .quality-controls select { background: #333; color: white; border: 1px solid #555; border-radius: 4px; padding: 0.3rem; }
    </style>
</head>
<body>
    <div class="header">
        <h1>🖥️ Remote Control - {{ client_id }}</h1>
        <div class="status">
            <div class="quality-controls">
                <select id="qualitySelect">
                    <option value="low">Low Quality</option>
                    <option value="medium" selected>Medium Quality</option>
                    <option value="high">High Quality</option>
                </select>
            </div>
            <div class="fps-counter" id="fpsCounter">0 FPS</div>
            <div class="status-dot status-connecting" id="statusDot"></div>
            <span id="statusText">Connecting...</span>
        </div>
    </div>
    
    <div class="controls">
        <button class="btn" id="refreshBtn">🔄 Force Refresh</button>
        <button class="btn" id="textModeBtn">📝 Text Mode</button>
        <button class="btn typing" id="humanTypeBtn">🤖 Human Type</button>
        <button class="btn" onclick="sendKey('ctrl+c')">📋 Copy</button>
        <button class="btn" onclick="sendKey('ctrl+v')">📋 Paste</button>
        <button class="btn" onclick="sendKey('alt+tab')">🔄 Alt+Tab</button>
        <button class="btn danger" onclick="window.location.href='/'">🏠 Home</button>
    </div>
    
    <div class="typing-status" id="typingStatus">🤖 Typing...</div>
    
    <div class="screen-container" id="screenContainer">
        <div class="loading" id="loading">
            <div class="loading-spinner"></div>
            <div>Connecting to {{ client_id }}...</div>
        </div>
        <img class="screen-image" id="screenImage" style="display: none;">
    </div>
    
    <div class="text-input-panel" id="textPanel">
        <textarea id="textInput" placeholder="Type text here..."></textarea>
        <div class="controls">
            <button class="btn" id="saveTextBtn">💾 Save Text</button>
            <button class="btn typing" id="triggerTypingBtn">🤖 Start Human Typing</button>
            <button class="btn" id="closeTextBtn">❌ Close</button>
            <span id="textStatus" style="color: #4CAF50; margin-left: 1rem;"></span>
        </div>
    </div>

    <script>
        const CLIENT_ID = "{{ client_id }}";
        const socket = io();
        const screenImage = document.getElementById('screenImage');
        const statusDot = document.getElementById('statusDot');
        const statusText = document.getElementById('statusText');
        const loading = document.getElementById('loading');
        const textPanel = document.getElementById('textPanel');
        const textModeBtn = document.getElementById('textModeBtn');
        const textInput = document.getElementById('textInput');
        const saveTextBtn = document.getElementById('saveTextBtn');
        const closeTextBtn = document.getElementById('closeTextBtn');
        const textStatus = document.getElementById('textStatus');
        const refreshBtn = document.getElementById('refreshBtn');
        const fpsCounter = document.getElementById('fpsCounter');
        const humanTypeBtn = document.getElementById('humanTypeBtn');
        const triggerTypingBtn = document.getElementById('triggerTypingBtn');
        const typingStatus = document.getElementById('typingStatus');
        const qualitySelect = document.getElementById('qualitySelect');
        
        let isTextMode = false;
        let currentImageUrl = null;
        let frameCount = 0;
        let lastFpsUpdate = Date.now();
        let isConnected = false;
        let isTyping = false;
        
        function updateFpsCounter() {
            frameCount++;
            const now = Date.now();
            if (now - lastFpsUpdate >= 1000) {
                const fps = Math.round(frameCount * 1000 / (now - lastFpsUpdate));
                fpsCounter.textContent = `${fps} FPS`;
                frameCount = 0;
                lastFpsUpdate = now;
            }
        }
        
        function updateStatus(status, message) {
            statusText.textContent = message;
            statusDot.className = `status-dot status-${status}`;
        }
        
        socket.on('connect', () => {
            updateStatus('connecting', 'Connected, joining client...');
            socket.emit('viewer_join_client', { client_id: CLIENT_ID });
        });
        
        socket.on('disconnect', () => {
            isConnected = false;
            updateStatus('disconnected', 'Disconnected');
            loading.style.display = 'block';
            screenImage.style.display = 'none';
        });
        
        socket.on('viewer_joined', (data) => {
            if (data.success) {
                isConnected = true;
                updateStatus('connected', 'Connected to client');
                loading.style.display = 'none';
                socket.emit('request_screen_frame', { client_id: CLIENT_ID, quality: qualitySelect.value });
            } else {
                updateStatus('disconnected', data.message || 'Failed to connect');
            }
        });
        
        socket.on('screen_frame', (data) => {
            if (data && data.image) {
                loading.style.display = 'none';
                screenImage.style.display = 'block';
                
                if (currentImageUrl) URL.revokeObjectURL(currentImageUrl);
                const blob = new Blob([Uint8Array.from(atob(data.image), c => c.charCodeAt(0))], {type: 'image/jpeg'});
                currentImageUrl = URL.createObjectURL(blob);
                screenImage.src = currentImageUrl;
                
                if (!isTyping) updateStatus('connected', 'Streaming Active');
                updateFpsCounter();
            }
        });
        
        socket.on('text_saved', (data) => {
            textStatus.textContent = data.success ? '✅ Text saved!' : '❌ Failed to save.';
            setTimeout(() => textStatus.textContent = '', 3000);
        });
        
        socket.on('typing_status', (data) => {
            isTyping = (data.status === 'started');
            typingStatus.classList.toggle('active', isTyping);
            humanTypeBtn.disabled = isTyping;
            triggerTypingBtn.disabled = isTyping;
            humanTypeBtn.textContent = isTyping ? '⏸️ Typing...' : '🤖 Human Type';
            
            if (isTyping) {
                updateStatus('typing', 'Human-like typing...');
            } else {
                updateStatus('connected', 'Streaming Active');
            }
        });
        
        function getRelativeCoords(e) {
            const rect = screenImage.getBoundingClientRect();
            return {
                x: (e.clientX - rect.left) / rect.width,
                y: (e.clientY - rect.top) / rect.height
            };
        }
        
        function showClickFeedback(x, y) {
            const fb = document.createElement('div');
            fb.className = 'click-feedback';
            fb.style.left = x + 'px';
            fb.style.top = y + 'px';
            document.body.appendChild(fb);
            setTimeout(() => fb.remove(), 500);
        }
        
        screenImage.addEventListener('click', (e) => {
            e.preventDefault();
            if (!isConnected || isTyping) return;
            const coords = getRelativeCoords(e);
            socket.emit('mouse_action', { client_id: CLIENT_ID, action: 'click', x: coords.x, y: coords.y, button: 'left' });
            showClickFeedback(e.clientX, e.clientY);
        });
        
        screenImage.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            if (!isConnected || isTyping) return;
            const coords = getRelativeCoords(e);
            socket.emit('mouse_action', { client_id: CLIENT_ID, action: 'click', x: coords.x, y: coords.y, button: 'right' });
            showClickFeedback(e.clientX, e.clientY);
        });
        
        screenImage.addEventListener('mousemove', (e) => {
            if (!isConnected || isTyping) return;
            socket.emit('mouse_action', { client_id: CLIENT_ID, action: 'move', ...getRelativeCoords(e) });
        });
        
        document.addEventListener('keydown', (e) => {
            if (isTextMode && e.target === textInput || !isConnected || isTyping) return;
            e.preventDefault();
            socket.emit('key_action', { client_id: CLIENT_ID, action: 'press', key: e.key, code: e.code, ctrlKey: e.ctrlKey, altKey: e.altKey, shiftKey: e.shiftKey });
        });
        
        function sendKey(combo) {
            if (isConnected && !isTyping) {
                socket.emit('key_action', { client_id: CLIENT_ID, action: 'combination', combination: combo });
            }
        }
        
        // UI Event Handlers
        textModeBtn.addEventListener('click', () => {
            isTextMode = !isTextMode;
            textPanel.classList.toggle('active', isTextMode);
            textModeBtn.textContent = isTextMode ? '🖥️ Screen Mode' : '📝 Text Mode';
            if (isTextMode) textInput.focus();
        });
        
        closeTextBtn.addEventListener('click', () => {
            isTextMode = false;
            textPanel.classList.remove('active');
            textModeBtn.textContent = '📝 Text Mode';
        });
        
        saveTextBtn.addEventListener('click', () => {
            if (isConnected) {
                socket.emit('save_text', { client_id: CLIENT_ID, text: textInput.value });
                textStatus.textContent = '💾 Saving...';
            }
        });
        
        humanTypeBtn.addEventListener('click', () => {
            if (isConnected && !isTyping) {
                socket.emit('trigger_human_typing', { client_id: CLIENT_ID });
            }
        });
        
        triggerTypingBtn.addEventListener('click', () => {
            if (isConnected && !isTyping) {
                socket.emit('trigger_human_typing', { client_id: CLIENT_ID });
            }
        });
        
        refreshBtn.addEventListener('click', () => {
            if (isConnected) {
                socket.emit('request_screen_frame', { client_id: CLIENT_ID, quality: qualitySelect.value });
            }
        });
        
        qualitySelect.addEventListener('change', () => {
            if (isConnected) {
                socket.emit('quality_change', { client_id: CLIENT_ID, quality: qualitySelect.value });
            }
        });
        
        // Auto-refresh screen every 100ms for smooth streaming
        setInterval(() => {
            if (isConnected && !isTyping) {
                socket.emit('request_screen_frame', { client_id: CLIENT_ID, quality: qualitySelect.value });
            }
        }, 100);
    </script>
</body>
</html>
"""

# Routes
@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/control/<client_id>')
def control(client_id):
    return render_template_string(CONTROL_HTML, client_id=client_id)

# SocketIO Events
@socketio.on('connect')
def handle_connect():
    logger.info(f"Connection from {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    # Clean up client or viewer
    sid = request.sid
    
    # Check if it was a client
    for client_id, info in list(connected_clients.items()):
        if info['socket_id'] == sid:
            logger.info(f"Client {client_id} disconnected")
            connected_clients[client_id]['online'] = False
            socketio.emit('client_disconnected', {'client_id': client_id}, broadcast=True)
            break
    
    # Check if it was a viewer
    for viewer_id, info in list(web_viewers.items()):
        if info['socket_id'] == sid:
            logger.info(f"Viewer {viewer_id} disconnected")
            del web_viewers[viewer_id]
            break

@socketio.on('client_register')
def handle_client_register(data):
    client_id = data['client_id']
    info = data['info']
    info['socket_id'] = request.sid
    info['last_seen'] = datetime.now().isoformat()
    info['online'] = True
    
    connected_clients[client_id] = info
    logger.info(f"Client registered: {client_id}")
    
    # Notify all viewers about new client
    socketio.emit('client_connected', {'client_id': client_id, 'info': info}, broadcast=True)
    emit('registration_success', {'client_id': client_id})

@socketio.on('screen_frame_from_client')
def handle_screen_frame_from_client(data):
    client_id = data['client_id']
    
    # Store latest screen for previews
    if 'preview' in data:
        if client_id in connected_clients:
            connected_clients[client_id]['preview'] = data['preview']
            # Send preview to index page viewers
            socketio.emit('client_screen_preview', {
                'client_id': client_id, 
                'image': data['preview']
            }, broadcast=True)
    
    # Forward full frame to viewers of this client
    if 'image' in data:
        for viewer_id, viewer_info in web_viewers.items():
            if viewer_info.get('watching_client') == client_id:
                socketio.emit('screen_frame', {
                    'image': data['image'],
                    'dimensions': data.get('dimensions')
                }, room=viewer_info['socket_id'])

@socketio.on('request_client_list')
def handle_request_client_list():
    emit('client_list', {'clients': connected_clients})

@socketio.on('viewer_join_client')
def handle_viewer_join_client(data):
    client_id = data['client_id']
    
    if client_id in connected_clients and connected_clients[client_id]['online']:
        web_viewers[request.sid] = {
            'socket_id': request.sid,
            'watching_client': client_id,
            'joined_at': datetime.now().isoformat()
        }
        emit('viewer_joined', {'success': True, 'client_id': client_id})
        logger.info(f"Viewer {request.sid} joined client {client_id}")
    else:
        emit('viewer_joined', {'success': False, 'message': 'Client not available'})

@socketio.on('request_screen_frame')
def handle_request_screen_frame(data):
    client_id = data['client_id']
    quality = data.get('quality', 'medium')
    
    if client_id in connected_clients and connected_clients[client_id]['online']:
        # Forward request to client
        socketio.emit('capture_screen', {
            'quality': quality,
            'requester': request.sid
        }, room=connected_clients[client_id]['socket_id'])

@socketio.on('quality_change')
def handle_quality_change(data):
    client_id = data['client_id']
    quality = data['quality']
    
    if client_id in connected_clients and connected_clients[client_id]['online']:
        socketio.emit('quality_change', {'quality': quality}, 
                     room=connected_clients[client_id]['socket_id'])

# Forward actions to client
@socketio.on('mouse_action')
def handle_mouse_action(data):
    client_id = data['client_id']
    if client_id in connected_clients and connected_clients[client_id]['online']:
        socketio.emit('mouse_action', data, room=connected_clients[client_id]['socket_id'])

@socketio.on('key_action')
def handle_key_action(data):
    client_id = data['client_id']
    if client_id in connected_clients and connected_clients[client_id]['online']:
        socketio.emit('key_action', data, room=connected_clients[client_id]['socket_id'])

@socketio.on('save_text')
def handle_save_text(data):
    client_id = data['client_id']
    if client_id in connected_clients and connected_clients[client_id]['online']:
        socketio.emit('save_text', data, room=connected_clients[client_id]['socket_id'])

@socketio.on('trigger_human_typing')
def handle_trigger_human_typing(data):
    client_id = data['client_id']
    if client_id in connected_clients and connected_clients[client_id]['online']:
        socketio.emit('trigger_human_typing', {}, room=connected_clients[client_id]['socket_id'])

# Forward responses back to viewers
@socketio.on('text_saved_response')
def handle_text_saved_response(data):
    # Forward to all viewers of this client
    client_id = data['client_id']
    for viewer_id, viewer_info in web_viewers.items():
        if viewer_info.get('watching_client') == client_id:
            socketio.emit('text_saved', data, room=viewer_info['socket_id'])

@socketio.on('typing_status_update')
def handle_typing_status_update(data):
    # Forward to all viewers of this client
    client_id = data['client_id']
    for viewer_id, viewer_info in web_viewers.items():
        if viewer_info.get('watching_client') == client_id:
            socketio.emit('typing_status', data, room=viewer_info['socket_id'])

if __name__ == '__main__':
    logger.info(f"🚀 Starting Render.com Remote Desktop Server on port {PORT}")
    socketio.run(app, host='0.0.0.0', port=PORT, debug=False)
