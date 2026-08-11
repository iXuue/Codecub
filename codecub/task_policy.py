from pathlib import Path


CODE_EXPLANATION_MARKERS = ("怎么做", "如何实现", "怎么实现", "调用链", "架构", "原理", "how does", "how is", "implementation")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp"}


def requires_source_evidence(user_message):
    text = str(user_message or "").lower()
    return any(marker in text for marker in CODE_EXPLANATION_MARKERS)


def is_source_path(path):
    return Path(str(path or "")).suffix.lower() in SOURCE_SUFFIXES


def evidence_retry_notice():
    return (
        "Runtime notice: this code-explanation task has no source-file evidence from this run. "
        "Read a relevant implementation file before returning <final>."
    )
