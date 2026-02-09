import re
import json

def parse_stockfish_trace(trace_str):
    """
    Parses Stockfish 11 evaluation trace string into a JSON object.
    
    Args:
        trace_str (str): The raw output string from the engine.
        
    Returns:
        str: A JSON string containing the parsed evaluation metrics.
    """
    
    # Initialize the structure
    result = {
        "white": {},
        "black": {},
        "total": {},
        "final_evaluation": None
    }
    
    # Regex to match table rows:
    # 1. Term Name (e.g., "Pawns")
    # 2. White MG/EG
    # 3. Black MG/EG
    # 4. Total MG/EG
    # It handles both numbers (0.54) and placeholders (----)
    row_pattern = re.compile(
        r"^\s+(?P<term>[A-Za-z\s]+)\s+\|\s+"
        r"(?P<w_mg>[-\d\.]+)\s+(?P<w_eg>[-\d\.]+)\s+\|\s+"
        r"(?P<b_mg>[-\d\.]+)\s+(?P<b_eg>[-\d\.]+)\s+\|\s+"
        r"(?P<t_mg>[-\d\.]+)\s+(?P<t_eg>[-\d\.]+)"
    )
    
    # Regex for the final evaluation line
    final_eval_pattern = re.compile(r"Final evaluation: ([-\d\.]+)")

    lines = trace_str.split('\n')

    for line in lines:
        # 1. Check for Final Evaluation
        final_match = final_eval_pattern.search(line)
        if final_match:
            result["final_evaluation"] = float(final_match.group(1))
            continue

        # 2. Check for Table Rows
        match = row_pattern.search(line)
        if match:
            term_key = match.group("term").strip().lower().replace(" ", "_")
            
            # Helper to convert string to float, returning None for "----"
            def to_float(val):
                return float(val) if val.replace('.', '', 1).replace('-', '', 1).isdigit() else None

            # Extract values
            w_mg = to_float(match.group("w_mg"))
            w_eg = to_float(match.group("w_eg"))
            b_mg = to_float(match.group("b_mg"))
            b_eg = to_float(match.group("b_eg"))
            t_mg = to_float(match.group("t_mg"))
            t_eg = to_float(match.group("t_eg"))

            # only add to dictionary if values exist (not None)
            if w_mg is not None or w_eg is not None:
                result["white"][term_key] = {"mg": w_mg, "eg": w_eg}
            
            if b_mg is not None or b_eg is not None:
                result["black"][term_key] = {"mg": b_mg, "eg": b_eg}

            if t_mg is not None or t_eg is not None:
                result["total"][term_key] = {"mg": t_mg, "eg": t_eg}

    return result