import os
import sys
import unittest
import tempfile
import shutil

# Ensure base dir is in python path
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import config
import spaced_repetition as sr


class TestPersonalMemoryQudrat(unittest.TestCase):
    def setUp(self):
        # Create a dedicated temp database for testing
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_memory.db")
        config.DB_PATH = self.test_db

        import database as db
        self.db = db
        self.db.init_db()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_authorization(self):
        """Verify only the designated admin ID is authorized."""
        self.assertTrue(config.is_authorized(config.ADMIN_USER_ID))
        self.assertFalse(config.is_authorized(999999999))
        self.assertFalse(config.is_authorized(None))

    def test_zero_default_folders(self):
        """Verify the system starts clean with 0 default folders (no hardcoded pre-made folders)."""
        folders = self.db.get_folders()
        self.assertEqual(len(folders), 0)
        self.assertIsNone(self.db.get_active_folder_id())

    def test_nested_subfolders_and_breadcrumbs(self):
        """Verify creating nested folders inside folders, breadcrumb paths, and descendants."""
        root_fid = self.db.create_folder("قدرات كمي", icon="📐")
        self.assertIsNotNone(root_fid)
        self.assertEqual(self.db.get_active_folder_id(), root_fid)

        sub_fid = self.db.create_folder("هندسة", icon="🔺", parent_id=root_fid)
        sub_sub_fid = self.db.create_folder("دوائر ومثلثات", icon="⭕", parent_id=sub_fid)

        # Verify parent-child relationships
        subfolders = self.db.get_subfolders(root_fid)
        self.assertEqual(len(subfolders), 1)
        self.assertEqual(subfolders[0]["id"], sub_fid)

        sub_subfolders = self.db.get_subfolders(sub_fid)
        self.assertEqual(len(sub_subfolders), 1)
        self.assertEqual(sub_subfolders[0]["id"], sub_sub_fid)

        # Verify breadcrumb path string
        path_str = self.db.get_folder_path_string(sub_sub_fid)
        self.assertIn("قدرات كمي", path_str)
        self.assertIn("هندسة", path_str)
        self.assertIn("دوائر ومثلثات", path_str)
        self.assertEqual(path_str, "📐 قدرات كمي ❯ 🔺 هندسة ❯ ⭕ دوائر ومثلثات")

        # Verify all descendants
        descendants = self.db.get_all_descendant_folder_ids(root_fid)
        self.assertIn(root_fid, descendants)
        self.assertIn(sub_fid, descendants)
        self.assertIn(sub_sub_fid, descendants)
        self.assertEqual(len(descendants), 3)

    def test_folder_crud(self):
        """Verify creating, setting active, renaming, and deleting folders."""
        new_fid = self.db.create_folder("تجميعات النموذج 105", icon="🎯")
        self.assertIsNotNone(new_fid)

        # Set active folder
        self.db.set_active_folder_id(new_fid)
        self.assertEqual(self.db.get_active_folder_id(), new_fid)

        # Rename folder
        self.db.rename_folder(new_fid, "تجميعات 105 المحدثة")
        f = self.db.get_folder(new_fid)
        self.assertEqual(f["name"], "تجميعات 105 المحدثة")

        # Delete folder
        self.assertTrue(self.db.delete_folder(new_fid))
        self.assertIsNone(self.db.get_folder(new_fid))

    def test_item_ingestion_multi_modal(self):
        """Verify adding all 4 types of media items (photo, text, link, pdf)."""
        fid = self.db.create_folder("مجلد الاختبار")

        # 1. Photo in QA mode (reveal solution)
        p_id = self.db.add_item(
            item_type="photo",
            content="data/photos/test1.jpg",
            title="مسألة سرعة",
            solution="الحل 48 كم/س",
            folder_id=fid,
            review_mode="qa",
        )
        item1 = self.db.get_item(p_id)
        self.assertEqual(item1["item_type"], "photo")
        self.assertEqual(item1["review_mode"], "qa")
        self.assertEqual(item1["stage"], 0)

        # 2. Photo in Direct review mode (no reveal button needed)
        d_id = self.db.add_item(
            item_type="photo",
            content="data/photos/rules.jpg",
            title="جدول قوانين المثلثات",
            folder_id=fid,
            review_mode="direct",
        )
        item2 = self.db.get_item(d_id)
        self.assertEqual(item2["review_mode"], "direct")

        # 3. Forms Link
        l_id = self.db.add_item(
            item_type="link",
            content="https://docs.google.com/forms/d/e/1FAIpQLSc/viewform",
            title="كويز تجميع 1446",
            folder_id=fid,
        )
        item3 = self.db.get_item(l_id)
        self.assertEqual(item3["item_type"], "link")

        # 4. PDF Document
        doc_id = self.db.add_item(
            item_type="pdf",
            content="data/documents/summary.pdf",
            title="مذكرة التناظر اللفظي",
            folder_id=fid,
        )
        item4 = self.db.get_item(doc_id)
        self.assertEqual(item4["item_type"], "pdf")

    def test_adaptive_spaced_repetition_logic(self):
        """Verify mathematical calculation of stages, ease factors, and intervals."""
        # 1. Easy rating: jump +2 stages
        easy_res = sr.calculate_next_schedule(current_stage=0, rating="easy", ease_factor=2.5)
        self.assertEqual(easy_res["new_stage"], 2)
        self.assertGreater(easy_res["ease_factor"], 2.5)

        # 2. Good rating: advance +1 stage
        good_res = sr.calculate_next_schedule(current_stage=1, rating="good", ease_factor=2.5)
        self.assertEqual(good_res["new_stage"], 2)

        # 3. Hard rating: reset to stage 0 and flag lapse
        hard_res = sr.calculate_next_schedule(current_stage=3, rating="hard", ease_factor=2.5)
        self.assertEqual(hard_res["new_stage"], 0)
        self.assertTrue(hard_res["lapsed"])
        self.assertLess(hard_res["ease_factor"], 2.5)

        # 4. Reaching mastery
        master_res = sr.calculate_next_schedule(current_stage=4, rating="good", ease_factor=2.5)
        self.assertEqual(master_res["new_stage"], sr.MAX_STAGE)
        self.assertEqual(master_res["is_mastered"], 1)

    def test_record_item_review_and_stats(self):
        """Verify recording review updates database item, logs, and daily stats."""
        fid = self.db.create_folder("مجلد عام")
        item_id = self.db.add_item(
            item_type="photo",
            content="data/photos/q1.jpg",
            title="مسألة هندسة",
            folder_id=fid,
            review_mode="qa",
        )

        # Record 'good' review
        res = self.db.record_item_review(item_id, rating="good")
        self.assertEqual(res["new_stage"], 1)

        updated_item = self.db.get_item(item_id)
        self.assertEqual(updated_item["stage"], 1)

        # Verify daily stats updated
        stats = self.db.get_dashboard_stats()
        self.assertEqual(stats["total_items"], 1)
        self.assertGreaterEqual(len(stats["recent_daily"]), 1)
        self.assertEqual(stats["recent_daily"][0]["good_count"], 1)

    def test_due_items_subfolder_recursive_filtering(self):
        """Verify filtering due items across parent folders and subfolders."""
        root_f = self.db.create_folder("كمي رئيسي")
        child_f = self.db.create_folder("هندسة فرعي", parent_id=root_f)
        other_f = self.db.create_folder("لفظي")

        # Add item in root, child, and other
        id1 = self.db.add_item("text", "سؤال كمي عام", folder_id=root_f)
        id2 = self.db.add_item("text", "سؤال هندسة فرعي", folder_id=child_f)
        id3 = self.db.add_item("text", "سؤال لفظي", folder_id=other_f)

        # Force next_review_date to today so they appear due
        today = sr.get_riyadh_today_iso()
        with self.db.get_db_connection() as conn:
            conn.execute("UPDATE items SET next_review_date = ? WHERE id IN (?, ?, ?)", (today, id1, id2, id3))

        # Check due in all
        due_all = self.db.get_due_items()
        self.assertEqual(len(due_all), 3)

        # Check due in root_f WITH subfolders (should return id1 and id2)
        due_root_with_subs = self.db.get_due_items(folder_id=root_f, include_subfolders=True)
        self.assertEqual(len(due_root_with_subs), 2)
        item_ids = [it["id"] for it in due_root_with_subs]
        self.assertIn(id1, item_ids)
        self.assertIn(id2, item_ids)

        # Check due in child_f only
        due_child = self.db.get_due_items(folder_id=child_f)
        self.assertEqual(len(due_child), 1)
        self.assertEqual(due_child[0]["id"], id2)

    def test_move_item_between_folders(self):
        """Verify updating an item's folder."""
        f1 = self.db.create_folder("قديم")
        f2 = self.db.create_folder("جديد")
        item_id = self.db.add_item("photo", "path/to/img.jpg", folder_id=f1)

        self.db.update_item_folder(item_id, f2)
        updated = self.db.get_item(item_id)
        self.assertEqual(updated["folder_id"], f2)

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

        # Verify 101 and 102 are now deleted from DB
        remaining = self.db.get_and_clear_chat_messages(chat_id)
        self.assertEqual(remaining, [103])


if __name__ == "__main__":
    unittest.main()
