import tempfile
import unittest
from pathlib import Path

from apply_reviewed_album_title_aliases import (
    _reviewed_file_receipt_matches,
)


class ReviewedAlbumAliasPathReceiptTests(unittest.TestCase):
    def test_workspace_relative_and_absolute_same_file_match_with_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            file = root / 'data' / 'source.json'
            file.parent.mkdir()
            file.write_bytes(b'{"source":1}')
            receipt = {'path': 'data/source.json', 'sha256': 'sha'}
            self.assertTrue(_reviewed_file_receipt_matches(
                receipt, file.resolve(), 'sha', workspace_root=root))

    def test_same_hash_at_different_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            a, b = root / 'data/a.json', root / 'data/b.json'
            a.parent.mkdir()
            a.write_bytes(b'same bytes')
            b.write_bytes(b'same bytes')
            self.assertFalse(_reviewed_file_receipt_matches(
                {'path': 'data/a.json', 'sha256': 'sha'}, b, 'sha', workspace_root=root))

    def test_absolute_reviewed_file_outside_workspace_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'workspace'
            root.mkdir()
            external = Path(directory) / 'approved-source.json'
            external.write_bytes(b'approved packet')
            self.assertTrue(_reviewed_file_receipt_matches(
                {'path': str(external), 'sha256': 'sha'}, external, 'sha',
                workspace_root=root))
            decoy = Path(directory) / 'same-bytes.json'
            decoy.write_bytes(b'approved packet')
            self.assertFalse(_reviewed_file_receipt_matches(
                {'path': str(external), 'sha256': 'sha'}, decoy, 'sha',
                workspace_root=root))

    def test_symlink_retarget_to_same_hash_different_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'data/source.json'
            decoy = root / 'data/decoy.json'
            link = root / 'data/current.json'
            source.parent.mkdir()
            source.write_bytes(b'same bytes')
            decoy.write_bytes(b'same bytes')
            link.symlink_to(source)
            receipt = {'path': 'data/current.json', 'sha256': 'sha'}
            self.assertTrue(_reviewed_file_receipt_matches(
                receipt, source, 'sha', workspace_root=root))
            link.unlink()
            link.symlink_to(decoy)
            self.assertFalse(_reviewed_file_receipt_matches(
                receipt, source, 'sha', workspace_root=root))

    def test_mismatched_digest_or_workspace_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'workspace'
            root.mkdir()
            outside = Path(directory) / 'outside.json'
            outside.write_bytes(b'same bytes')
            inside = root / 'data/source.json'
            inside.parent.mkdir()
            inside.write_bytes(b'same bytes')
            self.assertFalse(_reviewed_file_receipt_matches(
                {'path': 'data/source.json', 'sha256': 'wrong'}, inside, 'sha',
                workspace_root=root))
            self.assertFalse(_reviewed_file_receipt_matches(
                {'path': '../outside.json', 'sha256': 'sha'}, outside, 'sha',
                workspace_root=root))


if __name__ == '__main__':
    unittest.main()
