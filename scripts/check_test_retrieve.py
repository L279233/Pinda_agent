import sys, asyncio
sys.path.insert(0, ".")

from backend.core.knowledge_base import BGEMEmbedder, KnowledgeBaseClient

async def dm_test_retrieve():
    query = "AI的课程大纲是什么"

    embedder = BGEMEmbedder.get_instance()
    dense, sparse = embedder.encode_query(query)

    kb = KnowledgeBaseClient()
    result = await kb.retrieve(
        query=query,
        tenant_id="tenant_default",
        query_embedding=dense,
        query_sparse_embedding=sparse,
        top_k=3,
    )

    print(f"置信度          ：{result.confidence:.4f}")
    print(f"高置信度        ：{result.is_high_confidence}  （阈值 0.75）")
    print(f"Hybrid 召回数   ：{result.domain_hits}")
    print(f"精排后返回      ：{len(result.documents)} 条")

    for i, doc in enumerate(result.documents):
        print(f"\n  [{i+1}] score={doc.score:.4f}  来源：{doc.metadata.get('source_name','')}")
        print(f"       {doc.content[:80]}...")

    # score 必须在 [0,1]（验证没有 raw logit 泄漏）
    for doc in result.documents:
        assert 0.0 <= doc.score <= 1.0, f"score 超出 [0,1]：{doc.score}"
    print("\n✅ Reranker score 全部在 [0,1] 范围内")


asyncio.run(dm_test_retrieve())