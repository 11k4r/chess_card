from flask import Flask, render_template, request, jsonify
import requests
import random

app = Flask(__name__)

# Metrics list with abbreviations
METRICS = [
    "OPN", "MID", "END", 
    "TAC", "STR", "CAL", 
    "TMG", "INT", "ATK", 
    "DEF", "ACC", "RES"
]

# Player Descriptions
DESCRIPTIONS = [
    "The Blunder Master",
    "Tactics Wizard",
    "The Theoretician",
    "Endgame Specialist",
    "Speed Demon",
    "The Strategist",
    "Puzzle Solver",
    "The Swindler",
    "Time Trouble Addict",
    "Gambit Lover",
    "Checkmate Artist",
    "Positional Genius"
]

def get_chess_com_data(username):
    """Fetches public data from Chess.com API"""
    headers = {'User-Agent': 'ChessCardGenerator/1.0 (contact: your@email.com)'}
    
    # 1. Profile Data
    profile_resp = requests.get(f"https://api.chess.com/pub/player/{username}", headers=headers)
    if profile_resp.status_code != 200:
        return None
    
    profile = profile_resp.json()
    
    # 2. Stats Data
    stats_resp = requests.get(f"https://api.chess.com/pub/player/{username}/stats", headers=headers)
    stats = stats_resp.json() if stats_resp.status_code == 200 else {}
    
    return {
        "profile": profile,
        "stats": stats
    }

def calculate_theme(title):
    """Determines the card theme based on player title"""
    if not title:
        return "common"
    
    title = title.upper()
    if title in ['GM', 'WGM']:
        return "gm"
    elif title in ['IM', 'WIM', 'FM', 'WFM', 'NM', 'CM', 'WCM']:
        return "titled"
    else:
        return "common"

@app.route('/')
def home():
    return render_template('index.html')

@app.route('/api/generate', methods=['POST'])
def generate_card():
    data = request.json
    username = data.get('username')
    
    if not username:
        return jsonify({"error": "Username is required"}), 400

    chess_data = get_chess_com_data(username)
    
    if not chess_data:
        return jsonify({"error": "Player not found"}), 404

    profile = chess_data['profile']
    stats = chess_data['stats']
    
    # Extract specific ratings
    rapid_rating = stats.get('chess_rapid', {}).get('last', {}).get('rating', 'N/A')
    blitz_rating = stats.get('chess_blitz', {}).get('last', {}).get('rating', 'N/A')
    bullet_rating = stats.get('chess_bullet', {}).get('last', {}).get('rating', 'N/A')
    
    # Determine Title and Country
    title = profile.get('title', '')
    country_url = profile.get('country', '')
    country_code = country_url.split('/')[-1] if country_url else 'xx'
    
    # Generate Random Metrics (0-99)
    # In a real app, this would use game analysis data
    generated_metrics = {metric: random.randint(65, 99) for metric in METRICS}
    
    # Calculate an "Overall" score based on metrics + rating weight
    avg_metric = sum(generated_metrics.values()) / len(generated_metrics)
    overall_score = int(avg_metric)

    # Random Description
    description = random.choice(DESCRIPTIONS)

    return jsonify({
        "username": profile.get('username'),
        "name": profile.get('name', profile.get('username')),
        "avatar": profile.get('avatar', 'https://www.chess.com/bundles/web/images/user-image.svg'),
        "title": title,
        "country_code": country_code,
        "ratings": {
            "rapid": rapid_rating,
            "blitz": blitz_rating,
            "bullet": bullet_rating
        },
        "metrics": generated_metrics,
        "overall": overall_score,
        "theme": calculate_theme(title),
        "description": description
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)