"""
启煊智能体 - 三层持久化记忆工具
功能：SQLite FTS5 全文检索 + 向量语义召回 + MEMORY.md/USER.md 精简上下文注入

架构层：认知与进化核心层（Agent Core）
"""

import sqlite3
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------
DATA_DIR = Path(os.environ.get("HERMES_DATA_DIR", "/app/data"))
DEFAULT_PROFILE = os.environ.get("QIXUAN_PROFILE", "default")
PROFILES_DIR = DATA_DIR / "profiles"
SQLITE_DIR = DATA_DIR / "sqlite"
DB_NAME = "qixuan_memory.db"
MAX_FTS_RESULTS = 20


# ---------------------------------------------------------------------------
# 数据库初始化
# ---------------------------------------------------------------------------

def get_db_path(profile: str = DEFAULT_PROFILE) -> Path:
    """返回当前 profile 对应的 SQLite 数据库路径。"""
    db_dir = SQLITE_DIR / profile
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / DB_NAME


def init_db(profile: str = DEFAULT_PROFILE) -> sqlite3.Connection:
    """初始化 SQLite 数据库，创建 FTS5 全文检索表与元数据表。"""
    db_path = get_db_path(profile)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.executescript("""
        -- 会话历史与知识片段 FTS5 全文检索表
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            session_id,
            timestamp,
            category,       -- 类别: skill_experience | project_decision | bug_resolution | customer_note
            tags,           -- 逗号分隔的标签
            title,
            content,
            tokenize = 'unicode61 remove_diacritics 2'
        );

        -- 结构化元数据表（与 FTS 表关联，存储非检索字段）
        CREATE TABLE IF NOT EXISTS memory_meta (
            rowid       INTEGER PRIMARY KEY,
            profile     TEXT NOT NULL DEFAULT 'default',
            project_id  TEXT,
            source      TEXT,   -- 来源: session | learn_command | webhook
            created_at  TEXT NOT NULL,
            token_count INTEGER DEFAULT 0
        );

        -- 审计日志表
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            profile     TEXT NOT NULL,
            session_id  TEXT,
            tool_name   TEXT NOT NULL,
            input_hash  TEXT,
            status      TEXT NOT NULL,  -- pending | approved | rejected | executed
            approver    TEXT,
            created_at  TEXT NOT NULL,
            executed_at TEXT
        );
    """)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# 核心工具函数
# ---------------------------------------------------------------------------

