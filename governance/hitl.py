"""
启煊智能体 - 人工确认（Human-in-the-Loop）治理模块
功能：高危工具调用前拦截 → 生成 IM 审批卡片 → 等待审批结果 → 执行或拒绝

高危工具白名单（来自 config.yaml governance.human_in_the_loop.require_approval_tools）：
  - git_push             推送代码至远端分支
  - db_execute_write     数据库写操作（INSERT/UPDATE/DELETE）
  - send_external_email  发送外部邮件
  - deploy_staging       部署至预发环境
  - k8s_pod_restart      重启 K8s Pod（运维哨兵触发）
"""

import os
import asyncio
import time
import json
from datetime import datetime
from typing import Callable, Any, Optional

# 本地模块
import sys
sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent))
from tools.notifications.im_notifier import im_card_sender
from tools.memory.memory_tools import audit_log_write, audit_log_update

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
APPROVAL_TIMEOUT_SECONDS = int(os.environ.get("HITL_APPROVAL_TIMEOUT", "600"))  # 默认 10 分钟
HITL_ENABLED = os.environ.get("HITL_ENABLED", "true").lower() == "true"
HITL_CHANNEL = os.environ.get("HITL_NOTIFY_CHANNEL", "ops_alert")

REQUIRE_APPROVAL_TOOLS = {
    "git_push",
    "db_execute_write",
    "send_external_email",
    "deploy_staging",
    "k8s_pod_restart",
}

# 内存中等待审批的请求（key: log_id -> asyncio.Event）
# 生产部署时应替换为 Redis 或数据库持久化
_pending_approvals: dict[int, dict] = {}


# ---------------------------------------------------------------------------
# HITL 核心拦截器
# ---------------------------------------------------------------------------

class HITLTimeoutError(Exception):
    """审批超时异常。"""


class HITLRejectedError(Exception):
    """审批被拒绝异常。"""


def requires_approval(tool_name: str) -> bool:
    """判断工具调用是否需要人工审批。"""
    if not HITL_ENABLED:
        return False
    return tool_name in REQUIRE_APPROVAL_TOOLS


async def request_approval(
    tool_name: str,
    tool_args: dict,
    profile: str = "default",
    session_id: str = "",
    requester: str = "agent",
) -> dict:
    """
    拦截高危工具调用，向运维审批频道发送确认卡片，等待人工审批。

    Args:
        tool_name:  高危工具名称。
        tool_args:  工具调用参数（用于在卡片中展示）。
        profile:    当前 Profile（租户上下文）。
        session_id: 当前会话 ID。
        requester:  请求发起人（agent | user_id）。

    Returns:
        审批通过时返回 {"approved": True, "approver": str, "log_id": int}。

    Raises:
        HITLTimeoutError:  审批超时。
        HITLRejectedError: 审批被拒绝。
    """
    # 写入审计日志，获取 log_id
    log_id = audit_log_write(
        tool_name=tool_name,
        profile=profile,
        session_id=session_id,
        input_data=tool_args,
        status="pending",
    )

    # 构建审批卡片内容
    args_preview = json.dumps(tool_args, ensure_ascii=False, indent=2)[:600]
    card_content = (
        f"**工具名称：** `{tool_name}`\n\n"
        f"**调用参数预览：**\n```json\n{args_preview}\n```\n\n"
        f"**发起方：** {requester} | **Profile：** {profile}\n"
        f"**审批超时：** {APPROVAL_TIMEOUT_SECONDS // 60} 分钟"
    )

    # 注册等待状态
    _pending_approvals[log_id] = {
        "status": "pending",
        "approver": "",
        "created_at": datetime.now().isoformat(),
    }

    # 发送飞书审批卡片
    im_card_sender(
        channel=HITL_CHANNEL,
        title=f"⚠️ 高危操作审批请求 — {tool_name}",
        content=card_content,
        status="warning",
        hitl_action={
            "label": f"授权执行 {tool_name}",
            "action_id": f"hitl_approve_{tool_name}",
            "log_id": log_id,
        },
    )

    # 轮询等待审批结果（生产环境应替换为 Webhook 回调 + asyncio.Event）
    deadline = time.monotonic() + APPROVAL_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        await asyncio.sleep(5)
        pending = _pending_approvals.get(log_id, {})
        status = pending.get("status", "pending")

        if status == "approved":
            audit_log_update(log_id, "approved", pending.get("approver", ""), profile)
            return {"approved": True, "approver": pending["approver"], "log_id": log_id}

        if status == "rejected":
            audit_log_update(log_id, "rejected", pending.get("approver", ""), profile)
            raise HITLRejectedError(
                f"高危操作 '{tool_name}' 已被 {pending.get('approver', '未知审批人')} 拒绝。"
            )

    # 超时处理
    _pending_approvals.pop(log_id, None)
    audit_log_update(log_id, "timeout", "", profile)
    raise HITLTimeoutError(
        f"高危操作 '{tool_name}' 审批超时（>{APPROVAL_TIMEOUT_SECONDS}s），已自动取消。"
    )


def handle_approval_callback(log_id: int, action: str, approver: str) -> dict:
    """
    处理来自飞书 / 钉钉 Interactive Card 的审批回调。
    此函数由 Gateway Webhook Handler 调用。

    Args:
        log_id:   审批日志 ID（从卡片 value 中提取）。
        action:   动作（"approve" | "reject"）。
        approver: 审批人标识（飞书用户 ID 或 open_id）。

    Returns:
        处理结果。
    """
    if log_id not in _pending_approvals:
        return {"error": f"审批请求 log_id={log_id} 不存在或已超时。"}

    if action == "approve":
        _pending_approvals[log_id]["status"] = "approved"
        _pending_approvals[log_id]["approver"] = approver
        return {"status": "approved", "log_id": log_id, "approver": approver}
    elif action == "reject":
        _pending_approvals[log_id]["status"] = "rejected"
        _pending_approvals[log_id]["approver"] = approver
        return {"status": "rejected", "log_id": log_id, "approver": approver}
    else:
        return {"error": f"未知 action：'{action}'，仅支持 approve | reject。"}


# ---------------------------------------------------------------------------
# 装饰器：自动为高危工具注入 HITL 拦截
# ---------------------------------------------------------------------------

def hitl_guard(tool_name: str):
    """
    装饰器：为异步工具函数自动注入 HITL 审批拦截逻辑。

    用法：
        @hitl_guard("k8s_pod_restart")
        async def k8s_pod_restart(namespace: str, pod_name: str, **ctx):
            ...
    """
    def decorator(func: Callable) -> Callable:
        async def wrapper(*args, profile: str = "default", session_id: str = "", **kwargs):
            if requires_approval(tool_name):
                tool_args = {**{f"arg_{i}": a for i, a in enumerate(args)}, **kwargs}
                await request_approval(
                    tool_name=tool_name,
                    tool_args=tool_args,
                    profile=profile,
                    session_id=session_id,
                )
            return await func(*args, **kwargs)
        wrapper.__name__ = func.__name__
        wrapper.__doc__ = func.__doc__
        return wrapper
    return decorator
