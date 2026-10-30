from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

try:
    import jsonschema  # noqa: F401

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

from app.validation import ResultValidator

CASE_DIR = Path("stage0/fixtures/cases/L001")
ORG_FRAGMENT = Path("organization_fragment.json")


@unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema is not installed")
class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.validator = ResultValidator()
        cls.data = json.loads((CASE_DIR / "expected.data.json").read_text(encoding="utf-8"))
        cls.review = json.loads((CASE_DIR / "expected.review.json").read_text(encoding="utf-8"))

    def test_valid_l001_is_ok(self) -> None:
        outcome = self.validator.validate(self.data, self.review)
        self.assertEqual(outcome.status, "ok")
        self.assertIsNone(outcome.error)

    def test_principal_over_actual_needs_review(self) -> None:
        data = copy.deepcopy(self.data)
        data["诉讼请求"]["1.本金"]["尚欠本金"] = "99999.00"
        outcome = self.validator.validate(data, self.review)
        self.assertEqual(outcome.status, "needs_review")
        codes = {item["code"] for item in outcome.review["warnings"]}
        self.assertIn("PRINCIPAL_OVER_ACTUAL", codes)

    def test_agent_missing_needs_review(self) -> None:
        data = copy.deepcopy(self.data)
        data["委托诉讼代理人"]["是否委托"] = "有"
        outcome = self.validator.validate(data, self.review)
        self.assertEqual(outcome.status, "needs_review")
        codes = {item["code"] for item in outcome.review["warnings"]}
        self.assertIn("AGENT_MISSING", codes)

    def test_guarantee_detail_needs_review(self) -> None:
        data = copy.deepcopy(self.data)
        data["事实与理由"]["10.是否签订物的担保（抵押、质押）合同"]["是否签订"] = "否"
        data["事实与理由"]["11.担保人、担保物"]["担保人"] = ["某担保人"]
        outcome = self.validator.validate(data, self.review)
        self.assertEqual(outcome.status, "needs_review")
        codes = {item["code"] for item in outcome.review["warnings"]}
        self.assertIn("GUARANTEE_11_WITH_NO_CONTRACT", codes)

    def test_org_type_conflict_needs_review(self) -> None:
        data = copy.deepcopy(self.data)
        org = json.loads(ORG_FRAGMENT.read_text(encoding="utf-8"))["原告"]
        data["原告"] = copy.deepcopy(org)
        data["原告"][0]["data"]["type"] = ["有限责任公司", "股份有限公司"]
        outcome = self.validator.validate(data, self.review)
        self.assertEqual(outcome.status, "needs_review")
        codes = {item["code"] for item in outcome.review["warnings"]}
        self.assertIn("ORG_TYPE_MULTIPLE_FORMS", codes)

    def test_state_ownership_conflict_needs_review(self) -> None:
        data = copy.deepcopy(self.data)
        org = json.loads(ORG_FRAGMENT.read_text(encoding="utf-8"))["原告"]
        data["原告"] = copy.deepcopy(org)
        data["原告"][0]["data"]["ownership"] = "国有"
        data["原告"][0]["data"]["ownershipOther"] = None
        data["原告"][0]["data"]["stateOwnershipType"] = ["控股", "参股"]
        outcome = self.validator.validate(data, self.review)
        self.assertEqual(outcome.status, "needs_review")
        codes = {item["code"] for item in outcome.review["warnings"]}
        self.assertIn("STATE_OWNERSHIP_MULTIPLE", codes)

    def test_repayment_method_conflict_needs_review(self) -> None:
        data = copy.deepcopy(self.data)
        data["事实与理由"]["7.还款方式"]["方式"] = ["到期一次性还本付息", "其他"]
        data["事实与理由"]["7.还款方式"]["其他说明"] = "按实际还款安排"
        outcome = self.validator.validate(data, self.review)
        self.assertEqual(outcome.status, "needs_review")
        codes = {item["code"] for item in outcome.review["warnings"]}
        self.assertIn("REPAYMENT_METHOD_MULTIPLE", codes)

    def test_registration_type_conflict_needs_review(self) -> None:
        data = copy.deepcopy(self.data)
        data["事实与理由"]["13.是否办理抵押、质押登记"]["是否办理"] = "是"
        data["事实与理由"]["13.是否办理抵押、质押登记"]["登记类型"] = ["正式登记", "预告登记"]
        outcome = self.validator.validate(data, self.review)
        self.assertEqual(outcome.status, "needs_review")
        codes = {item["code"] for item in outcome.review["warnings"]}
        self.assertIn("REGISTRATION_TYPE_MULTIPLE", codes)

    def test_no_party_is_error(self) -> None:
        data = copy.deepcopy(self.data)
        data["原告"] = None
        outcome = self.validator.validate(data, self.review)
        self.assertEqual(outcome.status, "error")
        self.assertEqual(outcome.error["code"], "NO_PARTIES")

    def test_schema_invalid_is_error(self) -> None:
        data = copy.deepcopy(self.data)
        del data["诉讼请求"]["1.本金"]["尚欠本金"]
        outcome = self.validator.validate(data, self.review)
        self.assertEqual(outcome.status, "error")
        self.assertEqual(outcome.error["code"], "SCHEMA_INVALID")


if __name__ == "__main__":
    unittest.main()
