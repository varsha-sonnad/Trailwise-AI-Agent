# conftest.py: shared test setup. pytest loads this file automatically before running any test.

import os

# Give the tests a fake Gemini key so the app can start without a real one.
# This must run before app.config is imported.
os.environ.setdefault("GEMINI_API_KEY", "test-key")
# Keep the audit log off in tests so they never write to the real log file (test_audit.py turns it on).
os.environ.setdefault("AUDIT_LOG_ENABLED", "false")
