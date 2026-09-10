"""
Opt-in structured (JSON) logging.

Every module in this codebase logs via plain f-strings into whatever the
root logger's first logging.basicConfig() call configured — several
modules (utils/scheduler.py, utils/scheduler_firestore.py, this app's own
entrypoint) each call logging.basicConfig() themselves, and Python's
logging.basicConfig() is a documented no-op on every call after the first
in a process, so whichever one happens to import first silently wins.

Rather than rewriting every log call across the codebase to go through a
structured logger (a large, invasive change for a feature that's opt-in
anyway), this configures the *formatter* used by the one root handler that
ends up receiving all of them, controlled by the LOG_FORMAT env var:

  LOG_FORMAT unset or "text" (default) — unchanged plain-text output,
    identical to before this existed.
  LOG_FORMAT=json — one JSON object per log line, so logs become
    queryable by field (level, logger name, device-related text in the
    message) once volume is high enough that grepping plain text stops
    scaling.

configure_logging() must be called once, early in app startup (see
app.py), before any other module's own logging.basicConfig() call could
otherwise win that race. It passes force=True specifically so call order
doesn't actually matter — force=True tears down and replaces any handlers
a prior basicConfig() call already installed.
"""

import json
import logging
import os
from datetime import datetime, timezone


class JsonFormatter(logging.Formatter):
    """Renders each log record as a single JSON object per line."""

    def format(self, record):
        payload = {
            'timestamp': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }
        if record.exc_info:
            payload['exception'] = self.formatException(record.exc_info)
        return json.dumps(payload)


def configure_logging():
    """Configure the root logger once, early in app startup. Safe to call
    more than once (e.g. in tests) — each call fully replaces the prior
    configuration rather than layering handlers."""
    log_level = os.environ.get('LOG_LEVEL', 'INFO').upper()
    log_format = os.environ.get('LOG_FORMAT', 'text').lower()

    handler = logging.StreamHandler()
    if log_format == 'json':
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))

    logging.basicConfig(level=log_level, handlers=[handler], force=True)
