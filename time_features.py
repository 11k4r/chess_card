import math
import statistics
import re
from trace_parser import parse_stockfish_trace

# --- CONFIGURATION ---

THRESHOLDS = {
    'bullet': {'int_limit': 1.5, 'calc_start': 2.0,  'freeze': 5.0},
    'blitz':  {'int_limit': 4.0, 'calc_start': 5.0,  'freeze': 12.0},
    'rapid':  {'int_limit': 8.0, 'calc_start': 10.0, 'freeze': 30.0},
    'classical': {'int_limit': 15.0, 'calc_start': 20.0, 'freeze': 120.0}
}

WIN_GAMMA = 0.00368208
CAL_CLIFF_WP = 10.0  

# --- 1. PGN & TIME PARSING ---

def parse_time_control(pgn_headers):
    """Extracts time control and determines the category."""
    tc_tag = pgn_headers.get("TimeControl", "?")
    base, inc = 600.0, 0.0
    category = "blitz"

    if tc_tag and tc_tag not in ["?", "-"]:
        try:
            if "+" in tc_tag:
                parts = tc_tag.split("+")
                base, inc = float(parts[0]), float(parts[1])
            else:
                base, inc = float(tc_tag), 0.0
        except ValueError:
            pass

    est_duration = base + (40 * inc)
    if est_duration < 180: category = "bullet"
    elif est_duration < 600: category = "blitz"
    else: category = "rapid"

    return {"base": base, "inc": inc, "category": category}

def enrich_game_data_with_time(game_data):
    # (This function remains the same as your previous version)
    # It populates 'time_per_move' list.
    import io
    import chess.pgn
    
    pgn_str = game_data.get('pgn', '')
    if not pgn_str: return

    try:
        pgn_io = io.StringIO(pgn_str)
        game = chess.pgn.read_game(pgn_io)
    except:
        game = None

    if not game:
        game_data['time_metadata'] = {'base': 600, 'inc': 0, 'category': 'blitz'}
        game_data['time_per_move'] = []
        return

    headers = game.headers
    tc_info = parse_time_control(headers)
    game_data['time_metadata'] = tc_info

    clocks = {chess.WHITE: tc_info['base'], chess.BLACK: tc_info['base']}
    inc = tc_info['inc']
    time_spent_data = []

    node = game
    while node.variations:
        next_node = node.variation(0)
        turn = node.board().turn 
        
        # Clock Regex from comment
        clk_match = re.search(r'\[%clk\s+([\d:.]+)]', next_node.comment)
        current_clock = 0.0
        has_clock = False

        if clk_match:
            try:
                parts = clk_match.group(1).split(':')
                if len(parts) == 3: current_clock = float(parts[0])*3600 + float(parts[1])*60 + float(parts[2])
                elif len(parts) == 2: current_clock = float(parts[0])*60 + float(parts[1])
                elif len(parts) == 1: current_clock = float(parts[0])
                has_clock = True
            except ValueError: pass
        
        if not has_clock: current_clock = clocks[turn]

        delta = max(0.1, clocks[turn] - current_clock + inc)
        time_spent_data.append(delta)
        clocks[turn] = current_clock
        node = next_node

    game_data['time_per_move'] = time_spent_data

# --- 2. HELPER MATH ---

def to_win_percent(cp):
    if cp is None: return 50.0
    if isinstance(cp, str):
        if 'M' in cp or '#' in cp: return 0.0 if '-' in cp else 100.0
        try: cp = float(cp)
        except: return 50.0
    cp = max(-1000, min(1000, cp)) 
    return 100 / (1 + math.exp(-WIN_GAMMA * cp))

# --- 3. PSYCH METRICS (UPDATED) ---

