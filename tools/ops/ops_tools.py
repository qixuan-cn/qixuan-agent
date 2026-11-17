"""
启煊智能体 - 运维观测工具集（只读）
功能：Prometheus 指标查询、Elasticsearch 日志检索、K8s 只读巡检

安全约束：
- 所有查询使用只读令牌，严禁通过此模块执行写操作（Scale / Delete / Exec）。
- k8s_pod_restart 等写操作须通过 HITL 审批通道（governance/hitl.py）触发。
- 查询结果中的敏感字段（密码、Token）自动脱敏后返回。
"""

import os
import re
import httpx
from datetime import datetime, timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# 连接配置
# ---------------------------------------------------------------------------
PROMETHEUS_ENDPOINT = os.environ.get("PROMETHEUS_ENDPOINT", "http://prometheus.qixuan-soft.internal:9090")
PROMETHEUS_TOKEN = os.environ.get("PROMETHEUS_READONLY_TOKEN", "")
ELK_ENDPOINT = os.environ.get("ELK_ENDPOINT", "http://elasticsearch.qixuan-soft.internal:9200")
ELK_API_KEY = os.environ.get("ELK_READONLY_API_KEY", "")

TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0)

# 敏感字段脱敏模式
_SENSITIVE_PATTERNS = [
    (re.compile(r'(?i)(password|passwd|secret|token|api_?key|authorization)["\']?\s*[:=]\s*["\']?([^\s"\'&;,}]+)'), r'\1=***REDACTED***'),
]


def _redact(text: str) -> str:
    """对返回文本中的敏感字段进行脱敏处理。"""
    for pattern, replacement in _SENSITIVE_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Prometheus 只读查询
# ---------------------------------------------------------------------------

def prometheus_readonly_query(
    promql: str,
    time_range_hours: int = 24,
    step: str = "5m",
) -> dict:
    """
    执行 Prometheus PromQL 范围查询（只读）。

    Args:
        promql:           PromQL 查询表达式（如 'sum(rate(http_requests_total[5m])) by (service)'）。
        time_range_hours: 查询时间范围（小时），默认 24 小时。
        step:             查询步长（如 "5m" "1h"），默认 5 分钟。

    Returns:
        查询结果，包含时间序列数据点。
    """
    headers = {}
    if PROMETHEUS_TOKEN:
        headers["Authorization"] = f"Bearer {PROMETHEUS_TOKEN}"

    end_time = datetime.utcnow()
    start_time = end_time - timedelta(hours=time_range_hours)

    params = {
        "query": promql,
        "start": start_time.timestamp(),
        "end": end_time.timestamp(),
        "step": step,
    }

    try:
        resp = httpx.get(
            f"{PROMETHEUS_ENDPOINT}/api/v1/query_range",
            headers=headers,
            params=params,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "status": data.get("status"),
            "result_type": data.get("data", {}).get("resultType"),
            "result": data.get("data", {}).get("result", []),
            "query": promql,
            "time_range_hours": time_range_hours,
        }
    except httpx.HTTPStatusError as e:
        return {"error": f"Prometheus API 错误：{e.response.status_code}", "detail": e.response.text[:300]}
    except httpx.RequestError as e:
        return {"error": f"Prometheus 连接失败：{type(e).__name__}", "detail": str(e)}


def prometheus_golden_signals(cluster_label: Optional[str] = None) -> dict:
    """
    批量查询四大黄金信号（可用率、延迟、流量、错误率）。
    适用于每日早间例行巡检场景。

    Args:
        cluster_label: 按集群标签过滤（可选）。

    Returns:
        汇总的四大黄金信号指标字典。
    """
    label_filter = f', cluster="{cluster_label}"' if cluster_label else ""

    queries = {
        "error_rate_5xx": f'sum(rate(nginx_ingress_controller_requests_total{{status=~"5.."  {label_filter}}}[5m])) / sum(rate(nginx_ingress_controller_requests_total{{{label_filter}}}[5m]))',
        "p99_latency_ms": f'histogram_quantile(0.99, sum(rate(nginx_ingress_controller_response_duration_seconds_bucket{{{label_filter}}}[5m])) by (le)) * 1000',
        "cpu_usage_max": f'max(100 - (avg by (instance) (rate(node_cpu_seconds_total{{mode="idle" {label_filter}}}[5m])) * 100))',
        "memory_usage_max": f'max((1 - node_memory_MemAvailable_bytes{{{label_filter}}} / node_memory_MemTotal_bytes{{{label_filter}}}) * 100)',
        "disk_usage_max": f'max((node_filesystem_size_bytes{{{label_filter}}} - node_filesystem_free_bytes{{{label_filter}}}) / node_filesystem_size_bytes{{{label_filter}}} * 100)',
    }

    results = {}
    for metric_name, promql in queries.items():
        result = prometheus_readonly_query(promql, time_range_hours=1, step="1m")
        # 提取最新数据点的值
        series = result.get("result", [])
        if series:
            latest_value = series[0].get("values", [[None, "N/A"]])[-1][1] if series[0].get("values") else "N/A"
        else:
            latest_value = "N/A"
        results[metric_name] = latest_value

    return {
        "metrics": results,
        "snapshot_time": datetime.utcnow().isoformat() + "Z",
        "cluster_filter": cluster_label,
    }


# ---------------------------------------------------------------------------
# Elasticsearch 日志检索
# ---------------------------------------------------------------------------

