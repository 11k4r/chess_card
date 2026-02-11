import math
import statistics
from trace_parser import parse_stockfish_trace

# --- CONFIGURATION ---
RES_WIN_GAMMA = 0.00368208
RES_PRESSURE_MIN = 4.0   # 4% win chance (almost dead)
RES_PRESSURE_MAX = 35.0  # 35% win chance (clearly worse, but fighting)
RES_THREAT_THRESHOLD = 0.6 # If engine sees high threats, it's pressure even if eval is even

def _to_win_percent(cp):
    """Local helper to convert CP to Win%."""
    if cp is None: return 50.0
    if isinstance(cp, str):
        if 'M' in cp or '#' in cp:
            return 0.0 if '-' in cp else 100.0
        try: cp = float(cp)
        except: return 50.0
    cp = max(-1000, min(1000, cp)) 
    return 100 / (1 + math.exp(-RES_WIN_GAMMA * cp))

def calculate_resilience(game_data):
    """
    Calculates Resilience (RES) - performance in bad/difficult positions.
    Returns: {'white': Score (0-100) or None, 'black': Score (0-100) or None}
    """
    analysis = game_data.get('analysis', [])
    
    # Store resilience events: (score, weight)
    events = {'white': [], 'black': []}

    for i, step in enumerate(analysis):
        # 1. Determine whose turn it is
        is_white = (i % 2 == 0)
        color = 'white' if is_white else 'black'
        
        # 2. Extract Data
        top_lines = step.get('top_lines', [])
        played_eval = step.get('played_eval')
        static_trace = step.get('static_trace')
        
        if len(top_lines) < 1: continue

        # 3. Analyze the Position *BEFORE* the move (The "Pressure" Context)
        # The engine's top line tells us the objective truth of the position
        best_eval_cp = top_lines[0]['score']
        
        # Convert CP to Win% from the perspective of the current player
        # Note: Engine scores are usually usually "white-centric" or "side-to-move-centric"
        # We assume standard UCI: scores are relative to side to move OR absolute white. 
        # *Critically*, your existing trace_parser usually gives white-relative scores.
        # Let's assume CP is White-Relative.
        
        wp_white = _to_win_percent(best_eval_cp)
        current_player_wp = wp_white if is_white else (100.0 - wp_white)

        # 4. Check: Is this a "Pressure" Position?
        is_pressure = False
        
        # A. Evaluation Pressure (Losing but not dead)
        if RES_PRESSURE_MIN <= current_player_wp <= RES_PRESSURE_MAX:
            is_pressure = True
            
        # B. Tactical Pressure (Threats) - Optional override
        # If eval is equal (e.g. 50%) but threats are massive, it's resilience time.
        if not is_pressure and static_trace:
            try:
                # We look for threats AGAINST the current player
                parsed = parse_stockfish_trace(static_trace)
                # If I am white, I care about threats from Black
                threat_score = 0
                if is_white:
                    threat_score = parsed.get('black', {}).get('threats', {}).get('mg', 0)
                else:
                    threat_score = parsed.get('white', {}).get('threats', {}).get('mg', 0)
                
                # Heuristic: Threat score > Threshold implies dangerous position
                if threat_score and threat_score > RES_THREAT_THRESHOLD:
                    is_pressure = True
            except:
                pass # Trace parsing failed, ignore threat component

        if not is_pressure:
            continue

        # 5. Calculate Performance (Continuous Scoring)
        # How much Equity did we lose?
        # played_eval is usually White-Relative.
        played_cp = played_eval
        # Handle mate strings in played_eval if necessary
        if isinstance(played_cp, str): 
             # Simplify: if it's a mate, treated as massive CP
             played_cp = 2000 if not '-' in played_cp else -2000

        wp_played_white = _to_win_percent(played_cp)
        played_wp_relative = wp_played_white if is_white else (100.0 - wp_played_white)
        
        # Loss = Max Potential Win% - Actual Realized Win%
        # Example: I had 20%. I played a move and now have 15%. Loss = 5.
        equity_loss = max(0.0, current_player_wp - played_wp_relative)
        
        # Scoring Curve:
        # Loss 0.0 -> Score 100 (Perfect Defense)
        # Loss 2.0 -> Score 82
        # Loss 5.0 -> Score 60
        # Loss 10.0 -> Score 36 (Collapse)
        score = 100.0 * math.exp(-0.10 * equity_loss)
        
        events[color].append(score)

    # 6. Final Aggregation
    results = {}
    for c in ['white', 'black']:
        scores = events[c]
        if not scores:
            results[c] = None # Explicitly None if no pressure encountered
        else:
            # Simple average of all pressure situations
            final_res = statistics.mean(scores)
            results[c] = int(max(0, min(100, final_res)))
            
    return results