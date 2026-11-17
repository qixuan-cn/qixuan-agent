"""
启煊智能体 - 飞书（Lark）接入网关
功能：
  - 接收飞书事件推送（@机器人消息、群消息、卡片按钮回调）
  - 验证消息合法性（Encrypt Key 解密 + Verification Token 校验）
  - 将飞书消息转化为标准内部会话指令，转发至 Agent 核心处理
  - 将 Agent 响应回写至飞书会话（文本消息 / 富文本卡片）
  - 处理 HITL 审批卡片按钮回调（approve / reject）

部署要求：飞书开放平台需将事件订阅回调 URL 配置为：
  https://<your-domain>:8090/gateway/feishu/event
"""

import os
import json
import hmac
import hashlib
import base64
import asyncio
from datetime import datetime
from typing import Optional

import httpx
from fastapi import FastAPI, Request, Response, HTTPException

# 本地模块
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from governance.hitl import handle_approval_callback

# ---------------------------------------------------------------------------
# 飞书应用配置（从环境变量加载）
# ---------------------------------------------------------------------------
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_VERIFICATION_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "")
FEISHU_ENCRYPT_KEY = os.environ.get("FEISHU_ENCRYPT_KEY", "")
FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)

# 允许使用机器人的飞书用户 open_id 白名单（逗号分隔，空则允许所有员工）
ALLOWED_USERS_RAW = os.environ.get("FEISHU_ALLOWED_USERS", "")
ALLOWED_USERS: set[str] = set(u.strip() for u in ALLOWED_USERS_RAW.split(",") if u.strip())

app = FastAPI(title="Qixuan Agent - Feishu Gateway")


# ---------------------------------------------------------------------------
# 加解密工具
# ---------------------------------------------------------------------------

def _decrypt_feishu_event(encrypted: str) -> dict:
    """
    解密飞书 AES-CBC 加密的事件推送体。
    飞书加密方案：SHA256(EncryptKey) 作为 AES-256 密钥，前 16 字节为 IV。
    """
    if not FEISHU_ENCRYPT_KEY:
        raise ValueError("FEISHU_ENCRYPT_KEY 未配置，无法解密飞书事件推送。")

    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    from cryptography.hazmat.backends import default_backend

    key = hashlib.sha256(FEISHU_ENCRYPT_KEY.encode()).digest()
    cipher_bytes = base64.b64decode(encrypted)
    iv = cipher_bytes[:16]
    ciphertext = cipher_bytes[16:]

    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    decryptor = cipher.decryptor()
    decrypted = decryptor.update(ciphertext) + decryptor.finalize()

    # 移除 PKCS7 填充
    pad_len = decrypted[-1]
    decrypted = decrypted[:-pad_len]
    return json.loads(decrypted.decode("utf-8"))


def _verify_token(token: str) -> bool:
    """验证飞书 Verification Token（用于无加密场景）。"""
    return hmac.compare_digest(token, FEISHU_VERIFICATION_TOKEN)


# ---------------------------------------------------------------------------
# 飞书消息发送
# ---------------------------------------------------------------------------

async def _get_tenant_access_token() -> str:
    """获取飞书应用 Tenant Access Token（用于主动发消息）。"""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("tenant_access_token", "")


async def send_feishu_message(
    receive_id: str,
    content: str,
    receive_id_type: str = "open_id",
    msg_type: str = "text",
) -> dict:
    """
    通过飞书开放 API 发送消息回复。

    Args:
        receive_id:      接收方 ID（open_id / chat_id）。
        content:         消息内容（text 类型直接传字符串，card 类型传 JSON 字符串）。
        receive_id_type: ID 类型（open_id | chat_id | user_id）。
        msg_type:        消息类型（text | interactive）。
    """
    token = await _get_tenant_access_token()
    if not token:
        return {"error": "无法获取飞书 Tenant Access Token，请检查 App ID / Secret 配置。"}

    if msg_type == "text":
        msg_content = json.dumps({"text": content[:4000]})
    else:
        msg_content = content  # card 类型已是 JSON 字符串

    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        resp = await client.post(
            f"{FEISHU_API_BASE}/im/v1/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"receive_id_type": receive_id_type},
            json={
                "receive_id": receive_id,
                "msg_type": msg_type,
                "content": msg_content,
            },
        )
    try:
        return resp.json()
    except Exception:
        return {"status_code": resp.status_code, "raw": resp.text[:200]}


