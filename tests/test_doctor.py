"""Self-contained tests: run with  python -m unittest discover tests"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "doctor"))
import abend_doctor as ad  # noqa: E402

FIX = ROOT / "tests" / "fixtures"


class ParseSpool(unittest.TestCase):
    def test_s0c7(self):
        s = ad.parse_spool((FIX / "synthetic_s0c7_pstask14.txt").read_text())
        self.assertEqual(s["abend"], "S0C7")
        self.assertEqual(s["le"]["program"], "PSTASK14")
        self.assertEqual(s["le"]["statement"], 230)

    def test_user_abend_and_file_status(self):
        s = ad.parse_spool((FIX / "synthetic_u4038_vsamjob9.txt").read_text())
        self.assertEqual(s["abend"], "U4038")
        self.assertEqual(s["file_errors"][0], {"file": "SRTDD", "program": "VSAMJOB9", "status": "35"})

    def test_jcl_error(self):
        s = ad.parse_spool((FIX / "synthetic_jclerr_task26.txt").read_text())
        self.assertTrue(s["jcl_error"])
        self.assertIsNone(s["abend"])
        self.assertEqual(s["jcl_issues"][0]["dd"], "INPDD")


class DiagnoseDemo(unittest.TestCase):
    def test_paycalc_s0c7_points_to_compute(self):
        idx = ad.index_repo(ROOT / "demo")
        r = ad.diagnose((FIX / "synthetic_s0c7_paycalc.txt").read_text(), idx)
        self.assertEqual(r["kind"], "abend")
        self.assertEqual(r["program"]["name"], "PAYCALC")
        self.assertIn("COMPUTE WS-PAY", r["suspects"][0]["code"])
        self.assertEqual(r["last_display"]["paragraph"], "CALC-PAY")


if __name__ == "__main__":
    unittest.main()
