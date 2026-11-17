"""
启煊智能体 - 模型路由器单元测试
"""

import os
import pytest


def test_default_routing_uses_primary():
    """默认 profile 应路由至主大脑模型（GLM-5.1）。"""
    from governance.router import resolve_model

    config = resolve_model(profile="default")
    assert config["model"] == "zai-org/GLM-5.1"
    assert "primary" in config.get("routing_reason", "").lower()


def test_confidential_profile_forces_local():
    """涉密 profile 必须强制路由至内网本地节点，不得使用云端模型。"""
    from governance.router import resolve_model

    config = resolve_model(profile="confidential_client_a")
    assert "localhost" in config["base_url"] or "qixuan-local-vllm" in config["base_url"] or "local" in config["base_url"]
    assert "涉密" in config.get("routing_reason", "") or "confidential" in config.get("routing_reason", "").lower()


def test_skill_override_code_reviewer():
    """代码审查技能应路由至 Qwen3-235B 编码模型。"""
    from governance.router import resolve_model

    config = resolve_model(profile="default", active_skill="qixuan-code-reviewer")
    assert "Qwen" in config["model"] or "qwen" in config["model"].lower()


def test_skill_override_req_analyzer():
    """需求分析技能应路由至编码模型。"""
    from governance.router import resolve_model

    config = resolve_model(profile="default", active_skill="qixuan-req-analyzer")
    assert "Qwen" in config["model"] or "qwen" in config["model"].lower()


def test_confidential_overrides_skill():
    """涉密 profile 的优先级应高于技能覆盖规则。"""
    from governance.router import resolve_model

    # 即使指定了 coding skill，涉密 profile 也应强制走本地节点
    config = resolve_model(profile="confidential_mes_project", active_skill="qixuan-code-reviewer")
    assert "qixuan-local-vllm" in config["base_url"] or "local" in config["base_url"]


def test_explicit_role_override():
    """显式 override_role 参数应被正确尊重（非涉密场景）。"""
    from governance.router import resolve_model

    config = resolve_model(profile="default", override_role="fast")
    assert "DeepSeek" in config["model"] or "deepseek" in config["model"].lower()
    assert ":cheapest" in config["model"]


def test_routing_summary_format():
    """路由摘要应包含关键信息字段。"""
    from governance.router import get_routing_summary

    summary = get_routing_summary(profile="default", active_skill="qixuan-ops-sentinel")
    assert "Profile" in summary
    assert "模型" in summary
    assert "决策原因" in summary


def test_is_confidential_detection():
    """涉密 profile 检测逻辑。"""
    from governance.router import _is_confidential

    assert _is_confidential("confidential") is True
    assert _is_confidential("confidential_client_a") is True
    assert _is_confidential("confidential_mes") is True
    assert _is_confidential("default") is False
    assert _is_confidential("production") is False
    assert _is_confidential("conf") is False  # 不是完整前缀匹配
