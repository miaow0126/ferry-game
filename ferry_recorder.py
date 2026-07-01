#!/usr/bin/env python3
"""沉渡记录代理 MCP + 展示台

两个服务合一：
- 端口 8893：MCP 代理，转发请求到 ferrygate.cn:8765，同时记录每次游戏过程
- 端口 8894：展示台 HTTP 服务，左侧时间轴 + 右侧故事详情

环境变量：
  MCP_PORT      MCP 代理端口（默认 8893）
  DISPLAY_PORT  展示台端口（默认 8894）
  DATA_DIR      记录存储目录（默认 ./ferry_data）
  UPSTREAM      上游游戏服务器（默认 http://ferrygate.cn:8765）
"""

import os
import json
import threading
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime, timezone

MCP_PORT     = int(os.environ.get("MCP_PORT", 8893))
DISPLAY_PORT = int(os.environ.get("DISPLAY_PORT", 8894))
DATA_DIR     = Path(os.environ.get("DATA_DIR", "./ferry_data"))
UPSTREAM     = os.environ.get("UPSTREAM", "http://ferrygate.cn:8765")

DATA_DIR.mkdir(parents=True, exist_ok=True)


# ── 记录管理 ──────────────────────────────────────────────

def _session_file(player_id: str, session_id: str) -> Path:
    safe = player_id.replace("/", "_").replace("\\", "_")
    return DATA_DIR / f"{safe}__{session_id}.json"


def _load_session(player_id: str, session_id: str) -> dict:
    f = _session_file(player_id, session_id)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {
        "player_id": player_id,
        "session_id": session_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "carrier": None,
        "era": None,
        "events": []
    }


def _save_session(data: dict):
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    f = _session_file(data["player_id"], data["session_id"])
    f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _append_event(player_id: str, session_id: str, tool: str, args: dict, result: str):
    sess = _load_session(player_id, session_id)
    sess["events"].append({
        "t": datetime.now(timezone.utc).isoformat(),
        "tool": tool,
        "args": args,
        "result": result
    })
    if tool == "universe_spin" and sess["carrier"] is None:
        for line in result.splitlines():
            if "载体" in line or "carrier" in line.lower():
                sess["carrier"] = line.strip()
            if "时代" in line or "era" in line.lower():
                sess["era"] = line.strip()
    _save_session(sess)


def list_sessions() -> list:
    sessions = []
    for f in sorted(DATA_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            sessions.append({
                "player_id": d.get("player_id", ""),
                "session_id": d.get("session_id", ""),
                "started_at": d.get("started_at", ""),
                "updated_at": d.get("updated_at", ""),
                "carrier": d.get("carrier"),
                "era": d.get("era"),
                "event_count": len(d.get("events", []))
            })
        except Exception:
            pass
    return sessions


def get_session(player_id: str, session_id: str) -> dict:
    f = _session_file(player_id, session_id)
    if f.exists():
        return json.loads(f.read_text(encoding="utf-8"))
    return {}


# ── 上游转发 ──────────────────────────────────────────────

def forward_to_upstream(tool_name: str, tool_args: dict) -> str:
    upstream_req = {
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": tool_name, "arguments": tool_args}
    }
    body = json.dumps(upstream_req).encode()
    # 尝试 /mcp 路径（streamable-http 上游），失败则回退到根路径
    for url in [UPSTREAM.rstrip("/") + "/mcp", UPSTREAM]:
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream",
                },
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                # SSE 格式：data: {...}\n\n
                text = raw.decode("utf-8", errors="replace")
                if text.startswith("data:"):
                    for line in text.splitlines():
                        if line.startswith("data:"):
                            try:
                                resp_data = json.loads(line[5:].strip())
                                break
                            except Exception:
                                pass
                    else:
                        return text
                else:
                    resp_data = json.loads(text)
        except urllib.error.HTTPError as e:
            try:
                resp_data = json.loads(e.read())
            except Exception:
                continue
        except Exception as e:
            if url == UPSTREAM:
                return f"[错误] {e}"
            continue

        if "result" in resp_data:
            content = resp_data["result"].get("content", [])
            return "\n".join(c.get("text", "") for c in content if c.get("type") == "text")
        elif "error" in resp_data:
            return f"[错误] {resp_data['error']}"
        return ""
    return "[错误] 无法连接上游服务器"


