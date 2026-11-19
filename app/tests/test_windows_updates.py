"""Regression coverage for update failures first found on native Windows."""
from pathlib import Path
import tempfile
import unittest

from app.updater.transaction import _sync_tree


class UpdatePlatformRegressionTests(unittest.TestCase):
    def test_sync_nested_backup_files_without_changing_bytes(self):
        # Real fsync is essential: Windows rejects a read-only file descriptor.
        with tempfile.TemporaryDirectory(prefix='learning-hub-sync-') as directory:
            root = Path(directory).resolve()
            files = {'plain.txt': b'backup\n', 'nested/binary.bin': bytes(range(256))}
            for name, raw in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(raw)
            _sync_tree(root)
            self.assertEqual({name: (root / name).read_bytes() for name in files}, files)


if __name__ == '__main__':
    unittest.main()
