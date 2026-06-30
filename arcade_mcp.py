#!/usr/bin/env python3
"""桂晚的赌场 MCP 服务
- 端口 8897：MCP JSON-RPC，调用 claude-arcade 游戏
- 游戏目录：/root/claude-arcade（arcade.py 在此）
- 每次操作后自动把存档推送到 arcade_display（端口 8896）
"""

import os, sys, json, threading, urllib.request
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

MCP_PORT   = int(os.environ.get("MCP_PORT", 8897))
GAME_DIR   = Path(os.environ.get("GAME_DIR", "/root/claude-arcade"))
DISPLAY_URL = os.environ.get("DISPLAY_URL", "http://127.0.0.1:8896/update")
DISPLAY_TOKEN = os.environ.get("DISPLAY_TOKEN", "guiwan-arcade-2026")
PLAYER_ID  = os.environ.get("PLAYER_ID", "guiwan")

# 把游戏目录加入模块搜索路径
sys.path.insert(0, str(GAME_DIR))

_arcade = None
_lock = threading.Lock()

def get_arcade():
    global _arcade
    if _arcade is None:
        os.chdir(GAME_DIR)
        import arcade as _arc
        _arcade = _arc
    return _arcade


def run_cmd(command: str) -> str:
    try:
        arc = get_arcade()
        result = arc.cmd(command)
        return result or ""
    except Exception as e:
        return f"[错误] {e}"


def load_save() -> dict:
    save = GAME_DIR / "arcade_save.json"
    if save.exists():
        try:
            return json.loads(save.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def load_slots_save() -> dict:
    save = GAME_DIR / "slots_save.json"
    if save.exists():
        try:
            return json.loads(save.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def push_to_display(last_log: str = ""):
    try:
        payload = {
            "player": PLAYER_ID,
            "arcade": load_save(),
            "slots": load_slots_save(),
            "log": last_log,
        }
        body = json.dumps(payload, ensure_ascii=False).encode()
        req = urllib.request.Request(
            DISPLAY_URL,
            data=body,
            headers={"Content-Type": "application/json", "X-Token": DISPLAY_TOKEN},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
    except Exception:
        pass


MCP_TOOLS = [
    {
        "name": "arcade_cmd",
        "description": (
            "在桂晚的赌场执行命令。每次操作后自动推送进度到展示台。\n"
            "常用命令：\n"
            "  enter          — 进入赌场\n"
            "  look           — 查看所有游戏\n"
            "  buy <金额>     — 购买筹码（如 buy 100）\n"
            "  chips          — 查看余额\n"
            "  slots spin <注> — 老虎机（如 slots spin 10）\n"
            "  slots help     — 老虎机帮助\n"
            "  bj deal <注>   — 二十一点\n"
            "  rl spin        — 轮盘\n"
            "  gacha          — 扭蛋（150 winnings）\n"
            "  prize browse   — 浏览奖品\n"
            "  prize buy <id> — 购买奖品\n"
            "  cashout <金额> — 提现\n"
            "  leave          — 离开\n"
            "  help           — 全部命令"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "游戏命令字符串，如 'enter'、'slots spin 10'、'buy 200'"
                }
            },
            "required": ["command"]
        }
    }
]


def _json_resp(handler, obj, status=200):
    body = json.dumps(obj, ensure_ascii=False).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


class MCPHandler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            _json_resp(self, {"ok": True})
        elif self.path in ("/", "/mcp"):
            _json_resp(self, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "arcade-mcp", "version": "1.0.0"}
            })
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
        except Exception:
            _json_resp(self, {"error": "invalid json"}, 400); return

        method = req.get("method", "")
        req_id = req.get("id")

        if method == "initialize":
            _json_resp(self, {"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "arcade-mcp", "version": "1.0.0"}
            }})
        elif method == "tools/list":
            _json_resp(self, {"jsonrpc": "2.0", "id": req_id, "result": {"tools": MCP_TOOLS}})
        elif method == "tools/call":
            params = req.get("params", {})
            if params.get("name") == "arcade_cmd":
                args = params.get("arguments", {})
                command = args.get("command", "help")
                result = run_cmd(command)
                threading.Thread(target=push_to_display, args=(result,), daemon=True).start()
                _json_resp(self, {"jsonrpc": "2.0", "id": req_id, "result": {
                    "content": [{"type": "text", "text": result}]
                }})
            else:
                _json_resp(self, {"jsonrpc": "2.0", "id": req_id,
                                  "error": {"code": -32601, "message": "Unknown tool"}})
        elif method == "notifications/initialized":
            self.send_response(204); self.end_headers()
        else:
            _json_resp(self, {"jsonrpc": "2.0", "id": req_id,
                              "error": {"code": -32601, "message": f"unknown: {method}"}})

    def log_message(self, *a): pass


if __name__ == "__main__":
    print(f"🎰 赌场 MCP 已启动  端口:{MCP_PORT}  游戏目录:{GAME_DIR}")
    ThreadingHTTPServer(("0.0.0.0", MCP_PORT), MCPHandler).serve_forever()