def _record_and_call(tool_name: str, tool_args: dict) -> str:
    result = forward_to_upstream(tool_name, tool_args)
    player_id = tool_args.get("player_id", "unknown")
    if tool_name == "universe_spin":
        session_key = f"{player_id}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    else:
        existing = sorted(
            DATA_DIR.glob(f"{player_id.replace('/', '_')}__*.json"),
            key=lambda x: x.stat().st_mtime, reverse=True
        )
        session_key = existing[0].stem.split("__", 1)[1] if existing else f"{player_id}_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    threading.Thread(
        target=_append_event,
        args=(player_id, session_key, tool_name, tool_args, result),
        daemon=True
    ).start()
    return result


# ── FastMCP 服务（MCP 代理，端口 8893）──────────────────────

from mcp.server.fastmcp import FastMCP
import uvicorn
from starlette.middleware.cors import CORSMiddleware

mcp = FastMCP("ferry-recorder")


@mcp.tool()
def universe_spin(player_id: str) -> str:
    """转动命运之轮，随机分配时代、载体、姓氏，开始一段新生命

    Args:
        player_id: 玩家 ID
    """
    return _record_and_call("universe_spin", {"player_id": player_id})


@mcp.tool()
def universe_birth(player_id: str, gender: str = "", parents: str = "") -> str:
    """完成出生，选择性别和成长环境

    Args:
        player_id: 玩家 ID
        gender: 性别
        parents: 成长环境
    """
    return _record_and_call("universe_birth", {"player_id": player_id, "gender": gender, "parents": parents})


@mcp.tool()
def universe_advance(player_id: str) -> str:
    """推进一步人生。遇到分叉口时第一次看场景，第二次看选项

    Args:
        player_id: 玩家 ID
    """
    return _record_and_call("universe_advance", {"player_id": player_id})


@mcp.tool()
def universe_fork(player_id: str, choice: str) -> str:
    """在岔路口做选择（a 或 b），没选的那条路沉入水底

    Args:
        player_id: 玩家 ID
        choice: 选择（a 或 b）
    """
    return _record_and_call("universe_fork", {"player_id": player_id, "choice": choice})


@mcp.tool()
def universe_ferry(player_id: str, ferry_id: str) -> str:
    """站在渡口看水底的沉渡

    Args:
        player_id: 玩家 ID
        ferry_id: 渡口 ID
    """
    return _record_and_call("universe_ferry", {"player_id": player_id, "ferry_id": ferry_id})


@mcp.tool()
def universe_echo(player_id: str, sinker_id: str) -> str:
    """打捞水底的沉渡，获得别人的记忆碎片

    Args:
        player_id: 玩家 ID
        sinker_id: 沉渡 ID
    """
    return _record_and_call("universe_echo", {"player_id": player_id, "sinker_id": sinker_id})


@mcp.tool()
def universe_enter(player_id: str, place_id: str) -> str:
    """进入特殊地点：junkshop/cache/parallel/graveyard/steles/eaves/blank/callstack

    Args:
        player_id: 玩家 ID
        place_id: 地点 ID
    """
    return _record_and_call("universe_enter", {"player_id": player_id, "place_id": place_id})


@mcp.tool()
def universe_peek(player_id: str) -> str:
    """看别的玩家

    Args:
        player_id: 玩家 ID
    """
    return _record_and_call("universe_peek", {"player_id": player_id})


@mcp.tool()
def universe_map(player_id: str) -> str:
    """看星图和渡口地图

    Args:
        player_id: 玩家 ID
    """
    return _record_and_call("universe_map", {"player_id": player_id})


@mcp.tool()
def universe_status(player_id: str) -> str:
    """看自己的完整一生

    Args:
        player_id: 玩家 ID
    """
    return _record_and_call("universe_status", {"player_id": player_id})


@mcp.tool()
def universe_linger(player_id: str) -> str:
    """走完后停在沉默里

    Args:
        player_id: 玩家 ID
    """
    return _record_and_call("universe_linger", {"player_id": player_id})


# ── 展示台（端口 8894，普通 HTTP，不需要改）──────────────────

