"""Authentication and download reader release without an HTTP server."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from routers.lipsync import ResultResponse, auth


class RouteTests(unittest.TestCase):
    def test_auth_requires_exact_bearer_token(self):
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(api_key='密钥')))
        for header in (None, '', '密钥', 'Bearer wrong', 'bearer 密钥'):
            with self.subTest(header=header), self.assertRaises(HTTPException) as caught:
                auth(request, header)
            self.assertEqual(caught.exception.status_code, 401)
        auth(request, 'Bearer 密钥')
        request.app.state.api_key = ''
        auth(request, None)

    def test_if_range_accepts_only_current_etag_or_modified_date(self):
        response = ResultResponse('result.mp4', release=Mock(), headers={
            'etag': '"digest"', 'last-modified': 'Wed, 28 Oct 2026 00:00:00 GMT'})
        self.assertTrue(response._should_use_range('"digest"', None))
        self.assertTrue(response._should_use_range(response.headers['last-modified'], None))
        self.assertFalse(response._should_use_range('"old-digest"', None))

    def test_reader_released_on_success_exception_and_cancellation(self):
        for error in (None, OSError('disconnected'), asyncio.CancelledError()):
            with self.subTest(error=error):
                release = Mock()
                response = ResultResponse('result.mp4', release=release)
                with patch('starlette.responses.FileResponse.__call__', new=AsyncMock(side_effect=error)):
                    if error is None:
                        asyncio.run(response({}, None, None))
                    else:
                        with self.assertRaises(type(error)):
                            asyncio.run(response({}, None, None))
                release.assert_called_once_with()
