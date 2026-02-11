import math
import statistics
import io
import re
import chess.pgn
from trace_parser import parse_stockfish_trace

# --- CONFIGURATION ---
TAC_WIN_GAMMA = 0.00368208

# Thresholds
TAC_THREAT_MIN = 0.5        
TAC_BLUNDER_OPP = 15.0      

STR_THREAT_MAX = 0.4        
STR_EVAL_STABILITY = 15.0   

# Strategy Baselines: "Patience Factor"
# In Bullet, maintaining the status quo (doing nothing) is rewarded (Score 85).
# In Rapid, you are expected to IMPROVE the position (Start at 65), so maintaining is "mediocre".
STR_BASELINES = {
    'bullet': 85.0, 
    'blitz': 75.0,
    'rapid': 65.0,
    'classical': 60.0
}

STR_WEIGHTS = {
    'mobility':    {'delta': 20.0, 'abs': 10.0}, 
    'space':       {'delta': 25.0, 'abs': 15.0}, 
    'king_safety': {'delta': 30.0, 'abs': 10.0}, 
    'pawns':       {'delta': 20.0, 'abs': 10.0}, 
    'imbalance':   {'delta': 10.0, 'abs': 5.0}   
}

def _to_win_percent(cp):
    if cp is None: return 50.0
    if isinstance(cp, str):
        if 'M' in cp or '#' in cp:
            return 0.0 if '-' in cp else 100.0
        try: cp = float(cp)
        except: return 50.0
    cp = max(-1000, min(1000, cp)) 
    return 100 / (1 + math.exp(-TAC_WIN_GAMMA * cp))

def _get_time_category(game_data):
    """Parses/Defaults the time control category."""
    # If already enriched
    if 'time_metadata' in game_data:
        return game_data['time_metadata'].get('category', 'blitz')
        
    # Fallback parsing
    pgn = game_data.get('pgn', '')
    if 'TimeControl' in pgn:
        try:
            match = re.search(r'\[TimeControl "(\d+)(\+\d+)?"\]', pgn)
            if match:
                base = float(match.group(1))
                inc = 0
                if match.group(2): inc = float(match.group(2).replace('+', ''))
                est = base + (40 * inc)
                if est < 180: return 'bullet'
                if est < 600: return 'blitz'
                return 'rapid'
        except: pass
    return 'blitz'

def calculate_tactics_and_strategy(game_data):
    """
    Calculates Tactics (TAC) and Strategy (STR) with Time Control Context.
    """
    analysis = game_data.get('analysis', [])
    category = _get_time_category(game_data)
    baseline = STR_BASELINES.get(category, 75.0)
    
    stats = {
        'white': {'tac_scores': [], 'str_scores': []},
        'black': {'tac_scores': [], 'str_scores': []}
    }

    for i in range(len(analysis) - 1):
        step_curr = analysis[i]
        step_next = analysis[i+1]
        
        is_white = (i % 2 == 0)
        color = 'white' if is_white else 'black'
        
        top_lines = step_curr.get('top_lines', [])
        played_eval = step_curr.get('played_eval')
        trace_str_curr = step_curr.get('static_trace')
        trace_str_next = step_next.get('static_trace')
        
        if len(top_lines) < 1 or not trace_str_curr: 
            continue

        wp_best = _to_win_percent(top_lines[0]['score'])
        wp_played = _to_win_percent(played_eval)
        accuracy_loss = max(0, wp_best - wp_played)
        
        try:
            trace_curr = parse_stockfish_trace(trace_str_curr)
            trace_next = parse_stockfish_trace(trace_str_next) if trace_str_next else None
        except:
            continue

        threats_mg = abs(trace_curr.get('total', {}).get('threats', {}).get('mg', 0))

        # --- 2. TACTICS (TAC) ---
        opp_blundered = False
        if i > 0:
            prev_step = analysis[i-1]
            prev_best = _to_win_percent(prev_step.get('top_lines', [{}])[0].get('score'))
            prev_played = _to_win_percent(prev_step.get('played_eval'))
            if abs(prev_best - prev_played) > TAC_BLUNDER_OPP:
                opp_blundered = True

        is_tactical = (threats_mg > TAC_THREAT_MIN) or opp_blundered

        if is_tactical:
            tac_score = 100.0 * math.exp(-0.10 * accuracy_loss)
            stats[color]['tac_scores'].append(tac_score)

        # --- 3. STRATEGY (STR) ---
        is_strategic = (threats_mg < STR_THREAT_MAX) and (not is_tactical) and trace_next
        
        if is_strategic:
            my_key = 'white' if is_white else 'black'
            
            raw_str_sum = 0.0
            for term, w_conf in STR_WEIGHTS.items():
                val_curr = trace_curr.get(my_key, {}).get(term, {}).get('mg', 0) or 0
                val_next = trace_next.get(my_key, {}).get(term, {}).get('mg', 0) or 0
                
                delta = val_next - val_curr
                absolute = val_next 
                
                # Delta matters less in Bullet (hard to improve), Abs matters more
                if category == 'bullet':
                    term_score = (delta * (w_conf['delta'] * 0.5)) + (absolute * (w_conf['abs'] * 1.5))
                else:
                    term_score = (delta * w_conf['delta']) + (absolute * w_conf['abs'])
                
                raw_str_sum += term_score
            
            # Apply Time-Control Adjusted Baseline
            str_score = baseline + raw_str_sum
            
            # Punishment for inaccuracy
            if accuracy_loss > 5.0:
                str_score -= (accuracy_loss * 2.0)
            
            stats[color]['str_scores'].append(max(0, min(100, str_score)))

    # --- 4. AGGREGATE ---
    results = {}
    for c in ['white', 'black']:
        d = stats[c]
        
        tac_final = 50
        if d['tac_scores']: tac_final = statistics.mean(d['tac_scores'])
            
        str_final = 50
        if d['str_scores']: str_final = statistics.mean(d['str_scores'])
            
        results[c] = {
            'TAC': int(max(0, min(100, tac_final))),
            'STR': int(max(0, min(100, str_final)))
        }
        
    return results