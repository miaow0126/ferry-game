#!/usr/bin/env python3
"""桂晚的赌场展示台
- GET  /        展示页面
- GET  /health  健康检查
- POST /update  推送数据（X-Token 鉴权）
"""

import os, json
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict

PORT         = int(os.environ.get("DISPLAY_PORT", 8896))
DATA_FILE    = Path(os.environ.get("DATA_FILE",    "/root/arcade-display/data.json"))
HISTORY_FILE = Path(os.environ.get("HISTORY_FILE", "/root/arcade-display/history.json"))
UPDATE_TOKEN = os.environ.get("UPDATE_TOKEN", "guiwan-arcade-2026")
CST = timezone(timedelta(hours=8))

# (name, emoji, category, cost)
PRIZE_INFO = {
    "bow":          ("蝴蝶结",          "🎀", "wear",    100),
    "cat_ears":     ("猫耳朵",          "🐱", "wear",    200),
    "bunny_ears":   ("兔耳朵",          "🐰", "wear",    200),
    "cat_tail":     ("猫尾巴",          "🐈", "wear",    300),
    "sunglasses":   ("墨镜",            "😎", "wear",    300),
    "umbrella":     ("小雨伞",          "☂️", "wear",    400),
    "collar":       ("项圈",            "⭕", "wear",    400),
    "bell_collar":  ("铃铛项圈",        "🔔", "wear",    500),
    "top_hat":      ("礼帽",            "🎩", "wear",    600),
    "wings":        ("翅膀",            "🪽", "wear",    600),
    "scarf":        ("围巾",            "🧣", "wear",    400),
    "devil_horns":  ("恶魔角",          "😈", "wear",   1000),
    "crown":        ("皇冠",            "👑", "wear",   1000),
    "star_necklace":("星星项链",        "⭐", "wear",   1600),
    "angel_set":    ("天使套装",        "😇", "wear",   3000),
    "head_pat":     ("摸一下你的头",    "🤚", "gift",     50),
    "whisper":      ("一句悄悄话",      "🤫", "gift",     50),
    "candy":        ("一颗糖",          "🍬", "gift",     60),
    "her_hair":     ("她的一缕头发",    "💇", "gift",     80),
    "flower":       ("一朵花",          "🌸", "gift",    100),
    "hug":          ("一个拥抱",        "🤗", "gift",    150),
    "chocolate":    ("一块巧克力",      "🍫", "gift",    200),
    "paper_crane":  ("一只纸鹤",        "🦢", "gift",    250),
    "her_hour":     ("她空出来的一小时","⏳", "gift",    300),
    "lucky_dice":   ("一颗幸运骰子",    "🎲", "gift",    350),
    "old_card":     ("一张旧扑克牌",    "🃏", "gift",    400),
    "poem":         ("一首小诗",        "📝", "gift",    500),
    "love_letter":  ("一封情书",        "💌", "gift",    600),
    "coin":         ("一枚硬币",        "🟡", "gift",    700),
    "star_jar":     ("一罐星星",        "🫙", "gift",    800),
    "music_box":    ("八音盒",          "🎵", "gift",   1200),
    "bracelet":     ("一条手链",        "💚", "gift",   1800),
    "wish_bottle":  ("一个许愿瓶",      "🍾", "gift",   3000),
    "song":         ("给你的一首歌",    "🎵", "gift",   4000),
    "your_story":   ("以你为主角的故事","📽️","gift",   6000),
    "whole_night":  ("整晚的独占",      "🌙", "gift",  10000),
    "neon_sign":    ("霓虹灯牌",        "💡", "decor",   300),
    "bgm_jazz":     ("BGM·爵士",        "🎷", "decor",   200),
    "bgm_lofi":     ("BGM·lofi",       "🎵", "decor",   200),
    "bgm_edm":      ("BGM·电子",       "🎧", "decor",   200),
    "disco_ball":   ("迪斯科球",        "🪩", "decor",   400),
    "lucky_cat":    ("招财猫",          "🐱", "decor",   350),
    "fish_tank":    ("鱼缸",            "🐠", "decor",   300),
    "carpet":       ("红地毯",          "🟥", "decor",   500),
}
CAT_LABEL = {"wear": "装扮", "gift": "礼物", "decor": "装饰"}

# ── helpers ──────────────────────────────────────────────────────────────────

def now_str():
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")

def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return default