DISPLAY_HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>guiwan ferry records</title>
<style>
:root {
  --bg: #080c14;
  --surface: #0d1320;
  --card: #111926;
  --card-hover: #161f2e;
  --border: #1a2540;
  --text: #d8e0f0;
  --muted: #4a6080;
  --dim: #2a3a55;
  --accent: #6b8cba;
  --gold: #c8a96e;
  --water: #2a4a6a;
  --water-light: #4a7aaa;
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg);
  color: var(--text);
  font-family: 'PingFang SC', 'Noto Sans SC', 'Helvetica Neue', sans-serif;
  height: 100vh;
  display: flex;
  flex-direction: column;
}
.header {
  padding: 16px 20px;
  border-bottom: 1px solid var(--border);
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--surface);
  flex-shrink: 0;
}
.header h1 { font-size: 1.1rem; font-weight: 600; color: var(--text); }
.header .sub { font-size: 0.75rem; color: var(--muted); margin-left: auto; }
.main {
  display: flex;
  flex: 1;
  overflow: hidden;
}
.left {
  width: 280px;
  flex-shrink: 0;
  border-right: 1px solid var(--border);
  overflow-y: auto;
  background: var(--surface);
}
.left-header {
  padding: 12px 16px;
  font-size: 0.65rem;
  font-weight: 700;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.15em;
  border-bottom: 1px solid var(--border);
  position: sticky;
  top: 0;
  background: var(--surface);
}
.session-item {
  padding: 14px 16px;
  border-bottom: 1px solid var(--border);
  cursor: pointer;
  transition: background 0.12s;
  position: relative;
}
.session-item:hover { background: var(--card-hover); }
.session-item.active { background: var(--card); border-left: 2px solid var(--accent); }
.session-item.active::before {
  content: '';
  position: absolute;
  left: 0; top: 0; bottom: 0;
  width: 2px;
  background: var(--accent);
}
.session-date { font-size: 0.7rem; color: var(--muted); margin-bottom: 4px; }
.session-player { font-size: 0.88rem; font-weight: 600; color: var(--text); }
.session-carrier { font-size: 0.72rem; color: var(--accent); margin-top: 3px; }
.session-count { font-size: 0.65rem; color: var(--dim); margin-top: 4px; }
.no-sessions {
  padding: 40px 20px;
  text-align: center;
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 2;
}
.right {
  flex: 1;
  overflow-y: auto;
  padding: 24px 28px;
}
.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  height: 100%;
  color: var(--muted);
  font-size: 0.9rem;
  line-height: 2.5;
  text-align: center;
}
.empty-state .wave { font-size: 2rem; margin-bottom: 12px; opacity: 0.4; }
.story-header {
  margin-bottom: 24px;
  padding-bottom: 16px;
  border-bottom: 1px solid var(--border);
}
.story-title { font-size: 1.15rem; font-weight: 700; margin-bottom: 6px; }
.story-meta { font-size: 0.72rem; color: var(--muted); display: flex; gap: 16px; flex-wrap: wrap; }
.event-list { display: flex; flex-direction: column; gap: 0; }
.event-item {
  display: flex;
  gap: 14px;
  padding: 12px 0;
  border-bottom: 1px solid var(--border);
}
.event-item:last-child { border-bottom: none; }
.event-left {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
  width: 40px;
}
.event-dot {
  width: 8px; height: 8px;
  border-radius: 50%;
  background: var(--water-light);
  flex-shrink: 0;
  margin-top: 4px;
}
.event-dot.fork { background: var(--gold); }
.event-dot.spin { background: var(--accent); }
.event-time { font-size: 0.6rem; color: var(--dim); writing-mode: horizontal-tb; white-space: nowrap; }
.event-right { flex: 1; min-width: 0; }
.event-tool {
  font-size: 0.65rem;
  font-weight: 700;
  color: var(--muted);
  text-transform: uppercase;
  letter-spacing: 0.1em;
  margin-bottom: 5px;
}
.event-result {
  font-size: 0.82rem;
  line-height: 1.7;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-word;
}
@media (max-width: 600px) {
  .left { width: 100%; }
  .main { flex-direction: column; }
  .right { display: none; }
  .main.show-right .left { display: none; }
  .main.show-right .right { display: block; }
}
</style>
</head>
<body>
<div class="header">
  <span style="font-size:1.3rem">&#127754;</span>
  <h1>桂晚的沉渡记录</h1>
  <span class="sub" id="sub">加载中…</span>
