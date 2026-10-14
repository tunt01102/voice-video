import os
import tempfile

# Never touch the real data store (biometric recordings) from tests.
os.environ.setdefault("MEDIAFORGE_DATA_DIR", tempfile.mkdtemp(prefix="mf-test-"))
