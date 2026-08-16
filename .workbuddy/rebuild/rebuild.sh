#!/usr/bin/env bash
# Phase 1.5 — 重建正式实验历史 + Phase 1 commit（方案 C：等价重建）
# 全程使用临时 index，不触碰工作区文件。
set -euo pipefail
cd "D:/代码备份/pico/pico-main"
R=".workbuddy/rebuild"

AUTHOR="iXuue <425096798@qq.com>"
TS_34F4F8C="1786767709 +0800"   # 2026-08-15 12:21:49
TS_7621201="1786768901 +0800"   # 2026-08-15 12:41:41

hash_file() { git hash-object -w "$1"; }

# ---------- Tree A: 等价 34f4f8c (formal holdout) ----------
export GIT_INDEX_FILE="$R/index_A"
git read-tree d5600f0^{tree}
git update-index --add --cacheinfo 100644 "$(hash_file $R/tasks_A.py)" codecub/experiments/tasks.py
git update-index --add --cacheinfo 100644 "$(hash_file $R/runner_A.py)" codecub/experiments/runner.py
git update-index --add --cacheinfo 100644 "$(hash_file $R/test_A.py)" tests/test_experiments.py
TREE_A=$(git write-tree)
echo "TREE_A=$TREE_A"

# ---------- Tree B: 等价 7621201 (windows workspace path) ----------
export GIT_INDEX_FILE="$R/index_B"
git read-tree "$TREE_A"
git update-index --add --cacheinfo 100644 "$(hash_file $R/runner_B.py)" codecub/experiments/runner.py
git update-index --add --cacheinfo 100644 "$(hash_file $R/test_B.py)" tests/test_experiments.py
TREE_B=$(git write-tree)
echo "TREE_B=$TREE_B"

# ---------- Tree C: restore pre-phase1 working tree（用户未提交状态）----------
export GIT_INDEX_FILE="$R/index_C"
git read-tree "$TREE_B"
for pair in \
  "codecub/connections/presets.py" \
  "codecub/connections/schema.py" \
  "codecub/models.py" \
  "codecub/tools.py"; do
  git update-index --add --cacheinfo 100644 "$(hash_file "$pair")" "$pair"
done
git update-index --add --cacheinfo 100644 "$(hash_file $R/runtime_C.py)" codecub/runtime.py
git update-index --add --cacheinfo 100644 "$(hash_file $R/testpico_C.py)" tests/test_pico.py
TREE_C=$(git write-tree)
echo "TREE_C=$TREE_C"

# ---------- Tree D: Phase 1 long-horizon runtime ----------
export GIT_INDEX_FILE="$R/index_D"
git read-tree "$TREE_C"
git update-index --add --cacheinfo 100644 "$(hash_file $R/watchdog_D.py)" codecub/watchdog.py
git update-index --add --cacheinfo 100644 "$(hash_file $R/runtime_D.py)" codecub/runtime.py
git update-index --add --cacheinfo 100644 "$(hash_file $R/cli_D.py)" codecub/cli.py
git update-index --add --cacheinfo 100644 "$(hash_file $R/metrics_D.py)" codecub/experiments/metrics.py
git update-index --add --cacheinfo 100644 "$(hash_file $R/runner_D.py)" codecub/experiments/runner.py
git update-index --add --cacheinfo 100644 "$(hash_file $R/testpico_D.py)" tests/test_pico.py
git update-index --add --cacheinfo 100644 "$(hash_file $R/testwatchdog_D.py)" tests/test_watchdog.py
TREE_D=$(git write-tree)
echo "TREE_D=$TREE_D"

# ---------- Commits ----------
GIT_AUTHOR_NAME="iXuue" GIT_AUTHOR_EMAIL="425096798@qq.com" GIT_AUTHOR_DATE="$TS_34F4F8C" \
GIT_COMMITTER_NAME="iXuue" GIT_COMMITTER_EMAIL="425096798@qq.com" GIT_COMMITTER_DATE="$TS_34F4F8C" \
  COMMIT_A=$(git commit-tree "$TREE_A" -p d5600f0 -m "Add fresh formal experiment holdout")
echo "COMMIT_A=$COMMIT_A"

GIT_AUTHOR_NAME="iXuue" GIT_AUTHOR_EMAIL="425096798@qq.com" GIT_AUTHOR_DATE="$TS_7621201" \
GIT_COMMITTER_NAME="iXuue" GIT_COMMITTER_EMAIL="425096798@qq.com" GIT_COMMITTER_DATE="$TS_7621201" \
  COMMIT_B=$(git commit-tree "$TREE_B" -p "$COMMIT_A" -m "Shorten experiment workspace paths on Windows")
echo "COMMIT_B=$COMMIT_B"

COMMIT_C=$(git commit-tree "$TREE_C" -p "$COMMIT_B" -m "restore pre-phase1 working tree state (native tool-calling related local changes)")
echo "COMMIT_C=$COMMIT_C"

COMMIT_D=$(git commit-tree "$TREE_D" -p "$COMMIT_C" -m "feat: add long-horizon runtime watchdog")
echo "COMMIT_D=$COMMIT_D"

echo "$COMMIT_A" > "$R/commit_A.txt"
echo "$COMMIT_B" > "$R/commit_B.txt"
echo "$COMMIT_C" > "$R/commit_C.txt"
echo "$COMMIT_D" > "$R/commit_D.txt"
echo "===REBUILD_TREES_COMMITS_DONE==="
