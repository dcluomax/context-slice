from pathlib import Path
import tempfile
import unittest

from context_slice.locking import file_lock


class LockingTests(unittest.TestCase):
    def test_contention_is_bounded_and_release_allows_next_owner(self):
        with tempfile.TemporaryDirectory(prefix="context-slice-lock-") as directory:
            path = Path(directory) / "local.lock"
            with file_lock(path):
                with self.assertRaises(TimeoutError):
                    with file_lock(path, timeout=0):
                        self.fail("A second owner must not enter the same lock.")
            with file_lock(path, timeout=0):
                self.assertTrue(path.is_file())

    def test_linked_lock_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix="context-slice-lock-link-") as directory:
            target = Path(directory) / "target"
            target.write_text("untouched")
            link = Path(directory) / "linked.lock"
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("Symlink creation is unavailable for this account.")
            with self.assertRaises(OSError):
                with file_lock(link):
                    self.fail("Linked lock must not be used.")
            self.assertEqual(target.read_text(), "untouched")


if __name__ == "__main__":
    unittest.main()
