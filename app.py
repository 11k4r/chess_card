from flask import Flask, render_template, request, jsonify, Response
import requests
import os
import json
import pandas as pd
import re
from urllib.parse import urlparse

# Local imports
from game_accuracy import calculate_game_phase_accuracy
from user_color import get_user_color

app = Flask(__name__)

# --- CONFIGURATION & SECURITY ---
METRICS = ["OPN", "MID", "END", "TAC", "STR", "CAL", "TMG", "INT", "ATK", "DEF", "ACC", "RES"]
ALLOWED_IMAGE_DOMAINS = {
    'chess.com', 'www.chess.com', 'images.chesscomfiles.com', 
    'flagcdn.com', 'avatars.chess.com'
}

SESSIONS = {}

# --- SECURITY HEADERS ---
@app.after_request
def add_security_headers(response):
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Embedder-Policy'] = 'require-corp'
    
    csp = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com blob:; "
        "worker-src 'self' blob:; "
        "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
        "font-src https://cdnjs.cloudflare.com https://fonts.gstatic.com; "
        "img-src 'self' data: blob:; "
        "connect-src 'self' https://api.chess.com;"
    )
    response.headers['Content-Security-Policy'] = csp
    return response

# --- HELPER FUNCTIONS ---
def is_safe_url(url):
    try:
        parsed = urlparse(url)
        return parsed.netloc in ALLOWED_IMAGE_DOMAINS
    except:
        return False

def validate_username(username):
    return re.match(r'^[a-zA-Z0-9_-]+$', username) is not None

def get_headers():
    return {'User-Agent': 'ChessCardGenerator/2.0 (SecurityEnhanced)'}

def calculate_theme(title):
    if not title: return "common"
    title = title.upper()
    if title in ['GM', 'WGM']: return "gm"
    elif title in ['IM', 'WIM', 'FM', 'WFM', 'NM', 'CM', 'WCM']: return "titled"
    return "common"

# --- ROUTES ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return "", 204

@app.route('/proxy_image')
def proxy_image():
    url = request.args.get('url')
    if not url or not is_safe_url(url):
        return "Forbidden or Invalid URL", 403

    try:
        resp = requests.get(url, stream=True, timeout=5)
        resp.raise_for_status()
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(name, value) for (name, value) in resp.raw.headers.items()
                   if name.lower() not in excluded_headers]
        return Response(resp.content, headers=headers)
    except Exception as e:
        app.logger.error(f"Proxy error: {e}")
        return "Error fetching image", 502

@app.route('/api/init_session', methods=['POST'])
def init_session():
    data = request.json
    username = data.get('username')
    
    if not username or not validate_username(username):
        return jsonify({"error": "Invalid username"}), 400

    try:
        profile_resp = requests.get(f"https://api.chess.com/pub/player/{username}", headers=get_headers())
        if profile_resp.status_code != 200:
            return jsonify({"error": "Player not found"}), 404
        profile = profile_resp.json()
        
        stats_resp = requests.get(f"https://api.chess.com/pub/player/{username}/stats", headers=get_headers())
        stats = stats_resp.json() if stats_resp.status_code == 200 else {}
    except requests.RequestException:
        return jsonify({"error": "Chess.com API unavailable"}), 503

    SESSIONS[username] = {
        "games_processed": 0,
        "metrics_list": {m: [] for m in METRICS}, 
        "description": "Blunder Master" # Default final description
    }

    rapid = stats.get('chess_rapid', {}).get('last', {}).get('rating', 'N/A')
    blitz = stats.get('chess_blitz', {}).get('last', {}).get('rating', 'N/A')
    bullet = stats.get('chess_bullet', {}).get('last', {}).get('rating', 'N/A')
    
    country_url = profile.get('country', '')
    country_code = country_url.split('/')[-1] if country_url else 'xx'

    return jsonify({
        "status": "ready",
        "username": profile.get('username'),
        "avatar": profile.get('avatar', 'https://www.chess.com/bundles/web/images/user-image.svg'),
        "title": profile.get('title', ''),
        "country_code": country_code,
        "theme": calculate_theme(profile.get('title', '')),
        "ratings": {"rapid": rapid, "blitz": blitz, "bullet": bullet},
        "description": "Analyzing...", 
        "metrics": {m: 0 for m in METRICS}, 
        "overall": 0
    })

@app.route('/api/process_game_result', methods=['POST'])
def process_game_result():
    data = request.json
    username = data.get('username')

    if not username or username not in SESSIONS:
        return jsonify({"error": "Session expired"}), 400

    # 1. Archive Data
    os.makedirs('data', exist_ok=True)
    safe_filename = re.sub(r'[^a-zA-Z0-9_-]', '', username)
    with open(f"data/{safe_filename}_games.jsonl", 'a') as f:
        f.write(json.dumps(data) + "\n")

    # 2. Process Metrics
    session = SESSIONS[username]
    game_metrics = {m: 0 for m in METRICS}
    
    try:
        user_color = get_user_color(data)
        if user_color:
            accuracy_list = calculate_game_phase_accuracy(data)[user_color]
            game_metrics['ACC'] = accuracy_list.get('accuracy', 0)
            game_metrics['OPN'] = accuracy_list.get('opening', 0)
            game_metrics['MID'] = accuracy_list.get('middlegame', 0)
            game_metrics['END'] = accuracy_list.get('endgame', 0)
    except Exception as e:
        app.logger.error(f"Metric error {username}: {e}")

    session["games_processed"] += 1
    for m in METRICS:
        session["metrics_list"][m].append(game_metrics[m])

    # 3. Aggregate (Intermediate State)
    current_metrics = _calculate_aggregates(session)
    overall = int(sum(current_metrics.values()) / len(METRICS)) if METRICS else 0
    
    return jsonify({
        "games_processed": session["games_processed"],
        "metrics": current_metrics,
        "overall": overall,
        "description": "Analyzing..." # Always return analyzing during stream
    })

@app.route('/api/finalize_session', methods=['POST'])
def finalize_session():
    """Explicitly marks the session as complete and returns final stats."""
    data = request.json
    username = data.get('username')

    if not username or username not in SESSIONS:
        return jsonify({"error": "Session expired"}), 400
        
    session = SESSIONS[username]
    current_metrics = _calculate_aggregates(session)
    overall = int(sum(current_metrics.values()) / len(METRICS)) if METRICS else 0
    
    return jsonify({
        "games_processed": session["games_processed"],
        "metrics": current_metrics,
        "overall": overall,
        "description": session["description"] # Return the actual final title
    })

def _calculate_aggregates(session):
    aggregates = {}
    for m in METRICS:
        series = pd.Series(session['metrics_list'][m])
        mean_val = series.mean()
        aggregates[m] = int(mean_val) if not pd.isna(mean_val) else 0
    return aggregates

if __name__ == '__main__':
    app.run(debug=True, port=5000)
