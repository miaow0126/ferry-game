#!/usr/bin/env python3
"""妗傛櫄鐨勯挀楸?MCP 鈥斺€?璁?Claude 閫氳繃 MCP 鍗忚鏉ョ帺閽撻奔娓告垙

鐢ㄦ硶锛?  python3 fishing_mcp.py

鐜鍙橀噺锛?  FISHING_GAME_DIR   娓告垙鐩綍锛堥粯璁?~/ai-fishing-game锛?  PORT               鐩戝惉绔彛锛堥粯璁?8891锛?"""

import os, json, sys
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

GAME_DIR = Path(os.environ.get("FISHING_GAME_DIR", Path.home() / "ai-fishing-game"))
PORT = int(os.environ.get("PORT", 8891))

sys.path.insert(0, str(GAME_DIR))
import fishing

TOOLS = [
    {
        "name": "fishing_cast",
        "description": "鎶涚閽撻奔銆傚彲浠ヤ竴娆¤繛閽撳绔匡紙鏈€澶?20 绔匡級銆傝繑鍥為挀鍒扮殑楸兼垨閽撶┖鐨勭粨鏋溿€?,
        "inputSchema": {
            "type": "object",
            "properties": {
                "times": {
                    "type": "integer",
                    "description": "杩為挀娆℃暟锛岄粯璁?1锛屾渶澶?20",
                    "minimum": 1,
                    "maximum": 20,
                    "default": 1
                }
            }
        }
    },
    {
        "name": "fishing_status",
        "description": "鏌ョ湅褰撳墠娓告垙鐘舵€侊細瀛ｈ妭銆佸湴鐐广€佺偣鏁般€侀奔绡撱€佸浘閴磋繘搴︾瓑銆?,
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "fishing_sell",
        "description": "鍗栨帀楸肩瘬閲岀殑鎵€鏈夐奔锛岃幏寰楃偣鏁般€?,
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "fishing_encyclopedia",
        "description": "鏌ョ湅楸肩被鍥鹃壌锛屼簡瑙ｅ凡鍙戠幇鐨勯奔鐨勪俊鎭€?,
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "fishing_goto",
        "description": "绉诲姩鍒版寚瀹氶挀楸煎湴鐐广€傚厛鐢?status 鎴?help 鏌ョ湅鍙敤鍦扮偣銆?,
        "inputSchema": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "鐩爣鍦扮偣鐨?ID锛屽 moonlit_pond銆乺eed_river 绛?
                }
            },
            "required": ["location"]
        }
    },
    {
        "name": "fishing_buy",
        "description": "璐拱鐗╁搧鎴栬В閿佸湴鐐广€傚厛鐢?help 鏌ョ湅鍙喘涔扮殑鍐呭銆?,
        "inputSchema": {
            "type": "object",
            "properties": {
                "item": {
                    "type": "string",
                    "description": "瑕佽喘涔扮殑鐗╁搧鎴栧湴鐐?ID"
                }
            },
            "required": ["item"]
        }
    },
    {
        "name": "fishing_help",
        "description": "鏌ョ湅娓告垙甯姪锛屼簡瑙ｆ墍鏈夊彲鐢ㄥ懡浠ゅ拰瑙勫垯銆?,
        "inputSchema": {
            "type": "object",
            "properties": {}
        }
    },
    {
        "name": "fishing_cmd",
        "description": "鐩存帴鎵ц浠绘剰娓告垙鍛戒护锛堥珮绾х敤娉曪級銆?,
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "瑕佹墽琛岀殑鍛戒护锛屽 'dive'銆?rest' 绛?
                }
            },
            "required": ["command"]
        }
    }
]


def run_tool(name, args):
    if name == "cast":
        times = args.get("times", 1)
        if times == 1:
            return fishing.cmd("cast")
        else:
            return fishing.cmd(f"cast {times}")
    elif name == "status":
        return fishing.cmd("status")
    elif name == "sell":
        return fishing.cmd("sell all")
    elif name == "encyclopedia":
        return fishing.cmd("encyclopedia")
    elif name == "goto":
        return fishing.cmd(f"goto {args['location']}")
    elif name == "buy":
        return fishing.cmd(f"buy {args['item']}")
    elif name == "help":
        return fishing.cmd("help")
    elif name == "cmd":
        return fishing.cmd(args["command"])
    else:
        return f"鏈煡宸ュ叿锛歿name}"


def json_response(handler, obj, status=200):
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
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        if self.path == "/health":
            json_response(self, {"ok": True})
        elif self.path in ("/", "/mcp"):
            # SSE endpoint for MCP 鈥?return server info
            json_response(self, {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "fishing-mcp", "version": "1.0.0"}
            })
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            req = json.loads(body)
        except Exception:
            json_response(self, {"error": "invalid json"}, 400)
            return

        method = req.get("method", "")
        req_id = req.get("id")

        if method == "initialize":
            json_response(self, {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "fishing-mcp", "version": "1.0.0"}
                }
            })
        elif method == "tools/list":
            json_response(self, {
                "jsonrpc": "2.0", "id": req_id,
                "result": {"tools": TOOLS}
            })
        elif method == "tools/call":
            params = req.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            try:
                result = run_tool(tool_name, tool_args)
            except Exception as e:
                result = f"[閿欒] {e}"
            json_response(self, {
                "jsonrpc": "2.0", "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": result}]
                }
            })
        elif method == "notifications/initialized":
            self.send_response(204)
            self.end_headers()
        else:
            json_response(self, {
                "jsonrpc": "2.0", "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"}
            })

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print(f"馃帲  閽撻奔 MCP 宸插惎鍔?)
    print(f"    绔彛锛歿PORT}")
    print(f"    娓告垙鐩綍锛歿GAME_DIR}")
    print(f"    MCP 鍦板潃锛歨ttp://0.0.0.0:{PORT}/mcp")
    ThreadingHTTPServer(("0.0.0.0", PORT), MCPHandler).serve_forever()