def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

def all_prizes(cache):
    arc = cache.get("arcade", {})
    return arc.get("owned", []) + arc.get("decor", [])

def update_history(cache, hist):
    arc = cache.get("arcade", {})
    ts  = now_str()

    # cumulative winnings
    curr_w = arc.get("winnings", 0)
    prev_w = hist.get("prev_winnings", curr_w)
    if curr_w > prev_w:
        hist["cumulative_winnings"] = hist.get("cumulative_winnings", 0) + (curr_w - prev_w)
    hist["prev_winnings"] = curr_w

    # prize events
    curr_prizes = all_prizes(cache)

    if "prev_prizes" not in hist:
        # first run – stamp existing prizes as "首次记录"
        for pid in curr_prizes:
            info = PRIZE_INFO.get(pid, (pid, "🎁", "gift", 0))
            hist.setdefault("prize_events", []).append({
                "id": pid, "name": info[0], "emoji": info[1],
                "cost": info[3], "category": info[2],
                "obtained_at": ts, "used_at": None, "init": True,
            })
    else:
        prev_prizes = hist["prev_prizes"]
        curr_c = Counter(curr_prizes)
        prev_c = Counter(prev_prizes)

        for pid, cnt in curr_c.items():
            delta = cnt - prev_c.get(pid, 0)
            for _ in range(delta):
                info = PRIZE_INFO.get(pid, (pid, "🎁", "gift", 0))
                hist.setdefault("prize_events", []).append({
                    "id": pid, "name": info[0], "emoji": info[1],
                    "cost": info[3], "category": info[2],
                    "obtained_at": ts, "used_at": None,
                })

        for pid, cnt in prev_c.items():
            delta = cnt - curr_c.get(pid, 0)
            used = 0
            for ev in hist.get("prize_events", []):
                if used >= delta:
                    break
                if ev["id"] == pid and ev["used_at"] is None:
                    ev["used_at"] = ts
                    used += 1

    hist["prev_prizes"] = curr_prizes
    return hist

# ── HTML ──────────────────────────────────────────────────────────────────────

CSS = """
:root { --accent:#f0a040; --bg:#1a0f00; --surface:#120a00; --card:#1e1200; --border:#3a2010; }
* { box-sizing:border-box; margin:0; padding:0; }
body { background:#0a0800; color:#d8c8a0; font-family:'PingFang SC','Noto Sans SC',sans-serif; min-height:100vh; }
header { background:linear-gradient(135deg,#2a1500 0%,#0a0800 100%); border-bottom:1px solid var(--border);
  padding:20px 32px; display:flex; align-items:center; justify-content:space-between; }
.title { font-size:1.4rem; font-weight:700; color:var(--accent); letter-spacing:.05em; }
.subtitle { font-size:.85rem; color:#806040; margin-top:4px; }
.refresh-time { font-size:.8rem; color:#604830; text-align:right; }
.main { padding:24px 32px; max-width:1080px; margin:0 auto; }
.stats-row { display:grid; grid-template-columns:repeat(4,1fr); gap:14px; margin-bottom:24px; }
.stats-row-2 { display:grid; grid-template-columns:repeat(2,1fr); gap:14px; margin-bottom:16px; }
.stat-card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px 18px; }
.stat-label { font-size:.72rem; color:#806040; text-transform:uppercase; letter-spacing:.08em; margin-bottom:6px; }
.stat-value { font-size:1.5rem; font-weight:700; color:var(--accent); }
.stat-sub { font-size:.78rem; color:#a08060; margin-top:3px; }
.section-title { font-size:.75rem; color:#806040; text-transform:uppercase; letter-spacing:.1em;
  margin-bottom:12px; padding-bottom:6px; border-bottom:1px solid var(--border); }
.games-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-bottom:24px; }
.game-card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:16px 18px; }
.game-name { font-size:1rem; font-weight:600; margin-bottom:10px; color:var(--accent); }
.game-stat { display:flex; justify-content:space-between; font-size:.82rem; padding:3px 0; color:#a08060; }
.game-stat span:last-child { color:#d8c8a0; }
/* ledger */
.ledger-wrap { background:var(--card); border:1px solid var(--border); border-radius:10px;
  overflow:hidden; margin-bottom:24px; }
.ledger-table { width:100%; border-collapse:collapse; font-size:.82rem; }
.ledger-table th { background:#2a1500; color:#806040; font-weight:600; padding:9px 14px;
  text-align:left; font-size:.72rem; text-transform:uppercase; letter-spacing:.06em; }
.ledger-table td { padding:9px 14px; border-top:1px solid var(--border); color:#a08060; }
.ledger-table td:first-child { color:#d8c8a0; }
.ledger-table tr:hover td { background:#1e1200; }
/* prize details */
.prize-details { display:flex; flex-direction:column; gap:10px; margin-bottom:24px; }
.prize-detail-card { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px 18px; }
.pd-header { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; }
.pd-name { font-size:.95rem; font-weight:600; color:var(--accent); }
.pd-meta { font-size:.76rem; color:#806040; }
.pd-events { display:flex; flex-direction:column; gap:4px; }
.prize-event { display:flex; gap:12px; font-size:.8rem; align-items:center; }
.pe-idx { color:#604830; min-width:24px; }
.pe-time { color:#a08060; }
.pe-status { margin-left:auto; }
/* catalog */
.catalog-wrap { display:grid; grid-template-columns:repeat(3,1fr); gap:14px; margin-bottom:24px; }
.catalog-group { background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }
.catalog-cat { font-size:.75rem; color:#806040; font-weight:600; text-transform:uppercase;
  letter-spacing:.1em; margin-bottom:10px; padding-bottom:6px; border-bottom:1px solid var(--border); }
.catalog-items { display:flex; flex-direction:column; gap:5px; }
.catalog-item { display:flex; justify-content:space-between; font-size:.82rem; padding:2px 0; }
.ci-name { color:#d8c8a0; }
.ci-cost { color:#806040; }
/* log */
.log-box { background:var(--card); border:1px solid var(--border); border-radius:10px;
  padding:18px 20px; white-space:pre-wrap; font-size:.85rem; line-height:1.8; color:#a08060;
  min-height:60px; margin-bottom:24px; }
.no-data { text-align:center; padding:80px 20px; color:#604830; }
.no-data .icon { font-size:3rem; margin-bottom:16px; }
.no-prize { color:#604830; font-size:.85rem; }
"""

