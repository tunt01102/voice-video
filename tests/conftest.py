import os
import tempfile

# Never touch the real data store (biometric recordings) from tests, even if the shell sets MEDIAFORGE_DATA_DIR.
os.environ["MEDIAFORGE_DATA_DIR"] = tempfile.mkdtemp(prefix="mf-test-")
