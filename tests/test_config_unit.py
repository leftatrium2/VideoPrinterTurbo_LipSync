"""Configuration and request schema validation, without starting models."""
import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError
from app import create_app, positive_env
from routers.lipsync import FileSpec, SubmitSpec


class ConfigurationTests(unittest.TestCase):
    def test_positive_environment_values(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(positive_env('TEST_LIMIT', 4, int), 4)
        for value in ('0', '-1', 'nan', 'inf', '-inf', 'bad'):
            with self.subTest(value=value), patch.dict(os.environ, {'TEST_LIMIT': value}):
                with self.assertRaises(ValueError):
                    positive_env('TEST_LIMIT', 4)

    def test_invalid_engine_and_precision(self):
        for setting in ({'LIPSYNC_ENGINE': 'unknown'}, {'LIPSYNC_FLOAT16': 'yes'}):
            with self.subTest(setting=setting), patch.dict(os.environ, setting, clear=True):
                with self.assertRaises(ValueError):
                    create_app()

    def test_command_contract(self):
        for value in ('{}', '[1]', '["model", "{video}"]', 'null'):
            with self.subTest(value=value), patch.dict(os.environ, {'LIPSYNC_COMMAND': value}, clear=True):
                with self.assertRaises(ValueError):
                    create_app()
        with patch.dict(os.environ, {'LIPSYNC_COMMAND': '["model", "{video}", "{audio}", "{output}"]'}, clear=True):
            app = create_app(api_key='explicit')
            self.assertTrue(app.state.engine.ready)
            self.assertEqual(app.state.api_key, 'explicit')

    def test_filename_and_metadata_validation(self):
        valid = {'name': 'speaker.MP4', 'size': 1, 'sha256': 'a' * 64}
        self.assertEqual(FileSpec(**valid).name, 'speaker.MP4')
        for update in ({'name': '../video.mp4'}, {'name': 'dir\\video.mp4'},
                       {'name': 'script.py'}, {'name': ''}, {'size': True},
                       {'size': '1'}, {'size': 0}, {'sha256': 'A' * 64}, {'sha256': 'abc'}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                FileSpec(**(valid | update))
        self.assertEqual(SubmitSpec().timeline, 'strict')
        with self.assertRaises(ValidationError):
            SubmitSpec(timeline='invalid')

    def test_streaming_limits_and_optional_total_deadline(self):
        with patch.dict(os.environ, {}, clear=True):
            configured = create_app().state.engine
            self.assertEqual(configured.timeout, 0)
            self.assertEqual(configured.idle_timeout, 600)
            self.assertEqual(configured.settings['frame_memory_mb'], 256)
        for key, values in {
            'LIPSYNC_INFERENCE_TIMEOUT': ('-1', 'nan', 'inf'),
            'LIPSYNC_IDLE_TIMEOUT': ('0', '-1', 'nan'),
            'LIPSYNC_MEDIA_IDLE_TIMEOUT': ('0', '-1', 'inf'),
            'LIPSYNC_FRAME_MEMORY_MB': ('0', '-1', 'bad'),
        }.items():
            for value in values:
                with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}, clear=True):
                    with self.assertRaises(ValueError):
                        create_app()