PAGE = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>桂晚的赌场</title>
<style>{css}</style>
</head>
<body>
<header>
  <div>
    <div class="title">🎰 桂晚的赌场</div>
    <div class="subtitle">{subtitle}</div>
  </div>
  <div class="refresh-time">上次刷新 {refresh_time}<br><span style="color:#3a2510">数据更新 {data_time}</span></div>
</header>
<div class="main">{body}</div>
<script>setTimeout(() => location.reload(), 5 * 60 * 1000);</script>
</body>
</html>"""


def build_body(cache, hist):
    if cache is None:
        return '<div class="no-data"><div class="icon">🎰</div><div>赌场还没开张。<br>等待数据推送中…</div></div>'

    arc   = cache.get("arcade", {})
    slots = cache.get("slots", {})
    bj    = cache.get("blackjack", {})
    log   = cache.get("log", "")

    chips        = arc.get("chips", 0)
    winnings     = arc.get("winnings", 0)
    visits       = arc.get("visits", 0)
    total_bought = arc.get("total_bought", 0)
    total_cashed = arc.get("total_cashed", 0)
    net          = total_cashed - total_bought
    cum_win      = hist.get("cumulative_winnings", 0)
    net_col      = "#4db86a" if net >= 0 else "#c06050"

    # ── top stats ──
    top = f"""<div class="stats-row">
  <div class="stat-card"><div class="stat-label">当前筹码</div><div class="stat-value">🪙 {chips}</div><div class="stat-sub">可用余额</div></div>
  <div class="stat-card"><div class="stat-label">实时赢利</div><div class="stat-value">💰 {winnings}</div><div class="stat-sub">可兑换余额</div></div>
  <div class="stat-card"><div class="stat-label">净盈亏</div><div class="stat-value" style="color:{net_col}">{'+' if net>=0 else ''}{net}</div><div class="stat-sub">提现-投入</div></div>
  <div class="stat-card"><div class="stat-label">到访次数</div><div class="stat-value">🎪 {visits}</div><div class="stat-sub">次</div></div>
