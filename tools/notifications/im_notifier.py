"""
启煊智能体 - 企业 IM 推送工具（飞书 / 钉钉 Webhook）
功能：发送纯文本消息、富文本卡片（飞书 Interactive Card）、运维告警卡片（含 HITL 审批按钮）

安全约束：
- 仅支持向预配置的 Webhook URL 发送消息（不支持任意 URL），防止 SSRF 利用。
- HITL 审批卡片的回调 URL 由内部 governance 模块生成，不可由用户自定义。
"""

import os
import httpx
from datetime import datetime
from typing import Optional

# ---------------------------------------------------------------------------
# Webhook 白名单（仅允许向这些端点发送消息）
# ---------------------------------------------------------------------------
OPS_ALERT_WEBHOOK = os.environ.get("OPS_ALERT_WEBHOOK_URL", "")
DEV_TEAM_WEBHOOK = os.environ.get("DEV_TEAM_WEBHOOK_URL", "")

ALLOWED_WEBHOOK_URLS: dict[str, str] = {
    "ops_alert": OPS_ALERT_WEBHOOK,
    "dev_team": DEV_TEAM_WEBHOOK,
}

TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)

# 状态 -> 飞书卡片颜色映射
STATUS_COLOR = {
    "info": "blue",
    "warning": "yellow",
    "critical": "red",
    "success": "green",
}


def _safe_webhook_url(channel: str) -> str | None:
    """返回预配置频道的 Webhook URL；不在白名单内返回 None。"""
    url = ALLOWED_WEBHOOK_URLS.get(channel, "")
    return url if url else None


# ---------------------------------------------------------------------------
# 飞书 Webhook 富文本卡片
# ---------------------------------------------------------------------------

def im_card_sender(
    channel: str,
    title: str,
    content: str,
    status: str = "info",
    hitl_action: Optional[dict] = None,
) -> dict:
    """
    通过飞书 Webhook 发送富文本消息卡片。

    Args:
        channel:     推送频道（"ops_alert" | "dev_team"）。
        title:       卡片标题。
        content:     卡片正文（Markdown）。
        status:      状态标识（info | warning | critical | success），影响卡片颜色。
        hitl_action: HITL 审批按钮配置，格式：
                     {"label": "一键重启", "action_id": "k8s_restart", "log_id": 123}
                     仅在高危操作需人工确认时传入。

    Returns:
        飞书 API 响应字典。
    """
    webhook_url = _safe_webhook_url(channel)
    if not webhook_url:
        return {
            "error": f"频道 '{channel}' 未配置或不在白名单中。"
                     f"请在 .env 中设置 OPS_ALERT_WEBHOOK_URL / DEV_TEAM_WEBHOOK_URL。"
        }

    color = STATUS_COLOR.get(status, "blue")
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 飞书卡片 JSON 结构
    elements: list[dict] = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": content[:3000],  # 飞书卡片单元素字符限制
            },
        },
        {
            "tag": "note",
            "elements": [
                {"tag": "plain_text", "content": f"启煊智能体 · {timestamp}"}
            ],
        },
    ]

    # 高危操作追加 HITL 审批按钮
    if hitl_action:
        elements.insert(-1, {
            "tag": "action",
            "actions": [
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": f"✅ {hitl_action.get('label', '确认执行')}"},
                    "type": "primary",
                    "value": {
                        "action_id": hitl_action.get("action_id", ""),
                        "log_id": str(hitl_action.get("log_id", "")),
                    },
                },
                {
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": "❌ 拒绝"},
                    "type": "danger",
                    "value": {
                        "action_id": hitl_action.get("action_id", "") + "_reject",
                        "log_id": str(hitl_action.get("log_id", "")),
                    },
                },
            ],
        })

    payload = {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "title": {"tag": "plain_text", "content": title},
                "template": color,
            },
            "elements": elements,
        },
    }

    try:
        resp = httpx.post(webhook_url, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        return {"status": "sent", "channel": channel, "title": title, "response": resp.json()}
    except httpx.HTTPStatusError as e:
        return {"error": f"飞书 Webhook 返回错误：{e.response.status_code}", "detail": e.response.text[:200]}
    except httpx.RequestError as e:
        return {"error": f"飞书 Webhook 连接失败：{type(e).__name__}", "detail": str(e)}


# ---------------------------------------------------------------------------
# 钉钉 Webhook 文本消息（Markdown 格式）
# ---------------------------------------------------------------------------

def dingtalk_notify(
    channel: str,
    title: str,
    content: str,
    at_all: bool = False,
) -> dict:
    """
    通过钉钉 Webhook 发送 Markdown 格式消息。

    Args:
        channel:  推送频道（同飞书白名单 key）。
        title:    消息标题（钉钉 Markdown 消息必填）。
        content:  消息正文（Markdown）。
        at_all:   是否 @ 全员（高危告警时使用）。

    Returns:
        钉钉 API 响应字典。
    """
    webhook_url = _safe_webhook_url(channel)
    if not webhook_url:
        return {"error": f"频道 '{channel}' 未配置或不在白名单中。"}

    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": f"## {title}\n\n{content[:4000]}",
        },
        "at": {"isAtAll": at_all},
    }

    try:
        resp = httpx.post(webhook_url, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        return {"status": "sent", "channel": channel, "title": title, "response": resp.json()}
    except httpx.HTTPStatusError as e:
        return {"error": f"钉钉 Webhook 错误：{e.response.status_code}", "detail": e.response.text[:200]}
    except httpx.RequestError as e:
        return {"error": f"钉钉 Webhook 连接失败：{type(e).__name__}", "detail": str(e)}
