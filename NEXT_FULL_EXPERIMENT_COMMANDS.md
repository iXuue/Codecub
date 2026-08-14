# CodeCub 全量真实模型实验

先在已配置真实 provider/model 的终端中进行每组 1 task × 1 repeat 的 Stage 1；不要使用 FakeModelClient：

```powershell
python -m codecub.experiments --suite real-agent --task flag_memory_default --repeat 1 --provider openai --model <MODEL>
python -m codecub.experiments --suite context --variant off --task flag_context_default --repeat 1 --provider openai --model <MODEL>
python -m codecub.experiments --suite memory --variant on --task flag_memory_default --repeat 1 --provider openai --model <MODEL>
python -m codecub.experiments --suite recovery --task invalid_patch_failure --repeat 1 --provider openai --model <MODEL>
```

检查每个 `artifacts/experiments/<run>/` 下的 `manifest.json`、`runs.jsonl`、`summary.json`、`report.md` 后，再运行完整实验：

```powershell
python -m codecub.experiments --suite real-agent --repeat 3 --provider openai --model <MODEL>
python -m codecub.experiments --suite context --variant off --repeat 3 --provider openai --model <MODEL>
python -m codecub.experiments --suite context --variant on --repeat 3 --provider openai --model <MODEL>
python -m codecub.experiments --suite memory --variant off --repeat 3 --provider openai --model <MODEL>
python -m codecub.experiments --suite memory --variant on --repeat 3 --provider openai --model <MODEL>
python -m codecub.experiments --suite recovery --repeat 3 --provider openai --model <MODEL>
```

中断后，用相同的 `--suite` 和 `--output-dir` 加 `--resume` 续跑。无 API 配置时只可使用 `--dry-run` 验证隔离、任务 mutation 和工件结构；dry-run 不产生真实能力数据。
