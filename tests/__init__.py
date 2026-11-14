"""Run all tests from the project folder with:  python -m unittest -v

Tests never touch tutor_app.db: the app is pointed at a throwaway database
before it's imported.
"""
import os
import tempfile

os.environ["STUDYBYSARA_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
# A fake key, set before app.py loads .env, so tests can never use (or bill) a real key.
os.environ["ANTHROPIC_API_KEY"] = "sk-test-not-a-real-key"

import logging

logging.disable(logging.CRITICAL)   # expected warnings from failure-path tests would clutter the output
