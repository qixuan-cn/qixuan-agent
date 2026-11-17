"""
启煊智能体 - Git / GitLab 只读工具集
功能：读取 MR Diff、获取提交日志、发布代码审查评论

安全约束：
- 默认持有只读令牌（GIT_REVIEWER_TOKEN），仅授予 read_repository + comment_mr 权限。
- 写操作（git_push、merge）触发 HITL 人工审批，不在此模块中静默执行。
- 严禁将令牌打印至日志或返回给客户端。
"""

import os
import hashlib
import httpx
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# GitLab 连接配置（从环境变量读取，不硬编码）
# ---------------------------------------------------------------------------
GITLAB_BASE_URL = os.environ.get("GIT_INTERNAL_URL", "https://git.qixuan-soft.internal")
REVIEWER_TOKEN = os.environ.get("GIT_REVIEWER_TOKEN", "")
SKILL_TOKEN = os.environ.get("GIT_SKILL_TOKEN", "")
WEBHOOK_SECRET = os.environ.get("GIT_WEBHOOK_SECRET", "")

DEFAULT_HEADERS = {
    "PRIVATE-TOKEN": REVIEWER_TOKEN,
    "Content-Type": "application/json",
}

# 合理的 HTTP 超时：连接 5s，读取 30s
TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)


def _gitlab_get(path: str, params: Optional[dict] = None) -> dict | list:
    """通用 GitLab REST API GET 请求（只读）。"""
    if not REVIEWER_TOKEN:
        return {"error": "GIT_REVIEWER_TOKEN 未配置，请在 .env 中填入只读令牌。"}
    url = f"{GITLAB_BASE_URL}/api/v4{path}"
    try:
        resp = httpx.get(url, headers=DEFAULT_HEADERS, params=params, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"GitLab API 返回错误：{e.response.status_code}", "detail": e.response.text[:500]}
    except httpx.RequestError as e:
        return {"error": f"GitLab 连接失败：{type(e).__name__}", "detail": str(e)}


