import os
import logging
import sys
from flask import Flask, request, session, redirect, render_template_string
from flask_socketio import SocketIO, emit
import eventlet

# IMPORTANT: This must be called first.
eventlet.monkey_patch()

# --- Configuration ---
# Set these as Environment Variables on Render.com for security
# Go to your Render service -> Environment -> Add Environment Variable
FLASK_SECRET_KEY = os.environ.get('FLASK_SECRET_KEY', 'a-very-strong-default-secret-key')
REMOTE_PASSWORD = os.environ.get('REMOTE_PASSWORD', 'remote123')
CLIENT_SECRET_KEY = os.environ.get('CLIENT_SECRET_KEY', 'a-secret-key-for-the-client-pc')

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Flask App & SocketIO Setup ---
app = Flask(__name__)
app.config['SECRET_KEY'] = FLASK_SECRET_KEY
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", ping_timeout=60, ping_interval=25)

# --- Global State ---
# In a real multi-user system, you'd use a dictionary. For a single client, this is fine.
active_client_sid = None
active_controller_sids = set()

# --- HTML Templates ---
LOGIN_HTML = """
<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>Remote Login</title><style>body{font-family:sans-serif;background:#2c3e50;color:white;display:flex;justify-content:center;align-items:center;height:100vh;margin:0} .login-box{background:#34495e;padding:40px;border-radius:10px;box-shadow:0 10px 25px rgba(0,0,0,0.3);width:300px} h1{text-align:center;margin-bottom:20px} input{width:100%;padding:10px;margin-bottom:20px;border:none;border-radius:5px;box-sizing:border-box} button{width:100%;padding:10px;border:none;border-radius:5px;background:#2980b9;color:white;cursor:pointer} .error{color:#e74c3c;text-align:center;margin-bottom:15px}</style></head>
<body><div class="login-box"><h1>Remote Access</h1>{% if error %}<p class="error">{{ error }}</p>{% endif %}<form method="POST"><input type="password" name="password" placeholder="Password" required><button type="submit">Connect</button></form></div></body></html>
"""

