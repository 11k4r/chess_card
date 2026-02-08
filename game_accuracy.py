import io
import math
import statistics
import re
import chess.pgn

def calculate_game_phase_accuracy(game_data):
    """
    Calculates Lichess-style accuracy for White and Black, 
    broken down by Game Phase (Opening, Middlegame, Endgame).
    
    Adapted for the specific data format where 'analysis' contains 'played_eval'.
    """
    
    # --- 1. Constants & Configuration ---
    
    # Lichess Accuracy Model
    ACC_A = 103.1668100711649
    ACC_B = -3.166924740191411
    ACC_K = 0.04354415386753951
    WIN_GAMMA = 0.00368208
    
    # Phase Definition
    OPENING_MOVE_LIMIT = 15
    ENDGAME_MATERIAL_THRESHOLD = 20 
    
    PIECE_VALUES = {
        chess.QUEEN: 9, 
        chess.ROOK: 5, 
        chess.BISHOP: 3, 
        chess.KNIGHT: 3, 
        chess.PAWN: 0, 
        chess.KING: 0
    }

    # --- 2. Helper Math Functions ---

    def to_win_percent(cp):
        """Converts CP (White perspective) to Win% (0-100)."""
        # Clamp massive values (mates)
        cp = max(-10000, min(10000, cp))
        return 100 / (1 + math.exp(-WIN_GAMMA * cp))

    def calculate_move_accuracy(wp_before, wp_after):
        """Calculates accuracy 0-100 based on Win% loss."""
        if wp_after >= wp_before:
            return 100.0
        diff = wp_before - wp_after
        raw = ACC_A * math.exp(-ACC_K * diff) + ACC_B
        return max(0.0, min(100.0, raw + 1.0)) 

    def aggregate_score(accs, weights):
        """Lichess Aggregation: Mean of (Weighted Mean) and (Harmonic Mean)."""
        if not accs: return None 
        
        # Weighted Mean
        total_weight = sum(weights)
        if total_weight == 0: return 0.0
        w_mean = sum(a * w for a, w in zip(accs, weights)) / total_weight
        
        # Harmonic Mean (handles 0s)
        try:
            h_mean = statistics.harmonic_mean(accs)
        except statistics.StatisticsError:
            h_mean = 0.0
            
        return (w_mean + h_mean) / 2

    def get_material_score(board):
        """Calculates total material value on board (excluding pawns/kings)."""
        score = 0
        for piece_type in [chess.QUEEN, chess.ROOK, chess.BISHOP, chess.KNIGHT]:
            score += len(board.pieces(piece_type, chess.WHITE)) * PIECE_VALUES[piece_type]
            score += len(board.pieces(piece_type, chess.BLACK)) * PIECE_VALUES[piece_type]
        return score

    # --- 3. Data Preparation ---
    
    raw_analysis = game_data.get('analysis', [])
    
    # 3.1 Build Win Percentages
    # Start with initial pos (CP 20)
    cps = [20] 
    win_percents = [to_win_percent(20)] 
    
    # Iterate through the analysis list. 
    # Based on your data, this list is sequential (Ply 1, Ply 2...)
    for item in raw_analysis:
        # UPDATED: Use 'played_eval' instead of 'played_move_eval'
        val = item.get('played_eval')
        
        # Handle cases where eval might be missing
        if val is None: 
            val = cps[-1] if cps else 20
            
        # If val is a string (e.g. mate), convert to max CP
        if isinstance(val, str):
            # Check for mate patterns
            if 'M' in val or '#' in val:
                val = 10000 if not val.startswith('-') else -10000
            else:
                try:
                    val = int(val)
                except:
                    val = 0 
            
        cps.append(val)
        win_percents.append(to_win_percent(val))

    # 3.2 Calculate Accuracies & Volatility Weights
    move_accuracies = [] # Index 0 = 1. e4 (White)
    
    # Calculate Weights (Volatility)
    n_moves = len(win_percents) - 1
    if n_moves < 1: return None # Not enough data
    
    window_size = max(2, min(8, n_moves // 10))
    weights = []
    
    # Sliding window for StdDev
    all_windows = []
    raw_windows = []
    
    # Create windows
    if len(win_percents) <= window_size:
        raw_windows = [win_percents]
    else:
        for j in range(len(win_percents) - window_size + 1):
            raw_windows.append(win_percents[j : j + window_size])
            
    # Pad start
    pad_cnt = min(len(win_percents), window_size) - 2
    if pad_cnt > 0 and raw_windows:
        all_windows.extend([raw_windows[0]] * pad_cnt)
    all_windows.extend(raw_windows)
    
    # Loop through moves to calculate Accuracy and assign Weight
    for i in range(len(win_percents) - 1):
        wp_prev = win_percents[i]
        wp_next = win_percents[i+1]
        
        # i=0 -> Ply 1 (White). i=1 -> Ply 2 (Black).
        is_white = (i % 2 == 0)
        
        # Accuracy
        if is_white:
            acc = calculate_move_accuracy(wp_prev, wp_next)
        else:
            acc = calculate_move_accuracy(100 - wp_prev, 100 - wp_next)
            
        move_accuracies.append(acc)
        
        # Weight
        if i < len(all_windows):
            w_data = all_windows[i]
            std = statistics.stdev(w_data) if len(w_data) > 1 else 0.0
            weights.append(max(0.5, min(12.0, std)))
        else:
            weights.append(0.5)

    # --- 4. Phase Categorization (via PGN) ---
    
    buckets = {
        'white': {'opening': [], 'middlegame': [], 'endgame': [], 'all': []},
        'black': {'opening': [], 'middlegame': [], 'endgame': [], 'all': []}
    }
    
    # Parse PGN
    pgn_str = game_data.get('pgn', '')
    pgn_io = io.StringIO(pgn_str)
    game = chess.pgn.read_game(pgn_io)
    
    if not game:
        return {'error': 'Invalid PGN'}

    board = game.board()
    
    ply_index = 0
    for move in game.mainline_moves():
        if ply_index >= len(move_accuracies): break
        
        # Data for this move
        acc = move_accuracies[ply_index]
        w = weights[ply_index]
        color_key = 'white' if board.turn == chess.WHITE else 'black'
        move_num = board.fullmove_number
        
        # 1. Determine Phase
        mat_score = get_material_score(board)
        
        phase = 'middlegame' # default
        
        if mat_score <= ENDGAME_MATERIAL_THRESHOLD:
            phase = 'endgame'
        elif move_num <= OPENING_MOVE_LIMIT:
            phase = 'opening'
            
        # 2. Store Data
        buckets[color_key][phase].append((acc, w))
        buckets[color_key]['all'].append((acc, w))
        
        # 3. Advance Board
        board.push(move)
        ply_index += 1

    # --- 5. Final Calculation ---
    
    results = {'white': {}, 'black': {}}
    
    for color in ['white', 'black']:
        for cat in ['opening', 'middlegame', 'endgame', 'all']:
            data = buckets[color][cat]
            if not data:
                results[color][cat] = None
            else:
                accs = [d[0] for d in data]
                ws = [d[1] for d in data]
                score = aggregate_score(accs, ws)
                results[color][cat] = round(score, 2)
                
    # Renaming 'all' to 'accuracy'
    results['white']['accuracy'] = results['white'].pop('all')
    results['black']['accuracy'] = results['black'].pop('all')

    return results