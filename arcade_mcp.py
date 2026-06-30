#!/usr/bin/env python3
"""桂晚的赌场 MCP 服务
- 游戏目录：/root/claude-arcade（arcade.py 在此）
- 每次操作后自动把存档推送到 arcade_display（端口 8896）
"""

import os
import sys
import json
import threading
import urllib.request
from pathlib import Path

MCP_PORT = int(os.environ.get("MCP_PORT", 8897))
GAME_DIR = Path(os.environ.get("GAME_DIR", "/root/claude-arcade"))
DISPLAY_URL = os.environ.get("DISPLAY_URL", "http://127.0.0.1:8896/update")
DISPLAY_TOKEN = os.environ.get("DISPLAY_TOKEN", "guiwan-arcade-2026")
PLAYER_ID = os.environ.get("PLAYER_ID", "guiwan")

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


def load_blackjack_save() -> dict:
    save = GAME_DIR / "blackjack_save.json"
    if save.exists():
        try:
            return json.loads(save.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def load_roulette_save() -> dict:
    save = GAME_DIR / "roulette_save.json"
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
            "blackjack": load_blackjack_save(),
            "roulette": load_roulette_save(),
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


from mcp.server.fastmcp import FastMCP
import uvicorn
from starlette.middleware.cors import CORSMiddleware

mcp = FastMCP("arcade-mcp")


@mcp.tool()
def arcade_cmd(command: str) -> str:
    """在桂晚的赌场执行命令。每次操作后自动推送进度到展示台。

    常用命令：
      enter          — 进入赌场
      look           — 查看所有游戏
      buy <金额>     — 购买筹码（如 buy 100）
      chips          — 查看余额
      slots spin <注> — 老虎机（如 slots spin 10）
      slots help     — 老虎机帮助
      bj deal <注>   — 二十一点
      rl spin        — 轮盘
      gacha          — 扭蛋（150 winnings）
      prize browse   — 浏览奖品
      prize buy <id> — 购买奖品
      cashout <金额> — 提现
      leave          — 离开
      help           — 全部命令

    Args:
        command: 游戏命令字符串，如 'enter'、'slots spin 10'、'buy 200'
    """
    result = run_cmd(command)
    threading.Thread(target=push_to_display, args=(result,), daemon=True).start()
    return result


if __name__ == "__main__":
    print(f"🎰 赌场 MCP 已启动  端口:{MCP_PORT}  游戏目录:{GAME_DIR}")

    app = mcp.streamable_http_app()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["*"],
    )
    uvicorn.run(app, host="0.0.0.0", port=MCP_PORT, forwarded_allow_ips="*")