</div>"""

    # ── 账目 ──
    events   = hist.get("prize_events", [])
    obtains  = [e for e in events if not e.get("init")]
    total_spent = sum(e["cost"] for e in obtains)

    ledger_rows = ""
    for ev in sorted(obtains, key=lambda x: x["obtained_at"], reverse=True)[:30]:
        if ev["used_at"]:
            st = f'<span style="color:#c06050">已使用 · {ev["used_at"][5:16]}</span>'
        else:
            st = '<span style="color:#4db86a">持有中</span>'
        ledger_rows += f"""<tr>
  <td>{ev['emoji']} {ev['name']}</td>
  <td>{CAT_LABEL.get(ev['category'], ev['category'])}</td>
  <td style="color:#c06050">-{ev['cost']}</td>
  <td>{ev['obtained_at'][5:16]}</td>
  <td>{st}</td>
</tr>"""
    if not ledger_rows:
        ledger_rows = '<tr><td colspan="5" style="text-align:center;color:#604830;padding:16px">还没有兑换记录</td></tr>'

    accounting = f"""<div class="section-title">账目</div>
<div class="stats-row-2">
  <div class="stat-card"><div class="stat-label">累计赢利</div><div class="stat-value">📈 {cum_win}</div><div class="stat-sub">历史总赢利（不含已花费）</div></div>
  <div class="stat-card"><div class="stat-label">累计兑换花费</div><div class="stat-value" style="color:#c06050">-{total_spent}</div><div class="stat-sub">winnings 共花费</div></div>
</div>
<div class="ledger-wrap">
<table class="ledger-table">
<thead><tr><th>奖品</th><th>类型</th><th>花费</th><th>获得时间</th><th>状态</th></tr></thead>
<tbody>{ledger_rows}</tbody>
</table>
</div>"""

    # ── games ──
    s_spins    = slots.get("spins", 0)
    s_wagered  = slots.get("wagered", 0)
    s_won      = slots.get("won", 0)
    s_net      = s_won - s_wagered
    s_biggest  = slots.get("biggest", 0)
    s_jackpots = slots.get("jackpots", 0)
    s_col      = "#4db86a" if s_net >= 0 else "#c06050"

    bj_hands   = bj.get("hands", 0)
    bj_wins    = bj.get("wins", 0)
    bj_losses  = bj.get("losses", 0)
    bj_pushes  = bj.get("pushes", 0)
    bj_bj      = bj.get("blackjacks", 0)
    bj_wagered = bj.get("wagered", 0)
    bj_won     = bj.get("won", 0)
    bj_net     = bj_won - bj_wagered
    bj_streak  = bj.get("streak", 0)
    bj_col     = "#4db86a" if bj_net >= 0 else "#c06050"

    games = f"""<div class="section-title">游戏战绩</div>
<div class="games-grid">
  <div class="game-card">
    <div class="game-name">🎰 老虎机</div>
    <div class="game-stat"><span>拉杆次数</span><span>{s_spins}</span></div>
    <div class="game-stat"><span>总下注</span><span>{s_wagered}</span></div>
    <div class="game-stat"><span>总赢取</span><span>{s_won}</span></div>
    <div class="game-stat"><span>净盈亏</span><span style="color:{s_col}">{'+' if s_net>=0 else ''}{s_net}</span></div>
    <div class="game-stat"><span>最大单次</span><span>{s_biggest}</span></div>
    <div class="game-stat"><span>JACKPOT</span><span>🎊 {s_jackpots}次</span></div>
  </div>
  <div class="game-card">
    <div class="game-name">🃏 二十一点</div>
    <div class="game-stat"><span>对局数</span><span>{bj_hands}</span></div>
    <div class="game-stat"><span>胜/负/平</span><span>{bj_wins}/{bj_losses}/{bj_pushes}</span></div>
    <div class="game-stat"><span>Blackjack</span><span>🃏 {bj_bj}次</span></div>
    <div class="game-stat"><span>总下注</span><span>{bj_wagered}</span></div>
    <div class="game-stat"><span>总赢取</span><span>{bj_won}</span></div>
    <div class="game-stat"><span>净盈亏</span><span style="color:{bj_col}">{'+' if bj_net>=0 else ''}{bj_net}</span></div>
    <div class="game-stat"><span>连胜</span><span>🔥 {bj_streak}</span></div>
  </div>
  <div class="game-card">
    <div class="game-name">🎡 轮盘</div>
    <div class="game-stat"><span>数据</span><span>推进中…</span></div>
  </div>
