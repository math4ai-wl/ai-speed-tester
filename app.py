from __future__ import annotations

import asyncio
import json
import time
from html import escape
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
import socket

import httpx
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, JSONResponse

APP_DIR = Path(__file__).resolve().parent
STATE_FILE = APP_DIR / "state.json"

DEFAULT_PROMPT = "Reply with ok."
DEFAULT_MODEL = "gpt-4.1-mini"
HISTORY_KEY = "ai-speed-tester-history-v1"

app = FastAPI(title="AI Speed Tester")


def load_state() -> dict[str, str]:
    if not STATE_FILE.exists():
        return {
            "base_url": "https://api.openai.com/v1",
            "model": DEFAULT_MODEL,
        }
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return {
            "base_url": str(data.get("base_url", "https://api.openai.com/v1")),
            "model": str(data.get("model", DEFAULT_MODEL)),
        }
    except Exception:
        return {
            "base_url": "https://api.openai.com/v1",
            "model": DEFAULT_MODEL,
        }


def save_state(base_url: str, model: str) -> None:
    payload = {"base_url": base_url, "model": model}
    STATE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def normalize_base_url(base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if value.endswith("/v1"):
        return value
    return f"{value}/v1"


def extract_host(base_url: str) -> str:
    parsed = urlparse(base_url.strip())
    if parsed.hostname:
        return parsed.hostname
    return base_url.strip().split("/")[0]


def classify_region(text: str) -> str:
    lowered = text.lower()
    if any(keyword in lowered for keyword in ["hong kong", "hk", "香港"]):
        return "香港"
    if any(keyword in lowered for keyword in ["singapore", "sg", "新加坡"]):
        return "新加坡"
    if any(keyword in lowered for keyword in ["japan", "tokyo", "osaka", "jp", "日本"]):
        return "日本"
    if any(keyword in lowered for keyword in ["korea", "seoul", "kr", "韩国", "南韩"]):
        return "韩国"
    if any(keyword in lowered for keyword in ["taiwan", "tw", "台灣", "台湾"]):
        return "台湾"
    if any(keyword in lowered for keyword in ["us", "united states", "san francisco", "lax", "virginia", "new york"]):
        return "美国"
    if any(keyword in lowered for keyword in ["china", "cn", "mainland", "中国"]):
        return "中国"
    return "未知"


def update_node_result(
    result: dict[str, str],
    *,
    country: str = "",
    region: str = "",
    asn: str = "",
    org: str = "",
) -> dict[str, str]:
    result["country"] = country
    result["region"] = region
    result["asn"] = asn
    result["org"] = org
    text = " ".join(filter(None, [country, region, asn, org]))
    result["label"] = classify_region(text)
    result["status"] = "resolved"
    result["error"] = ""
    return result


def parse_first_token(line: str) -> bool:
    if not line.startswith("data:"):
        return False

    content = line[5:].strip()
    if not content or content == "[DONE]":
        return False

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return True

    choices = payload.get("choices") or []
    if not choices:
        return False

    delta = choices[0].get("delta") or {}
    return bool(delta.get("content"))


def resolve_node(base_url: str) -> dict[str, str]:
    host = extract_host(base_url)
    result: dict[str, str] = {
        "host": host,
        "ip": "",
        "country": "",
        "region": "",
        "asn": "",
        "org": "",
        "status": "unresolved",
        "error": "",
        "label": "未解析",
    }

    try:
        socket.getaddrinfo(host, None)
        addresses = []
        for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
            ip = item[4][0]
            try:
                addresses.append(str(ip_address(ip)))
            except ValueError:
                continue
        if addresses:
            result["ip"] = addresses[0]
    except Exception as exc:
        result["status"] = "error"
        result["label"] = "解析失败"
        result["error"] = str(exc)
        return result

    lookup_target = result["ip"] or host
    errors: list[str] = []
    try:
        response = httpx.get(f"https://ipinfo.io/{lookup_target}/json", timeout=10.0)
        response.raise_for_status()
        payload = response.json()
        return update_node_result(
            result,
            country=str(payload.get("country", "")),
            region=str(payload.get("region", "")) or str(payload.get("city", "")),
            asn=str(payload.get("org", "")),
            org=str(payload.get("org", "")),
        )
    except Exception as exc:
        errors.append(f"ipinfo: {exc}")

    try:
        fields = "status,country,countryCode,regionName,city,isp,org,as,query,message"
        response = httpx.get(f"http://ip-api.com/json/{lookup_target}?fields={fields}", timeout=10.0)
        response.raise_for_status()
        payload = response.json()
        if payload.get("status") == "success":
            return update_node_result(
                result,
                country=str(payload.get("countryCode", "")) or str(payload.get("country", "")),
                region=str(payload.get("regionName", "")) or str(payload.get("city", "")),
                asn=str(payload.get("as", "")),
                org=str(payload.get("isp", "")) or str(payload.get("org", "")),
            )
        errors.append(f"ip-api: {payload.get('message', 'lookup failed')}")
    except Exception as exc:
        errors.append(f"ip-api: {exc}")

    if result["ip"]:
        result["status"] = "partial"
        result["label"] = "仅解析到 IP"
        result["error"] = " | ".join(errors)
        return result

    result["status"] = "error"
    result["label"] = "解析失败"
    result["error"] = " | ".join(errors)
    return result


def build_html() -> str:
    state = load_state()
    base_url = escape(state["base_url"], quote=True)
    model = escape(state["model"], quote=True)
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AI Speed Tester</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f4f1ea;
      --panel: #ffffff;
      --text: #18212f;
      --muted: #64748b;
      --line: #d8dee7;
      --accent: #0f766e;
      --accent-strong: #115e59;
      --good: #0f766e;
      --bad: #b91c1c;
      --shadow: 0 16px 40px rgba(15, 23, 42, 0.08);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background:
        linear-gradient(180deg, rgba(255,255,255,0.75), rgba(255,255,255,0.92)),
        radial-gradient(circle at top left, #e2f1ef 0, transparent 28%),
        radial-gradient(circle at right 18%, #fff1d6 0, transparent 24%),
        var(--bg);
      color: var(--text);
      min-height: 100vh;
    }}
    .wrap {{
      max-width: 1160px;
      margin: 0 auto;
      padding: 28px 18px 40px;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }}
    h1 {{
      margin: 0;
      font-size: 28px;
      letter-spacing: 0;
    }}
    .sub {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 14px;
    }}
    .panel {{
      background: rgba(255,255,255,0.88);
      backdrop-filter: blur(10px);
      border: 1px solid rgba(216,222,231,0.8);
      box-shadow: var(--shadow);
      border-radius: 14px;
      padding: 18px;
    }}
    form {{
      display: grid;
      grid-template-columns: 1.2fr 1.1fr 0.8fr auto;
      gap: 12px;
      align-items: end;
    }}
    label {{
      display: block;
      font-size: 13px;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    input, textarea {{
      width: 100%;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 10px;
      padding: 11px 12px;
      font: inherit;
      outline: none;
    }}
    input:focus, textarea:focus {{
      border-color: rgba(17,94,89,0.5);
      box-shadow: 0 0 0 3px rgba(17,94,89,0.09);
    }}
    .hint-row {{
      display: flex;
      gap: 10px;
      align-items: center;
      justify-content: space-between;
      margin-top: 12px;
      flex-wrap: wrap;
    }}
    .prompt-box {{
      margin-top: 12px;
    }}
    textarea {{
      min-height: 82px;
      resize: vertical;
    }}
    button {{
      border: 0;
      background: linear-gradient(180deg, var(--accent), var(--accent-strong));
      color: white;
      border-radius: 10px;
      padding: 11px 16px;
      font: inherit;
      font-weight: 600;
      cursor: pointer;
      height: 42px;
    }}
    button:disabled {{
      opacity: 0.65;
      cursor: progress;
    }}
    .meta {{
      color: var(--muted);
      font-size: 13px;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
      margin-top: 16px;
    }}
    .result {{
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 10px;
      margin-top: 16px;
    }}
    .stat {{
      border: 1px solid var(--line);
      border-radius: 12px;
      background: #fff;
      padding: 14px;
    }}
    .stat .k {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .stat .v {{
      font-size: 22px;
      font-weight: 700;
      letter-spacing: 0;
      line-height: 1.1;
    }}
    .table {{
      width: 100%;
      border-collapse: collapse;
      overflow: hidden;
      border-radius: 12px;
      background: #fff;
      border: 1px solid var(--line);
      margin-top: 16px;
    }}
    .table th, .table td {{
      text-align: left;
      padding: 12px 14px;
      border-bottom: 1px solid #eef2f7;
      font-size: 14px;
      vertical-align: top;
    }}
    .table th {{
      color: var(--muted);
      font-weight: 600;
      background: #fbfcfe;
    }}
    .table tr:last-child td {{
      border-bottom: 0;
    }}
    .ok {{ color: var(--good); font-weight: 700; }}
    .err {{ color: var(--bad); font-weight: 700; }}
    .mono {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 13px;
      word-break: break-all;
    }}
    .note {{
      margin-top: 10px;
      color: var(--muted);
      font-size: 13px;
    }}
    .footer-note {{
      margin-top: 14px;
      color: var(--muted);
      font-size: 12px;
    }}
    .node-shell {{
      margin-top: 16px;
      border: 1px solid rgba(216,222,231,0.9);
      border-radius: 18px;
      overflow: hidden;
      background: rgba(255,255,255,0.82);
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
    }}
    .node-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid #eef2f7;
      background: linear-gradient(180deg, rgba(250,252,255,0.96), rgba(245,248,252,0.92));
    }}
    .node-grid {{
      display: grid;
      grid-template-columns: repeat(6, minmax(0, 1fr));
      gap: 10px;
      padding: 14px;
    }}
    .node-card {{
      border: 1px solid #e5ebf2;
      border-radius: 14px;
      background: #fff;
      padding: 12px 14px;
      min-height: 86px;
    }}
    .node-card .k {{
      color: var(--muted);
      font-size: 12px;
      margin-bottom: 8px;
    }}
    .node-card .v {{
      font-size: 14px;
      font-weight: 600;
      word-break: break-word;
      line-height: 1.4;
    }}
    .node-badge {{
      display: inline-flex;
      align-items: center;
      padding: 5px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 700;
      background: #ecfeff;
      color: #0f766e;
    }}
    .history-shell {{
      margin-top: 16px;
      border: 1px solid rgba(216,222,231,0.9);
      border-radius: 18px;
      overflow: hidden;
      background: rgba(255,255,255,0.82);
      box-shadow: 0 12px 28px rgba(15, 23, 42, 0.06);
    }}
    .history-head {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 16px;
      border-bottom: 1px solid #eef2f7;
      background: linear-gradient(180deg, rgba(250,252,255,0.96), rgba(245,248,252,0.92));
    }}
    .history-title {{
      display: flex;
      flex-direction: column;
      gap: 4px;
    }}
    .history-title strong {{
      font-size: 14px;
    }}
    .history-title span {{
      color: var(--muted);
      font-size: 12px;
    }}
    .history-actions {{
      display: flex;
      gap: 8px;
      flex-wrap: wrap;
    }}
    .ghost-btn {{
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      height: 34px;
      padding: 0 12px;
      border-radius: 10px;
      font-weight: 600;
      cursor: pointer;
    }}
    .ghost-btn:hover {{
      border-color: #b8c2cf;
    }}
    .history-list {{
      display: grid;
      gap: 10px;
      padding: 14px;
    }}
    .history-empty {{
      padding: 16px;
      color: var(--muted);
      font-size: 13px;
      border: 1px dashed #d7dee8;
      border-radius: 12px;
      background: #fbfcfe;
    }}
    .history-item {{
      display: grid;
      grid-template-columns: 1.4fr 0.9fr 0.9fr 0.8fr auto;
      gap: 12px;
      align-items: center;
      border: 1px solid #e5ebf2;
      border-radius: 14px;
      background: #fff;
      padding: 12px 14px;
    }}
    .history-item strong {{
      display: block;
      margin-bottom: 4px;
      font-size: 14px;
    }}
    .history-item .small {{
      color: var(--muted);
      font-size: 12px;
    }}
    .history-item .value {{
      font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
      font-size: 13px;
      word-break: break-word;
    }}
    .history-item.ok {{
      border-left: 4px solid #0f766e;
    }}
    .history-item.fail {{
      border-left: 4px solid #b91c1c;
    }}
    .history-item .load-btn {{
      white-space: nowrap;
      border: 0;
      background: #111827;
      color: #fff;
      height: 34px;
      padding: 0 12px;
      border-radius: 10px;
      cursor: pointer;
    }}
    @media (max-width: 960px) {{
      form, .grid, .result {{
        grid-template-columns: 1fr;
      }}
      .node-grid {{
        grid-template-columns: 1fr;
      }}
      .history-item {{
        grid-template-columns: 1fr;
      }}
      .history-head {{
        align-items: flex-start;
        flex-direction: column;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <h1>AI Speed Tester</h1>
        <div class="sub">测首 token 和完整耗时，后端代发流式请求。</div>
      </div>
      <div class="meta">本地保存到 state.json</div>
    </div>

    <div class="panel">
      <form id="speed-form">
        <div>
          <label>Base URL</label>
          <input name="base_url" value="{base_url}" placeholder="https://api.example.com" spellcheck="false" autocomplete="off">
        </div>
        <div>
          <label>API Key</label>
          <input name="api_key" value="" placeholder="sk-..." autocomplete="off" spellcheck="false">
        </div>
        <div>
          <label>Model</label>
          <input name="model" value="{model}" placeholder="gpt-4.1-mini" spellcheck="false" autocomplete="off">
        </div>
        <div>
          <button id="run-btn" type="submit">开始测速并解析节点</button>
        </div>
      </form>

      <div class="prompt-box">
        <label>Prompt</label>
        <textarea name="prompt" form="speed-form">{DEFAULT_PROMPT}</textarea>
      </div>

      <div class="hint-row">
        <div class="meta">默认使用流式请求，首个增量内容到达时记为首 token。</div>
        <div class="meta" id="status-line">等待测试</div>
      </div>

      <div class="result">
        <div class="stat"><div class="k">首 token</div><div class="v" id="ttfb">-</div></div>
        <div class="stat"><div class="k">完整耗时</div><div class="v" id="total">-</div></div>
        <div class="stat"><div class="k">状态码</div><div class="v" id="code">-</div></div>
        <div class="stat"><div class="k">结果</div><div class="v" id="result">-</div></div>
      </div>

      <table class="table">
        <thead>
          <tr>
            <th>请求地址</th>
            <th>模型</th>
            <th>错误</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td class="mono" id="req-url">-</td>
            <td class="mono" id="req-model">-</td>
            <td class="mono" id="error-cell">-</td>
          </tr>
        </tbody>
      </table>

      <div class="note">后端会按 OpenAI 兼容的 `/chat/completions` 流式接口测速；如果你的中转站路径不同，后面可以再加一个可选路径字段。</div>
      <div class="footer-note">API Key 只会用于这一次请求，不会写入本地 state 文件。</div>
    </div>

    <div class="node-shell">
      <div class="node-head">
        <div class="history-title">
          <strong>节点归属地</strong>
          <span>测速时自动同步解析，基于域名和公开 IP 信息做粗略判断。</span>
        </div>
      </div>
      <div class="node-grid" id="node-grid">
        <div class="node-card"><div class="k">域名</div><div class="v" id="node-host">-</div></div>
        <div class="node-card"><div class="k">IP</div><div class="v" id="node-ip">-</div></div>
        <div class="node-card"><div class="k">国家 / 地区</div><div class="v" id="node-country">-</div></div>
        <div class="node-card"><div class="k">城市 / 区域</div><div class="v" id="node-region">-</div></div>
        <div class="node-card"><div class="k">ASN / 运营商</div><div class="v" id="node-asn">-</div></div>
        <div class="node-card"><div class="k">判断</div><div class="v"><span class="node-badge" id="node-status">未解析</span></div></div>
      </div>
    </div>

    <div class="history-shell">
      <div class="history-head">
        <div class="history-title">
          <strong>测速记录</strong>
          <span>记录保存在浏览器本地存储里，刷新后仍然可见。</span>
        </div>
        <div class="history-actions">
          <button class="ghost-btn" id="copy-last-btn" type="button">复制最近结果</button>
          <button class="ghost-btn" id="clear-history-btn" type="button">清空记录</button>
        </div>
      </div>
      <div class="history-list" id="history-list"></div>
    </div>
  </div>

  <script>
    const HISTORY_KEY = "{HISTORY_KEY}";
    const form = document.getElementById('speed-form');
    const runBtn = document.getElementById('run-btn');
    const statusLine = document.getElementById('status-line');
    const historyList = document.getElementById('history-list');
    const clearHistoryBtn = document.getElementById('clear-history-btn');
    const copyLastBtn = document.getElementById('copy-last-btn');
    const nodeFields = {{
      host: document.getElementById('node-host'),
      ip: document.getElementById('node-ip'),
      country: document.getElementById('node-country'),
      region: document.getElementById('node-region'),
      asn: document.getElementById('node-asn'),
      status: document.getElementById('node-status'),
    }};
    const fields = {{
      ttfb: document.getElementById('ttfb'),
      total: document.getElementById('total'),
      code: document.getElementById('code'),
      result: document.getElementById('result'),
      reqUrl: document.getElementById('req-url'),
      reqModel: document.getElementById('req-model'),
      errorCell: document.getElementById('error-cell'),
    }};
    let latestResult = null;

    function loadHistory() {{
      try {{
        const raw = localStorage.getItem(HISTORY_KEY);
        if (!raw) return [];
        const parsed = JSON.parse(raw);
        return Array.isArray(parsed) ? parsed : [];
      }} catch {{
        return [];
      }}
    }}

    function saveHistory(items) {{
      localStorage.setItem(HISTORY_KEY, JSON.stringify(items.slice(0, 20)));
    }}

    function formatTime(ts) {{
      return new Date(ts).toLocaleString('zh-CN', {{
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      }});
    }}

    function renderHistory() {{
      const items = loadHistory();
      historyList.replaceChildren();

      if (!items.length) {{
        const empty = document.createElement('div');
        empty.className = 'history-empty';
        empty.textContent = '还没有测速记录。跑一次测试后，这里会自动记录下来。';
        historyList.appendChild(empty);
        return;
      }}

      items.forEach((item, index) => {{
        const row = document.createElement('div');
        row.className = `history-item ${{item.ok ? 'ok' : 'fail'}}`;

        const metaCell = document.createElement('div');
        const title = document.createElement('strong');
        title.textContent = item.model || '-';
        const subtitle = document.createElement('div');
        subtitle.className = 'small';
        subtitle.textContent = `${{formatTime(item.timestamp)}} · ${{item.base_url || '-'}}`;
        metaCell.append(title, subtitle);

        const ttfbCell = document.createElement('div');
        const ttfbLabel = document.createElement('div');
        ttfbLabel.className = 'small';
        ttfbLabel.textContent = '首 token';
        const ttfbValue = document.createElement('div');
        ttfbValue.className = 'value';
        ttfbValue.textContent = item.ttfb_text || '-';
        ttfbCell.append(ttfbLabel, ttfbValue);

        const totalCell = document.createElement('div');
        const totalLabel = document.createElement('div');
        totalLabel.className = 'small';
        totalLabel.textContent = '完整耗时';
        const totalValue = document.createElement('div');
        totalValue.className = 'value';
        totalValue.textContent = item.total_text || '-';
        totalCell.append(totalLabel, totalValue);

        const statusCell = document.createElement('div');
        const statusLabel = document.createElement('div');
        statusLabel.className = 'small';
        statusLabel.textContent = '状态';
        const statusValue = document.createElement('div');
        statusValue.className = 'value';
        statusValue.textContent = `${{item.ok ? '成功' : '失败'}} · ${{item.status_code ?? '-'}}`;
        statusCell.append(statusLabel, statusValue);

        const loadBtn = document.createElement('button');
        loadBtn.type = 'button';
        loadBtn.className = 'load-btn';
        loadBtn.textContent = '载入';
        loadBtn.addEventListener('click', () => {{
          const current = loadHistory()[index];
          if (!current) return;
          form.base_url.value = current.base_url || form.base_url.value;
          form.model.value = current.model || form.model.value;
          form.prompt.value = current.prompt || form.prompt.value;
          statusLine.textContent = '已载入历史记录';
        }});

        row.append(metaCell, ttfbCell, totalCell, statusCell, loadBtn);
        historyList.appendChild(row);
      }});
    }}

    function addHistory(entry) {{
      const items = loadHistory();
      items.unshift(entry);
      saveHistory(items);
      renderHistory();
    }}

    function setIdle() {{
      statusLine.textContent = '等待测试';
      fields.ttfb.textContent = '-';
      fields.total.textContent = '-';
      fields.code.textContent = '-';
      fields.result.textContent = '-';
      fields.reqUrl.textContent = '-';
      fields.reqModel.textContent = '-';
      fields.errorCell.textContent = '-';
    }}

    function setNodeIdle() {{
      nodeFields.host.textContent = '-';
      nodeFields.ip.textContent = '-';
      nodeFields.country.textContent = '-';
      nodeFields.region.textContent = '-';
      nodeFields.asn.textContent = '-';
      nodeFields.status.textContent = '未解析';
    }}

    function ms(value) {{
      return `${{value.toFixed(0)}} ms`;
    }}

    function copyText(text) {{
      if (navigator.clipboard && navigator.clipboard.writeText) {{
        return navigator.clipboard.writeText(text);
      }}
      const textarea = document.createElement('textarea');
      textarea.value = text;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      textarea.remove();
      return Promise.resolve();
    }}

    form.addEventListener('submit', async (event) => {{
      event.preventDefault();
      runBtn.disabled = true;
      statusLine.textContent = '测速并解析中...';
      fields.result.textContent = 'running';
      fields.errorCell.textContent = '-';
      nodeFields.status.textContent = '解析中';

      const data = new FormData(form);
      const body = new URLSearchParams();
      for (const [key, value] of data.entries()) {{
        body.set(key, value);
      }}

      try {{
        const response = await fetch('/api/run', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/x-www-form-urlencoded' }},
          body,
        }});
        const payload = await response.json();
        latestResult = payload;
        fields.ttfb.textContent = payload.ttfb_ms == null ? '-' : ms(payload.ttfb_ms);
        fields.total.textContent = payload.total_ms == null ? '-' : ms(payload.total_ms);
        fields.code.textContent = payload.status_code ?? '-';
        fields.result.textContent = payload.ok ? 'ok' : 'fail';
        fields.reqUrl.textContent = payload.request_url ?? '-';
        fields.reqModel.textContent = payload.model ?? '-';
        fields.errorCell.textContent = payload.error ?? '-';
        statusLine.textContent = payload.ok ? '测试完成' : '测试失败';
        const node = payload.node || {{}};
        nodeFields.host.textContent = node.host || '-';
        nodeFields.ip.textContent = node.ip || '-';
        nodeFields.country.textContent = node.country || '-';
        nodeFields.region.textContent = node.region || '-';
        nodeFields.asn.textContent = node.asn || node.org || '-';
        nodeFields.status.textContent = node.label || node.status || '未知';
        addHistory({{
          timestamp: Date.now(),
          base_url: form.base_url.value.trim(),
          model: form.model.value.trim(),
          prompt: form.prompt.value.trim(),
          ok: !!payload.ok,
          status_code: payload.status_code,
          ttfb_text: payload.ttfb_ms == null ? '-' : ms(payload.ttfb_ms),
          total_text: payload.total_ms == null ? '-' : ms(payload.total_ms),
          request_url: payload.request_url,
          node_label: node.label || node.status || '',
          error: payload.error || '',
        }});
      }} catch (error) {{
        statusLine.textContent = '请求失败';
        fields.errorCell.textContent = error?.message ?? String(error);
      }} finally {{
        runBtn.disabled = false;
      }}
    }});

    clearHistoryBtn.addEventListener('click', () => {{
      localStorage.removeItem(HISTORY_KEY);
      renderHistory();
      statusLine.textContent = '记录已清空';
    }});

    copyLastBtn.addEventListener('click', async () => {{
      if (!latestResult) return;
      const text = [
        `Model: ${{latestResult.model ?? '-'}}`,
        `Base URL: ${{fields.reqUrl.textContent}}`,
        `TTFB: ${{fields.ttfb.textContent}}`,
        `Total: ${{fields.total.textContent}}`,
        `Status: ${{fields.code.textContent}}`,
        `Result: ${{fields.result.textContent}}`,
        `Node: ${{nodeFields.status.textContent}}`,
        `Error: ${{fields.errorCell.textContent}}`,
      ].join('\\n');
      try {{
        await copyText(text);
        statusLine.textContent = '最近结果已复制';
      }} catch {{
        statusLine.textContent = '复制失败';
      }}
    }});

    renderHistory();
    setNodeIdle();
    setIdle();
  </script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return build_html()


async def execute_run(
    base_url: str = Form(...),
    api_key: str = Form(...),
    model: str = Form(...),
    prompt: str = Form(DEFAULT_PROMPT),
) -> dict[str, Any]:
    save_state(base_url, model)
    base_url_value = base_url.strip()
    request_url = f"{normalize_base_url(base_url_value)}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt.strip() or DEFAULT_PROMPT}],
        "temperature": 0,
        "stream": True,
    }

    start = time.perf_counter()
    first_token_at: float | None = None
    speed_end_at: float | None = None
    status_code: int | None = None
    error: str | None = None
    ok = False

    async def run_speed_probe() -> None:
        nonlocal first_token_at, speed_end_at, status_code, error, ok
        timeout = httpx.Timeout(90.0, connect=20.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", request_url, headers=headers, json=payload) as response:
                    status_code = response.status_code
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        if parse_first_token(line) and first_token_at is None:
                            first_token_at = time.perf_counter()
                    ok = True
        except Exception as exc:
            error = str(exc)
        finally:
            speed_end_at = time.perf_counter()

    speed_task = asyncio.create_task(run_speed_probe())
    node_task = asyncio.to_thread(resolve_node, base_url_value)
    node_result = await node_task
    await speed_task

    result: dict[str, Any] = {
        "ok": ok,
        "status_code": status_code,
        "ttfb_ms": round((first_token_at - start) * 1000, 2) if first_token_at else None,
        "total_ms": round((speed_end_at - start) * 1000, 2) if speed_end_at else None,
        "request_url": request_url,
        "model": model,
        "error": error,
        "node": node_result,
    }
    return result


@app.post("/api/run")
async def run_speed(
    base_url: str = Form(...),
    api_key: str = Form(...),
    model: str = Form(...),
    prompt: str = Form(DEFAULT_PROMPT),
) -> JSONResponse:
    return JSONResponse(await execute_run(base_url, api_key, model, prompt))


@app.post("/api/test")
async def test_speed(
    base_url: str = Form(...),
    api_key: str = Form(...),
    model: str = Form(...),
    prompt: str = Form(DEFAULT_PROMPT),
) -> JSONResponse:
    return JSONResponse(await execute_run(base_url, api_key, model, prompt))


@app.post("/api/resolve-node")
async def api_resolve_node(base_url: str = Form(...)) -> JSONResponse:
    return JSONResponse(resolve_node(base_url))
