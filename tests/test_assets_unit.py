"""Model integrity and download recovery; all network I/O is mocked."""
import hashlib
import io
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from lipsync_service import model_assets as assets


class Response(io.BytesIO):
    status = 200
    headers = {}


class AssetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.data = b'model weights'
        self.spec = {'path': 'model.bin', 'size': len(self.data),
                     'sha256': hashlib.sha256(self.data).hexdigest(), 'url': 'https://example.invalid/model'}

    def test_integrity_algorithms_and_missing_empty_wrong_size(self):
        path = self.root / 'model.bin'
        self.assertFalse(assets.check_asset(path, self.spec))
        path.touch()
        self.assertFalse(assets.check_asset(path, self.spec))
        path.write_bytes(b'wrong')
        self.assertFalse(assets.check_asset(path, self.spec))
        path.write_bytes(self.data)
        for spec in (self.spec, {'sha256_prefix': self.spec['sha256'][:8]},
                     {'git_blob_sha1': hashlib.sha1(f'blob {len(self.data)}\0'.encode() + self.data).hexdigest()}):
            self.assertTrue(assets.check_asset(path, spec))
        with patch.object(assets, 'assets', return_value=[self.spec, {'path': 'missing', 'sha256': '0'}]):
            with self.assertRaisesRegex(RuntimeError, 'missing'):
                assets.verify_models(self.root)

    def test_verified_destination_and_partial_skip_network(self):
        target = self.root / 'model.bin'
        for partial in (False, True):
            with self.subTest(partial=partial):
                target.unlink(missing_ok=True)
                source = target.with_name('model.bin.part') if partial else target
                source.write_bytes(self.data)
                with patch.object(assets.urllib.request, 'urlopen') as request:
                    assets.download_asset(self.root, self.spec, 'https://example.invalid')
                    request.assert_not_called()
                self.assertEqual(target.read_bytes(), self.data)

    def test_server_ignoring_range_replaces_partial_instead_of_appending(self):
        (self.root / 'model.bin.part').write_bytes(self.data[:3])
        with patch.object(assets.urllib.request, 'urlopen', return_value=Response(self.data)) as request:
            assets.download_asset(self.root, self.spec, 'https://example.invalid')
        self.assertEqual(request.call_args.args[0].get_header('Range'), 'bytes=3-')
        self.assertEqual((self.root / 'model.bin').read_bytes(), self.data)

    def test_wrong_range_retries_and_never_publishes(self):
        def wrong_range(*args, **kwargs):
            response = Response(b'bad')
            response.status, response.headers = 206, {'Content-Range': 'bytes 99-100/101'}
            return response
        with patch.object(assets.urllib.request, 'urlopen', side_effect=wrong_range) as request, \
             patch.object(assets.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'Unexpected download range'):
                assets.download_asset(self.root, self.spec, 'https://example.invalid')
            self.assertEqual(request.call_count, 3)
        self.assertFalse((self.root / 'model.bin').exists())

    def test_incomplete_download_retained_then_resumed(self):
        response = Response(self.data[3:])
        response.status = 206
        response.headers = {'Content-Range': f'bytes 3-{len(self.data)-1}/{len(self.data)}'}
        with patch.object(assets.urllib.request, 'urlopen', side_effect=[Response(self.data[:3]), response]), \
             patch.object(assets.time, 'sleep'):
            assets.download_asset(self.root, self.spec, 'https://example.invalid')
        self.assertEqual((self.root / 'model.bin').read_bytes(), self.data)
        self.assertFalse((self.root / 'model.bin.part').exists())

    def test_range_not_satisfiable_discards_stale_partial(self):
        (self.root / 'model.bin.part').write_bytes(b'bad')
        error = urllib.error.HTTPError(self.spec['url'], 416, 'Range', {}, None)
        with patch.object(assets.urllib.request, 'urlopen', side_effect=[error, Response(self.data)]) as request, \
             patch.object(assets.time, 'sleep'):
            assets.download_asset(self.root, self.spec, 'https://example.invalid')
        self.assertIsNone(request.call_args.args[0].get_header('Range'))
        self.assertEqual((self.root / 'model.bin').read_bytes(), self.data)
