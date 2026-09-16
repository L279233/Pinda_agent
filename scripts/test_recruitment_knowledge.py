import tempfile
import unittest
import uuid
from pathlib import Path

from scripts.build_knowledge_base import (
    annotate_recruitment_metadata,
    load_document,
    resolve_document_id,
    split_documents,
)


class RecruitmentKnowledgeImportTest(unittest.TestCase):
    def test_markdown_dry_run_path_uses_no_external_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "faq.md"
            path.write_text("# 招聘 FAQ\n\n## 流程\n\n简历通过后会通知笔试。", encoding="utf-8")
            docs = load_document(str(path))
            chunks = split_documents(docs, str(path))
            annotated = annotate_recruitment_metadata(
                chunks,
                tenant_id="tenant-1",
                position_id=None,
                document_type="faq",
            )
            self.assertTrue(annotated)
            self.assertEqual(annotated[0].metadata["document_type"], "faq")
            self.assertEqual(annotated[0].metadata["position_id"], "")

    def test_position_jd_requires_valid_position_id(self):
        with self.assertRaises(ValueError):
            annotate_recruitment_metadata(
                [], tenant_id="tenant-1", position_id=None,
                document_type="position_jd",
            )
        with self.assertRaises(ValueError):
            annotate_recruitment_metadata(
                [], tenant_id="tenant-1", position_id="not-a-uuid",
                document_type="position_jd",
            )

    def test_generated_document_id_is_stable_and_tenant_scoped(self):
        first = resolve_document_id("faq.md", "tenant-1")
        second = resolve_document_id("faq.md", "tenant-1")
        other_tenant = resolve_document_id("faq.md", "tenant-2")
        uuid.UUID(first)
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_tenant)


if __name__ == "__main__":
    unittest.main()
