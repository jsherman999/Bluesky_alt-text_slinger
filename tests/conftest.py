import os
import tempfile

# Importing the app initializes SQLite; never use a real user's cache in tests.
_test_db = tempfile.TemporaryDirectory()
os.environ['ALTTS_DB_PATH'] = os.path.join(_test_db.name, 'initial.db')
