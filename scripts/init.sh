#!/usr/bin/env bash
# ==============================================================================
# 启煊智能体 (Qixuan Agent) 一键初始化脚本
# 功能：创建标准目录结构、归位配置文件、初始化记忆库、设置文件权限
# 用法：bash scripts/init.sh [--base-dir /opt/qixuan-agent]
# ==============================================================================
set -euo pipefail

# ----- 参数解析 -----
BASE_DIR="${1:-/opt/qixuan-agent}"
if [[ "$1" == "--base-dir" && -n "${2:-}" ]]; then
    BASE_DIR="$2"
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"

echo "╔══════════════════════════════════════════════════════════════╗"
echo "║      启煊软件企业级智能体 (Qixuan Agent) 初始化脚本          ║"
echo "╚══════════════════════════════════════════════════════════════╝"
echo ""
echo ">>> 目标目录：${BASE_DIR}"
echo ">>> 项目源码：${PROJECT_ROOT}"
echo ""

# ----- 创建标准目录树 -----
echo "[1/6] 创建目录结构..."
mkdir -p "${BASE_DIR}"/{config,logs,workspace/req_analysis,deliverables/releases,hf_cache}
mkdir -p "${BASE_DIR}"/data/{profiles/default,sqlite,logs}
mkdir -p "${BASE_DIR}"/skills/qixuan/{qixuan-req-analyzer,qixuan-code-reviewer,qixuan-ops-sentinel,qixuan-release-notes}
echo "      ✅ 目录结构创建完成"

# ----- 归位配置文件 -----
echo "[2/6] 归位配置文件..."
if [ -f "${PROJECT_ROOT}/config/config.yaml" ]; then
    cp "${PROJECT_ROOT}/config/config.yaml" "${BASE_DIR}/config/config.yaml"
    echo "      ✅ config.yaml 已归位"
fi

if [ -f "${PROJECT_ROOT}/.env.example" ]; then
    cp "${PROJECT_ROOT}/.env.example" "${BASE_DIR}/.env.example"
    if [ ! -f "${BASE_DIR}/.env" ]; then
        cp "${PROJECT_ROOT}/.env.example" "${BASE_DIR}/.env"
        echo "      ✅ .env 已从模板创建（请填入真实密钥！）"
    else
        echo "      ⚠️  .env 已存在，跳过覆盖"
    fi
fi

if [ -f "${PROJECT_ROOT}/docker-compose.yml" ]; then
    cp "${PROJECT_ROOT}/docker-compose.yml" "${BASE_DIR}/docker-compose.yml"
    echo "      ✅ docker-compose.yml 已归位"
fi

# ----- 归位技能文件 -----
echo "[3/6] 归位技能文件..."
for skill in qixuan-req-analyzer qixuan-code-reviewer qixuan-ops-sentinel qixuan-release-notes; do
    src="${PROJECT_ROOT}/skills/qixuan/${skill}/SKILL.md"
    dst="${BASE_DIR}/skills/qixuan/${skill}/SKILL.md"
    if [ -f "${src}" ]; then
        cp "${src}" "${dst}"
        echo "      ✅ ${skill}/SKILL.md 已归位"
    else
        echo "      ⚠️  ${src} 不存在，跳过"
    fi
done

# ----- 初始化记忆文件 -----
echo "[4/6] 初始化企业默认记忆文件..."
MEMORY_FILE="${BASE_DIR}/data/profiles/default/MEMORY.md"
USER_FILE="${BASE_DIR}/data/profiles/default/USER.md"

if [ ! -f "${MEMORY_FILE}" ]; then
    if [ -f "${PROJECT_ROOT}/data/profiles/default/MEMORY.md" ]; then
        cp "${PROJECT_ROOT}/data/profiles/default/MEMORY.md" "${MEMORY_FILE}"
    else
        cat > "${MEMORY_FILE}" << 'MEMORY_EOF'
# 启煊软件企业核心研发与交付约定

## 技术栈规范
- 后端：Java 17 (Spring Boot 3 / MyBatis-Plus)、Go
- 前端：Vue 3 + TypeScript + Element Plus
- 数据库：MySQL 8.0 + Redis 7 + Elasticsearch 8
- 多租户红线：所有 SaaS 表必须含 tenant_id，严禁跨租户无条件查询
- 交付红线：对外版本说明禁止出现内部类名、表名、缺陷单号
MEMORY_EOF
    fi
    echo "      ✅ MEMORY.md 初始化完成"
else
    echo "      ⚠️  MEMORY.md 已存在，跳过"
fi

if [ ! -f "${USER_FILE}" ]; then
    if [ -f "${PROJECT_ROOT}/data/profiles/default/USER.md" ]; then
        cp "${PROJECT_ROOT}/data/profiles/default/USER.md" "${USER_FILE}"
    else
        touch "${USER_FILE}"
    fi
    echo "      ✅ USER.md 初始化完成"
fi

# ----- 设置文件权限 -----
echo "[5/6] 设置文件权限..."
if [ -f "${BASE_DIR}/.env" ]; then
    chmod 600 "${BASE_DIR}/.env"
    echo "      ✅ .env 权限设置为 600"
fi
chmod 700 "${BASE_DIR}/data"
chmod 755 "${BASE_DIR}/skills"
echo "      ✅ 目录权限设置完成"

# ----- 打印验收清单 -----
echo "[6/6] 初始化完成！"
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  目录结构："
echo "════════════════════════════════════════════════════════════════"
tree "${BASE_DIR}" --noreport -L 4 2>/dev/null || find "${BASE_DIR}" -maxdepth 4 | sort | sed "s|${BASE_DIR}||" | sed 's|/[^/]*$|  &|'
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  下一步操作："
echo "════════════════════════════════════════════════════════════════"
echo ""
echo "  1. 编辑 ${BASE_DIR}/.env，填入以下关键配置："
echo "     HF_TOKEN=hf_xxxx          (Hugging Face 推理令牌)"
echo "     FEISHU_APP_ID=cli_xxxx    (飞书应用 ID)"
echo "     FEISHU_APP_SECRET=xxxx    (飞书应用密钥)"
echo "     GIT_REVIEWER_TOKEN=xxxx   (GitLab 只读令牌)"
echo ""
echo "  2. 启动标准模式："
echo "     cd ${BASE_DIR} && docker compose up -d"
echo ""
echo "  3. 启动涉密混部模式（需要 NVIDIA GPU）："
echo "     cd ${BASE_DIR} && docker compose --profile confidential up -d"
echo ""
echo "  4. 验收检查："
echo "     docker compose ps                    # 确认所有容器 Up (healthy)"
echo "     docker exec -it qixuan-agent-core hermes doctor"
echo ""
