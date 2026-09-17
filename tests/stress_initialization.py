"""Repeat the real multi-process cold-start regression on each native runner."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from test_engine import EngineTests


if __name__ == "__main__":
    suite = unittest.TestSuite(
        EngineTests("test_concurrent_first_use_serializes_initialization")
        for _ in range(20)
    )
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    raise SystemExit(not result.wasSuccessful())