def memory_search(
    query: str,
    profile: str = DEFAULT_PROFILE,
    category: Optional[str] = None,
    project_id: Optional[str] = None,
    limit: int = 10,
) -> list[dict]:
    """
    FTS5 全文检索历史记忆。

    Args:
        query:      检索关键词（支持自然语言与 FTS5 语法）
        profile:    租户 Profile（默认 default）
        category:   按分类过滤（skill_experience | project_decision | bug_resolution | customer_note）
        project_id: 按项目 ID 过滤
        limit:      返回结果数量上限（最多 20）

    Returns:
        命中的记忆片段列表，按相关性排序。
    """
    conn = init_db(profile)
    limit = min(limit, MAX_FTS_RESULTS)

    # 构建 FTS5 查询
    # SQLite unicode61 分词器在中文场景下将连续汉字序列作为整体 token 存储。
    # 因此检索策略：
    #   - 中文片段：提取所有最长连续汉字子串，每个子串加 * 前缀通配，用 OR 连接。
    #   - ASCII 词：直接用空格分隔，FTS5 默认 AND 语义。
    # 同时维护一个 LIKE 回退列表，用于 FTS 无命中时的兜底查询。
    cleaned = re.sub(r'[^\w\s\u4e00-\u9fff]', ' ', query).strip()
    if not cleaned:
        return []

    # 提取连续汉字子串（最长优先）
    chinese_segments = re.findall(r'[\u4e00-\u9fff]+', cleaned)
    ascii_words = re.findall(r'[a-zA-Z0-9_]+', cleaned)

    fts_parts: list[str] = []
    if chinese_segments:
        # 每个汉字子串加前缀 * 通配，用 OR 连接（FTS5: "多租户" OR "越权" ... ）
        fts_parts.append(" OR ".join(f'"{seg}"*' for seg in chinese_segments))
    if ascii_words:
        fts_parts.extend(ascii_words)

    if not fts_parts:
        return []
    safe_query = " AND ".join(f"({p})" for p in fts_parts) if len(fts_parts) > 1 else fts_parts[0]

    # LIKE 回退条件（FTS 无结果时兜底）
    like_conditions = []
    like_params_extra: list = []
    for seg in chinese_segments:
        like_conditions.append("(f.title LIKE ? OR f.content LIKE ? OR f.tags LIKE ?)")
        like_params_extra += [f"%{seg}%", f"%{seg}%", f"%{seg}%"]

    def _run_query(conn, use_fts: bool) -> list[dict]:
        """执行 FTS 或 LIKE 回退查询。"""
        if use_fts:
            sql = """
                SELECT
                    f.rowid,
                    f.session_id,
                    f.timestamp,
                    f.category,
                    f.tags,
                    f.title,
                    f.content AS excerpt,
                    m.project_id,
                    m.source,
                    m.created_at,
                    rank
                FROM memory_fts f
                JOIN memory_meta m ON f.rowid = m.rowid
                WHERE memory_fts MATCH ?
                AND m.profile = ?
            """
            params: list = [safe_query, profile]
        else:
            # LIKE 回退：当 FTS 无结果时使用
            if not like_conditions:
                return []
            sql = """
                SELECT
                    f.rowid,
                    f.session_id,
                    f.timestamp,
                    f.category,
                    f.tags,
                    f.title,
                    f.content AS excerpt,
                    m.project_id,
                    m.source,
                    m.created_at,
                    0 AS rank
                FROM memory_fts f
                JOIN memory_meta m ON f.rowid = m.rowid
                WHERE ({like_clause})
                AND m.profile = ?
            """.format(like_clause=" AND ".join(like_conditions))
            params = like_params_extra + [profile]

        if category:
            sql += " AND f.category = ?"
            params.append(category)
        if project_id:
            sql += " AND m.project_id = ?"
            params.append(project_id)

        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)

        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    try:
        # 先尝试 FTS，无结果则 LIKE 回退
        results = _run_query(conn, use_fts=True)
        if not results and like_conditions:
            results = _run_query(conn, use_fts=False)
        conn.close()
        return results
    except sqlite3.OperationalError as e:
        conn.close()
        return [{"error": f"检索失败：{e}", "query": query}]


def memory_save(
    title: str,
    content: str,
    category: str,
    tags: list[str],
    profile: str = DEFAULT_PROFILE,
    session_id: str = "",
    project_id: Optional[str] = None,
    source: str = "session",
    token_count: int = 0,
) -> dict:
    """
    将经验片段写入长期记忆库。

    Args:
        title:      记忆标题（简明扼要）
        content:    记忆内容正文（Markdown 格式）
        category:   分类（skill_experience | project_decision | bug_resolution | customer_note）
        tags:       标签列表（如 ["java", "多租户", "性能优化"]）
        profile:    目标 Profile
        session_id: 来源会话 ID
        project_id: 关联项目 ID（可选）
        source:     写入来源（session | learn_command | webhook）
        token_count: 本次内容的 Token 估算数

    Returns:
        写入结果（包含 rowid）
    """
    conn = init_db(profile)
    timestamp = datetime.now().isoformat()
    tags_str = ",".join(tags) if tags else ""

    cursor = conn.execute(
        "INSERT INTO memory_fts (session_id, timestamp, category, tags, title, content) VALUES (?,?,?,?,?,?)",
        (session_id, timestamp, category, tags_str, title, content),
    )
    rowid = cursor.lastrowid

    conn.execute(
        "INSERT INTO memory_meta (rowid, profile, project_id, source, created_at, token_count) VALUES (?,?,?,?,?,?)",
        (rowid, profile, project_id, source, timestamp, token_count),
    )
    conn.commit()
    conn.close()

    return {
        "status": "saved",
        "rowid": rowid,
        "profile": profile,
        "category": category,
        "title": title,
        "timestamp": timestamp,
    }


