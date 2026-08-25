from pathlib import Path
import posixpath
import re


CODE_EXPLANATION_MARKERS = ("怎么做", "如何实现", "怎么实现", "调用链", "架构", "原理", "how does", "how is", "implementation")
SOURCE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".c", ".cc", ".cpp", ".h", ".hpp"}


def requires_source_evidence(user_message):
    text = str(user_message or "").lower()
    return any(marker in text for marker in CODE_EXPLANATION_MARKERS)


def research_tool_budget(user_message):
    return 6 if requires_source_evidence(user_message) else None


def is_research_tool(name):
    return name in {"list_files", "search", "read_file"}


EXPLORATION_TOOLS = {"list_files", "search", "read_file"}
ACTION_TOOLS = {"patch_file", "write_file"}


def canonical_path(path):
    return posixpath.normpath(str(path or "").replace("\\", "/")).casefold()


def normalize_search(args):
    pattern = re.sub(r"\s+", " ", str((args or {}).get("pattern", "")).strip()).casefold()
    return canonical_path((args or {}).get("path", ".")), pattern


def normalize_shell_command(args):
    """Normalize a shell command for same-workspace-state verification checks."""
    command = str((args or {}).get("command", "")).strip()
    return re.sub(r"\s+", " ", command).casefold()


def read_overlap_ratio(first, second):
    """Return overlap relative to the shorter inclusive line range."""
    start_a, end_a = int(first.get("start", 1)), int(first.get("end", 200))
    start_b, end_b = int(second.get("start", 1)), int(second.get("end", 200))
    intersection = max(0, min(end_a, end_b) - max(start_a, start_b) + 1)
    shorter = min(end_a - start_a + 1, end_b - start_b + 1)
    return intersection / shorter if shorter > 0 else 0.0


def is_semantic_repeat(name, args, prior_name, prior_args, overlap_threshold=0.8):
    if name == "search" and prior_name == "search":
        return normalize_search(args) == normalize_search(prior_args)
    if name == "read_file" and prior_name == "read_file":
        return canonical_path(args.get("path")) == canonical_path(prior_args.get("path")) and read_overlap_ratio(args, prior_args) >= overlap_threshold
    return False


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