CONTROL_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>Remote Control</title>
    <script src="https://cdn.socket.io/4.7.2/socket.io.min.js"></script>
    <style>
        body, html { margin: 0; padding: 0; background-color: #000; overflow: hidden; font-family: sans-serif; }
        #screenCanvas { display: block; width: 100vw; height: 100vh; object-fit: contain; cursor: crosshair; }
        #status { position: fixed; top: 10px; left: 10px; background: rgba(0,0,0,0.7); color: white; padding: 8px 12px; border-radius: 5px; font-size: 14px; z-index: 100; }
        .dot { height: 10px; width: 10px; border-radius: 50%; display: inline-block; margin-right: 8px; animation: pulse 2s infinite; }
        .connected { background-color: #2ecc71; } .disconnected { background-color: #e74c3c; } .waiting { background-color: #f1c40f; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.4; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div id="status"><span id="statusDot" class="dot waiting"></span><span id="statusText">Waiting for client PC...</span></div>
    <canvas id="screenCanvas"></canvas>

    <script>
        const socket = io({transports: ['websocket']});
        const canvas = document.getElementById('screenCanvas');
        const ctx = canvas.getContext('2d', { alpha: false }); // Perf optimization
        const statusText = document.getElementById('statusText');
        const statusDot = document.getElementById('statusDot');
        let nativeWidth = 0, nativeHeight = 0;

        function updateStatus(state, text) {
            statusDot.className = `dot ${state}`;
            statusText.innerText = text;
        }

        socket.on('connect', () => {
            console.log('Connected to server!');
            updateStatus('waiting', 'Authenticating...');
            socket.emit('register_controller');
        });
        
        socket.on('disconnect', () => updateStatus('disconnected', 'Server disconnected!'));
        socket.on('client_status', (data) => {
             if (data.status === 'connected') {
                updateStatus('waiting', 'Client PC connected. Waiting for stream...');
            } else {
                updateStatus('disconnected', 'Client PC disconnected.');
                ctx.clearRect(0, 0, canvas.width, canvas.height); // Clear screen
            }
        });

        socket.on('initial_frame', (data) => {
            console.log('Received initial frame.');
            updateStatus('connected', 'Streaming...');
            const img = new Image();
            img.src = `data:image/jpeg;base64,${data.image}`;
            img.onload = () => {
                nativeWidth = canvas.width = img.width;
                nativeHeight = canvas.height = img.height;
                ctx.drawImage(img, 0, 0);
            };
        });

        socket.on('screen_update', (data) => {
            // Use requestAnimationFrame for smoother rendering
            requestAnimationFrame(() => {
                data.updates.forEach(patch => {
                    const img = new Image();
                    img.src = `data:image/jpeg;base64,${patch.image}`;
                    img.onload = () => {
                        ctx.drawImage(img, patch.x, patch.y);
                    };
                });
            });
        });

        function sendMouseEvent(type, event) {
            if (!nativeWidth || !nativeHeight) return;
            const rect = canvas.getBoundingClientRect();
            const scaleX = nativeWidth / rect.width;
            const scaleY = nativeHeight / rect.height;
            const x = (event.clientX - rect.left) * scaleX;
            const y = (event.clientY - rect.top) * scaleY;
            
            let button = 'left';
            if (event.button === 2) button = 'right';
            if (event.button === 1) button = 'middle';

            socket.emit(type, { x, y, button });
        }

        canvas.addEventListener('mousemove', (e) => sendMouseEvent('mouse_move', e));
        canvas.addEventListener('mousedown', (e) => sendMouseEvent('mouse_down', e));
        canvas.addEventListener('mouseup', (e) => sendMouseEvent('mouse_up', e));
        canvas.addEventListener('contextmenu', (e) => e.preventDefault());

        document.addEventListener('keydown', (e) => {
            e.preventDefault();
            socket.emit('key_down', { key: e.key, code: e.code });
        });
        document.addEventListener('keyup', (e) => {
            e.preventDefault();
            socket.emit('key_up', { key: e.key, code: e.code });
        });

    </script>
</body>
</html>
"""

# --- Flask Routes ---
@app.route('/')
def index():
    if session.get('authenticated'):
        return redirect('/control')
    return render_template_string(LOGIN_HTML)

@app.route('/', methods=['POST'])
def login():
    if request.form.get('password') == REMOTE_PASSWORD:
        session['authenticated'] = True
        return redirect('/control')
    return render_template_string(LOGIN_HTML, error="Invalid password")

@app.route('/control')
def control():
    if not session.get('authenticated'):
        return redirect('/')
    return render_template_string(CONTROL_HTML)

@app.route('/logout')
def logout():
    session.clear()
    return redirect('/')

# --- SocketIO Event Handlers (The Broker Logic) ---
@socketio.on('connect')
def handle_connect():
    logger.info(f"A user or client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    global active_client_sid
    sid = request.sid
    logger.info(f"User or client disconnected: {sid}")
    if sid == active_client_sid:
        active_client_sid = None
        logger.warning("Controlled client PC has disconnected.")
        # Notify all controllers
        for controller_sid in active_controller_sids:
             socketio.emit('client_status', {'status': 'disconnected'}, room=controller_sid)
    elif sid in active_controller_sids:
        active_controller_sids.discard(sid)
        logger.info(f"Controller {sid} disconnected.")

@socketio.on('register_client')
def handle_register_client(data):
    global active_client_sid
    if data.get('secret') == CLIENT_SECRET_KEY:
        active_client_sid = request.sid
        logger.info(f"Client PC registered successfully with SID: {active_client_sid}")
        # Notify all controllers that the client is now connected
        for controller_sid in active_controller_sids:
             socketio.emit('client_status', {'status': 'connected'}, room=controller_sid)
             if controller_sid in active_controller_sids: # double check
                socketio.emit('request_initial_frame', room=active_client_sid)
    else:
        logger.warning(f"Failed client registration from {request.sid}. Disconnecting.")
        socketio.disconnect(request.sid)

@socketio.on('register_controller')
def handle_register_controller():
    if not session.get('authenticated'):
        logger.warning(f"Unauthenticated controller registration attempt from {request.sid}. Disconnecting.")
        return
    
    controller_sid = request.sid
    active_controller_sids.add(controller_sid)
    logger.info(f"Controller registered: {controller_sid}")
    
    if active_client_sid:
        logger.info(f"Notifying new controller {controller_sid} that client is online.")
        socketio.emit('client_status', {'status': 'connected'}, room=controller_sid)
        logger.info(f"Requesting new initial frame for controller {controller_sid}.")
        socketio.emit('request_initial_frame', room=active_client_sid)
    else:
        logger.info(f"Notifying controller {controller_sid} to wait for client.")
        socketio.emit('client_status', {'status': 'disconnected'}, room=controller_sid)

# --- Passthrough Events ---
# These events simply relay data from the controller to the client, or vice-versa

def forward_to_client(event_name):
    @socketio.on(event_name)
    def handler(data):
        if request.sid in active_controller_sids and active_client_sid:
            socketio.emit(event_name, data, room=active_client_sid)

def forward_to_controllers(event_name):
    @socketio.on(event_name)
    def handler(data):
        if request.sid == active_client_sid:
            for controller_sid in active_controller_sids:
                socketio.emit(event_name, data, room=controller_sid)

# Controller -> Client
forward_to_client('mouse_move')
forward_to_client('mouse_down')
forward_to_client('mouse_up')
forward_to_client('key_down')
forward_to_client('key_up')

# Client -> Controller(s)
forward_to_controllers('initial_frame')
forward_to_controllers('screen_update')

if __name__ == '__main__':
    logger.info("Starting server...")
    # Use Gunicorn on Render, but this is for local testing.
    socketio.run(app, host='0.0.0.0', port=5000)
