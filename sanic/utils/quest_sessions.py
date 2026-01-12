from typing import Optional

import services.postgres as postgres_client


def get_quest_id_for_area(area_id: int) -> Optional[int]:
    """Lookup quest_id from area_id. Returns None if area is not associated with a quest."""
    return postgres_client.get_quest_id_for_area(area_id)


def is_quest_area(area_id: int) -> bool:
    """Check if area is associated with a quest."""
    return get_quest_id_for_area(area_id) is not None


def process_location_activity(
    character_id: int,
    area_id: Optional[int],
    timestamp,
    current_session: Optional[dict],
) -> tuple[Optional[dict], Optional[dict]]:
    """
    Process a location activity to determine if a quest session should be opened or closed.
    
    Args:
        character_id: The character ID
        area_id: The new area ID (from location activity)
        timestamp: The timestamp of the location change
        current_session: Current active session dict with keys: id, quest_id, entry_timestamp, or None
    
    Returns:
        Tuple of (session_to_close, session_to_open):
        - session_to_close: Dict with session_id if current session should be closed, None otherwise
        - session_to_open: Dict with quest_id, entry_timestamp if new session should be opened, None otherwise
    """
    session_to_close = None
    session_to_open = None

    # If character has an active quest session, close it (they're leaving that area)
    if current_session is not None:
        session_to_close = {
            "session_id": current_session["id"],
            "exit_timestamp": timestamp,
        }

    # Check if new area is a quest area
    if area_id is not None:
        quest_id = get_quest_id_for_area(area_id)
        if quest_id is not None:
            # Character is entering a quest area - open new session
            session_to_open = {
                "character_id": character_id,
                "quest_id": quest_id,
                "entry_timestamp": timestamp,
            }

    return session_to_close, session_to_open
