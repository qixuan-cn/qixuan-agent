# 启煊软件企业级智能体（Qixuan Agent）

> 基于 **[Hermes Agent](https://github.com/NousResearch/hermes-agent)** 持久记忆架构 ＋ **Hugging Face Inference Providers** 顶级开源模型矩阵，为启煊软件定制的企业级自主智能体中枢。

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-Proprietary-red)](LICENSE)
[![Hermes](https://img.shields.io/badge/Base-Hermes%20Agent-purple)](https://github.com/NousResearch/hermes-agent)

---

## 目录

- [项目背景与建设目标](#项目背景与建设目标)
- [系统架构](#系统架构)
- [模型路由策略](#模型路由策略)
- [四大核心技能](#四大核心技能)
- [仓库结构](#仓库结构)
- [文档目录](#文档目录)
- [快速部署](#快速部署)
- [安全合规](#安全合规)
- [实施里程碑](#实施里程碑)

---

## 项目背景与建设目标

随着启煊软件研发项目与客户交付规模扩大，团队在需求分析、代码审查、环境巡检与版本交付等环节面临大量重复性工作。传统单轮对话式 AI 缺乏跨会话记忆与团队知识沉淀能力，难以形成组织级资产。

**Qixuan Agent** 以 Hermes Agent 为底座，围绕四项核心目标构建：

| 目标 | 实现方式 |
|:--|:--|
| **经验复利** | 三层持久化记忆（`USER.md` + `MEMORY.md` + SQLite FTS5），跨会话保留项目规约与排障经验 |
| **技能沉淀** | 标准化 `SKILL.md` 协议 + `/learn` 自进化命令，高频 SOP 封装为热插拔技能包 |
| **混合算力与数据安全** | 通用任务路由至 HF 云端旗舰模型；涉密项目物理隔离，路由至内网 vLLM 节点，数据零出网 |
| **全天候多端协同** | 飞书机器人、GitLab Webhook、Alertmanager 告警、Cron 定时任务统一接入 |

---

## 系统架构

Qixuan Agent 采用五层解耦架构：

```
┌─────────────────────────────────────────────────────────────────┐
│  1. 多端接入网关层（Gateway Layer）                               │
│     飞书机器人  │  GitLab Webhook  │  Alertmanager  │  Cron     │
│     gateway/feishu/          gateway/webhook_router.py          │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│  2. 企业治理与隔离层（Governance Layer）                          │
│     多租户 Profile 隔离  │  HITL 高危审批  │  审计日志           │
│     governance/hitl.py        governance/router.py              │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│  3. 认知与进化核心层（Agent Core）                                │
│     USER.md & MEMORY.md  │  SQLite FTS5 检索  │  /learn 进化    │
│     tools/memory/memory_tools.py    scripts/learn.py            │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│  4. 安全执行沙盒层（Execution Sandbox）                           │
│     Docker socket 代理  │  Git 只读工具  │  Prometheus/ELK/K8s  │
│     tools/git/   tools/ops/   tools/notifications/              │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│  5. 混合模型路由层（Model & Resource Layer）                      │
│     HF Router（云端）  ←── 安全路由 ──►  内网 vLLM / bge-m3     │
│     GLM-5.1 / Qwen3-235B / DeepSeek-Flash  │  Gemma-4 / bge-m3 │
└─────────────────────────────────────────────────────────────────┘
```

---

## 模型路由策略

| 角色 | 模型 | 部署方式 | 职责 |
|:--|:--|:--|:--|
| **主大脑** | `zai-org/GLM-5.1` | HF Inference Providers | 复杂任务规划、长程多工具编排（200+ 步稳定） |
| **代码主力** | `Qwen/Qwen3-235B-A22B` | HF Inference Providers | 需求架构设计、Code Review、Bug 定位 |
| **轻量摘要** | `deepseek-ai/DeepSeek-V4-Flash:cheapest` | HF Router 最低价路由 | 意图分类、上下文压缩、会话摘要 |
| **涉密本地** | `google/gemma-4-26b-a4b-it` | 内网 GPU vLLM | 客户核心数据处理，数据零出网 |
| **语义检索** | `BAAI/bge-m3` | 内网 TEI 节点 | 中英双语 RAG 向量化 |

**路由优先级**：`confidential_* Profile（强制本地）` > `显式 role 覆盖` > `技能指定模型` > `默认 primary`

技能 → 模型映射：

| 技能 | 路由模型 |
|:--|:--|
| `qixuan-req-analyzer` | coding（Qwen3-235B） |
| `qixuan-code-reviewer` | coding（Qwen3-235B） |
| `qixuan-ops-sentinel` | primary（GLM-5.1） |
| `qixuan-release-notes` | coding（Qwen3-235B） |

---

## 四大核心技能

### 1. `qixuan-req-analyzer` — 客户需求拆解与工时评估

将客户原始需求、会议纪要、招标文件转化为标准《结构化需求分析与评估报告》。

**触发方式**：飞书 @机器人 / 命令 `/analyze`

**核心工作流**：
1. `memory_search` 检索项目历史技术栈约定与踩坑记录
2. 识别业务角色、拆解 MVP 核心模块与二期增强模块
3. 设计核心数据模型草案（核心表结构与状态机）
4. 按角色估算人天（前端/后端/UI/测试），统一加 15% 风险缓冲
5. 保存报告至 `/app/workspace/req_analysis/`，询问是否写入 `MEMORY.md`

**输出示例**：WBS 功能清单表 + 人天估算表 + 待澄清风险清单

---

### 2. `qixuan-code-reviewer` — Git MR 自动化代码审计

在代码合入主干前，对 Git Diff 进行深度静态审查，拦截多租户越权、SQL 注入、N+1 查询等问题。

**触发方式**：GitLab MR 创建/更新自动触发 / 命令 `/review`

**四维审查矩阵**：

| 优先级 | 维度 | 典型问题 |
|:--|:--|:--|
| P0（阻断合入） | 多租户与数据安全 | 缺少 `tenant_id` 条件、SQL 注入、硬编码密钥 |
| P1（严重） | 数据库与并发性能 | N+1 查询、大表全量拉取、长事务含 HTTP 调用 |
| P2（警告） | 异常容错 | 无超时配置、空 catch 块、空指针风险 |
| P3（建议） | 编码规范 | 函数超 50 行、命名不清晰、缺少注释 |

**输出**：结构化审计报告（含 diff 修复代码块）自动发布至 MR 评论区

---

### 3. `qixuan-ops-sentinel` — 生产环境智能巡检与 RCA

7×24 小时监控客户 SaaS 环境，每日推送健康早报，突发告警时自动完成根因定位。

**触发方式**：每日 08:00 Cron（工作日）/ Alertmanager 告警推送 / 命令 `/ops-check`

**巡检四大黄金信号**：HTTP 5xx 错误率、P99 接口延迟、CPU/内存/磁盘水位、数据库连接池与慢查询

**RCA 流程**：现场快照（指标波动）→ 日志关联（ELK ERROR 堆栈）→ 历史比对（`memory_search`）→ 根因推演（置信度评估）→ 应急止损（HITL 审批重启）

**安全红线**：`k8s_pod_restart` 等写操作强制触发飞书卡片人工确认，Agent 不得静默执行

---

### 4. `qixuan-release-notes` — 自动化版本交付文档

打 Tag 或执行 `/release-notes` 时，自动生成面向客户和面向研发两份文档。

**触发方式**：GitLab Tag Webhook / 命令 `/release-notes`

**双视角输出**：

- **对客版本说明**：通俗业务语言，聚焦用户价值，**严禁泄露**内部类名、表名、缺陷单号
- **内部技术手册**：DDL 脚本、新增环境变量、发版前置 Checklist、回滚预案

---

## 仓库结构

```
qixuan-agent/
├── config/
│   └── config.yaml                 # 模型路由、记忆预算、HITL 白名单、Cron 调度
├── data/
│   ├── profiles/default/
│   │   ├── MEMORY.md               # 企业技术栈规约、多租户红线、工时标准
│   │   └── USER.md                 # 团队角色偏好配置
│   └── sqlite/                     # FTS5 全文检索数据库（运行时生成）
├── docs/
│   ├── 01-架构说明书.md
│   ├── 02-功能设计说明书.md
│   ├── 03-集成方案.md
│   └── 04-测试方案与测试报告.md
├── gateway/
│   ├── webhook_router.py           # 统一 Webhook 入口（GitLab / Alertmanager）
│   └── feishu/
│       └── feishu_gateway.py       # 飞书事件网关（消息 + HITL 卡片回调）
├── governance/
│   ├── hitl.py                     # HITL 高危操作人工审批拦截器
│   └── router.py                   # 安全分级模型路由器
├── tools/
│   ├── memory/memory_tools.py      # SQLite FTS5 记忆读写与上下文加载
│   ├── git/git_tools.py            # GitLab MR Diff 读取 / 评论发布（只读令牌）
│   ├── ops/ops_tools.py            # Prometheus / ELK / K8s 只读巡检
│   └── notifications/im_notifier.py # 飞书 / 钉钉 Webhook 卡片推送
├── skills/
│   └── qixuan/                     # 启煊专属技能库
│       ├── qixuan-req-analyzer/SKILL.md
│       ├── qixuan-code-reviewer/SKILL.md
│       ├── qixuan-ops-sentinel/SKILL.md
│       └── qixuan-release-notes/SKILL.md
├── scripts/
│   ├── init.sh                     # 一键初始化目录结构
│   ├── health_check.sh             # 六项验收健康检查
│   ├── learn.py                    # /learn 技能自进化命令
│   └── cron_runner.py              # 定时任务调度器
├── tests/
│   ├── test_memory.py              # 记忆系统单元测试（5 用例，全部通过）
│   ├── test_router.py              # 模型路由器单元测试（7 用例，全部通过）
│   └── test_gateway.py             # Webhook 网关集成测试（6 用例）
├── docker-compose.yml              # 生产容器编排（4 服务）
├── .env.example                    # 环境变量模板
├── requirements.txt                # Python 依赖
└── pyproject.toml                  # 项目元数据与工具配置
```

---

## 文档目录

完整设计文档位于 [`docs/`](docs/) 目录：

| 文档 | 说明 |
|:--|:--|
| [架构说明书](docs/01-架构说明书.md) | 五层架构详述、数据流、安全架构、容器拓扑（9 章 / 873 行） |
| [功能设计说明书](docs/02-功能设计说明书.md) | 十大功能模块接口设计、处理流程、异常处理（6 章 / 1300 行） |
| [集成方案](docs/03-集成方案.md) | 八路外部系统接入配置与验收清单（11 章 / 1017 行） |
| [测试方案与测试报告](docs/04-测试方案与测试报告.md) | 27 个测试用例 + 执行结果 + 缺陷记录（12 章 / 979 行） |

---

## 快速部署

### 前置要求

- Docker Engine ≥ 24.0，Docker Compose Plugin ≥ v2.20
- 标准模式：4 核 CPU / 8 GB 内存 / 50 GB SSD
- 涉密 GPU 模式：NVIDIA RTX 4090 (24 GB) 或同等，已安装 NVIDIA Container Toolkit

### 一键初始化

```bash
git clone https://github.com/qixuan-cn/qixuan-agent.git /opt/qixuan-agent
cd /opt/qixuan-agent
bash scripts/init.sh --base-dir /opt/qixuan-agent
```

### 配置环境变量

```bash
cp .env.example .env
chmod 600 .env
# 至少填写以下关键项：
# HF_TOKEN=hf_xxxx                     Hugging Face 推理令牌（必填）
# FEISHU_APP_ID / FEISHU_APP_SECRET    飞书应用凭证
# GIT_REVIEWER_TOKEN                   GitLab 只读令牌
# OPS_ALERT_WEBHOOK_URL                飞书运维群 Webhook
nano .env
```

### 启动服务

```bash
# 标准模式（HF 云端模型 + 本地 bge-m3 + 安全沙盒）
docker compose up -d

# 涉密混部模式（额外启动内网 GPU vLLM 节点）
docker compose --profile confidential up -d
```

### 验收检查

```bash
bash scripts/health_check.sh --base-dir /opt/qixuan-agent
docker exec -it qixuan-agent-core hermes doctor
```

---

## 安全合规

| 控制项 | 实现机制 |
|:--|:--|
| **涉密数据不出网** | `confidential_` 前缀 Profile 在代码层强制切断 HF 云端调用，路由至内网 vLLM |
| **最小权限** | GitLab 令牌仅 `read_repository + comment_mr`；数据库工具默认只读 `SELECT` |
| **高危操作 HITL** | `git_push` / `k8s_pod_restart` / `db_execute_write` 等强制发飞书卡片审批，支持超时自动取消 |
| **审计可追溯** | 每次工具调用记录入 SQLite `audit_log`（入参哈希、审批人、执行时间），留存 ≥ 180 天 |
| **密钥安全** | API Key 仅从环境变量读取，`.env` 已加入 `.gitignore`，日志输出自动脱敏 |
| **Webhook 鉴权** | GitLab Token 比对、Alertmanager HMAC-SHA256、飞书 AES-CBC 解密 |
| **Docker 沙盒** | 受限 socket 代理（tecnativa/docker-socket-proxy），Agent 仅可操作容器，无宿主机逃逸能力 |

---

## 实施里程碑

| 阶段 | 周期 | 目标 |
|:--|:--|:--|
| **Phase 1** | 第 1–2 周 | 基础底座搭建、飞书联调、`qixuan-req-analyzer` 内部试用 |
| **Phase 2** | 第 3–4 周 | 内网 GPU 节点混部、HITL 卡片审批流、代码审查上线 |
| **Phase 3** | 第 5–8 周 | 全员技能众筹、内部 Git 技能仓库、运维巡检全面覆盖 |

---

## 技术支持

- 内部文档：`https://wiki.qixuan-soft.internal/ai-platform/qixuan-agent`
- 技能仓库：`https://git.qixuan-soft.internal/ai-platform/qixuan-agent-skills`
- 告警频道：飞书「AI 平台运维值班」群
- 底座项目：[Hermes Agent (NousResearch)](https://github.com/NousResearch/hermes-agent)
