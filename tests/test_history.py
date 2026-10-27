"""history.py: the SQLite run history is made on first use, records runs and their values, and never raises."""
from __future__ import annotations

import os
import sqlite3
import unittest

from tests.base import TempData

import history
import shobs_profile as sp


class TestHistory(TempData):
    def test_record_and_read(self):
        key = history.record_run("check", "C:/x.fit", {"n_stars": 3}, camera_id="cam__g1__o1__bin1", n_frames=1,
                                 status="warn", app="SHOBS-FLUX test",
                                 values=[{"key": "sky_e", "channel": "G", "value": 33.8, "unit": "e-", "source":
                                          "measured"}])
        self.assertTrue(key and key.startswith("check-"))
        self.assertTrue(os.path.exists(sp.runs_db_path()))
        runs = history.recent_runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "warn")
        con = sqlite3.connect(sp.runs_db_path())
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], history.SCHEMA_VERSION)
        self.assertEqual(con.execute("SELECT key, channel, value FROM run_values").fetchall(), [("sky_e", "G", 33.8)])
        con.close()

    def test_unwritable_database_never_raises(self):
        os.makedirs(sp.runs_db_path())          # a folder where the file should be
        self.assertIsNone(history.record_run("check", "x", {}))
        self.assertEqual(history.recent_runs(), [])


if __name__ == "__main__":
    unittest.main()
