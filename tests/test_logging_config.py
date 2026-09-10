"""
Tests for utils/logging_config.py — opt-in structured JSON logging.

Default behavior (LOG_FORMAT unset) must stay plain text, unchanged from
before this existed. LOG_FORMAT=json must produce one valid JSON object
per log line with the fields a log aggregator would actually want to
query on (timestamp, level, logger name, message, and exception info when
present) — without requiring any of the hundreds of existing f-string log
calls across the codebase to change.
"""

import json
import logging
import os
import sys
from io import StringIO

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from utils.logging_config import JsonFormatter, configure_logging


def _capture_one_log(record_fn, formatter=None):
    """Configure a throwaway logger with a single in-memory handler,
    run record_fn() to emit a log record, and return the raw formatted
    line."""
    buf = StringIO()
    handler = logging.StreamHandler(buf)
    if formatter:
        handler.setFormatter(formatter)
    logger = logging.getLogger('test_logging_config')
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    record_fn(logger)
    return buf.getvalue().strip()


class TestJsonFormatter:
    def test_produces_valid_json_with_expected_fields(self):
        line = _capture_one_log(lambda l: l.info('hello world'), JsonFormatter())
        parsed = json.loads(line)

        assert parsed['level'] == 'INFO'
        assert parsed['logger'] == 'test_logging_config'
        assert parsed['message'] == 'hello world'
        assert 'timestamp' in parsed
        assert 'exception' not in parsed

    def test_preserves_fstring_interpolated_message(self):
        device_id = 'dev-123'
        line = _capture_one_log(
            lambda l: l.warning(f"Device {device_id}: something happened"),
            JsonFormatter()
        )
        parsed = json.loads(line)
        assert parsed['message'] == 'Device dev-123: something happened'

    def test_includes_exception_info_when_present(self):
        def emit(logger):
            try:
                raise ValueError('boom')
            except ValueError:
                logger.error('failed', exc_info=True)

        line = _capture_one_log(emit, JsonFormatter())
        parsed = json.loads(line)
        assert parsed['level'] == 'ERROR'
        assert 'exception' in parsed
        assert 'ValueError' in parsed['exception']
        assert 'boom' in parsed['exception']

    def test_timestamp_is_iso8601_utc(self):
        line = _capture_one_log(lambda l: l.info('x'), JsonFormatter())
        parsed = json.loads(line)
        # Must parse cleanly as ISO 8601 and carry UTC offset info.
        from datetime import datetime
        dt = datetime.fromisoformat(parsed['timestamp'])
        assert dt.tzinfo is not None


class TestConfigureLogging:
    def setup_method(self):
        # 'test_logging_config' is a process-wide singleton by name — reset
        # it here since TestJsonFormatter's tests (which run first) leave it
        # with propagate=False and a stale/closed handler of its own,
        # which would otherwise swallow every message before it ever
        # reaches the root handler these tests are actually inspecting.
        test_logger = logging.getLogger('test_logging_config')
        test_logger.propagate = True
        test_logger.handlers = []
        test_logger.setLevel(logging.NOTSET)

    def teardown_method(self):
        os.environ.pop('LOG_FORMAT', None)
        os.environ.pop('LOG_LEVEL', None)

    def test_default_format_is_plain_text_not_json(self):
        os.environ.pop('LOG_FORMAT', None)
        configure_logging()

        buf = StringIO()
        root = logging.getLogger()
        root.handlers[0].stream = buf

        logging.getLogger('test_logging_config').info('plain text check')
        output = buf.getvalue().strip()

        # Must NOT be JSON — confirms default behavior is unchanged.
        assert not output.startswith('{')
        assert 'plain text check' in output

    def test_json_format_env_var_switches_formatter(self):
        os.environ['LOG_FORMAT'] = 'json'
        configure_logging()

        buf = StringIO()
        root = logging.getLogger()
        root.handlers[0].stream = buf

        logging.getLogger('test_logging_config').info('json check')
        output = buf.getvalue().strip()

        parsed = json.loads(output)
        assert parsed['message'] == 'json check'

    def test_force_true_replaces_prior_handlers_not_layers_them(self):
        """Calling configure_logging() twice must not result in duplicate
        log lines — force=True should fully replace, not accumulate,
        handlers (this is what makes call-order irrelevant, per the
        module's own docstring)."""
        configure_logging()
        configure_logging()

        buf = StringIO()
        root = logging.getLogger()
        root.handlers[0].stream = buf

        logging.getLogger('test_logging_config').info('single line check')
        lines = [l for l in buf.getvalue().strip().split('\n') if l]
        assert len(lines) == 1
