import io
import chess.pgn

def get_user_color(game_data):
    """
    Determines if the 'username' in the data object played White or Black.
    Uses the python-chess library for robust PGN parsing.
    
    :param game_data: Dictionary containing 'username' and 'pgn'
    :return: 'white', 'black', or None
    """
    target_user = game_data.get('username')
    pgn_str = game_data.get('pgn', '')

    if not target_user or not pgn_str:
        return None

    try:
        # Fast parse of headers only
        pgn_io = io.StringIO(pgn_str)
        headers = chess.pgn.read_headers(pgn_io)
        
        if not headers:
            return None

        white_player = headers.get("White", "")
        black_player = headers.get("Black", "")

        # Normalize strings for comparison
        target_user = target_user.strip().lower()
        
        if white_player.strip().lower() == target_user:
            return 'white'
        elif black_player.strip().lower() == target_user:
            return 'black'
            
    except Exception as e:
        print(f"Error parsing PGN headers: {e}")
        return None
    
    return None