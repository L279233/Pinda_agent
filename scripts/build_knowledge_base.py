from pathlib import Path
from langchain_core.documents import Document
from langchain_text_splitters import (
    MarkdownHeaderTextSplitter,
    RecursiveCharacterTextSplitter,
)
import argparse
import asyncio
import sys
import uuid
import fitz
sys.path.insert(0, str(Path(__file__).parent.parent))

from backend.core.knowledge_base import KnowledgeBaseClient
from backend.core.llm_factory import get_llm


# ── 模块级分块器单例 ──────────────────────────────────────────
_MD_HEADER_SPLITTER = MarkdownHeaderTextSplitter(
    headers_to_split_on=[
        ("#",   "H1"),
        ("##",  "H2"),
        ("###", "H3"),
    ],
    strip_headers=False,
)

_CHAR_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size=512,
    chunk_overlap=100,
    separators=["\n\n", "\n", "。", "，", " ", ""],
)


# ── 文档加载（5.2 内容，此处合并为完整文件）────────────────────

def load_document(file_path: str) -> list[Document]:
    """统一文档加载入口，根据扩展名选择 Loader"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")
    ext = path.suffix.lower()
    if ext == ".pdf":
        pdf = fitz.open(file_path)
        pages = [
            Document(
                page_content=page.get_text(),
                metadata={"source": file_path, "page": page.number},
            )
            for page in pdf
        ]
        pdf.close()
        print(f"  [PDF] 加载完成：{len(pages)} 页 ← {path.name}")
        return pages
    elif ext in (".md", ".markdown"):
        docs = [Document(
            page_content=Path(file_path).read_text(encoding="utf-8"),
            metadata={"source": file_path},
        )]
        print(f"  [MD]  加载完成：{len(docs[0].page_content)} 字符 ← {path.name}")
        return docs
    else:
        raise ValueError(
            f"不支持的文件类型：{ext}\n"
            f"当前支持：.pdf / .md / .markdown\n"
            f"提示：可用 markitdown 将 Word/PPT 转换为 .md 后再导入"
        )


# ── PDF 分块 ──────────────────────────────────────────────────

def split_pdf_documents(pages: list[Document]) -> list[Document]:
    """PDF 文档分块：过滤空页 + RecursiveCharacterTextSplitter"""
    non_empty_pages = [p for p in pages if len(p.page_content.strip()) > 20]
    skipped = len(pages) - len(non_empty_pages)
    if skipped > 0:
        print(f"  过滤空页：{skipped} 页（图片/扫描件页）")

    chunks = _CHAR_SPLITTER.split_documents(non_empty_pages)

    for chunk in chunks:
        filename = Path(chunk.metadata.get("source", "未知文件")).stem
        page_num = chunk.metadata.get("page", 0) + 1
        chunk.metadata["source_name"] = f"{filename} 第{page_num}页"

    print(f"  [PDF] 分块完成：{len(non_empty_pages)} 页 → {len(chunks)} 个 chunk")
    return chunks


# ── Markdown 分块 ─────────────────────────────────────────────

def split_markdown_documents(docs: list[Document]) -> list[Document]:
    """Markdown 文档分块：MarkdownHeaderTextSplitter + 二次递归切分"""
    header_chunks: list[Document] = []
    for doc in docs:
        sections = _MD_HEADER_SPLITTER.split_text(doc.page_content)
        source_path = doc.metadata.get("source", "")
        for section in sections:
            section.metadata["source"] = source_path
        header_chunks.extend(sections)
    # print(f'header_chunks-->{len(header_chunks)}')
    # print(f'header_chunks-->{header_chunks[0]}')
    final_chunks = _CHAR_SPLITTER.split_documents(header_chunks)

    for chunk in final_chunks:
        source_path = chunk.metadata.get("source", "")
        filename    = Path(source_path).stem if source_path else "未知文件"
        parts = [
            chunk.metadata.get("H1", ""),
            chunk.metadata.get("H2", ""),
            chunk.metadata.get("H3", ""),
        ]
        parts = [p for p in parts if p]
        chunk.metadata["source_name"] = (
            f"{filename} > {' > '.join(parts)}" if parts else filename
        )
    print(f"  [MD]  分块完成：{len(docs)} 个文件 → {len(final_chunks)} 个 chunk")
    return final_chunks


# ── 统一分块入口 ──────────────────────────────────────────────

def split_documents(docs: list[Document], file_path: str) -> list[Document]:
    """统一分块入口，根据文件类型自动选择分块策略"""
    ext = Path(file_path).suffix.lower()
    if ext == ".pdf":
        return split_pdf_documents(docs)
    elif ext in (".md", ".markdown"):
        return split_markdown_documents(docs)
    else:
        raise ValueError(f"不支持的文件类型：{ext}")


def annotate_recruitment_metadata(
    chunks: list[Document], *, tenant_id: str, position_id: str | None,
    document_type: str,
) -> list[Document]:
    """Attach retrieval-scope metadata to every chunk before embedding."""
    allowed_types = {"position_jd", "policy", "company", "faq", "question_bank"}
    if document_type not in allowed_types:
        raise ValueError(f"document_type 必须是：{', '.join(sorted(allowed_types))}")
    if not tenant_id.strip():
        raise ValueError("tenant_id 不能为空")
    if document_type == "position_jd" and not position_id:
        raise ValueError("position_jd 文档必须提供 position_id")
    if position_id:
        uuid.UUID(position_id)
    for chunk in chunks:
        chunk.metadata.update({
            "tenant_id": tenant_id,
            "position_id": position_id or "",
            "document_type": document_type,
        })
    return chunks


def resolve_document_id(
    file_path: str, tenant_id: str, explicit_id: str | None = None
) -> str:
    """Return a validated explicit ID or a stable ID for repeatable imports."""
    if explicit_id:
        return str(uuid.UUID(explicit_id))
    source_key = f"{tenant_id}:{Path(file_path).resolve().as_posix().lower()}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, source_key))



import uuid
import sys
from pathlib import Path

# 把项目根目录加入 Python 路径，使得 build_knowledge_base.py 能 import backend.*
sys.path.insert(0, str(Path(__file__).parent.parent))

from langchain_core.documents import Document
from backend.core.knowledge_base import BGEMEmbedder, DocumentChunk, generate_chunk_id

BATCH_SIZE = 12   # BGE-M3 批量推理大小（12 = 速度与显存的经验平衡点）


def embed_chunks(
    chunks: list[Document],
    course_id: str,
    document_id: str,
    tenant_id: str = "tenant_default",
    version: str = "1.0",
    document_type: str = "policy",
    position_id: str | None = None,
) -> list[DocumentChunk]:
    """
    对 split_documents() 产出的 chunk 列表做 BGE-M3 嵌入，返回 DocumentChunk 列表。

    BGE-M3 推理为 CPU / GPU-bound，按 BATCH_SIZE 批量处理：
    - 减少模型推理次数（每次推理有固定启动开销）
    - 控制显存/内存峰值（整批一次性推理会爆显存）

    Args:
        chunks:      split_documents() 返回的 list[Document]
        course_id:   所属课程 UUID
        document_id: knowledge_documents 表中的 UUID（用于幂等更新）
        tenant_id:   租户 ID，用于 Milvus 多租户过滤
        version:     课程版本号

    Returns:
        list[DocumentChunk]，每项包含 dense + sparse 向量，可直接写入 Milvus
    """
    embedder = BGEMEmbedder.get_instance()   # 单例，首次调用加载模型
    all_doc_chunks: list[DocumentChunk] = []

    total = len(chunks)
    for batch_start in range(0, total, BATCH_SIZE):
        batch = chunks[batch_start: batch_start + BATCH_SIZE]
        texts = [c.page_content for c in batch]

        # BGE-M3 批量推理：同时拿到 dense 和 sparse
        dense_vecs, sparse_vecs = embedder.encode(texts, batch_size=BATCH_SIZE)

        for i, (chunk, dense, sparse) in enumerate(zip(batch, dense_vecs, sparse_vecs)):
            global_index = batch_start + i    # 在整个文档中的顺序编号

            all_doc_chunks.append(DocumentChunk(
                id=generate_chunk_id(chunk.page_content, document_id, global_index),
                content=chunk.page_content,
                embedding=dense,
                sparse_embedding=sparse,
                course_id=course_id,
                document_id=document_id,
                source_name=chunk.metadata.get("source_name", ""),
                chunk_type=chunk.metadata.get("chunk_type", "text"),
                chunk_index=global_index,
                version=version,
                tenant_id=tenant_id,
                document_type=document_type,
                position_id=position_id or "",
            ))

        done = min(batch_start + BATCH_SIZE, total)
        print(f"  嵌入进度：{done}/{total}")

    print(f"  嵌入完成：{len(all_doc_chunks)} 个 DocumentChunk")
    return all_doc_chunks

# ── Step 2.5：Contextual RAG 上下文增强 ─────────────────────
# ── 常量 ─────────────────────────────────────────────────────

MAX_CONTEXT_CONCURRENCY = 5    # Contextual 上下文生成的最大并发 LLM 请求数

CONTEXTUAL_CHUNK_PROMPT = """\
<document>
{document_text}
</document>

