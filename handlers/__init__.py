"""
Handlers package for MemoryQudrat V3 Platform.
"""

from handlers.main_menu import (
    start_command,
    today_command,
    subjects_command,
    backup_command,
    button_router,
)
from handlers.schedule_wizard import (
    handle_wizard_callback,
    handle_wizard_text_input,
)
from handlers.today_handler import (
    show_today_tasks,
    handle_today_callback,
)
from handlers.subjects_handler import (
    show_subjects_menu,
    handle_subjects_callback,
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
    "today_command",
    "subjects_command",
    "backup_command",
    "button_router",
    "handle_wizard_callback",
    "handle_wizard_text_input",
    "show_today_tasks",
    "handle_today_callback",
    "show_subjects_menu",
    "handle_subjects_callback",
    "subscription_command",
    "handle_subscription_callback",
    "precheckout_callback",
    "successful_payment_callback",
    "admin_command",
    "gift_sub_command",
    "broadcast_command",
    "handle_admin_callback",
]