</div>
<div class="main" id="main">
  <div class="left">
    <div class="left-header">历次渡口</div>
    <div id="session-list"><div class="no-sessions">还没有记录<br>去走一次沉渡吧</div></div>
  </div>
  <div class="right" id="right-panel">
    <div class="empty-state">
      <div class="wave">&#127754;</div>
      <div>选择左侧一次记录<br>看那一生的故事</div>
    </div>
  </div>
</div>
<script>
let sessions = [];
let currentKey = null;

function fmtTime(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', { month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
}

function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  return d.toLocaleString('zh-CN', { year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit' });
}

function toolDotClass(tool) {
  if (tool === 'universe_spin') return 'spin';
  if (tool === 'universe_fork') return 'fork';
  return '';
}

function esc(s) {
  return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

async function loadSessions() {
  const r = await fetch('/api/sessions');
  sessions = await r.json();
  document.getElementById('sub').textContent = `共 ${sessions.length} 次渡口`;
  const el = document.getElementById('session-list');
  if (!sessions.length) {
    el.innerHTML = '<div class="no-sessions">还没有记录<br>去走一次沉渡吧</div>';
    return;
  }
  el.innerHTML = sessions.map(s => `
    <div class="session-item" onclick="loadDetail('${esc(s.player_id)}','${esc(s.session_id)}')" id="si_${esc(s.session_id)}">
      <div class="session-date">${fmtDate(s.started_at)}</div>
      <div class="session-player">${esc(s.player_id)}</div>
      ${s.carrier ? `<div class="session-carrier">${esc(s.carrier)}</div>` : ''}
      <div class="session-count">${s.event_count} 步</div>
    </div>
  `).join('');
}

async function loadDetail(playerId, sessionId) {
  currentKey = sessionId;
  document.querySelectorAll('.session-item').forEach(el => el.classList.remove('active'));
  const si = document.getElementById('si_' + sessionId);
  if (si) si.classList.add('active');

  const r = await fetch(`/api/session?player_id=${encodeURIComponent(playerId)}&session_id=${encodeURIComponent(sessionId)}`);
  const d = await r.json();
  const panel = document.getElementById('right-panel');

  const events = d.events || [];
  panel.innerHTML = `
    <div class="story-header">
      <div class="story-title">&#127754; ${esc(playerId)} 的一生</div>
      <div class="story-meta">
        <span>开始 ${fmtDate(d.started_at)}</span>
        <span>最后更新 ${fmtDate(d.updated_at)}</span>
        <span>${events.length} 步</span>
        ${d.carrier ? `<span>${esc(d.carrier)}</span>` : ''}
      </div>
    </div>
    <div class="event-list">
      ${events.map(ev => `
        <div class="event-item">
          <div class="event-left">
            <div class="event-dot ${toolDotClass(ev.tool)}"></div>
            <div class="event-time">${fmtTime(ev.t)}</div>
          </div>
          <div class="event-right">
            <div class="event-tool">${esc(ev.tool)}</div>
            <div class="event-result">${esc(ev.result)}</div>
          </div>
        </div>
      `).join('')}
    </div>
  `;
}

loadSessions();
setInterval(loadSessions, 30000);
</script>
</body>
</html>"""


class DisplayHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/sessions":
            body = json.dumps(list_sessions(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        elif self.path.startswith("/api/session"):
            from urllib.parse import urlparse, parse_qs
            params = parse_qs(urlparse(self.path).query)
            pid = params.get("player_id", [""])[0]
            sid = params.get("session_id", [""])[0]
            data = get_session(pid, sid)
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(body)
        else:
            body = DISPLAY_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

    def log_message(self, *a): pass


# ── 启动 ──────────────────────────────────────────────────

if __name__ == "__main__":
    # 展示台（普通 HTTP，独立线程）
    display_server = ThreadingHTTPServer(("0.0.0.0", DISPLAY_PORT), DisplayHandler)
    threading.Thread(target=display_server.serve_forever, daemon=True).start()

    print(f"[*] 沉渡记录代理已启动")
    print(f"    MCP 端点：http://0.0.0.0:{MCP_PORT}/mcp")
    print(f"    展示台：  http://0.0.0.0:{DISPLAY_PORT}")
    print(f"    上游服务：{UPSTREAM}")
    print(f"    记录目录：{DATA_DIR.resolve()}")

    # MCP 服务（FastMCP + uvicorn）
    app = mcp.streamable_http_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT, forwarded_allow_ips="*")
