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


class TestMemoryQudratV3(unittest.TestCase):
    def setUp(self):
        # Create a dedicated temp database for testing
        self.test_dir = tempfile.mkdtemp()
        self.test_db = os.path.join(self.test_dir, "test_v3.db")
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

    def test_student_registration_and_anti_exploit_trial(self):
        """Verify new students have unstarted trial, which locks upon creating their first schedule."""
        user = self.db.get_or_create_user(self.student_1, username="student_saad", full_name="سعد الشهري")
        self.assertEqual(user["id"], self.student_1)
        self.assertEqual(user["status"], "trial")
        self.assertIsNone(user["trial_started_at"])

        # Check subscription status before creating any schedule
        sub = self.db.check_user_subscription(self.student_1)
        self.assertTrue(sub["is_active"])
        self.assertTrue(sub["is_trial"])
        self.assertEqual(sub["days_left"], 7)
        self.assertTrue(sub.get("not_started"))

        # Create first schedule -> locks trial
        sub_id = self.db.create_subject(self.student_1, "قدرات كمي", total_lessons=10, target_days=5)
        self.assertIsNotNone(sub_id)

        user_after = self.db.get_user(self.student_1)
        self.assertIsNotNone(user_after["trial_started_at"])
        initial_trial_start = user_after["trial_started_at"]

        # Anti-exploit check: calling get_or_create_user again (/start) does NOT wipe or reset trial_started_at
        self.db.get_or_create_user(self.student_1, username="new_name")
        self.db.activate_trial_if_needed(self.student_1)
        user_check = self.db.get_user(self.student_1)
        self.assertEqual(user_check["trial_started_at"], initial_trial_start)

        # Super admin must be lifetime VIP
        admin_user = self.db.get_or_create_user(self.admin_id, username="admin")
        self.assertEqual(admin_user["status"], "lifetime")
        self.assertEqual(admin_user["is_admin"], 1)
        admin_sub = self.db.check_user_subscription(self.admin_id)
        self.assertTrue(admin_sub["is_active"])
        self.assertTrue(admin_sub["is_admin"])

    def test_lesson_distribution_math(self):
        """Verify even mathematical distribution of lessons across days."""
        # 30 lessons across 20 days
        dist_30_20 = sr.distribute_lessons(30, 20)
        self.assertEqual(len(dist_30_20), 30)
        self.assertEqual(dist_30_20[0], 1)
        self.assertEqual(dist_30_20[-1], 20)
        # All days from 1 to 20 should be covered
        self.assertEqual(set(dist_30_20), set(range(1, 21)))

        # 5 lessons across 10 days
        dist_5_10 = sr.distribute_lessons(5, 10)
        self.assertEqual(len(dist_5_10), 5)
        # Each lesson on distinct day
        self.assertEqual(len(set(dist_5_10)), 5)
        self.assertLessEqual(max(dist_5_10), 10)

        # 10 lessons in 1 day
        dist_10_1 = sr.distribute_lessons(10, 1)
        self.assertEqual(dist_10_1, [1] * 10)

    def test_conditional_spaced_repetition(self):
        """
        Core V3 Innovation:
        1. When lessons are scheduled, NO reviews exist yet.
        2. Reviews are scheduled ONLY after the student marks a lesson completed.
        3. Subsequent review stages advance upon each completion.
        """
        sub_id = self.db.create_subject(
            user_id=self.student_1,
            name="رياضيات 101",
            total_lessons=3,
            target_days=3,
            lesson_titles=["الجبر", "الهندسة", "التفاضل"],
        )
        lessons = self.db.get_subject_lessons(sub_id, self.student_1)
        self.assertEqual(len(lessons), 3)

        # Confirm 0 reviews exist right now
        today_tasks = self.db.get_today_tasks(self.student_1)
        self.assertEqual(len(today_tasks["due_reviews"]), 0)
        self.assertEqual(len(today_tasks["new_lessons"]), 1)  # Only lesson 1 is on day 1

        # Student completes lesson 1
        l1 = lessons[0]
        res1 = self.db.complete_lesson(l1["id"], self.student_1)
        self.assertEqual(res1["lesson"]["is_completed"], 1)
        self.assertEqual(res1["next_review_stage"], 1)

        # Lesson 1 now has 1 review scheduled for +3 days
        lessons_after = self.db.get_subject_lessons(sub_id, self.student_1)
        self.assertEqual(lessons_after[0]["review_count"], 1)

        # Fast forward time: simulate review becoming due
        today_iso = sr.get_riyadh_today_iso()
        with self.db.get_db_connection() as conn:
            conn.execute("UPDATE reviews SET due_date = ? WHERE user_id = ?", (today_iso, self.student_1))

        # Check today tasks: review is now due!
        tasks_now = self.db.get_today_tasks(self.student_1)
        self.assertEqual(len(tasks_now["due_reviews"]), 1)
        rev = tasks_now["due_reviews"][0]
        self.assertEqual(rev["lesson_title"], "الجبر")
        self.assertEqual(rev["stage"], 1)

        # Student completes review stage 1 -> advances to stage 2 (+7 days)
        rev_res = self.db.complete_review(rev["id"], self.student_1)
        self.assertEqual(rev_res["completed_stage"], 1)
        self.assertEqual(rev_res["next_stage"], 2)
        self.assertFalse(rev_res["is_mastered"])

    def test_smart_catchup_rescheduling(self):
        """Verify redistributing remaining uncompleted lessons over target days."""
        sub_id = self.db.create_subject(
            user_id=self.student_1,
            name="تجميعات أرامكو",
            total_lessons=10,
            target_days=5,
        )
        lessons = self.db.get_subject_lessons(sub_id, self.student_1)

        # Complete lessons 1 and 2
        self.db.complete_lesson(lessons[0]["id"], self.student_1)
        self.db.complete_lesson(lessons[1]["id"], self.student_1)

        # 8 lessons remaining. Student fell behind and triggers Catch-up for 4 days
        resched = self.db.reschedule_subject(sub_id, self.student_1, target_days=4)
        self.assertEqual(resched["remaining_count"], 8)
        self.assertEqual(resched["new_target_days"], 4)

        # Check lessons: uncompleted lessons must now be distributed between days 1..4
        lessons_after = self.db.get_subject_lessons(sub_id, self.student_1)
        completed = [l for l in lessons_after if l["is_completed"] == 1]
        remaining = [l for l in lessons_after if l["is_completed"] == 0]

        self.assertEqual(len(completed), 2)
        self.assertEqual(len(remaining), 8)
        # All remaining days must be <= 4
        for rem in remaining:
            self.assertGreaterEqual(rem["scheduled_day"], 1)
            self.assertLessEqual(rem["scheduled_day"], 4)

    def test_multi_tenant_data_isolation(self):
        """Verify complete isolation: student 1 cannot see, reschedule, or complete student 2's data."""
        self.db.get_or_create_user(self.student_1)
        self.db.get_or_create_user(self.student_2)

        s1_id = self.db.create_subject(self.student_1, "مادة طالب 1", total_lessons=5, target_days=5)
        s2_id = self.db.create_subject(self.student_2, "مادة طالب 2", total_lessons=8, target_days=4)

        # Student 1 sees only their subject
        subs1 = self.db.get_user_subjects(self.student_1)
        self.assertEqual(len(subs1), 1)
        self.assertEqual(subs1[0]["id"], s1_id)

        # Student 2 sees only their subject
        subs2 = self.db.get_user_subjects(self.student_2)
        self.assertEqual(len(subs2), 1)
        self.assertEqual(subs2[0]["id"], s2_id)

        # Cross-access must fail safely
        self.assertIsNone(self.db.get_subject(s2_id, self.student_1))
        self.assertIsNone(self.db.get_subject(s1_id, self.student_2))

        # Cross-deletion must fail
        self.assertFalse(self.db.delete_subject(s2_id, self.student_1))

        # Cross-reschedule must fail
        self.assertEqual(self.db.reschedule_subject(s2_id, self.student_1), {})

        # Cross-complete lesson must fail
        s2_lessons = self.db.get_subject_lessons(s2_id, self.student_2)
        cross_comp = self.db.complete_lesson(s2_lessons[0]["id"], self.student_1)
        self.assertEqual(cross_comp, {})

    def test_telegram_stars_payment_and_extension(self):
        """Verify extending subscription upon successful Stars payment."""
        self.db.get_or_create_user(self.student_1)

        # Student purchases 30 days
        ext = self.db.extend_user_subscription(
            user_id=self.student_1,
            days=30,
            stars_paid=config.STARS_MONTHLY_PRICE,
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

    def test_chat_history_tracking(self):
        """Verify chat message ID tracking and single-screen clearing."""
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


    def test_image_generator_html_and_rendering(self):
        """Verify schedule HTML templates generate correctly and Chrome renders if available."""
        from handlers.image_generator import generate_schedule_html_a4, generate_schedule_html_wallpaper, render_schedule_images
        subject = {"id": 99, "name": "قدرات اختبار تجريبي", "target_days": 5}
        lessons = [
            {"id": 1, "lesson_order": 1, "title": "مقدمة الجبر", "scheduled_day": 1},
            {"id": 2, "lesson_order": 2, "title": "المعادلات الخطية", "scheduled_day": 2},
            {"id": 3, "lesson_order": 3, "title": "المتطابقات", "scheduled_day": 3},
        ]
        html_a4 = generate_schedule_html_a4(subject, lessons, "سعد")
        self.assertIn("قدرات اختبار تجريبي", html_a4)
        self.assertIn("مقدمة الجبر", html_a4)

        html_wp = generate_schedule_html_wallpaper(subject, lessons, "سعد")
        self.assertIn("قدرات اختبار تجريبي", html_wp)
        self.assertIn("المعادلات الخطية", html_wp)

        # Test rendering
        a4_path, wp_path = render_schedule_images(subject, lessons, "سعد")
        # If Chrome is installed on this machine, both images will exist
        if a4_path:
            self.assertTrue(os.path.exists(a4_path))
            self.assertTrue(os.path.exists(wp_path))


if __name__ == "__main__":
    unittest.main()