def elk_log_search(
    keywords: list[str],
    service_name: Optional[str] = None,
    log_level: str = "ERROR",
    minutes_back: int = 60,
    max_hits: int = 50,
) -> dict:
    """
    检索 Elasticsearch 中的应用错误日志。

    Args:
        keywords:     关键词列表（如 ["OutOfMemoryError", "heap"]）。
        service_name: 微服务名过滤（如 "qixuan-mes-worker"）。
        log_level:    日志级别过滤（ERROR | WARN | INFO），默认 ERROR。
        minutes_back: 检索时间窗口（分钟），默认过去 60 分钟。
        max_hits:     最多返回日志条数，默认 50。

    Returns:
        命中的日志条目列表（已脱敏）。
    """
    headers = {}
    if ELK_API_KEY:
        headers["Authorization"] = f"ApiKey {ELK_API_KEY}"
    headers["Content-Type"] = "application/json"

    now = datetime.utcnow()
    start_time = (now - timedelta(minutes=minutes_back)).isoformat() + "Z"
    end_time = now.isoformat() + "Z"

    must_clauses: list[dict] = [
        {"range": {"@timestamp": {"gte": start_time, "lte": end_time}}},
        {"term": {"level": log_level}},
    ]

    if service_name:
        must_clauses.append({"term": {"service.name": service_name}})

    if keywords:
        must_clauses.append({
            "multi_match": {
                "query": " ".join(keywords),
                "fields": ["message", "exception.stacktrace", "log.message"],
            }
        })

    query = {
        "query": {"bool": {"must": must_clauses}},
        "sort": [{"@timestamp": {"order": "desc"}}],
        "size": max_hits,
        "_source": ["@timestamp", "level", "service.name", "message", "exception.class", "trace.id"],
    }

    try:
        resp = httpx.post(
            f"{ELK_ENDPOINT}/*-app-logs*/_search",
            headers=headers,
            json=query,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        hits = data.get("hits", {}).get("hits", [])

        logs = []
        for hit in hits:
            source = hit.get("_source", {})
            message = _redact(source.get("message", ""))
            logs.append({
                "timestamp": source.get("@timestamp", ""),
                "level": source.get("level", log_level),
                "service": source.get("service.name", service_name or "unknown"),
                "message": message[:1000],  # 单条日志截断防撑爆上下文
                "exception_class": source.get("exception.class", ""),
                "trace_id": source.get("trace.id", ""),
            })

        return {
            "total_hits": data.get("hits", {}).get("total", {}).get("value", 0),
            "returned": len(logs),
            "logs": logs,
            "time_window": f"past {minutes_back} minutes",
            "service_filter": service_name,
        }

    except httpx.HTTPStatusError as e:
        return {"error": f"Elasticsearch 查询失败：{e.response.status_code}", "detail": _redact(e.response.text[:300])}
    except httpx.RequestError as e:
        return {"error": f"Elasticsearch 连接失败：{type(e).__name__}", "detail": str(e)}


# ---------------------------------------------------------------------------
# K8s 只读巡检（通过 kubectl 只读 ServiceAccount 或 Kubernetes API）
# ---------------------------------------------------------------------------

def k8s_readonly_inspect(
    namespace: str = "default",
    resource_type: str = "pods",
    label_selector: Optional[str] = None,
) -> dict:
    """
    执行 K8s 集群只读资源查询（通过宿主机 kubectl 命令或 K8s API）。

    注意：此函数通过安全沙盒执行 kubectl，不直接修改任何资源。
    写操作（restart / scale / delete）通过 HITL 审批通道触发，不在本函数中实现。

    Args:
        namespace:      Kubernetes 命名空间，默认 default。
        resource_type:  资源类型（pods | deployments | services | events）。
        label_selector: 标签选择器过滤（如 "app=qixuan-mes"）。

    Returns:
        K8s 资源摘要列表。
    """
    import subprocess
    import json as _json

    allowed_types = {"pods", "deployments", "services", "events", "nodes", "configmaps"}
    if resource_type not in allowed_types:
        return {"error": f"不允许查询资源类型 '{resource_type}'，仅支持：{allowed_types}"}

    cmd = ["kubectl", "get", resource_type, "-n", namespace, "-o", "json"]
    if label_selector:
        cmd += ["-l", label_selector]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            return {"error": f"kubectl 执行失败：{result.stderr[:300]}"}

        data = _json.loads(result.stdout)
        items = data.get("items", [])

        resources = []
        for item in items:
            meta = item.get("metadata", {})
            status = item.get("status", {})
            entry = {
                "name": meta.get("name", ""),
                "namespace": meta.get("namespace", namespace),
                "labels": meta.get("labels", {}),
                "creation_timestamp": meta.get("creationTimestamp", ""),
            }

            if resource_type == "pods":
                conditions = status.get("conditions", [])
                container_statuses = status.get("containerStatuses", [])
                entry["phase"] = status.get("phase", "Unknown")
                entry["ready"] = all(c.get("ready", False) for c in container_statuses)
                entry["restart_count"] = sum(c.get("restartCount", 0) for c in container_statuses)
                for cs in container_statuses:
                    waiting = cs.get("state", {}).get("waiting", {})
                    if waiting.get("reason") in ("CrashLoopBackOff", "OOMKilled", "Error"):
                        entry["anomaly"] = waiting.get("reason")
                        entry["anomaly_message"] = waiting.get("message", "")

            resources.append(entry)

        # 高亮异常 Pod
        anomalies = [r for r in resources if r.get("anomaly")]
        return {
            "namespace": namespace,
            "resource_type": resource_type,
            "total": len(resources),
            "anomaly_count": len(anomalies),
            "anomalies": anomalies,
            "resources": resources,
        }

    except subprocess.TimeoutExpired:
        return {"error": "kubectl 命令超时（>15s），请检查 K8s 集群连接。"}
    except Exception as e:
        return {"error": f"K8s 查询异常：{type(e).__name__}", "detail": str(e)}
