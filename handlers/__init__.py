"""
Handlers package for MemoryQudrat SaaS Platform.
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
from handlers.subscription_handler import (
    subscription_command,
    handle_subscription_callback,
    precheckout_callback,
    successful_payment_callback,
)
from handlers.admin_handler import (
    admin_command,
    gift_sub_command,
    broadcast_command,
    handle_admin_callback,
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
    "subscription_command",
    "handle_subscription_callback",
    "precheckout_callback",
    "successful_payment_callback",
    "admin_command",
    "gift_sub_command",
    "broadcast_command",
    "handle_admin_callback",
]
