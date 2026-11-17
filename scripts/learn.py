"""
启煊智能体 - /learn 技能自进化命令
功能：分析当前会话的工具调用链，自动生成标准 SKILL.md 草稿并提交内部 Git 审核

用法：
  python scripts/learn.py --skill-name <技能名> --session-id <会话ID> [--push-to-git]

工作流：
  1. 从 SQLite 读取指定会话的完整工具调用轨迹
  2. 调用快速摘要模型（fast）提炼成功路径与避坑要点
  3. 按 SKILL.md 标准格式生成草稿
  4. 保存至 skills/qixuan/<skill-name>/SKILL.md
  5. 可选：提交 Git PR 至内部技能仓库供团队审核
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# 将项目根加入路径
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.memory.memory_tools import init_db, memory_save


SKILL_MD_TEMPLATE = '''---
name: {skill_name}
version: 1.0.0
description: {description}
author: 启煊软件（由 /learn 自动生成，经人工审核）
generated_from_session: {session_id}
generated_at: {generated_at}
tags: {tags}
---

# 技能：{skill_name}

## 适用场景
{use_cases}

## 执行工作流

{workflow}

## 工具调用链
{tool_chain}

## 避坑要点（从本次实践中提炼）
{pitfalls}

## 输出格式参考
{output_format}
'''


def load_session_trajectory(session_id: str, profile: str = "default") -> list[dict]:
    """从 SQLite FTS 库中加载会话工具调用轨迹。"""
    conn = init_db(profile)
    rows = conn.execute(
        "SELECT f.title, f.content, f.category, f.tags, m.created_at "
        "FROM memory_fts f JOIN memory_meta m ON f.rowid = m.rowid "
        "WHERE f.session_id = ? AND m.source = 'session' "
        "ORDER BY m.created_at",
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def generate_skill_draft(
    skill_name: str,
    session_id: str,
    trajectory: list[dict],
    profile: str = "default",
) -> str:
    """
    根据会话轨迹生成 SKILL.md 草稿。
    生产环境：调用 fast 模型（DeepSeek-V4-Flash）进行智能摘要提炼。
    当前实现：基于规则的结构化提取（离线可用）。
    """
    # 提取工具调用记录
    tool_calls = [r for r in trajectory if "tool" in r.get("category", "").lower()]
    decisions = [r for r in trajectory if "decision" in r.get("category", "").lower()]
    errors = [r for r in trajectory if "error" in r.get("category", "").lower() or "pitfall" in r.get("tags", "")]

    # 构建工具链描述
    tool_chain_lines = []
    for i, t in enumerate(tool_calls, 1):
        tool_chain_lines.append(f"{i}. `{t.get('title', 'unknown_tool')}` — {t.get('content', '')[:100]}")
    tool_chain = "\n".join(tool_chain_lines) if tool_chain_lines else "（请手动补充工具调用步骤）"

    # 构建避坑要点
    pitfall_lines = []
    for e in errors:
        pitfall_lines.append(f"- {e.get('content', '')[:200]}")
    pitfalls = "\n".join(pitfall_lines) if pitfall_lines else "（本次实践未记录明显避坑点）"

    # 推导 tags
    all_tags = set()
    for r in trajectory:
        for tag in r.get("tags", "").split(","):
            if tag.strip():
                all_tags.add(tag.strip())
    tags_str = str(list(all_tags)[:8])

    draft = SKILL_MD_TEMPLATE.format(
        skill_name=skill_name,
        description=f"由 /learn 命令从会话 {session_id} 自动提炼，请人工审核补充。",
        session_id=session_id,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        tags=tags_str,
        use_cases="（请根据本次任务场景补充适用条件）",
        workflow="（请根据工具调用链整理标准执行步骤）",
        tool_chain=tool_chain,
        pitfalls=pitfalls,
        output_format="（请补充标准输出格式示例）",
    )
    return draft


def save_skill_draft(skill_name: str, draft: str, skills_base: Path) -> Path:
    """将生成的 SKILL.md 草稿保存到技能目录。"""
    skill_dir = skills_base / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)
    output_path = skill_dir / "SKILL.md"

    # 若已存在，备份旧版
    if output_path.exists():
        backup = skill_dir / f"SKILL.md.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        output_path.rename(backup)
        print(f"  已备份旧版至：{backup}")

    output_path.write_text(draft, encoding="utf-8")
    return output_path


def push_to_git(skill_name: str, skills_base: Path) -> bool:
    """将新技能提交至内部 Git 技能仓库（需预先配置远端）。"""
    import subprocess
    skill_path = skills_base / skill_name / "SKILL.md"

    cmds = [
        ["git", "-C", str(skills_base), "add", str(skill_path)],
        ["git", "-C", str(skills_base), "commit", "-m",
         f"feat(skill): /learn 自动生成技能草稿 - {skill_name} [{datetime.now().strftime('%Y-%m-%d')}]"],
        ["git", "-C", str(skills_base), "push"],
    ]
    for cmd in cmds:
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"  Git 命令失败：{' '.join(cmd)}\n  {result.stderr[:300]}")
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description="启煊智能体 /learn 技能自进化命令")
    parser.add_argument("--skill-name", required=True, help="新技能名称（如 qixuan-contract-review）")
    parser.add_argument("--session-id", required=True, help="来源会话 ID")
    parser.add_argument("--profile", default="default", help="Profile 名称（默认 default）")
    parser.add_argument("--skills-dir", default=str(PROJECT_ROOT / "skills" / "qixuan"), help="技能目录路径")
    parser.add_argument("--push-to-git", action="store_true", help="生成后自动提交至内部 Git 仓库")
    args = parser.parse_args()

    print(f"\n🧠 /learn 技能自进化启动")
    print(f"   技能名称：{args.skill_name}")
    print(f"   来源会话：{args.session_id}")
    print(f"   Profile：{args.profile}")

    # 加载会话轨迹
    print("\n[1/4] 加载会话轨迹...")
    trajectory = load_session_trajectory(args.session_id, args.profile)
    print(f"      找到 {len(trajectory)} 条轨迹记录")
    if not trajectory:
        print("      ⚠️  会话轨迹为空。请确认 session_id 正确，且会话数据已保存至记忆库。")

    # 生成草稿
    print("\n[2/4] 生成 SKILL.md 草稿...")
    draft = generate_skill_draft(args.skill_name, args.session_id, trajectory, args.profile)
    print(f"      草稿生成完成（{len(draft)} 字符）")

    # 保存文件
    print("\n[3/4] 保存技能文件...")
    skills_base = Path(args.skills_dir)
    output_path = save_skill_draft(args.skill_name, draft, skills_base)
    print(f"      ✅ 已保存至：{output_path}")

    # 可选：推送 Git
    if args.push_to_git:
        print("\n[4/4] 提交至内部 Git 仓库...")
        success = push_to_git(args.skill_name, skills_base)
        if success:
            print("      ✅ 已成功提交，等待团队成员审核 PR")
        else:
            print("      ❌ Git 提交失败，请手动检查仓库配置")
    else:
        print("\n[4/4] 跳过 Git 提交（如需提交请加 --push-to-git 参数）")

    print(f"\n🎉 /learn 完成！请打开 {output_path} 人工补充与审核技能内容。\n")


if __name__ == "__main__":
    main()
