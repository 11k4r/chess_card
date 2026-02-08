from flask import Flask, render_template, request, jsonify, Response
import requests
import random
import os
import json
import pandas as pd

from game_accuracy import calculate_game_phase_accuracy
from user_color import get_user_color

app = Flask(__name__)

# --- SECURITY HEADERS ---
# Essential for Stockfish (SharedArrayBuffer), but blocks external images.
@app.after_request
def add_header(response):
    response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
    response.headers['Cross-Origin-Embedder-Policy'] = 'require-corp'
    return response

# --- PROXY ROUTE ---
# Fixes the image block by serving images from "localhost" instead of external URLs.
@app.route('/proxy_image')
def proxy_image():
    url = request.args.get('url')
    if not url:
        return "No URL provided", 400
    
    try:
        # Fetch the external image
        resp = requests.get(url, stream=True)
        
        # Forward the image data, excluding problematic headers
        excluded_headers = ['content-encoding', 'content-length', 'transfer-encoding', 'connection']
        headers = [(name, value) for (name, value) in resp.raw.headers.items()
                   if name.lower() not in excluded_headers]
        
        return Response(resp.content, headers=headers)
    except Exception as e:
        return f"Error proxying image: {e}", 500

# --- CONFIG & STORAGE ---
METRICS = ["OPN", "MID", "END", "TAC", "STR", "CAL", "TMG", "INT", "ATK", "DEF", "ACC", "RES"]
SESSIONS = {}

# --- HELPER FUNCTIONS ---
def get_headers():
    return {'User-Agent': 'ChessCardGenerator/1.0 (contact: iikar6427@gmail.com)'}

def get_chess_com_profile(username):
    try:
        resp = requests.get(f"https://api.chess.com/pub/player/{username}", headers=get_headers())
        if resp.status_code == 200: return resp.json()
        return None
    except: return None

def get_stats(username):
    try:
        resp = requests.get(f"https://api.chess.com/pub/player/{username}/stats", headers=get_headers())
        return resp.json() if resp.status_code == 200 else {}
    except: return {}

def calculate_theme(title):
    if not title: return "common"
    title = title.upper()
    if title in ['GM', 'WGM']: return "gm"
    elif title in ['IM', 'WIM', 'FM', 'WFM', 'NM', 'CM', 'WCM']: return "titled"
    return "common"

# --- API ROUTES ---

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/init_session', methods=['POST'])
def init_session():
    data = request.json
    username = data.get('username')
    
    if not username: return jsonify({"error": "Username required"}), 400

    profile = get_chess_com_profile(username)
    if not profile: return jsonify({"error": "Player not found"}), 404

    stats = get_stats(username)
    
    SESSIONS[username] = {
        "games_processed": 0,
        "metrics_list": {m: [] for m in METRICS}, 
        "profile": profile,
        "stats": stats,
        "description": "Blunder Master"
    }

    rapid = stats.get('chess_rapid', {}).get('last', {}).get('rating', 'N/A')
    blitz = stats.get('chess_blitz', {}).get('last', {}).get('rating', 'N/A')
    bullet = stats.get('chess_bullet', {}).get('last', {}).get('rating', 'N/A')
    title = profile.get('title', '')
    country_url = profile.get('country', '')
    country = country_url.split('/')[-1] if country_url else 'xx'

    return jsonify({
        "status": "ready",
        "username": profile.get('username'),
        "avatar": profile.get('avatar', 'https://www.chess.com/bundles/web/images/user-image.svg'),
        "title": title,
        "country_code": country,
        "theme": calculate_theme(title),
        "ratings": {"rapid": rapid, "blitz": blitz, "bullet": bullet},
        "description": "Analyzing...", 
        "metrics": {m: 0 for m in METRICS}, 
        "overall": 0
    })

@app.route('/api/process_game_result', methods=['POST'])
def process_game_result():
    data = request.json
    username = data.get('username')

    if not os.path.exists('data'):
        os.makedirs('data')
    
    # Save to data/{username}_games.jsonl
    save_file = os.path.join('data', f"{username}_games.jsonl")
    
    with open(save_file, 'a') as f:
        # data contains: username, pgn, evals, is_final
        f.write(json.dumps(data) + "\n")

    if username not in SESSIONS: return jsonify({"error": "Session expired"}), 400

    session = SESSIONS[username]
    
    # Calculate simulated metrics
    game_metrics = {m: 0 for m in METRICS} #["TAC", "STR", "CAL", "TMG", "INT", "ATK", "DEF", "RES"]
    user_color = get_user_color(data)
    accuracy_list = calculate_game_phase_accuracy(data)[user_color]
    print(user_color)
    print(accuracy_list)
    game_metrics['ACC'] = accuracy_list['accuracy']
    game_metrics['OPN'] = accuracy_list['opening']
    game_metrics['MID'] = accuracy_list['middlegame']
    game_metrics['END'] = accuracy_list['endgame']
    
    session["games_processed"] += 1
    for m in METRICS:
        session["metrics_list"][m].append(game_metrics[m])

    count = session["games_processed"]
    current_metrics = {m: int(pd.Series(session['metrics_list'][m]).mean()) for m in METRICS}
    overall = int(sum(current_metrics.values()) / len(METRICS))
    
    # Check if client says this is the final game
    is_final = data.get('is_final', False)
    return jsonify({
        "games_processed": count,
        "metrics": current_metrics,
        "overall": overall,
        "description": session["description"] if is_final else "Analyzing..."
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)