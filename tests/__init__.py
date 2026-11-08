"""Run all tests from the project folder with:  python -m unittest -v

Tests never touch tutor_app.db: the app is pointed at a throwaway database
before it's imported.
"""
import os
import tempfile

os.environ["STUDYBYSARA_DB"] = os.path.join(tempfile.mkdtemp(), "test.db")