def memory_load_context(profile: str = DEFAULT_PROFILE) -> dict:
    """
    加载精简上下文注入内容（USER.md + MEMORY.md），总 Token 预算 ≤ 4000。

    Returns:
        {"user_md": str, "memory_md": str, "total_chars": int}
    """
    profile_dir = PROFILES_DIR / profile
    user_md_path = profile_dir / "USER.md"
    memory_md_path = profile_dir / "MEMORY.md"

    # Token 预算（以字符数粗估：1 Token ≈ 1.5 中文字符 或 4 英文字符）
    USER_MD_CHAR_LIMIT = 1500 * 2      # ~1500 Tokens
    MEMORY_MD_CHAR_LIMIT = 2500 * 2    # ~2500 Tokens

    user_md = ""
    if user_md_path.exists():
        content = user_md_path.read_text(encoding="utf-8")
        user_md = content[:USER_MD_CHAR_LIMIT]
        if len(content) > USER_MD_CHAR_LIMIT:
            user_md += "\n... [USER.md 已截断以控制上下文预算] ..."

    memory_md = ""
    if memory_md_path.exists():
        content = memory_md_path.read_text(encoding="utf-8")
        memory_md = content[:MEMORY_MD_CHAR_LIMIT]
        if len(content) > MEMORY_MD_CHAR_LIMIT:
            memory_md += "\n... [MEMORY.md 已截断以控制上下文预算] ..."

    return {
        "user_md": user_md,
        "memory_md": memory_md,
        "total_chars": len(user_md) + len(memory_md),
        "profile": profile,
    }


def memory_update_md(
    section: str,
    content: str,
    profile: str = DEFAULT_PROFILE,
    file: str = "MEMORY.md",
) -> dict:
    """
    向 MEMORY.md 或 USER.md 追加或更新一个 Section。

    Args:
        section:  Markdown 二级标题（如 "## 项目：XX工厂MES系统"）
        content:  该 section 下的内容
        profile:  目标 Profile
        file:     目标文件（MEMORY.md 或 USER.md）

    Returns:
        {"status": "appended" | "updated", "file": str}
    """
    profile_dir = PROFILES_DIR / profile
    profile_dir.mkdir(parents=True, exist_ok=True)
    file_path = profile_dir / file

    existing = file_path.read_text(encoding="utf-8") if file_path.exists() else ""

    # 检查 section 是否已存在（以标题行匹配）
    section_header = section.strip().split("\n")[0]
    if section_header in existing:
        # 替换已有 section 内容
        pattern = re.escape(section_header) + r".*?(?=\n## |\Z)"
        new_content = re.sub(pattern, section.strip() + "\n" + content.strip(), existing, flags=re.DOTALL)
        status = "updated"
    else:
        new_content = existing.rstrip("\n") + "\n\n" + section.strip() + "\n" + content.strip() + "\n"
        status = "appended"

    file_path.write_text(new_content, encoding="utf-8")
    return {"status": status, "file": str(file_path), "profile": profile}


def audit_log_write(
    tool_name: str,
    profile: str,
    session_id: str,
    input_data: dict,
    status: str = "pending",
) -> int:
    """写入审计日志，返回日志记录 ID（用于后续状态更新）。"""
    conn = init_db(profile)
    input_hash = str(hash(json.dumps(input_data, sort_keys=True, ensure_ascii=False)))
    created_at = datetime.now().isoformat()

    cursor = conn.execute(
        "INSERT INTO audit_log (profile, session_id, tool_name, input_hash, status, created_at) VALUES (?,?,?,?,?,?)",
        (profile, session_id, tool_name, input_hash, status, created_at),
    )
    log_id = cursor.lastrowid
    conn.commit()
    conn.close()
    return log_id


def audit_log_update(log_id: int, status: str, approver: str = "", profile: str = DEFAULT_PROFILE) -> dict:
    """更新审计日志状态（approved / rejected / executed）。"""
    conn = init_db(profile)
    executed_at = datetime.now().isoformat() if status == "executed" else None
    conn.execute(
        "UPDATE audit_log SET status=?, approver=?, executed_at=? WHERE id=?",
        (status, approver, executed_at, log_id),
    )
    conn.commit()
    conn.close()
    return {"log_id": log_id, "status": status, "approver": approver}
