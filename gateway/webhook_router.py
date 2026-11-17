"""
启煊智能体 - 统一 Webhook 入口路由器
功能：
  - 路由 GitLab MR Webhook → 触发 qixuan-code-reviewer 技能
  - 路由 GitLab Tag Webhook → 触发 qixuan-release-notes 技能
  - 路由 Alertmanager Webhook → 触发 qixuan-ops-sentinel 技能
  - 所有 Webhook 验证签名后转为标准内部事件

监听端口：8090（由 Docker Compose 映射）
"""

import os
import json
import hmac
import hashlib
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException, Header
from fastapi.middleware.cors import CORSMiddleware

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from tools.git.git_tools import verify_gitlab_webhook

app = FastAPI(title="Qixuan Agent - Webhook Router", version="1.0.0")

# 仅允许内网来源（生产环境应配置真实内网 CIDR，此处为示例）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost", "https://git.qixuan-soft.internal"],
    allow_methods=["POST"],
    allow_headers=["*"],
)

ALERTMANAGER_WEBHOOK_SECRET = os.environ.get("ALERTMANAGER_WEBHOOK_SECRET", "")


# ---------------------------------------------------------------------------
# GitLab Webhook 路由
# ---------------------------------------------------------------------------

@app.post("/webhook/gitlab")
async def gitlab_webhook(
    request: Request,
    x_gitlab_token: Optional[str] = Header(None, alias="X-Gitlab-Token"),
    x_gitlab_event: Optional[str] = Header(None, alias="X-Gitlab-Event"),
):
    """
    接收 GitLab Webhook 推送，根据事件类型触发对应技能。

    支持事件：
      - Merge Request Hook  → qixuan-code-reviewer
      - Tag Push Hook       → qixuan-release-notes
    """
    body = await request.body()

    # 验证 GitLab Webhook 签名
    if not verify_gitlab_webhook(body, x_gitlab_token or ""):
        raise HTTPException(status_code=403, detail="GitLab Webhook 签名验证失败")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event_type = x_gitlab_event or payload.get("object_kind", "")
    project = payload.get("project", {})
    project_id = project.get("path_with_namespace", str(project.get("id", "")))

    if event_type in ("Merge Request Hook", "merge_request"):
        mr = payload.get("object_attributes", {})
        mr_iid = mr.get("iid")
        mr_state = mr.get("state", "")
        mr_action = mr.get("action", "")

        # 仅处理 open / update 状态的 MR
        if mr_action in ("open", "reopen", "update") and mr_state == "opened":
            event = {
                "type": "gitlab.merge_request.opened",
                "skill": "qixuan-code-reviewer",
                "payload": {
                    "project_id": project_id,
                    "mr_iid": mr_iid,
                    "title": mr.get("title", ""),
                    "source_branch": mr.get("source_branch", ""),
                    "target_branch": mr.get("target_branch", ""),
                    "author": payload.get("user", {}).get("name", ""),
                },
            }
            # TODO: 将 event 推送至 Agent 核心消息队列
            # await agent_queue.put(event)
            return {"status": "queued", "skill": "qixuan-code-reviewer", "mr_iid": mr_iid}

    elif event_type in ("Tag Push Hook", "tag_push"):
        ref = payload.get("ref", "")
        tag_name = ref.replace("refs/tags/", "") if ref.startswith("refs/tags/") else ref
        event = {
            "type": "gitlab.tag.created",
            "skill": "qixuan-release-notes",
            "payload": {
                "project_id": project_id,
                "tag_name": tag_name,
                "before": payload.get("before", ""),
                "after": payload.get("after", ""),
                "pusher": payload.get("user_name", ""),
            },
        }
        # TODO: await agent_queue.put(event)
        return {"status": "queued", "skill": "qixuan-release-notes", "tag": tag_name}

    return {"status": "ignored", "event_type": event_type}


# ---------------------------------------------------------------------------
# Alertmanager Webhook 路由
# ---------------------------------------------------------------------------

@app.post("/webhook/alertmanager")
async def alertmanager_webhook(request: Request):
    """
    接收 Prometheus Alertmanager 告警推送，触发 qixuan-ops-sentinel RCA 分析。
    """
    body = await request.body()

    # 验证 HMAC-SHA256 签名（Alertmanager webhook_configs.http_config.authorization）
    auth_header = request.headers.get("Authorization", "")
    if ALERTMANAGER_WEBHOOK_SECRET:
        expected = "Bearer " + hmac.new(
            ALERTMANAGER_WEBHOOK_SECRET.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(auth_header, expected):
            raise HTTPException(status_code=403, detail="Alertmanager 签名验证失败")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    alerts = payload.get("alerts", [])
    firing_alerts = [a for a in alerts if a.get("status") == "firing"]

    if not firing_alerts:
        return {"status": "resolved_or_empty", "alert_count": len(alerts)}

    # 提取关键告警信息
    alert_summary = []
    for alert in firing_alerts[:5]:  # 最多处理前 5 条，防止批量告警淹没
        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        alert_summary.append({
            "name": labels.get("alertname", "Unknown"),
            "severity": labels.get("severity", "unknown"),
            "service": labels.get("service", labels.get("job", "unknown")),
            "namespace": labels.get("namespace", ""),
            "summary": annotations.get("summary", ""),
            "description": annotations.get("description", ""),
            "starts_at": alert.get("startsAt", ""),
        })

    event = {
        "type": "alertmanager.firing",
        "skill": "qixuan-ops-sentinel",
        "payload": {
            "alert_count": len(firing_alerts),
            "alerts": alert_summary,
            "group_labels": payload.get("groupLabels", {}),
            "external_url": payload.get("externalURL", ""),
        },
    }
    # TODO: await agent_queue.put(event)
    return {"status": "queued", "skill": "qixuan-ops-sentinel", "firing_count": len(firing_alerts)}


# ---------------------------------------------------------------------------
# 健康检查
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "qixuan-agent-webhook-router",
        "timestamp": datetime.now().isoformat(),
    }


@app.get("/")
async def root():
    return {
        "name": "启煊软件智能体网关",
        "version": "1.0.0",
        "endpoints": [
            "POST /webhook/gitlab",
            "POST /webhook/alertmanager",
            "POST /gateway/feishu/event",
            "GET  /health",
        ],
    }
