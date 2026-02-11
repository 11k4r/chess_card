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
from time_features import enrich_game_data_with_time, calculate_psych_metrics
from trace_parser import parse_stockfish_trace
from resilience import calculate_resilience
from positional_features import calculate_tactics_and_strategy
from atk_def import calculate_attack_and_defense

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
    enrich_game_data_with_time(data)
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


def get_player_rating(game_data, username):
    """
    Extracts the rating of the specific user from the PGN headers.
    """
    pgn = game_data.get('pgn', '')
    if not pgn:
        return 1200 # Default if no PGN

    # Normalize username for comparison
    target_user = username.strip().lower()
    
    # 1. Extract White and Black Player Names
    # Pattern: [White "Username"]
    white_match = re.search(r'\[White\s+"([^"]+)"\]', pgn)
    black_match = re.search(r'\[Black\s+"([^"]+)"\]', pgn)
    
    white_player = white_match.group(1).strip().lower() if white_match else ""
    black_player = black_match.group(1).strip().lower() if black_match else ""

    # 2. Determine User's Color
    is_white = (target_user == white_player)
    is_black = (target_user == black_player)
    
    # If username doesn't match either (e.g. slight mismatch), try partial match
    if not is_white and not is_black:
        if target_user in white_player: is_white = True
        elif target_user in black_player: is_black = True
    
    # 3. Extract Rating based on Color
    # Pattern: [WhiteElo "3204"]
    rating_pattern = r'\[WhiteElo\s+"(\d+)"\]' if is_white else r'\[BlackElo\s+"(\d+)"\]'
    
    # If user is not found in headers, default to 1200
    if not is_white and not is_black:
        return 1200

    rating_match = re.search(rating_pattern, pgn)
    
    if rating_match:
        try:
            return int(rating_match.group(1))
        except ValueError:
            pass
            
    return 1200 # Default if tag exists but is empty/invalid

def get_elo_target(rating):
    """
    Returns the 'Center of Gravity' for a given Elo based on user ranges.
    """
    if rating < 0: rating = 0
    r = min(3200, rating)

    # 1. Define the Center Points of your ranges
    # 0-400 (Avg 15) | 400-800 (Avg 37.5) | ...
    if r <= 400:  return 15.0  
    if r <= 800:  return 37.5  
    if r <= 1200: return 50.0  
    if r <= 1600: return 60.0  
    if r <= 2000: return 72.5  
    if r <= 2600: return 85.0  
    if r <= 3000: return 92.5  
    return 98.0

