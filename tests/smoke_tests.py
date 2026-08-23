"""Simple smoke tests using Flask test client.
This script mocks external `requests.head` used by `/api/test-connection`
so the test does not require network access.
"""
import os
import json
from unittest.mock import patch

# Ensure development environment
os.environ['FLASK_ENV'] = os.environ.get('FLASK_ENV', 'development')
# Optional: set SECRET_KEY for deterministic behavior
os.environ['SECRET_KEY'] = os.environ.get('SECRET_KEY', '')

from app import create_app

app = create_app('default')

# Mock object for requests.head
class MockHeadResponse:
    def __init__(self, status_code=200):
        self.status_code = status_code


def run_test_connection_mock():
    payload = {
        'url': 'https://ctop.iiit.ac.in/api/nodes/create-cin/391',
        'auth_token': 'b1a8f4503e741e05f88699bb7c91c02a'
    }

    with patch('requests.head') as mock_head:
        mock_head.return_value = MockHeadResponse(status_code=200)

        with app.test_client() as client:
            resp = client.post('/api/test-connection', json=payload)
            print('Status code:', resp.status_code)
            print('Response body:', resp.get_data(as_text=True))


if __name__ == '__main__':
    print('Running smoke tests (test_connection with mocked head)...')
    run_test_connection_mock()
