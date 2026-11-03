import os
import sys
import unittest
import tempfile
import shutil
from datetime import timedelta

# Ensure base dir is in python path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import config
import spaced_repetition as sr
from handlers.subscription_handler import can_user_capture
from utils import resolve_media_path


class TestCommercialSaaSMemoryQudrat(unittest.TestCase):
    def setUp(self):
        # Create a dedicated temp database for testing
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_commercial.db")
        config.DB_PATH = self.test_db

        import database as db
        self.db = db
        self.db.init_db()

        self.admin_id = config.ADMIN_USER_ID
        self.student_1 = 11111111
        self.student_2 = 22222222

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_authorization_and_admin_roles(self):
        """Verify role segregation: public access for users, restricted admin control."""
        self.assertTrue(config.is_authorized(self.admin_id))
        self.assertTrue(config.is_authorized(self.student_1))
        self.assertFalse(config.is_authorized(None))
        self.assertFalse(config.is_authorized(0))

        self.assertTrue(config.is_admin(self.admin_id))
        self.assertFalse(config.is_admin(self.student_1))
        self.assertFalse(config.is_admin(999999999))

    def test_student_registration_and_freemium_trial(self):
        """Verify new students automatically receive a 3-day full free trial."""
        user = self.db.get_or_create_user(self.student_1, username="student_saad", full_name="سعد الشهري")
        self.assertEqual(user["id"], self.student_1)
        self.assertEqual(user["status"], "trial")
        self.assertEqual(user["is_admin"], 0)

        # Check subscription status
        sub = self.db.check_user_subscription(self.student_1)
        self.assertTrue(sub["is_active"])
        self.assertTrue(sub["is_trial"])
        self.assertEqual(sub["days_left"], config.TRIAL_DAYS)

        # Super admin must be lifetime VIP
        admin_user = self.db.get_or_create_user(self.admin_id, username="admin")
        self.assertEqual(admin_user["status"], "lifetime")
        self.assertEqual(admin_user["is_admin"], 1)
        admin_sub = self.db.check_user_subscription(self.admin_id)
        self.assertTrue(admin_sub["is_active"])
        self.assertTrue(admin_sub["is_admin"])

    def test_telegram_stars_payment_and_extension(self):
        """Verify extending subscription upon successful Stars payment."""
        self.db.get_or_create_user(self.student_1)

        # Student purchases 30 days
        ext = self.db.extend_user_subscription(
            user_id=self.student_1,
            days=30,
            stars_paid=config.STARS_MONTHLY_PRICE,
            charge_id="test_charge_stars_001",
            payload=f"sub_month_{self.student_1}",
        )
        self.assertEqual(ext["days_added"], 30)

        sub = self.db.check_user_subscription(self.student_1)
        self.assertTrue(sub["is_active"])
        self.assertFalse(sub["is_trial"])
        self.assertGreaterEqual(sub["days_left"], 30)

        # Verify payment recorded in admin metrics
        stats = self.db.get_admin_platform_stats()
        self.assertEqual(stats["total_stars"], config.STARS_MONTHLY_PRICE)
        self.assertEqual(stats["active_subscribers"], 2)  # admin + student_1

    def test_payment_replay_protection(self):
        """Verify duplicate charge_id replay attack is detected and ignored."""
        self.db.get_or_create_user(self.student_1)

        # First legitimate payment
        ext1 = self.db.extend_user_subscription(
            user_id=self.student_1,
            days=30,
            stars_paid=70,
            charge_id="unique_charge_12345",
            payload="sub_month_11111111",
        )
        self.assertEqual(ext1["days_added"], 30)

        # Replay attempt with same charge_id
        ext2 = self.db.extend_user_subscription(
            user_id=self.student_1,
            days=30,
            stars_paid=70,
            charge_id="unique_charge_12345",
            payload="sub_month_11111111",
        )
        self.assertEqual(ext2["days_added"], 0)
        self.assertTrue(ext2.get("already_processed"))

    def test_paywall_guard_and_graceful_expiry(self):
        """Verify paywall blocks new item capture when expired, but allows review."""
        self.db.get_or_create_user(self.student_1)

        # Manually expire subscription in DB
        yesterday = (sr.get_riyadh_now() - timedelta(days=2)).strftime("%Y-%m-%d %H:%M:%S")
        with self.db.get_db_connection() as conn:
            conn.execute("UPDATE users SET status = 'expired', subscription_expires_at = ? WHERE id = ?", (yesterday, self.student_1))

        # Check paywall guard
        can_capture, err_msg = can_user_capture(self.student_1)
        self.assertFalse(can_capture)
        self.assertIn("انتهت فترة اشتراكك الحالية", err_msg)

        # But student should still be able to read existing items and review
        fid = self.db.create_folder(self.student_1, "مجلدي القديم")
        p_id = self.db.add_item(self.student_1, "text", "سؤال سابق", folder_id=fid)
        item = self.db.get_item(p_id, self.student_1)
        self.assertIsNotNone(item)
        self.assertEqual(item["content"], "سؤال سابق")

    def test_multi_tenant_data_isolation(self):
        """Verify complete isolation: student 1 cannot see or manipulate student 2's folders/items."""
        self.db.get_or_create_user(self.student_1)
        self.db.get_or_create_user(self.student_2)

        # Student 1 creates a folder and an item
        f1 = self.db.create_folder(self.student_1, "تجميعات كمي طالب 1")
        item1 = self.db.add_item(self.student_1, "text", "مسألة طالب 1", folder_id=f1)

        # Student 2 creates a folder and an item
        f2 = self.db.create_folder(self.student_2, "تجميعات لفظي طالب 2")
        item2 = self.db.add_item(self.student_2, "text", "مسألة طالب 2", folder_id=f2)

        # Student 1 sees only their folder
        s1_folders = self.db.get_folders(self.student_1)
        self.assertEqual(len(s1_folders), 1)
        self.assertEqual(s1_folders[0]["id"], f1)

        # Student 2 sees only their folder
        s2_folders = self.db.get_folders(self.student_2)
        self.assertEqual(len(s2_folders), 1)
        self.assertEqual(s2_folders[0]["id"], f2)

        # Cross-access attempts must fail safely
        self.assertIsNone(self.db.get_folder(f2, self.student_1))
        self.assertIsNone(self.db.get_folder(f1, self.student_2))

        self.assertFalse(self.db.delete_folder(f2, self.student_1))
        self.assertFalse(self.db.rename_folder(f2, self.student_1, "هاك"))

        self.assertIsNone(self.db.get_item(item2, self.student_1))
        self.assertIsNone(self.db.get_item(item1, self.student_2))

        self.assertFalse(self.db.delete_item(item2, self.student_1))
        self.assertFalse(self.db.update_item_folder(item2, self.student_1, f1))

        # Cross review attempt must be rejected
        cross_rev = self.db.record_item_review(item2, self.student_1, "good")
        self.assertEqual(cross_rev, {})

        # Student 1 items in folder
        s1_items = self.db.get_items_by_folder(f1, self.student_1)
        self.assertEqual(len(s1_items), 1)
        self.assertEqual(s1_items[0]["id"], item1)

        # Student 2 querying Student 1's folder gets 0 items
        s2_query_s1 = self.db.get_items_by_folder(f1, self.student_2)
        self.assertEqual(len(s2_query_s1), 0)

    def test_sql_injection_defense_in_reviews(self):
        """Verify arbitrary malicious rating strings cannot execute SQL injection."""
        self.db.get_or_create_user(self.student_1)
        fid = self.db.create_folder(self.student_1, "مجلد أمان")
        item_id = self.db.add_item(self.student_1, "text", "مسألة فحص أمان", folder_id=fid)

        # Attempt SQL injection through rating parameter
        malicious_ratings = [
            "easy; DROP TABLE users; --",
            "' OR 1=1 --",
            "good_count = good_count + 100",
            "unknown_mode",
            "",
            None,
        ]
        for bad_rate in malicious_ratings:
            res = self.db.record_item_review(item_id, self.student_1, str(bad_rate))
            self.assertEqual(res, {})

        # Ensure users table is intact
        users_count = self.db.get_admin_platform_stats()["total_users"]
        self.assertGreaterEqual(users_count, 1)

    def test_nested_subfolders_and_breadcrumbs(self):
        """Verify creating nested folders inside folders, breadcrumb paths, and descendants."""
        root_fid = self.db.create_folder(self.student_1, "قدرات كمي", icon="📐")
        self.assertIsNotNone(root_fid)
        self.assertEqual(self.db.get_active_folder_id(self.student_1), root_fid)

        sub_fid = self.db.create_folder(self.student_1, "هندسة", icon="🔺", parent_id=root_fid)
        sub_sub_fid = self.db.create_folder(self.student_1, "دوائر ومثلثات", icon="⭕", parent_id=sub_fid)

        # Verify parent-child relationships
        subfolders = self.db.get_subfolders(self.student_1, root_fid)
        self.assertEqual(len(subfolders), 1)
        self.assertEqual(subfolders[0]["id"], sub_fid)

        sub_subfolders = self.db.get_subfolders(self.student_1, sub_fid)
        self.assertEqual(len(sub_subfolders), 1)
        self.assertEqual(sub_subfolders[0]["id"], sub_sub_fid)

        # Verify breadcrumb path string
        path_str = self.db.get_folder_path_string(sub_sub_fid, self.student_1)
        self.assertIn("قدرات كمي", path_str)
        self.assertIn("هندسة", path_str)
        self.assertIn("دوائر ومثلثات", path_str)
        self.assertEqual(path_str, "📐 قدرات كمي ❯ 🔺 هندسة ❯ ⭕ دوائر ومثلثات")

        # Verify all descendants
        descendants = self.db.get_all_descendant_folder_ids(root_fid, self.student_1)
        self.assertIn(root_fid, descendants)
        self.assertIn(sub_fid, descendants)
        self.assertIn(sub_sub_fid, descendants)
        self.assertEqual(len(descendants), 3)

    def test_item_ingestion_multi_modal(self):
        """Verify adding all 4 types of media items (photo, text, link, pdf) with user isolation."""
        fid = self.db.create_folder(self.student_1, "مجلد الاختبار")

        # 1. Photo in QA mode (reveal solution)
        p_id = self.db.add_item(
            self.student_1,
            item_type="photo",
            content=f"data/photos/{self.student_1}/test1.jpg",
            title="مسألة سرعة",
            solution="الحل 48 كم/س",
            folder_id=fid,
            review_mode="qa",
        )
        item1 = self.db.get_item(p_id, self.student_1)
        self.assertEqual(item1["user_id"], self.student_1)
        self.assertEqual(item1["item_type"], "photo")
        self.assertEqual(item1["review_mode"], "qa")
        self.assertEqual(item1["stage"], 0)

        # 2. Photo in Direct review mode
        d_id = self.db.add_item(
            self.student_1,
            item_type="photo",
            content=f"data/photos/{self.student_1}/rules.jpg",
            title="جدول قوانين المثلثات",
            folder_id=fid,
            review_mode="direct",
        )
        item2 = self.db.get_item(d_id, self.student_1)
        self.assertEqual(item2["review_mode"], "direct")

        # 3. Forms Link
        l_id = self.db.add_item(
            self.student_1,
            item_type="link",
            content="https://docs.google.com/forms/d/e/1FAIpQLSc/viewform",
            title="كويز تجميع 1446",
            folder_id=fid,
        )
        item3 = self.db.get_item(l_id, self.student_1)
        self.assertEqual(item3["item_type"], "link")

        # 4. PDF Document
        doc_id = self.db.add_item(
            self.student_1,
            item_type="pdf",
            content=f"data/documents/{self.student_1}/summary.pdf",
            title="مذكرة التناظر اللفظي",
            folder_id=fid,
        )
        item4 = self.db.get_item(doc_id, self.student_1)
        self.assertEqual(item4["item_type"], "pdf")

    def test_record_item_review_and_stats(self):
        """Verify recording review updates database item, logs, and daily stats for user."""
        fid = self.db.create_folder(self.student_1, "مجلد عام")
        item_id = self.db.add_item(
            self.student_1,
            item_type="photo",
            content=f"data/photos/{self.student_1}/q1.jpg",
            title="مسألة هندسة",
            folder_id=fid,
            review_mode="qa",
        )

        # Record 'good' review
        res = self.db.record_item_review(item_id, rating="good", user_id=self.student_1)
        self.assertEqual(res["new_stage"], 1)

        updated_item = self.db.get_item(item_id, self.student_1)
        self.assertEqual(updated_item["stage"], 1)

        # Verify daily stats updated for student 1
        stats = self.db.get_dashboard_stats(self.student_1)
        self.assertEqual(stats["total_items"], 1)
        self.assertGreaterEqual(len(stats["recent_daily"]), 1)
        self.assertEqual(stats["recent_daily"][0]["good_count"], 1)

        # Student 2 stats must remain 0
        s2_stats = self.db.get_dashboard_stats(self.student_2)
        self.assertEqual(s2_stats["total_items"], 0)

    def test_due_items_subfolder_recursive_filtering(self):
        """Verify filtering due items across parent folders and subfolders per user."""
        root_f = self.db.create_folder(self.student_1, "كمي رئيسي")
        child_f = self.db.create_folder(self.student_1, "هندسة فرعي", parent_id=root_f)
        other_f = self.db.create_folder(self.student_1, "لفظي")

        # Add items
        id1 = self.db.add_item(self.student_1, "text", "سؤال كمي عام", folder_id=root_f)
        id2 = self.db.add_item(self.student_1, "text", "سؤال هندسة فرعي", folder_id=child_f)
        id3 = self.db.add_item(self.student_1, "text", "سؤال لفظي", folder_id=other_f)

        # Force next_review_date to today
        today = sr.get_riyadh_today_iso()
        with self.db.get_db_connection() as conn:
            conn.execute("UPDATE items SET next_review_date = ? WHERE id IN (?, ?, ?)", (today, id1, id2, id3))

        # Check due in all
        due_all = self.db.get_due_items(self.student_1)
        self.assertEqual(len(due_all), 3)

        # Check due in root_f WITH subfolders
        due_root_with_subs = self.db.get_due_items(self.student_1, folder_id=root_f, include_subfolders=True)
        self.assertEqual(len(due_root_with_subs), 2)
        item_ids = [it["id"] for it in due_root_with_subs]
        self.assertIn(id1, item_ids)
        self.assertIn(id2, item_ids)

        # Check due in child_f only
        due_child = self.db.get_due_items(self.student_1, folder_id=child_f)
        self.assertEqual(len(due_child), 1)
        self.assertEqual(due_child[0]["id"], id2)

    def test_chat_history_tracking(self):
        """Verify chat message ID tracking and clearing."""
        chat_id = 112233
        self.db.track_chat_message(chat_id, 101)
        self.db.track_chat_message(chat_id, 102)
        self.db.track_chat_message(chat_id, 103)

        cleared = self.db.get_and_clear_chat_messages(chat_id, keep_message_id=103)
        self.assertIn(101, cleared)
        self.assertIn(102, cleared)
        self.assertNotIn(103, cleared)

        remaining = self.db.get_and_clear_chat_messages(chat_id)
        self.assertEqual(remaining, [103])

    def test_resolve_media_path(self):
        """Verify robust media path resolution with user context prioritization."""
        self.assertEqual(resolve_media_path(""), "")
        self.assertEqual(resolve_media_path("does_not_exist_file.xyz"), "")

        test_file = os.path.join(self.test_dir, "sample.pdf")
        with open(test_file, "w") as f:
            f.write("dummy pdf")
        resolved = resolve_media_path(test_file, user_id=self.student_1)
        self.assertTrue(os.path.exists(resolved))
        self.assertEqual(resolved, os.path.abspath(test_file))

    def test_daily_stats_multi_user_conflict_and_ratings(self):
        """Verify rating buttons and daily_stats work across multiple users on same date without SQLite conflict error."""
        self.db.get_or_create_user(self.student_1, "user1", "User 1")
        self.db.get_or_create_user(self.student_2, "user2", "User 2")

        id1 = self.db.add_item(self.student_1, "photo", "data/p1.jpg", title="Q1")
        id2 = self.db.add_item(self.student_2, "pdf", "data/doc2.pdf", title="Q2")

        # Student 1 rates 'good'
        sched1 = self.db.record_item_review(id1, self.student_1, "good")
        self.assertTrue(sched1)
        self.assertEqual(sched1["new_stage"], 1)

        # Student 2 rates 'hard' on same date (this used to crash with ON CONFLICT error!)
        sched2 = self.db.record_item_review(id2, self.student_2, "hard")
        self.assertTrue(sched2)
        self.assertEqual(sched2["new_stage"], 0)

        # Student 1 rates again on same date (updates conflict)
        sched1_again = self.db.record_item_review(id1, self.student_1, "easy")
        self.assertTrue(sched1_again)

    def test_difficult_items_bank(self):
        """Verify that questions rated 'hard' or with lapses enter and graduate from the Difficult Bank."""
        self.db.get_or_create_user(self.student_1, "user1", "User 1")

        q_easy = self.db.add_item(self.student_1, "photo", "data/easy.jpg", title="Easy Q")
        q_hard = self.db.add_item(self.student_1, "pdf", "data/hard.pdf", title="Hard Q")

        # Rate easy Q with 'good'
        self.db.record_item_review(q_easy, self.student_1, "good")

        # Rate hard Q with 'hard'
        self.db.record_item_review(q_hard, self.student_1, "hard")

        # Check Difficult Bank
        diff_items = self.db.get_difficult_items(self.student_1)
        self.assertEqual(len(diff_items), 1)
        self.assertEqual(diff_items[0]["id"], q_hard)

        diff_stats = self.db.get_difficult_items_stats(self.student_1)
        self.assertEqual(diff_stats["total_difficult"], 1)

        # Graduate the item: mark mastered
        with self.db.get_db_connection() as conn:
            conn.cursor().execute("UPDATE items SET is_mastered = 1 WHERE id = ?", (q_hard,))

        diff_after = self.db.get_difficult_items(self.student_1)
        self.assertEqual(len(diff_after), 0)


if __name__ == "__main__":
    unittest.main()
