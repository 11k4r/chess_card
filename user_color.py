import io
import math
import statistics
import re
import chess.pgn


def get_user_color(game_data):
    """
    Determines if the 'username' in the data object played White or Black.
    Returns 'white', 'black', or None if not found.
    """
    target_user = game_data.get('username')
    pgn = game_data.get('pgn', '')

    # regex to find player names in PGN tags like [White "PlayerName"]
    white_match = re.search(r'\[White "(.*?)"\]', pgn)
    black_match = re.search(r'\[Black "(.*?)"\]', pgn)

    white_player = white_match.group(1) if white_match else None
    black_player = black_match.group(1) if black_match else None

    # Case insensitive comparison is often safer
    if target_user and white_player and target_user.lower() == white_player.lower():
        return 'white'
    elif target_user and black_player and target_user.lower() == black_player.lower():
        return 'black'
    
    return None