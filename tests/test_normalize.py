from __future__ import annotations

import unittest

from app.normalize import load_schema, normalize_merged, normalize_task_data


class NormalizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = load_schema()

    def test_flat_paths_wildcard_and_money(self) -> None:
        raw = {
            "原告": [{"data": {"name": "张三"}}],
            "/事实与理由/3.借款金额/实际提供": "60000",
            "/事实与理由/5.借款利率/利率": "4",
            "/事实与理由/6.借款提供时间/*/提供日期": "2025-01-01",
            "/事实与理由/6.借款提供时间/*/金额": "60000",
            "第三人": [],
        }
        result = normalize_task_data(raw, self.schema)
        self.assertEqual(result["原告"][0]["category"], "自然人")
        self.assertIn("certificateNumber", result["原告"][0]["data"])
        self.assertEqual(result["事实与理由"]["3.借款金额"]["实际提供"], "60000.00")
        self.assertEqual(result["事实与理由"]["5.借款利率"]["利率"], "4")
        self.assertEqual(len(result["事实与理由"]["6.借款提供时间"]), 1)
        self.assertEqual(result["事实与理由"]["6.借款提供时间"][0]["金额"], "60000.00")
        self.assertIsNone(result["第三人"])

    def test_money_with_currency_unit(self) -> None:
        raw = {"/诉讼请求/1.本金/尚欠本金": "人民币50,000元"}
        result = normalize_task_data(raw, self.schema)
        self.assertEqual(result["诉讼请求"]["1.本金"]["尚欠本金"], "50000.00")

    def test_money_with_explanatory_prefix(self) -> None:
        raw = {"/诉讼请求/2.利息/计算基数": "尚欠本金60000元"}
        result = normalize_task_data(raw, self.schema)
        self.assertEqual(result["诉讼请求"]["2.利息"]["计算基数"], "60000.00")

    def test_wildcard_list_stays_flat(self) -> None:
        raw = {
            "事实与理由": {
                "6.借款提供时间": {
                    "*": [
                        {"提供日期": "2025-01-01", "金额": "60000"},
                        {"提供日期": "2025-02-01", "金额": "15000"},
                    ]
                }
            }
        }
        result = normalize_task_data(raw, self.schema)
        records = result["事实与理由"]["6.借款提供时间"]
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0]["金额"], "60000.00")
        self.assertEqual(records[1]["金额"], "15000.00")

    def test_unknown_enum_normalized_to_other(self) -> None:
        raw = {
            "原告": [{"data": {"name": "张三"}}],
            "被告": [{"data": {"name": "李四"}}],
            "事实与理由": {"7.还款方式": {"方式": ["到期一次性还本"]}},
        }
        issues = []
        result = normalize_merged(raw, self.schema, issues)
        self.assertEqual(result["事实与理由"]["7.还款方式"]["方式"], ["其他"])
        self.assertEqual(issues[0]["code"], "ENUM_NORMALIZED")

    def test_merged_fills_fixed_objects_and_null_leaves(self) -> None:
        raw = {
            "原告": [{"data": {"name": "张三"}}],
            "被告": [{"data": {"name": "李四"}}],
            "诉讼请求": {"1.本金": {"尚欠本金": "50000"}},
        }
        result = normalize_merged(raw, self.schema)
        self.assertIn("委托诉讼代理人", result)
        self.assertIn("代理人", result["委托诉讼代理人"])
        self.assertIn("10.是否签订物的担保（抵押、质押）合同", result["事实与理由"])
        self.assertIn("18.证据清单（可另附页）", result["事实与理由"])
        self.assertIn("对纠纷解决方式的意愿", result)
        self.assertIn("签署信息", result)
        self.assertEqual(result["诉讼请求"]["1.本金"]["尚欠本金"], "50000.00")
        self.assertEqual(result["原告"][0]["category"], "自然人")


if __name__ == "__main__":
    unittest.main()