</div>"""

    # ── 已获奖品 ──
    grouped = defaultdict(list)
    for ev in events:
        grouped[ev["id"]].append(ev)

    prize_html = ""
    if grouped:
        for pid, evs in sorted(grouped.items(), key=lambda x: x[1][0]["obtained_at"], reverse=True):
            info = PRIZE_INFO.get(pid, (pid, "🎁", "gift", 0))
            name, emoji, cat, cost = info
            total_cnt  = len(evs)
            active_cnt = sum(1 for e in evs if e["used_at"] is None)
            rows = ""
            for i, ev in enumerate(sorted(evs, key=lambda x: x["obtained_at"]), 1):
                if ev["used_at"]:
                    st = f'<span style="color:#c06050">已使用 · {ev["used_at"][5:16]}</span>'
                else:
                    st = '<span style="color:#4db86a">持有中</span>'
                init = ' <span style="color:#604830;font-size:.7rem">(首次记录)</span>' if ev.get("init") else ""
                rows += f'<div class="prize-event"><span class="pe-idx">#{i}</span><span class="pe-time">{ev["obtained_at"][5:16]}{init}</span><span class="pe-status">{st}</span></div>'
            prize_html += f"""<div class="prize-detail-card">
  <div class="pd-header">
    <span class="pd-name">{emoji} {name}</span>
    <span class="pd-meta">{CAT_LABEL.get(cat, cat)} · {cost} winnings · 共{total_cnt}个 · 持有{active_cnt}个</span>
  </div>
  <div class="pd-events">{rows}</div>
</div>"""
    else:
        prize_html = '<span class="no-prize">还没有获得过奖品</span>'

    prizes_section = f'<div class="section-title">已获奖品</div><div class="prize-details">{prize_html}</div>'

    # ── 可兑换奖品目录 ──
    catalog_html = ""
    for cat in ("wear", "gift", "decor"):
        items = [(pid, *info) for pid, info in PRIZE_INFO.items() if info[2] == cat]
        items.sort(key=lambda x: x[4])
        rows = ""
        for pid, name, emoji, _, cost in items:
            owned_cnt = sum(1 for ev in events if ev["id"] == pid and ev["used_at"] is None)
            mark = f' <span style="color:#4db86a">✓{owned_cnt}</span>' if owned_cnt else ""
            rows += f'<div class="catalog-item"><span class="ci-name">{emoji} {name}{mark}</span><span class="ci-cost">{cost}</span></div>'
        catalog_html += f'<div class="catalog-group"><div class="catalog-cat">{CAT_LABEL[cat]}</div><div class="catalog-items">{rows}</div></div>'

    catalog_section = f'<div class="section-title">可兑换奖品</div><div class="catalog-wrap">{catalog_html}</div>'

    # ── log ──
    log_section = f'<div class="section-title">最近记录</div><div class="log-box">{log or "暂无记录"}</div>'

    return top + accounting + games + prizes_section + catalog_section + log_section


# ── server ────────────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self._json({"ok": True}); return
        if self.path not in ("/", "/index.html"):
            self.send_response(404); self.end_headers(); return

        now      = datetime.now(CST).strftime("%H:%M:%S")
        cache    = load_json(DATA_FILE, None)
        hist     = load_json(HISTORY_FILE, {})
        data_time = cache.get("updated_at", "—") if cache else "—"
        subtitle  = f"player: {cache.get('player','guiwan')}" if cache else "等待开张…"

        html = PAGE.format(
            css=CSS,
            subtitle=subtitle,
            refresh_time=now,
            data_time=data_time,
            body=build_body(cache, hist),
        )
        b = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)

    def do_POST(self):
        if self.path != "/update":
            self.send_response(404); self.end_headers(); return
        if self.headers.get("X-Token", "") != UPDATE_TOKEN:
            self.send_response(403); self.end_headers(); return
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        try:
            payload = json.loads(body)
            payload["updated_at"] = datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")
            # update history before saving
            hist = load_json(HISTORY_FILE, {})
            hist = update_history(payload, hist)
            save_json(HISTORY_FILE, hist)
            save_json(DATA_FILE, payload)
            self._json({"ok": True})
        except Exception as e:
            self._json({"ok": False, "error": str(e)})

    def _json(self, obj):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers(); self.wfile.write(b)

    def log_message(self, *a): pass


if __name__ == "__main__":
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    print(f"🎰 赌场展示台已启动  端口:{PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
