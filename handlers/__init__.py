"""
Handlers package for MemoryQudrat Personal OS.
"""

from handlers.main_menu import (
    start_command,
    review_command,
    folders_command,
    stats_command,
    backup_command,
    button_router,
)
from handlers.capture_handler import (
    handle_photo_capture,
    handle_text_and_url_capture,
    handle_document_capture,
)

__all__ = [
    "start_command",
    "review_command",
    "folders_command",
    "stats_command",
    "backup_command",
    "button_router",
    "handle_photo_capture",
    "handle_text_and_url_capture",
    "handle_document_capture",
]
