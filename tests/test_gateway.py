"""
启煊智能体 - Webhook 网关单元测试
"""

import json
import pytest
import os
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def set_env():
    """为测试设置必要的环境变量。"""
    os.environ["GIT_WEBHOOK_SECRET"] = "test_webhook_secret_123"
    os.environ["FEISHU_VERIFICATION_TOKEN"] = "test_verification_token"
    os.environ["FEISHU_APP_ID"] = "cli_test"
    os.environ["FEISHU_APP_SECRET"] = "test_secret"
    yield
    for k in ["GIT_WEBHOOK_SECRET", "FEISHU_VERIFICATION_TOKEN", "FEISHU_APP_ID", "FEISHU_APP_SECRET"]:
        os.environ.pop(k, None)


def test_health_endpoint():
    """网关健康检查端点应返回 200。"""
    from gateway.webhook_router import app
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "qixuan-agent" in data["service"]


def test_root_endpoint():
    """根路由应返回服务信息。"""
    from gateway.webhook_router import app
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert "启煊软件" in data["name"]
    assert len(data["endpoints"]) > 0


def test_gitlab_webhook_missing_token_rejected():
    """缺少有效 Webhook 令牌的 GitLab 请求应被拒绝（403）。"""
    from gateway.webhook_router import app
    client = TestClient(app)
    payload = json.dumps({"object_kind": "merge_request"}).encode()
    resp = client.post(
        "/webhook/gitlab",
        content=payload,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": "wrong_token",
            "X-Gitlab-Event": "Merge Request Hook",
        },
    )
    assert resp.status_code == 403


def test_gitlab_webhook_mr_event_accepted():
    """合法 GitLab MR Webhook 应被正确解析并返回 queued 状态。"""
    from gateway.webhook_router import app
    client = TestClient(app)

    payload = {
        "object_kind": "merge_request",
        "user": {"name": "张三"},
        "project": {"path_with_namespace": "qixuan-mes/backend", "id": 42},
        "object_attributes": {
            "iid": 88,
            "title": "feat: 添加库存预警功能",
            "state": "opened",
            "action": "open",
            "source_branch": "feature/inventory-alert",
            "target_branch": "main",
        },
    }
    body = json.dumps(payload).encode()

    resp = client.post(
        "/webhook/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": "test_webhook_secret_123",
            "X-Gitlab-Event": "Merge Request Hook",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "queued"
    assert data.get("skill") == "qixuan-code-reviewer"
    assert data.get("mr_iid") == 88


def test_gitlab_webhook_tag_event_queues_release_notes():
    """GitLab Tag 推送事件应触发 qixuan-release-notes 技能。"""
    from gateway.webhook_router import app
    client = TestClient(app)

    payload = {
        "object_kind": "tag_push",
        "ref": "refs/tags/v1.3.0",
        "before": "abc123",
        "after": "def456",
        "user_name": "李四",
        "project": {"path_with_namespace": "qixuan-mes/backend", "id": 42},
    }
    body = json.dumps(payload).encode()

    resp = client.post(
        "/webhook/gitlab",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-Gitlab-Token": "test_webhook_secret_123",
            "X-Gitlab-Event": "Tag Push Hook",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "queued"
    assert data.get("skill") == "qixuan-release-notes"
    assert data.get("tag") == "v1.3.0"


def test_alertmanager_webhook_routes_ops_sentinel():
    """Alertmanager 告警事件应触发 qixuan-ops-sentinel 技能。"""
    from gateway.webhook_router import app
    # 清除 ALERTMANAGER_WEBHOOK_SECRET 以跳过签名验证
    os.environ.pop("ALERTMANAGER_WEBHOOK_SECRET", None)
    client = TestClient(app)

    payload = {
        "version": "4",
        "status": "firing",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighMemoryUsage",
                    "severity": "critical",
                    "service": "qixuan-mes-worker",
                    "namespace": "prod",
                },
                "annotations": {
                    "summary": "内存使用率超过 90%",
                    "description": "qixuan-mes-worker Pod OOMKilled",
                },
                "startsAt": "2026-11-14T08:00:00Z",
            }
        ],
        "groupLabels": {"alertname": "HighMemoryUsage"},
        "externalURL": "http://alertmanager.qixuan-soft.internal",
    }
    resp = client.post(
        "/webhook/alertmanager",
        json=payload,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("status") == "queued"
    assert data.get("skill") == "qixuan-ops-sentinel"
    assert data.get("firing_count") == 1
