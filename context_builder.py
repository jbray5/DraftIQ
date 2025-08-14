import json
from typing import Dict, Any, List

def summarize_my_roster(my_roster: Dict[str, Any]) -> str:
    slots = [f"{k}:{v['name']}({v['position']})" for k,v in my_roster.items() if v]
    return ", ".join(slots) or "Empty"

def summarize_board(board: Dict[str, Dict[str, Any]]) -> List[str]:
    # Turn full board into compact lines: Team -> key picks
    lines = []
    for team, slots in board.items():
        picks = [v["name"] for v in slots.values() if v]
        if picks:
            lines.append(f"{team}: {', '.join(picks[:8])}")
    return lines[:12]  # keep prompt tight

def build_prompt(player: Dict[str, Any], my_team_name: str, my_roster: Dict[str,Any],
                 tendencies: Dict[str,Any], meta: Dict[str,Any]) -> Dict[str,Any]:
    # This returns a dict used to craft system/user messages
    return {
        "league": tendencies,
        "meta": meta,
        "me": {
            "team": my_team_name,
            "roster_summary": summarize_my_roster(my_roster),
        },
        "candidate": player
    }
