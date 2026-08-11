from pathlib import Path


CODE_EXPLANATION_MARKERS = ("怎么做", "如何实现", "怎么实现", "调用链", "架构", "原理", "how does", "how is", "implementation")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp"}


def requires_source_evidence(user_message):
    text = str(user_message or "").lower()
    return any(marker in text for marker in CODE_EXPLANATION_MARKERS)


def research_tool_budget(user_message):
    return 6 if requires_source_evidence(user_message) else None


def is_research_tool(name):
    return name in {"list_files", "search", "read_file"}


def is_source_path(path):
    return Path(str(path or "")).suffix.lower() in SOURCE_SUFFIXES


def evidence_retry_notice():
    return (
        "Runtime notice: this code-explanation task has no source-file evidence from this run. "
        "Read a relevant implementation file before returning <final>."
    )


def finalization_notice(source_reads, used, budget):
    paths = ", ".join(sorted(set(source_reads))) or "(none)"
    return (
        "Runtime notice: research evidence is sufficient and the research budget is exhausted "
        f"({used}/{budget}). Source evidence: {paths}. Return one <final> answer now; do not call more tools."
    )