以下是需要在整个文档中定位的 chunk：
<chunk>
{chunk_content}
</chunk>

请用一句简洁的中文，描述这段内容在整个文档中的位置和作用，以便改善检索效果。
只输出这一句描述，不要加任何前缀或标签。"""

async def generate_chunk_context(
    llm,
    document_text: str,
    chunk_content: str,
    semaphore: asyncio.Semaphore,
) -> str:
    """
    用 LLM 为单个 chunk 生成一句定位描述。

    失败时返回空字符串，调用方保留原始 chunk 文本（降级处理）。

    Args:
        llm:           DeepSeek LLM 实例（via get_llm）
        document_text: 整篇文档全文（截断至 8000 字）
        chunk_content: 当前 chunk 的原始文本
        semaphore:     并发限流（最多 MAX_CONTEXT_CONCURRENCY 个 LLM 请求同时进行）
    """
    async with semaphore:
        try:
            from langchain_core.messages import HumanMessage
            prompt = CONTEXTUAL_CHUNK_PROMPT.format(
                document_text=document_text,
                chunk_content=chunk_content,
            )
            resp = await llm.ainvoke([HumanMessage(content=prompt)])
            ctx = (
                resp.text
                if hasattr(resp, "text") and not callable(resp.text)
                else str(resp.content)
            ).strip()
            return ctx
        except Exception as e:
            print(f"   [warning] 上下文生成失败，保留原始 chunk：{e}")
            return ""


async def add_context(
    chunks: list[Document],
    docs: list[Document],
    concurrency: int = MAX_CONTEXT_CONCURRENCY,
) -> list[Document]:
    """
    Contextual RAG：并发为所有 chunk 生成上下文描述，拼接到 chunk 文本前方。

    拼接后格式：
        "<上下文描述一句话>\\n\\n<原始 chunk 文本>"

    拼接后再做嵌入（embed_chunks），向量同时编码"在哪里"和"说了什么"两层信息。

    Args:
        chunks:      split_documents() 输出的 list[Document]
        docs:        load_document() 输出的原始 list[Document]（用于构建全文参考）
        concurrency: 最大并发 LLM 请求数（默认 5，防止触发 API 限流）

    Returns:
        page_content 已被就地修改（拼接上下文）的 list[Document]
    """
    # 拼接全文供 LLM 参考（截断 8000 字，避免超出模型 context 长度）
    full_doc_text = "\n\n".join(d.page_content for d in docs)[:8000]

    llm       = get_llm("qa", temperature=0)
    semaphore = asyncio.Semaphore(concurrency)

    # 并发调用 LLM，为每个 chunk 生成上下文描述
    contexts = await asyncio.gather(*[
        generate_chunk_context(llm, full_doc_text, c.page_content, semaphore)
        for c in chunks
    ])

    enriched = 0
    for chunk, ctx in zip(chunks, contexts):
        if ctx:
            chunk.page_content = f"{ctx}\n\n{chunk.page_content}"
            enriched += 1

    print(f"  上下文增强完成：{enriched}/{len(chunks)} 个 chunk 已添加描述")
    return chunks


# ── Step 4：写入 Milvus ────────────────────────────────────────

def write_to_milvus(doc_chunks: list[DocumentChunk]) -> None:
    """
    将 embed_chunks() 产出的 DocumentChunk 列表写入 Milvus。

    先按 document_id 删除同文档旧版本 chunk，再批量 upsert，
    保证文档更新时不残留旧数据。
    """
    if not doc_chunks:
        print("  [warning] 无 chunk 可写入，跳过")
        return

    kb = KnowledgeBaseClient()
    document_id = doc_chunks[0].document_id

    print(f"  删除旧版本 chunk（document_id={document_id[:8]}）")
    kb.delete_document_chunks(document_id)

    written = kb.upsert_chunks(doc_chunks)
    print(f"  写入完成：{written} 个 chunk -> knowledge_domain")


# ── 主流水线 ─────────────────────────────────────────────────

async def build_pipeline(
    file_path:   str,
    course_id:   str,
    document_id: str,
    tenant_id:   str = "tenant_default",
    version:     str = "1.0",
    use_context: bool = True,
    document_type: str = "policy",
    position_id: str | None = None,
    dry_run: bool = False,
) -> int:
    """
    知识库建库完整流水线（五步）：

      Step 1   读取文档（PyPDFLoader / TextLoader）
      Step 2   智能分块（MarkdownHeaderTextSplitter / RecursiveCharacterTextSplitter）
      Step 2.5 Contextual RAG 上下文增强（LLM 并发，可跳过）
      Step 3   BGE-M3 嵌入（dense + sparse 双向量）
      Step 4   写入 Milvus（MilvusClient upsert）
    """
    print(f"\n{'='*55}")
    print(f" 聘达招聘知识库构建")
    print(f" 文件      ：{file_path}")
    print(f" 岗位范围  ：{position_id or '租户公共资料'}")
    print(f" 文档类型  ：{document_type}")
    print(f" 文档 ID   ：{document_id}")
    print(f" 租户      ：{tenant_id}")
    print(f" Contextual RAG：{'启用' if use_context else '跳过（--no-context）'}")
    print(f"{'='*55}\n")

    # Step 1：读取
    print("Step 1/4  读取文档...")
    docs = load_document(file_path)

    # Step 2：分块
    print("\nStep 2/4  智能分块...")
    chunks = split_documents(docs, file_path)
    chunks = annotate_recruitment_metadata(
        chunks, tenant_id=tenant_id, position_id=position_id,
        document_type=document_type,
    )

    if dry_run:
        print(f"  [dry-run] 文档类型={document_type}，岗位={position_id or '租户公共资料'}，chunk={len(chunks)}")
        return len(chunks)

    # Step 2.5：Contextual RAG（可选）
    if use_context and chunks:
        print(f"\nStep 2.5  Contextual RAG 上下文增强"
              f"（并发={MAX_CONTEXT_CONCURRENCY}）…")
        chunks = await add_context(chunks, docs)

    # Step 3：嵌入
    print("\nStep 3/4  BGE-M3 嵌入...")
    doc_chunks = embed_chunks(
        chunks,
        course_id=course_id,
        document_id=document_id,
        tenant_id=tenant_id,
        version=version,
        document_type=document_type,
        position_id=position_id,
    )
    # print(f'doc_chunks-->{doc_chunks}')
    # Step 4：写入
    print("\nStep 4/4  写入 Milvus...")
    write_to_milvus(doc_chunks)

    print(f"\n完成！共处理 {len(doc_chunks)} 个 chunk")
    print(f"   document_id = {document_id}")
    print(f"   [note] 更新此文档时请保留此 document_id")
    return len(doc_chunks)
if __name__ == '__main__':
    # file_path = "/Users/ligang/PycharmProjects/LLM/EduAgent/samples/ocr_01.md"
    # docs = load_document(file_path)
    # results = split_documents(docs, file_path)
    # course_id=str(uuid.uuid4()),
    # document_id=str(uuid.uuid4()),
    # results = embed_chunks(chunks=results,course_id=course_id,document_id=document_id)
    # print(len(results))
    # print(results[0])
    parser = argparse.ArgumentParser(
        description="EduAgent 知识库构建：PDF / Markdown 导入 Milvus"
    )
    parser.add_argument("--file",
                        help="文档路径（.pdf / .md / .markdown）")
    parser.add_argument("--directory",
                        help="批量扫描目录（递归读取 .pdf/.md/.markdown）")
    parser.add_argument("--course_id", default=None,
                        help="旧参数兼容；招聘资料请使用 --position-id")
    parser.add_argument("--document_id", default=None,
                        help="文档 UUID（不传则自动生成；更新同一文档时传相同 ID）")
    parser.add_argument("--tenant_id", default="tenant_default",
                        help="租户 ID（默认 tenant_default）")
    parser.add_argument("--version", default="1.0",
                        help="课程版本号（默认 1.0）")
    parser.add_argument("--position-id", default=None,
                        help="岗位 UUID；不传表示租户公共资料")
    parser.add_argument("--document-type", default="policy",
                        choices=["position_jd", "policy", "company", "faq", "question_bank"],
                        help="招聘文档类型")
    parser.add_argument("--dry-run", action="store_true",
                        help="只加载和分块，不加载嵌入模型、不连接 Milvus")
    parser.add_argument("--no-context", action="store_true",
                        help="跳过 Contextual RAG 上下文生成（快速调试用）")
    args = parser.parse_args()

    if bool(args.file) == bool(args.directory):
        parser.error("--file 和 --directory 必须且只能提供一个")
    paths = [Path(args.file)] if args.file else sorted(
        p for p in Path(args.directory).rglob("*")
        if p.is_file() and p.suffix.lower() in {".pdf", ".md", ".markdown"}
    )
    if not paths:
        parser.error("目录中没有可导入的 .pdf/.md/.markdown 文件")
    if args.directory and args.document_id:
        parser.error("目录批量导入不能共用 --document_id")
    async def run_all():
        for path in paths:
            await build_pipeline(
                file_path=str(path),
                course_id=args.position_id or args.course_id or "",
                document_id=resolve_document_id(
                    str(path), args.tenant_id, args.document_id
                ),
                tenant_id=args.tenant_id,
                version=args.version,
                use_context=not args.no_context,
                document_type=args.document_type,
                position_id=args.position_id,
                dry_run=args.dry_run,
            )
    asyncio.run(run_all())