def _gitlab_post(path: str, payload: dict) -> dict:
    """通用 GitLab REST API POST 请求（评论等写操作，权限受限）。"""
    if not REVIEWER_TOKEN:
        return {"error": "GIT_REVIEWER_TOKEN 未配置。"}
    url = f"{GITLAB_BASE_URL}/api/v4{path}"
    try:
        resp = httpx.post(url, headers=DEFAULT_HEADERS, json=payload, timeout=TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        return {"error": f"GitLab POST 失败：{e.response.status_code}", "detail": e.response.text[:500]}
    except httpx.RequestError as e:
        return {"error": f"GitLab 连接失败：{type(e).__name__}", "detail": str(e)}


# ---------------------------------------------------------------------------
# MR Diff 读取工具
# ---------------------------------------------------------------------------

def git_diff_reader(
    project_id: str,
    mr_iid: int,
    max_lines_per_file: int = 500,
) -> dict:
    """
    获取指定 Merge Request 的变更 Diff 内容。

    Args:
        project_id:         GitLab 项目路径（如 "qixuan-mes/backend"）或数字 ID。
        mr_iid:             MR 在项目内的编号（IID）。
        max_lines_per_file: 每个文件最多返回的 diff 行数，防止超长文件撑爆上下文。

    Returns:
        包含 MR 基本信息与变更文件列表的结构化字典。
    """
    encoded_id = httpx.URL(project_id).path if "/" in str(project_id) else str(project_id)
    encoded_id = encoded_id.replace("/", "%2F") if "/" in encoded_id else encoded_id

    # 获取 MR 基本信息
    mr_info = _gitlab_get(f"/projects/{encoded_id}/merge_requests/{mr_iid}")
    if "error" in mr_info:
        return mr_info

    # 获取 Diff（最多 100 个文件变更）
    diffs_raw = _gitlab_get(f"/projects/{encoded_id}/merge_requests/{mr_iid}/diffs", {"per_page": 100})
    if isinstance(diffs_raw, dict) and "error" in diffs_raw:
        return diffs_raw

    files = []
    for d in diffs_raw:
        diff_text = d.get("diff", "")
        lines = diff_text.split("\n")
        if len(lines) > max_lines_per_file:
            diff_text = "\n".join(lines[:max_lines_per_file]) + f"\n... [已截断，共 {len(lines)} 行] ..."
        files.append({
            "path": d.get("new_path", d.get("old_path", "unknown")),
            "old_path": d.get("old_path"),
            "is_new": d.get("new_file", False),
            "is_deleted": d.get("deleted_file", False),
            "is_renamed": d.get("renamed_file", False),
            "diff": diff_text,
        })

    return {
        "mr_iid": mr_iid,
        "title": mr_info.get("title", ""),
        "description": mr_info.get("description", ""),
        "author": mr_info.get("author", {}).get("name", ""),
        "source_branch": mr_info.get("source_branch", ""),
        "target_branch": mr_info.get("target_branch", ""),
        "state": mr_info.get("state", ""),
        "web_url": mr_info.get("web_url", ""),
        "created_at": mr_info.get("created_at", ""),
        "files_changed": len(files),
        "files": files,
    }


# ---------------------------------------------------------------------------
# Git 提交日志读取工具
# ---------------------------------------------------------------------------

def git_log_collector(
    project_id: str,
    from_ref: str,
    to_ref: str = "HEAD",
    max_commits: int = 200,
) -> dict:
    """
    收集两个 Git 引用之间的提交日志（用于自动生成版本说明）。

    Args:
        project_id: GitLab 项目路径或数字 ID。
        from_ref:   起始引用（如上个 Release Tag：v1.2.0）。
        to_ref:     结束引用（如 HEAD 或当前 Tag）。
        max_commits: 最多收集的提交数。

    Returns:
        提交列表（title、message、author、date、id）。
    """
    encoded_id = str(project_id).replace("/", "%2F")
    params = {
        "ref_name": to_ref,
        "per_page": min(max_commits, 100),
    }
    commits_raw = _gitlab_get(f"/projects/{encoded_id}/repository/commits", params)
    if isinstance(commits_raw, dict) and "error" in commits_raw:
        return commits_raw

    commits = []
    for c in commits_raw:
        commits.append({
            "id": c.get("short_id", ""),
            "title": c.get("title", ""),
            "message": c.get("message", "").strip(),
            "author": c.get("author_name", ""),
            "date": c.get("created_at", ""),
        })

    return {
        "project_id": project_id,
        "from_ref": from_ref,
        "to_ref": to_ref,
        "commit_count": len(commits),
        "commits": commits,
    }


# ---------------------------------------------------------------------------
# GitLab MR 评论发布工具
# ---------------------------------------------------------------------------

def gitlab_comment_poster(
    project_id: str,
    mr_iid: int,
    body: str,
    note_type: str = "comment",
) -> dict:
    """
    向 GitLab MR 发布代码审查评论（用于 Code Review 结论自动回写）。

    Args:
        project_id: GitLab 项目路径或数字 ID。
        mr_iid:     MR 编号。
        body:       评论内容（Markdown 格式）。
        note_type:  评论类型（comment: 普通评论 | system: 系统通知，默认 comment）。

    Returns:
        GitLab API 响应（包含评论 ID 与发布时间）。
    """
    if not body or len(body.strip()) == 0:
        return {"error": "评论内容不能为空。"}

    # 长度保护：GitLab API 评论上限约 1MB，实际控制在 50KB 以内
    if len(body) > 50_000:
        body = body[:50_000] + "\n\n... [内容已截断，完整报告请查看审计日志] ..."

    encoded_id = str(project_id).replace("/", "%2F")
    result = _gitlab_post(
        f"/projects/{encoded_id}/merge_requests/{mr_iid}/notes",
        {"body": body},
    )
    return result


# ---------------------------------------------------------------------------
# Webhook 签名验证
# ---------------------------------------------------------------------------

def verify_gitlab_webhook(payload_bytes: bytes, signature_header: str) -> bool:
    """
    验证来自 GitLab 的 Webhook 请求签名。

    Args:
        payload_bytes:      原始请求体字节。
        signature_header:   X-Gitlab-Token 请求头值。

    Returns:
        True 如果签名合法，否则 False。
    """
    if not WEBHOOK_SECRET:
        # 未配置密钥时拒绝所有未签名请求
        return False
    # GitLab Webhook 使用简单令牌比对（非 HMAC），使用 secrets.compare_digest 防时序攻击
    import secrets
    return secrets.compare_digest(signature_header.encode(), WEBHOOK_SECRET.encode())