# ---------------------------------------------------------------------------
# 事件路由处理
# ---------------------------------------------------------------------------

async def _process_message_event(event: dict, sender_open_id: str, chat_id: str, text: str):
    """处理飞书 @机器人 消息事件，调用 Agent 核心并回写响应。"""
    # 权限检查
    if ALLOWED_USERS and sender_open_id not in ALLOWED_USERS:
        await send_feishu_message(
            chat_id,
            "抱歉，您暂无使用启煊智能助手的权限，请联系管理员开通。",
            receive_id_type="chat_id",
        )
        return

    # 此处应调用 Hermes Agent 核心处理逻辑（通过内部 ACP 或直接调用）
    # 以下为占位响应，实际部署时替换为 agent_core.handle_message(text, sender_open_id)
    response_text = (
        f"[启煊智能助手] 已收到您的消息：「{text[:100]}」\n"
        f"正在处理中，请稍候..."
    )
    await send_feishu_message(chat_id, response_text, receive_id_type="chat_id")


# ---------------------------------------------------------------------------
# FastAPI 路由
# ---------------------------------------------------------------------------

@app.post("/gateway/feishu/event")
async def feishu_event_handler(request: Request) -> Response:
    """飞书事件推送接收端点。"""
    body = await request.body()

    # 解析请求体
    try:
        raw = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # 处理加密推送
    if "encrypt" in raw:
        try:
            raw = _decrypt_feishu_event(raw["encrypt"])
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"解密失败：{e}")

    # URL 验证挑战（飞书首次配置回调时的握手）
    if raw.get("type") == "url_verification":
        challenge = raw.get("challenge", "")
        # 验证 Token
        token = raw.get("token", "")
        if FEISHU_VERIFICATION_TOKEN and not _verify_token(token):
            raise HTTPException(status_code=403, detail="Verification token mismatch")
        return Response(content=json.dumps({"challenge": challenge}), media_type="application/json")

    # 事件分发
    header = raw.get("header", {})
    event_type = header.get("event_type", raw.get("event", {}).get("type", ""))
    event_data = raw.get("event", {})

    if event_type == "im.message.receive_v1":
        # @机器人 消息事件
        sender = event_data.get("sender", {})
        sender_open_id = sender.get("sender_id", {}).get("open_id", "")
        message = event_data.get("message", {})
        chat_id = message.get("chat_id", "")
        content_raw = message.get("content", "{}")
        try:
            text = json.loads(content_raw).get("text", "").strip()
            # 移除 @机器人 mention 标签
            import re
            text = re.sub(r'<at user_id="[^"]*">[^<]*</at>', "", text).strip()
        except Exception:
            text = content_raw

        # 异步处理消息（快速返回 200 给飞书，避免超时重试）
        asyncio.create_task(_process_message_event(event_data, sender_open_id, chat_id, text))

    elif event_type == "card.action.trigger":
        # Interactive Card 按钮回调（HITL 审批）
        action_value = event_data.get("action", {}).get("value", {})
        action_id: str = action_value.get("action_id", "")
        log_id_str: str = action_value.get("log_id", "")
        operator = event_data.get("operator", {}).get("open_id", "unknown")

        if log_id_str.isdigit():
            log_id = int(log_id_str)
            if action_id.endswith("_reject"):
                result = handle_approval_callback(log_id, "reject", operator)
            else:
                result = handle_approval_callback(log_id, "approve", operator)
            return Response(
                content=json.dumps({"toast": {"type": "success", "content": f"已{result.get('status', '处理')}审批请求"}}),
                media_type="application/json",
            )

    return Response(content=json.dumps({"code": 0}), media_type="application/json")


@app.get("/health")
async def health_check():
    """健康检查端点（供 Docker healthcheck 使用）。"""
    return {
        "status": "healthy",
        "service": "qixuan-agent-feishu-gateway",
        "timestamp": datetime.now().isoformat(),
        "feishu_configured": bool(FEISHU_APP_ID and FEISHU_APP_SECRET),
    }