def calibrate_score(raw_score, rating, metric_name):
    """
    Weighted Average Calibration:
    Final = (Anchor * (1 - Elasticity)) + (Raw * Elasticity)
    """
    if raw_score is None: 
        return None
    
    # 1. Get the Anchor (Target Score for this Elo)
    # This represents the "Mean Score" for a player of this rating.
    anchor = get_elo_target(rating)
    
    # 2. Define Elasticity (How much does the Raw Score matter?)
    # Higher Elasticity = Performance matters more (Score moves more freely)
    # Lower Elasticity = Rating matters more (Score sticks to Anchor)
    elasticity_map = {
        'ACC': 0.35, # Hard to fake, rating matters a lot
        'OPN': 0.30, 
        'MID': 0.35,
        'END': 0.35,
        'CAL': 0.40, # Calculation allows some variance
        'INT': 0.50, # Speed is a style choice, allow more variance
        'TMG': 0.50, # Time management varies wildly
        'RES': 0.45,
        'TAC': 0.40,
        'STR': 0.25, # Strategy is very rating-bound
        'ATK': 0.45,
        'DEF': 0.40  
    }
    
    elasticity = elasticity_map.get(metric_name, 0.35)
    
    # 3. Weighted Blend

    
    final_score = (anchor * (1.0 - elasticity)) + (raw_score * elasticity)
    
    return int(max(0, min(100, final_score)))
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
        user_color = get_user_color(data) # Ensure you have this helper
        user_rating = get_player_rating(data, username) # Get Elo
        
        if user_color:
            # --- CALCULATE RAW METRICS ---
            accuracy_list = calculate_game_phase_accuracy(data)[user_color]
            raw_acc = accuracy_list.get('accuracy', None)
            raw_opn = accuracy_list.get('opening', None)
            raw_mid = accuracy_list.get('middlegame', None)
            raw_end = accuracy_list.get('endgame', None)

            tf_list = calculate_psych_metrics(data)[user_color]
            raw_cal = tf_list.get('CAL', None)
            raw_int = tf_list.get('INT', None)
            raw_tmg = tf_list.get('TMG', None)

            raw_res = calculate_resilience(data)[user_color] or 50 # Handle None

            pos_list = calculate_tactics_and_strategy(data)[user_color]
            raw_tac = pos_list.get('TAC', None)
            raw_str = pos_list.get('STR', None)

            atk_def_list = calculate_attack_and_defense(data)[user_color]
            raw_atk = atk_def_list.get('ATK', None)
            raw_def = atk_def_list.get('DEF', None)

            # --- 2.1 CALIBRATE METRICS (SMOOTHING) ---
            game_metrics['ACC'] = calibrate_score(raw_acc, user_rating, 'ACC')
            game_metrics['OPN'] = calibrate_score(raw_opn, user_rating, 'OPN')
            game_metrics['MID'] = calibrate_score(raw_mid, user_rating, 'MID')
            game_metrics['END'] = calibrate_score(raw_end, user_rating, 'END')
            
            game_metrics['CAL'] = calibrate_score(raw_cal, user_rating, 'CAL')
            game_metrics['INT'] = calibrate_score(raw_int, user_rating, 'INT')
            game_metrics['TMG'] = calibrate_score(raw_tmg, user_rating, 'TMG')
            
            game_metrics['RES'] = calibrate_score(raw_res, user_rating, 'RES')
            game_metrics['TAC'] = calibrate_score(raw_tac, user_rating, 'TAC')
            game_metrics['STR'] = calibrate_score(raw_str, user_rating, 'STR')
            
            game_metrics['ATK'] = calibrate_score(raw_atk, user_rating, 'ATK')
            game_metrics['DEF'] = calibrate_score(raw_def, user_rating, 'DEF')

            
    except Exception as e:
        app.logger.error(f"Metric error {username}: {e}")
        import traceback
        traceback.print_exc()

    session["games_processed"] += 1
    for m in METRICS:
        # Avoid appending zeros if calculation failed, append previous avg or keep 0
        val = game_metrics.get(m, 0)
        session["metrics_list"][m].append(val)

    # 3. Aggregate
    current_metrics = _calculate_aggregates(session)
    # Filter out empty metrics for overall score
    valid_metrics = [v for k, v in current_metrics.items() if v > 0]
    overall = int(sum(valid_metrics) / len(valid_metrics)) if valid_metrics else 0
    
    return jsonify({
        "games_processed": session["games_processed"],
        "metrics": current_metrics,
        "overall": overall,
        "description": "Analyzing..." 
    })

def _calculate_aggregates(session):
    aggregates = {}
    
    for m in METRICS:
        # Filter out None values
        valid_values = [v for v in session['metrics_list'][m] if v is not None]
        
        if not valid_values:
            aggregates[m] = 0
            continue
            
        if len(valid_values) < 5:
            # Not enough data for fancy stats, use simple mean
            aggregates[m] = int(sum(valid_values) / len(valid_values))
        else:
            # --- STABILITY ALGORITHM ---
            # We want to remove outliers (e.g., one game where opponent disconnected).
            
            # 1. Sort values
            valid_values.sort()
            
            # 2. Trim top and bottom 10% (Trimmed Mean)
            cutoff = int(len(valid_values) * 0.1)
            trimmed = valid_values[cutoff : len(valid_values)-cutoff]
            
            if not trimmed: trimmed = valid_values # Fallback
            
            mean_val = sum(trimmed) / len(trimmed)
            
            # 3. Stability Bonus (Optional)
            # If standard deviation is low, we trust the mean more.
            # But for simplicity, Trimmed Mean is usually the best "True Skill" estimator.
            
            aggregates[m] = int(mean_val)
            
    return aggregates

if __name__ == '__main__':
    app.run(debug=True, port=5000)
