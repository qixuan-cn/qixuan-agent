"""
启煊智能体 - 安全模型路由器
功能：
  1. 根据会话 Profile 的 security_level 决定路由目标（云端 HF 或内网 vLLM）
  2. 根据活跃技能名称自动切换最优模型（coding / fast / primary）
  3. 涉密 Profile 强制断网，仅允许访问内网端点

路由决策优先级：
  confidential profile  →  confidential_local（内网 vLLM，数据零出网）
  skill override        →  skill 指定的 model 角色（如 coding）
  default               →  primary（主大脑模型）
"""

import os
from typing import Optional

# ---------------------------------------------------------------------------
# 模型端点配置（从环境变量加载，与 config.yaml 保持一致）
# ---------------------------------------------------------------------------
MODEL_CONFIGS = {
    "primary": {
        "provider": "huggingface",
        "model": "zai-org/GLM-5.1",
        "base_url": os.environ.get("HF_BASE_URL", "https://router.huggingface.co/v1"),
        "api_key_env": "HF_TOKEN",
        "temperature": 0.2,
        "max_tokens": 16384,
    },
    "coding": {
        "provider": "huggingface",
        "model": "Qwen/Qwen3-235B-A22B",
        "base_url": os.environ.get("HF_BASE_URL", "https://router.huggingface.co/v1"),
        "api_key_env": "HF_TOKEN",
        "temperature": 0.1,
        "max_tokens": 32768,
    },
    "fast": {
        "provider": "huggingface",
        "model": "deepseek-ai/DeepSeek-V4-Flash:cheapest",
        "base_url": os.environ.get("HF_BASE_URL", "https://router.huggingface.co/v1"),
        "api_key_env": "HF_TOKEN",
        "temperature": 0.1,
        "max_tokens": 4096,
    },
    "confidential_local": {
        "provider": "openai_compatible",
        "model": os.environ.get("LOCAL_VLLM_MODEL", "google/gemma-4-26b-a4b-it"),
        "base_url": os.environ.get("LOCAL_VLLM_HOST", "http://qixuan-local-vllm:8000/v1"),
        "api_key_env": "LOCAL_VLLM_API_KEY",
        "temperature": 0.1,
        "max_tokens": 8192,
    },
}

# 技能 -> 模型角色的覆盖映射
SKILL_MODEL_OVERRIDES: dict[str, str] = {
    "qixuan-req-analyzer": "coding",
    "qixuan-code-reviewer": "coding",
    "qixuan-ops-sentinel": "primary",
    "qixuan-release-notes": "coding",
}

# 涉密 Profile 标识（以此前缀命名的 profile 强制走本地节点）
CONFIDENTIAL_PROFILE_PREFIX = "confidential"


# ---------------------------------------------------------------------------
# 路由核心函数
# ---------------------------------------------------------------------------

def resolve_model(
    profile: str = "default",
    active_skill: Optional[str] = None,
    override_role: Optional[str] = None,
) -> dict:
    """
    根据当前 profile 和活跃技能，返回应使用的模型配置。

    Args:
        profile:       当前 Profile 名称。
        active_skill:  当前活跃的技能名称（可选）。
        override_role: 强制指定模型角色，优先级高于技能覆盖但低于涉密强制（可选）。

    Returns:
        包含模型连接信息的配置字典（已注入实际 API Key）。
    """
    # 1. 涉密 Profile 强制使用本地节点，优先级最高
    if _is_confidential(profile):
        config = MODEL_CONFIGS["confidential_local"].copy()
        config["routing_reason"] = f"profile='{profile}' 标记为涉密，强制使用内网本地节点"
        return _inject_api_key(config)

    # 2. 显式 role 覆盖
    if override_role and override_role in MODEL_CONFIGS:
        config = MODEL_CONFIGS[override_role].copy()
        config["routing_reason"] = f"显式指定 role='{override_role}'"
        return _inject_api_key(config)

    # 3. 技能覆盖
    if active_skill and active_skill in SKILL_MODEL_OVERRIDES:
        role = SKILL_MODEL_OVERRIDES[active_skill]
        config = MODEL_CONFIGS[role].copy()
        config["routing_reason"] = f"技能 '{active_skill}' 指定使用 role='{role}'"
        return _inject_api_key(config)

    # 4. 默认使用主大脑模型
    config = MODEL_CONFIGS["primary"].copy()
    config["routing_reason"] = "默认主大脑路由 (primary)"
    return _inject_api_key(config)


def resolve_embedding_model() -> dict:
    """返回企业知识库向量模型配置（本地 bge-m3 节点）。"""
    return {
        "provider": "openai_compatible",
        "model": "BAAI/bge-m3",
        "base_url": "http://qixuan-local-embedding:8080/v1",
        "api_key": os.environ.get("LOCAL_EMBEDDING_KEY", ""),
    }


def _is_confidential(profile: str) -> bool:
    """判断 Profile 是否为涉密隔离模式。"""
    return profile.startswith(CONFIDENTIAL_PROFILE_PREFIX)


def _inject_api_key(config: dict) -> dict:
    """将 api_key_env 指向的环境变量值注入为 api_key 字段。"""
    env_key = config.pop("api_key_env", "")
    config["api_key"] = os.environ.get(env_key, "")
    if not config["api_key"]:
        config["warning"] = f"环境变量 {env_key} 未配置，API 调用将失败。"
    return config


def get_routing_summary(profile: str = "default", active_skill: Optional[str] = None) -> str:
    """返回当前路由决策的可读摘要（用于调试与审计日志）。"""
    config = resolve_model(profile, active_skill)
    return (
        f"模型路由摘要 | Profile: {profile} | 技能: {active_skill or 'N/A'} | "
        f"模型: {config.get('model')} | 端点: {config.get('base_url')} | "
        f"决策原因: {config.get('routing_reason', 'N/A')}"
    )
