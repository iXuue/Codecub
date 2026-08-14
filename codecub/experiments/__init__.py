"""真实模型 Coding Agent 实验框架。

本包只编排隔离工作区中的真实模型运行；既有 evaluator.py 的
FakeModelClient Harness Regression 仍是独立的确定性回归基线。
"""

from .runner import ExperimentConfig, ExperimentRunner

__all__ = ["ExperimentConfig", "ExperimentRunner"]
