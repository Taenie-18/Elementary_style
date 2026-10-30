from __future__ import annotations

import unittest

from app.adapter import MockModelAdapter
from app.models import InputDocument
from app.subject_index import (
    SubjectIndexError,
    build_subject_index,
    subjects_for_prompt,
)


def document(text: str) -> InputDocument:
    return InputDocument(
        request_id="req-1",
        template_id="private_lending_complaint",
        schema_version="0.1.0",
        file_context=(text,),
    )


def adapter(subjects: list[dict]) -> MockModelAdapter:
    return MockModelAdapter(responses={"subject_index": {"subjects": subjects}})


class SubjectIndexTests(unittest.TestCase):
    def test_two_parties(self) -> None:
        doc = document("原告：张三，男。被告：李四，女。")
        subjects = build_subject_index(
            adapter(
                [
                    {
                        "role": "原告",
                        "entityType": "自然人",
                        "name": "张三",
                        "aliases": ["原告"],
                        "quote": "原告：张三",
                    },
                    {
                        "role": "被告",
                        "entityType": "自然人",
                        "name": "李四",
                        "aliases": ["被告"],
                        "quote": "被告：李四",
                    },
                ]
            ),
            doc,
        )
        self.assertEqual([s.subject_id for s in subjects], ["P1", "P2"])
        self.assertEqual(subjects[0].output_pointer, "/原告/0")
        self.assertEqual(subjects[1].output_pointer, "/被告/0")
        self.assertEqual(subjects[0].source_refs[0].quote, "原告：张三")
        self.assertEqual(subjects[0].source_refs[0].char_start, 0)

    def test_multiple_plaintiffs(self) -> None:
        doc = document("原告：张三。原告：王五。被告：李四。")
        subjects = build_subject_index(
            adapter(
                [
                    {"role": "原告", "entityType": "自然人", "name": "张三", "aliases": [], "quote": "原告：张三"},
                    {"role": "原告", "entityType": "自然人", "name": "王五", "aliases": [], "quote": "原告：王五"},
                    {"role": "被告", "entityType": "自然人", "name": "李四", "aliases": [], "quote": "被告：李四"},
                ]
            ),
            doc,
        )
        self.assertEqual([s.output_pointer for s in subjects], ["/原告/0", "/原告/1", "/被告/0"])
        self.assertEqual(subjects[0].aliases, ("原告",))

    def test_no_defendant(self) -> None:
        doc = document("原告：张三。")
        with self.assertRaises(SubjectIndexError) as ctx:
            build_subject_index(
                adapter(
                    [
                        {"role": "原告", "entityType": "自然人", "name": "张三", "aliases": ["原告"], "quote": "原告：张三"}
                    ]
                ),
                doc,
            )
        self.assertEqual(ctx.exception.code, "NO_PARTIES")

    def test_quote_not_found(self) -> None:
        doc = document("原告：张三。被告：李四。")
        with self.assertRaises(SubjectIndexError) as ctx:
            build_subject_index(
                adapter(
                    [
                        {"role": "原告", "entityType": "自然人", "name": "王五", "aliases": [], "quote": "不存在"},
                        {"role": "被告", "entityType": "自然人", "name": "李四", "aliases": [], "quote": "被告：李四"},
                    ]
                ),
                doc,
            )
        self.assertEqual(ctx.exception.code, "SUBJECT_INDEX_INVALID")

    def test_invalid_role(self) -> None:
        doc = document("原告：张三。被告：李四。")
        with self.assertRaises(SubjectIndexError):
            build_subject_index(
                adapter(
                    [
                        {"role": "申请人", "entityType": "自然人", "name": "张三", "aliases": [], "quote": "原告：张三"},
                        {"role": "被告", "entityType": "自然人", "name": "李四", "aliases": [], "quote": "被告：李四"},
                    ]
                ),
                doc,
            )

    def test_prompt_projection(self) -> None:
        doc = document("原告：张三。被告：李四。")
        subjects = build_subject_index(
            adapter(
                [
                    {"role": "原告", "entityType": "自然人", "name": "张三", "aliases": ["原告"], "quote": "原告：张三"},
                    {"role": "被告", "entityType": "自然人", "name": "李四", "aliases": ["被告"], "quote": "被告：李四"},
                ]
            ),
            doc,
        )
        projected = subjects_for_prompt(subjects)
        self.assertEqual(projected[0]["subjectId"], "P1")
        self.assertEqual(projected[0]["outputPointer"], "/原告/0")
        self.assertEqual(projected[1]["role"], "被告")


if __name__ == "__main__":
    unittest.main()
