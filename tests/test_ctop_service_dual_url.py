"""
Regression tests for services/ctop_service.py sending to BOTH ctop_url_1
and ctop_url_2.

Before this fix, send_to_ctop() read ctop_url_2 from device config but
never sent to it — only ctop_url_1 was ever POSTed, even though
ctop_url_2 is a fully-wired, user-configurable field (form, validation,
both device models). A device with a secondary CTOP endpoint configured
silently never delivered to it, with no error anywhere.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from services.ctop_service import CTOPService


def make_response(status_code, body='{}'):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = body
    resp.history = []
    return resp


class TestCTOPDualURL(unittest.TestCase):
    def setUp(self):
        self.service = CTOPService()
        self.device_data = {
            'name': 'Tank 1',
            'device_type': 'EvaraTank',
            'ctop_url_1': 'https://ctop.example.com/primary',
            'ctop_url_2': 'https://ctop.example.com/secondary',
            'auth_token': 'tok-123',
        }

    @patch('time.sleep', return_value=None)
    def test_sends_to_both_urls_when_both_succeed(self, mock_sleep):
        with patch.object(self.service.session, 'post', return_value=make_response(200)) as mock_post:
            result = self.service.send_to_ctop('dev1', self.device_data, payload={'x': 1})

        self.assertEqual(mock_post.call_count, 2)
        called_urls = {call.args[0] for call in mock_post.call_args_list}
        self.assertEqual(called_urls, {'https://ctop.example.com/primary', 'https://ctop.example.com/secondary'})

        self.assertTrue(result['success'])
        self.assertEqual(len(result['results']), 2)
        self.assertTrue(all(r['success'] for r in result['results']))

    @patch('time.sleep', return_value=None)
    def test_primary_succeeds_secondary_fails_still_reports_overall_success(self, mock_sleep):
        # 404 on the secondary URL is a permanent failure (no retry loop).
        responses = {
            'https://ctop.example.com/primary': make_response(200),
            'https://ctop.example.com/secondary': make_response(404, 'not found'),
        }

        def fake_post(url, **kwargs):
            return responses[url]

        with patch.object(self.service.session, 'post', side_effect=fake_post) as mock_post:
            result = self.service.send_to_ctop('dev1', self.device_data, payload={'x': 1})

        self.assertEqual(mock_post.call_count, 2)
        self.assertTrue(result['success'])  # at least one endpoint took the data
        per_url = {r['url']: r['success'] for r in result['results']}
        self.assertTrue(per_url['https://ctop.example.com/primary'])
        self.assertFalse(per_url['https://ctop.example.com/secondary'])

    @patch('time.sleep', return_value=None)
    def test_both_fail_reports_overall_failure(self, mock_sleep):
        with patch.object(self.service.session, 'post', return_value=make_response(404, 'nope')) as mock_post:
            result = self.service.send_to_ctop('dev1', self.device_data, payload={'x': 1})

        self.assertEqual(mock_post.call_count, 2)
        self.assertFalse(result['success'])
        self.assertIsNotNone(result['error'])

    @patch('time.sleep', return_value=None)
    def test_only_ctop_url_1_configured_sends_once(self, mock_sleep):
        device_data = dict(self.device_data)
        device_data.pop('ctop_url_2')

        with patch.object(self.service.session, 'post', return_value=make_response(200)) as mock_post:
            result = self.service.send_to_ctop('dev1', device_data, payload={'x': 1})

        self.assertEqual(mock_post.call_count, 1)
        self.assertTrue(result['success'])
        self.assertEqual(len(result['results']), 1)


if __name__ == '__main__':
    unittest.main()
