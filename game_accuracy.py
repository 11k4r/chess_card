import io
import math
import statistics
import chess.pgn
from typing import Dict, List, Optional, Any

# --- Constants ---
ACC_A = 103.1668100711649
ACC_B = -3.166924740191411
ACC_K = 0.04354415386753951
WIN_GAMMA = 0.00368208
OPENING_MOVE_LIMIT = 15
ENDGAME_MATERIAL_THRESHOLD = 20 

PIECE_VALUES = {
    chess.QUEEN: 9, chess.ROOK: 5, chess.BISHOP: 3, 
    chess.KNIGHT: 3, chess.PAWN: 0, chess.KING: 0
}

def calculate_game_phase_accuracy(game_data: Dict[str, Any]) -> Dict[str, Dict[str, Optional[float]]]:
    """
    Calculates accuracy for White and Black broken down by Game Phase.
    """
    
    # --- Helper Functions ---
    def to_win_percent(cp: float) -> float:
        cp = max(-10000, min(10000, cp))
        return 100 / (1 + math.exp(-WIN_GAMMA * cp))

    def calculate_move_accuracy(wp_before: float, wp_after: float) -> float:
        if wp_after >= wp_before: return 100.0
        diff = wp_before - wp_after
        raw = ACC_A * math.exp(-ACC_K * diff) + ACC_B
        return max(0.0, min(100.0, raw + 1.0)) 

    def aggregate_score(accs: List[float], weights: List[float]) -> Optional[float]:
        if not accs or not weights: return None 
        total_weight = sum(weights)
        if total_weight == 0: return 0.0
        
        w_mean = sum(a * w for a, w in zip(accs, weights)) / total_weight
        try:
            h_mean = statistics.harmonic_mean(accs)
        except statistics.StatisticsError:
            h_mean = 0.0
        return (w_mean + h_mean) / 2

    def get_material_score(board: chess.Board) -> int:
        score = 0
        for pt in [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT]:
            score += len(board.pieces(pt, chess.WHITE)) * PIECE_VALUES[pt]
            score += len(board.pieces(pt, chess.BLACK)) * PIECE_VALUES[pt]
        return score

    # --- Execution ---
    raw_analysis = game_data.get('analysis', [])
    if not raw_analysis:
        return {'white': {}, 'black': {}}

    # 1. Build Win Percentages
    cps = [20] 
    win_percents = [to_win_percent(20)] 
    
    for item in raw_analysis:
        val = item.get('played_eval')
        if val is None: val = cps[-1] if cps else 20
            
        if isinstance(val, str):
            val = 10000 if ('M' in val and not val.startswith('-')) else -10000
        else:
            try: val = int(val)
            except: val = 0 
            
        cps.append(val)
        win_percents.append(to_win_percent(val))

    # 2. Calculate Accuracies
    move_accuracies = []
    weights = []
    
    # Calculate weights based on volatility
    all_windows = []
    window_size = max(2, min(8, len(win_percents) // 10))
    
    if len(win_percents) > window_size:
        for j in range(len(win_percents) - window_size + 1):
            all_windows.append(win_percents[j : j + window_size])
            
    # Pad weights to match length
    weights = [0.5] * (len(win_percents) - 1) # Default
    
    for i in range(len(win_percents) - 1):
        # Accuracy
        wp_prev, wp_next = win_percents[i], win_percents[i+1]
        is_white = (i % 2 == 0)
        acc = calculate_move_accuracy(wp_prev, wp_next) if is_white else calculate_move_accuracy(100-wp_prev, 100-wp_next)
        move_accuracies.append(acc)
        
        # Volatility Weight
        if i < len(all_windows):
            std = statistics.stdev(all_windows[i]) if len(all_windows[i]) > 1 else 0.0
            weights[i] = max(0.5, min(12.0, std))

    # 3. Phase Categorization
    buckets = {
        'white': {'opening': [], 'middlegame': [], 'endgame': [], 'all': []},
        'black': {'opening': [], 'middlegame': [], 'endgame': [], 'all': []}
    }
    
    try:
        pgn_io = io.StringIO(game_data.get('pgn', ''))
        game = chess.pgn.read_game(pgn_io)
        if not game: raise ValueError("Invalid PGN")
        
        board = game.board()
        for i, move in enumerate(game.mainline_moves()):
            if i >= len(move_accuracies): break
            
            acc, w = move_accuracies[i], weights[i]
            color = 'white' if board.turn == chess.WHITE else 'black'
            
            # Determine Phase
            phase = 'middlegame'
            if get_material_score(board) <= ENDGAME_MATERIAL_THRESHOLD: phase = 'endgame'
            elif board.fullmove_number <= OPENING_MOVE_LIMIT: phase = 'opening'
            
            buckets[color][phase].append((acc, w))
            buckets[color]['all'].append((acc, w))
            board.push(move)

    except Exception as e:
        print(f"PGN Error: {e}")
        return {'white': {}, 'black': {}}

    # 4. Final Aggregation
    results = {'white': {}, 'black': {}}
    for color in ['white', 'black']:
        for cat in ['opening', 'middlegame', 'endgame', 'all']:
            data = buckets[color][cat]
            if data:
                accs, ws = zip(*data)
                score = aggregate_score(accs, ws)
                results[color][cat] = round(score, 2) if score is not None else None
            else:
                results[color][cat] = None
        
        results[color]['accuracy'] = results[color].pop('all')

    return results