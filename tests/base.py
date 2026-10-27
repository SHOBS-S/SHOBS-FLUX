"""Shared test set-up: every test gets its own empty SHOBS data folder (SHOBS_DATA_HOME), so nothing touches ~/.shobs."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
FIXTURES = os.path.join(HERE, "fixtures")
sys.path.insert(0, APP)


class TempData(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="shobs_flux_test_")
        self._old = os.environ.get("SHOBS_DATA_HOME")
        os.environ["SHOBS_DATA_HOME"] = os.path.join(self.tmp, "data")

    def tearDown(self):
        if self._old is None:
            os.environ.pop("SHOBS_DATA_HOME", None)
        else:
            os.environ["SHOBS_DATA_HOME"] = self._old
        shutil.rmtree(self.tmp, ignore_errors=True)

    def path(self, name: str) -> str:
        return os.path.join(self.tmp, name)
