import io
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from secure_macos_probe import MAGIC, open_directory, seal_directory
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

class SecureTransportTests(unittest.TestCase):
    def test_roundtrip_and_authentication(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'source'; source.mkdir()
            (source / 'private.txt').write_text('private metadata')
            key = bytes(range(32)); encrypted = root / 'encrypted'
            seal_directory(source, encrypted, key)
            self.assertNotIn(b'private metadata', encrypted.read_bytes())
            open_directory(encrypted, root / 'recovered', key)
            self.assertEqual((root / 'recovered/private.txt').read_text(), 'private metadata')
            with self.assertRaises(InvalidTag):
                open_directory(encrypted, root / 'wrong', b'x' * 32)
            self.assertFalse((root / 'wrong').exists())
    def test_traversal_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); data = io.BytesIO()
            with zipfile.ZipFile(data, 'w') as archive:
                archive.writestr('../escaped', 'private')
            key = b'x' * 32; nonce = b'n' * 12
            encrypted = root / 'encrypted'
            encrypted.write_bytes(MAGIC + nonce + AESGCM(key).encrypt(nonce, data.getvalue(), MAGIC))
            with self.assertRaises(ValueError):
                open_directory(encrypted, root / 'recovered', key)
            self.assertFalse((root / 'recovered').exists())
    def test_key_not_in_native_process_environment(self):
        import contextlib
        import os
        from unittest.mock import patch
        from secure_macos_probe import run_private
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'source'; source.mkdir()
            (source / 'manifest.json').write_text('{}')
            key = bytes(range(32)); encrypted = root / 'input.enc'
            seal_directory(source, encrypted, key)
            old = Path.cwd()
            try:
                os.chdir(root)
                with patch.dict(os.environ, {'MACOS_PROBE_KEY': key.hex()}), patch('secure_macos_probe.subprocess.run') as runner:
                    runner.return_value.returncode = 0
                    with contextlib.redirect_stdout(io.StringIO()) as public:
                        self.assertEqual(run_private(encrypted, root / 'result.enc'), 0)
                    self.assertNotIn('MACOS_PROBE_KEY', runner.call_args.kwargs['env'])
                    self.assertNotIn(key.hex(), public.getvalue())
                open_directory(root / 'result.enc', root / 'recovered', key)
                self.assertTrue((root / 'recovered/collector.log').is_file())
            finally:
                os.chdir(old)
