import math
import statistics
from trace_parser import parse_stockfish_trace

# --- CONFIGURATION ---
ATK_WIN_GAMMA = 0.00368208

# Thresholds
THREAT_TRIGGER = 0.8        
KING_SAFETY_SENSITIVITY = 2.0 

def _to_win_percent(cp):
    if cp is None: return 50.0
    if isinstance(cp, str):
        if 'M' in cp or '#' in cp:
            return 0.0 if '-' in cp else 100.0
        try: cp = float(cp)
        except: return 50.0
    cp = max(-1000, min(1000, cp)) 
    return 100 / (1 + math.exp(-ATK_WIN_GAMMA * cp))

def calculate_attack_and_defense(game_data):
    """
    Calculates Attack (ATK) and Defense (DEF) scores (0-100).
    Optimized for high-level play: Rewards cashing out and holding vs heavy threats.
    """
    analysis = game_data.get('analysis', [])
    
    # Store tuples: (score, weight)
    # We weight "Heavy" moments more than "Light" moments.
    stats = {
        'white': {'atk_data': [], 'def_data': []},
        'black': {'atk_data': [], 'def_data': []}
    }

    for i in range(len(analysis) - 1):
        step_curr = analysis[i]
        step_next = analysis[i+1]
        
        is_white = (i % 2 == 0)
        color = 'white' if is_white else 'black'
        opp_color = 'black' if is_white else 'white'
        
        trace_str_curr = step_curr.get('static_trace')
        trace_str_next = step_next.get('static_trace')
        played_eval = step_curr.get('played_eval')
        top_lines = step_curr.get('top_lines', [])

        if not trace_str_curr or not trace_str_next or len(top_lines) < 1:
            continue

        try:
            trace_curr = parse_stockfish_trace(trace_str_curr)
            trace_next = parse_stockfish_trace(trace_str_next)
        except:
            continue

        wp_best = _to_win_percent(top_lines[0]['score'])
        wp_played = _to_win_percent(played_eval)
        # Accuracy is the baseline. 
        # If accuracy_loss is 0, you played PERFECTLY.
        accuracy_loss = max(0, wp_best - wp_played)

        # Extract Metrics
        my_threats_curr = trace_curr.get(color, {}).get('threats', {}).get('mg', 0) or 0
        my_threats_next = trace_next.get(color, {}).get('threats', {}).get('mg', 0) or 0
        
        opp_threats_curr = trace_curr.get(opp_color, {}).get('threats', {}).get('mg', 0) or 0
        opp_threats_next = trace_next.get(opp_color, {}).get('threats', {}).get('mg', 0) or 0
        
        opp_ks_curr = trace_curr.get(opp_color, {}).get('king_safety', {}).get('mg', 0) or 0
        opp_ks_next = trace_next.get(opp_color, {}).get('king_safety', {}).get('mg', 0) or 0

        my_ks_curr = trace_curr.get(color, {}).get('king_safety', {}).get('mg', 0) or 0
        my_ks_next = trace_next.get(color, {}).get('king_safety', {}).get('mg', 0) or 0

        # --- 3. ATTACK (ATK) ---
        is_attacking = (my_threats_curr > THREAT_TRIGGER)
        
        if is_attacking:
            score = 0.0
            weight = 1.0 # Default weight
            
            # Factor 1: Pressure Maintenance
            if my_threats_next >= my_threats_curr:
                # Sustained or Increased -> Perfect
                score = 100.0
                # Bonus weight for high-intensity sustained attacks
                if my_threats_curr > 2.0: weight = 2.0
            else:
                # Pressure dropped. Why?
                # Case A: "The Cash Out" (Good)
                # If accuracy is high (loss < 3%), we assume the drop was intentional (e.g., forced trade).
                if accuracy_loss < 3.0:
                    score = 95.0 # Nearly perfect
                else:
                    # Case B: "The Fizzle" (Bad)
                    # We dropped threats and lost eval -> Blunder
                    retention = my_threats_next / max(0.1, my_threats_curr)
                    score = 60.0 + (30.0 * retention)
            
            # Factor 2: Damage (King Safety)
            # If we hurt their king, score boosts to 100
            if (opp_ks_curr - opp_ks_next) > 0.2:
                score = 100.0
                weight = 2.0 # Critical moment

            # Accuracy Punishment
            final_score = score * math.exp(-0.04 * accuracy_loss)
            stats[color]['atk_data'].append((max(0, min(100, final_score)), weight))

        # --- 4. DEFENSE (DEF) ---
        is_defending = (opp_threats_curr > THREAT_TRIGGER)
        
        if is_defending:
            score = 0.0
            weight = 1.0
            
            # Magnitude Scaling: Defending a 5.0 threat is harder/more important than 0.8
            # We increase weight for heavy threats
            weight = 1.0 + (opp_threats_curr * 0.5)
            
            threat_change = opp_threats_next - opp_threats_curr
            
            # Scenario A: Reduced Threats (Excellent)
            if threat_change < -0.1:
                score = 100.0
            
            # Scenario B: Held the Line (Good)
            elif threat_change < 0.2:
                # If I held against a massive threat (e.g. 3.0), that is God-Tier
                if opp_threats_curr > 2.0: score = 100.0
                else: score = 90.0
                
            # Scenario C: Failed (Threats grew)
            else:
                score = max(0, 80.0 - (threat_change * 40.0))

            # King Safety Bonus
            if my_ks_next > my_ks_curr: score = 100.0

            # Accuracy Punishment (Defense allows ZERO mistakes)
            final_score = score * math.exp(-0.08 * accuracy_loss)
            stats[color]['def_data'].append((max(0, min(100, final_score)), weight))

    # --- 5. AGGREGATION (Weighted Average) ---
    results = {}
    for c in ['white', 'black']:
        d = stats[c]
        
        # Helper for weighted mean
        def get_weighted_score(data_list):
            if not data_list: return 50
            total_val = 0.0
            total_w = 0.0
            for val, w in data_list:
                total_val += (val * w)
                total_w += w
            return int(total_val / total_w)
            
        results[c] = {
            'ATK': get_weighted_score(d['atk_data']),
            'DEF': get_weighted_score(d['def_data'])
        }

    return results