#!/usr/bin/env bash
# ==============================================================================
# 启煊智能体健康检查与验收脚本
# 用法：bash scripts/health_check.sh [--base-dir /opt/qixuan-agent]
# ==============================================================================
set -euo pipefail

BASE_DIR="${1:-/opt/qixuan-agent}"
if [[ "${1:-}" == "--base-dir" && -n "${2:-}" ]]; then
    BASE_DIR="$2"
fi

PASS=0
WARN=0
FAIL=0

check_pass() { echo "  ✅ $1"; ((PASS++)) || true; }
check_warn() { echo "  ⚠️  $1"; ((WARN++)) || true; }
check_fail() { echo "  ❌ $1"; ((FAIL++)) || true; }

echo ""
echo "╔══════════════════════════════════════════════════════════════╗"
echo "║          启煊智能体（Qixuan Agent）健康验收检查              ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""

# ----- 1. Docker 容器状态 -----
echo "【1】容器运行状态"
if command -v docker &>/dev/null; then
    for container in qixuan-agent-core qixuan-sandbox-proxy qixuan-local-embedding; do
        status=$(docker inspect --format='{{.State.Status}}' "${container}" 2>/dev/null || echo "not_found")
        health=$(docker inspect --format='{{.State.Health.Status}}' "${container}" 2>/dev/null || echo "none")
        if [ "${status}" = "running" ]; then
            if [ "${health}" = "healthy" ] || [ "${health}" = "none" ]; then
                check_pass "${container}：running (${health})"
            else
                check_warn "${container}：running 但健康状态为 ${health}"
            fi
        elif [ "${status}" = "not_found" ]; then
            check_warn "${container}：未找到（可能未启动）"
        else
            check_fail "${container}：状态异常（${status}）"
        fi
    done
else
    check_warn "Docker 命令不可用，跳过容器检查"
fi

# ----- 2. 网关健康接口 -----
echo ""
echo "【2】网关 HTTP 健康接口"
GATEWAY_URL="${QIXUAN_GATEWAY_URL:-http://localhost:8090}"
if curl -sf --max-time 5 "${GATEWAY_URL}/health" -o /dev/null 2>/dev/null; then
    check_pass "网关健康接口 ${GATEWAY_URL}/health 响应正常"
else
    check_fail "网关健康接口 ${GATEWAY_URL}/health 无响应（请确认容器已启动）"
fi

# ----- 3. 核心配置文件 -----
echo ""
echo "【3】核心配置文件"
for f in "${BASE_DIR}/config/config.yaml" "${BASE_DIR}/docker-compose.yml"; do
    if [ -f "$f" ]; then
        check_pass "$(basename $f) 存在"
    else
        check_fail "$(basename $f) 缺失！"
    fi
done

if [ -f "${BASE_DIR}/.env" ]; then
    perm=$(stat -c "%a" "${BASE_DIR}/.env" 2>/dev/null || stat -f "%Lp" "${BASE_DIR}/.env" 2>/dev/null || echo "unknown")
    if [ "$perm" = "600" ]; then
        check_pass ".env 存在且权限为 600"
    else
        check_warn ".env 存在但权限为 ${perm}（建议设为 600：chmod 600 .env）"
    fi
    # 检查关键变量是否已填写
    if grep -q "HF_TOKEN=hf_" "${BASE_DIR}/.env" 2>/dev/null; then
        check_pass "HF_TOKEN 已配置"
    else
        check_warn "HF_TOKEN 似乎未填写真实值，请检查 .env"
    fi
else
    check_fail ".env 文件不存在！请执行：cp .env.example .env 并填入密钥"
fi

# ----- 4. 技能文件 -----
echo ""
echo "【4】技能文件完整性"
for skill in qixuan-req-analyzer qixuan-code-reviewer qixuan-ops-sentinel qixuan-release-notes; do
    skill_file="${BASE_DIR}/skills/qixuan/${skill}/SKILL.md"
    if [ -f "${skill_file}" ]; then
        check_pass "${skill}/SKILL.md 存在"
    else
        check_fail "${skill}/SKILL.md 缺失！"
    fi
done

# ----- 5. 记忆文件 -----
echo ""
echo "【5】记忆文件（Profile: default）"
for f in MEMORY.md USER.md; do
    fp="${BASE_DIR}/data/profiles/default/${f}"
    if [ -f "$fp" ]; then
        lines=$(wc -l < "$fp" 2>/dev/null || echo 0)
        if [ "$lines" -gt 2 ]; then
            check_pass "${f} 存在且有内容（${lines} 行）"
        else
            check_warn "${f} 存在但内容过少（${lines} 行），建议补充项目规约"
        fi
    else
        check_warn "${f} 不存在，将在首次对话时自动创建"
    fi
done

# ----- 6. 日志目录 -----
echo ""
echo "【6】日志目录"
if [ -d "${BASE_DIR}/data/logs" ] || [ -d "${BASE_DIR}/logs" ]; then
    check_pass "日志目录存在"
else
    check_warn "日志目录不存在，请检查 Docker volume 挂载配置"
fi

# ----- 汇总 -----
echo ""
echo "════════════════════════════════════════════════════════════════"
TOTAL=$((PASS + WARN + FAIL))
echo "  检查总计：${TOTAL} 项 | ✅ 通过 ${PASS} | ⚠️  警告 ${WARN} | ❌ 失败 ${FAIL}"
echo "════════════════════════════════════════════════════════════════"
echo ""

if [ "${FAIL}" -gt 0 ]; then
    echo "  存在 ${FAIL} 个失败项，请根据上方提示修复后重新运行本脚本。"
    exit 1
elif [ "${WARN}" -gt 0 ]; then
    echo "  存在 ${WARN} 个警告项，建议处理后进行生产部署。"
    exit 0
else
    echo "  🎉 所有检查项通过，启煊智能体已就绪！"
    exit 0
fi
