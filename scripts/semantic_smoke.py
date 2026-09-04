"""Low-cost real-provider smoke test for semantic retrieval."""
import json
from pathlib import Path

from codecub.cli import load_env_file
from codecub.code_index import CodeIndex
from codecub.retrieval import HybridRetriever


def main():
    load_env_file(".")
    root = Path(".").resolve()
    result = HybridRetriever(root, CodeIndex(root)).retrieve(
        "where is tool validation enforced", limit=5
    )
    print(json.dumps({"strategy": result.strategy, "semantic": result.semantic_applied, "rerank": result.rerank_applied, "hits": [(hit.path, hit.start_line) for hit in result.hits], "filtered_out": result.filtered_out, "elapsed_ms": result.elapsed_ms}))


if __name__ == "__main__":
    main()