def calculate_psych_metrics(game_data):
    if 'time_per_move' not in game_data:
        enrich_game_data_with_time(game_data)

    analysis = game_data.get('analysis', [])
    times = game_data.get('time_per_move', [])
    tc_meta = game_data.get('time_metadata', {'category': 'blitz'})
    t_cfg = THRESHOLDS.get(tc_meta['category'], THRESHOLDS['blitz'])
    
    # 1. Setup Clocks for Tracking
    base_time = tc_meta.get('base', 600)
    increment = tc_meta.get('inc', 0)
    clocks = { 'white': base_time, 'black': base_time }

    stats = {
        'white': {'cal_evs': [], 'int_evs': [], 'tmg_penalty_sum': 0.0, 'moves': 0, 'lost_on_time': False},
        'black': {'cal_evs': [], 'int_evs': [], 'tmg_penalty_sum': 0.0, 'moves': 0, 'lost_on_time': False}
    }

    # 2. Check for "Loss on Time"
    pgn_text = game_data.get('pgn', '')
    termination_match = re.search(r'\[Termination\s+"([^"]+)"\]', pgn_text)
    result_match = re.search(r'\[Result\s+"([^"]+)"\]', pgn_text)
    
    if termination_match and result_match:
        term_text = termination_match.group(1).lower()
        if 'time' in term_text or 'forfeit' in term_text:
            result = result_match.group(1)
            if result == "1-0": stats['black']['lost_on_time'] = True
            elif result == "0-1": stats['white']['lost_on_time'] = True

    count = min(len(analysis), len(times))
    
    for i in range(count):
        step = analysis[i]
        move_time = times[i]
        is_white = (i % 2 == 0)
        color = 'white' if is_white else 'black'
        opp_color = 'black' if is_white else 'white'
        
        stats[color]['moves'] += 1

        # Update Clocks (After my move)
        clocks[color] = max(0.1, clocks[color] - move_time + increment)
        
        # Calculate Clock Difference
        # Positive = I have more time. Negative = I have less.
        time_diff = clocks[color] - clocks[opp_color]
        
        has_time_advantage = time_diff > max(30.0, base_time * 0.15)
        
        # --- NEW: Chronic Time Disadvantage Penalty ---
        # If I am behind on time after move 10, apply a small "pressure penalty".
        if i >= 10 and time_diff < 0:
            deficit = abs(time_diff)
            # Scaling: 
            # Bullet (Int Limit 1.5) -> High Penalty factor
            # Rapid (Int Limit 8.0) -> Low Penalty factor
            # Formula: Deficit * (0.2 / Int_Limit)
            # Example: Down 10s in Blitz (Limit 4.0) -> 10 * 0.05 = 0.5 penalty points per move.
            factor = 0.2 / max(1.0, t_cfg['int_limit'])
            stats[color]['tmg_penalty_sum'] += (deficit * factor)

        top_lines = step.get('top_lines', [])
        played_eval = step.get('played_eval')
        if len(top_lines) < 2: continue

        # --- METRICS ---
        wp_best = to_win_percent(top_lines[0]['score'])
        wp_second = to_win_percent(top_lines[1]['score'])
        wp_played = to_win_percent(played_eval)
        
        cliff_diff = abs(wp_best - wp_second) 
        accuracy_loss = max(0, wp_best - wp_played)
        
        # --- 1. INTUITION ---
        is_opening = i < 16
        if not is_opening and move_time < t_cfg['calc_start']:
            fast_limit = t_cfg['int_limit'] * 0.5
            if move_time <= fast_limit:
                speed_score = 100
            else:
                ratio = (move_time - fast_limit) / (t_cfg['calc_start'] - fast_limit)
                speed_score = 100 - (50 * ratio)

            acc_score = 100 * math.exp(-0.06 * accuracy_loss)
            move_int_score = (acc_score * 0.7) + (speed_score * 0.3)
            stats[color]['int_evs'].append(move_int_score)

        # --- 2. CALCULATION ---
        if cliff_diff > CAL_CLIFF_WP:
            solve_score = 100 * math.exp(-0.05 * accuracy_loss)
            if move_time < t_cfg['calc_start']:
                if accuracy_loss < 5.0: solve_score = min(100, solve_score * 1.1)
                else: solve_score *= 0.8
            stats[color]['cal_evs'].append(solve_score)

        # --- 3. TIME MANAGEMENT ---
        penalty = 0.0
        
        # A. The Rush
        if cliff_diff > 15.0 and move_time < t_cfg['int_limit']:
            if accuracy_loss > 8.0: 
                rush_factor = max(0.0, (t_cfg['int_limit'] - move_time) / t_cfg['int_limit'])
                p_val = (accuracy_loss * 2.0)
                if accuracy_loss > 20.0: p_val += 20.0 
                penalty += (p_val * rush_factor)

        # B. The Freeze
        if cliff_diff < 5.0 and move_time > t_cfg['freeze']:
            if accuracy_loss > 4.0:
                overtime = move_time - t_cfg['freeze']
                freeze_penalty = (overtime * 1.0)
                if has_time_advantage:
                    freeze_penalty *= 0.2 
                penalty += freeze_penalty

        stats[color]['tmg_penalty_sum'] += penalty

    # --- 4. AGGREGATION ---
    results = {'white': {}, 'black': {}}
    
    for c in ['white', 'black']:
        d = stats[c]
        
        cal_final = 50
        if d['cal_evs']: cal_final = statistics.mean(d['cal_evs'])
            
        int_final = 50
        if d['int_evs']: int_final = statistics.mean(d['int_evs'])
            
        # TMG: Exponential Decay
        tmg_final = 100.0
        if d['moves'] > 0:
            avg_penalty = d['tmg_penalty_sum'] / d['moves']
            tmg_final = 100.0 * math.exp(-avg_penalty / 12.0)
        
        if d['lost_on_time']:
            tmg_final = min(tmg_final, 40.0)
            tmg_final -= 10.0 
        
        results[c] = {
            'CAL': int(max(0, min(100, cal_final))),
            'INT': int(max(0, min(100, int_final))),
            'TMG': int(max(0, min(100, tmg_final)))
        }

    return results