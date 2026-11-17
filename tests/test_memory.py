"""
启煊智能体 - 记忆系统单元测试
"""

import pytest
import tempfile
import os
from pathlib import Path

# 使用临时目录隔离测试数据
@pytest.fixture
def tmp_data_dir(tmp_path):
    os.environ["HERMES_DATA_DIR"] = str(tmp_path)
    yield tmp_path
    del os.environ["HERMES_DATA_DIR"]


def test_memory_save_and_search(tmp_data_dir):
    """测试写入记忆片段后可以通过 FTS5 检索到。"""
    from tools.memory.memory_tools import memory_save, memory_search

    result = memory_save(
        title="多租户越权修复经验",
        content="在 OrderService.getOrderDetail 方法中，查询前必须校验 tenant_id，否则存在水平越权风险。修复方式：使用 LambdaQueryWrapper 添加 eq(Order::getTenantId, currentTenantId) 条件。",
        category="bug_resolution",
        tags=["java", "多租户", "安全", "spring-boot"],
        profile="test",
        session_id="sess_test_001",
    )
    assert result["status"] == "saved"
    assert result["rowid"] > 0

    # FTS5 检索
    hits = memory_search("多租户越权", profile="test", limit=5)
    assert len(hits) >= 1
    assert "多租户" in hits[0].get("title", "") or "多租户" in hits[0].get("excerpt", "")


def test_memory_save_and_search_by_category(tmp_data_dir):
    """测试按分类过滤检索。"""
    from tools.memory.memory_tools import memory_save, memory_search

    memory_save(
        title="需求评估经验 - 供应链模块",
        content="供应链管理模块评估时，库存预警功能可复用公司现有消息推送中心，无需重新开发告警发送逻辑。",
        category="skill_experience",
        tags=["需求分析", "供应链", "复用"],
        profile="test",
    )

    # 按正确分类检索
    hits = memory_search("供应链", profile="test", category="skill_experience")
    assert len(hits) >= 1

    # 按错误分类检索应返回空
    hits_wrong = memory_search("供应链", profile="test", category="bug_resolution")
    assert len(hits_wrong) == 0


def test_memory_load_context(tmp_data_dir):
    """测试上下文加载，验证 Token 预算约束。"""
    from tools.memory.memory_tools import memory_load_context

    profile_dir = tmp_data_dir / "profiles" / "test_ctx"
    profile_dir.mkdir(parents=True)
    (profile_dir / "USER.md").write_text("# 测试用户偏好\n- 语言：中文\n", encoding="utf-8")
    (profile_dir / "MEMORY.md").write_text("# 测试企业规约\n- 技术栈：Java\n", encoding="utf-8")

    ctx = memory_load_context("test_ctx")
    assert "测试用户偏好" in ctx["user_md"]
    assert "测试企业规约" in ctx["memory_md"]
    assert ctx["total_chars"] < 8000  # 总预算约 4000 Tokens


def test_memory_update_md_append(tmp_data_dir):
    """测试向 MEMORY.md 追加新 Section。"""
    from tools.memory.memory_tools import memory_update_md, memory_load_context

    result = memory_update_md(
        section="## 项目：XX制造MES系统",
        content="- 技术栈：Java 17 + Vue 3\n- 核心模块：生产调度、库存管理\n",
        profile="test_update",
        file="MEMORY.md",
    )
    assert result["status"] == "appended"

    # 追加第二次应更新而非重复
    result2 = memory_update_md(
        section="## 项目：XX制造MES系统",
        content="- 技术栈：Java 17 + Vue 3（已更新）\n",
        profile="test_update",
        file="MEMORY.md",
    )
    assert result2["status"] == "updated"

    # 验证文件内容不重复
    ctx = memory_load_context("test_update")
    assert ctx["memory_md"].count("XX制造MES系统") == 1


def test_audit_log(tmp_data_dir):
    """测试审计日志写入与状态更新。"""
    from tools.memory.memory_tools import audit_log_write, audit_log_update, init_db

    log_id = audit_log_write(
        tool_name="k8s_pod_restart",
        profile="test",
        session_id="sess_ops_001",
        input_data={"namespace": "prod", "pod_name": "mes-worker-7d8f9"},
        status="pending",
    )
    assert log_id > 0

    result = audit_log_update(log_id, "approved", "ops_engineer_01", "test")
    assert result["status"] == "approved"

    # 验证数据库中状态已更新
    conn = init_db("test")
    row = conn.execute("SELECT status, approver FROM audit_log WHERE id=?", (log_id,)).fetchone()
    assert row["status"] == "approved"
    assert row["approver"] == "ops_engineer_01"
    conn.close()
