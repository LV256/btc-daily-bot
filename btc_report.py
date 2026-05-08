#!/usr/bin/env python3
"""BTC + 纳指定投 综合监控 — 每2h报告 + 暴跌告警"""
import json, os, sys, urllib.request, time, re
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TZ = timezone(timedelta(hours=8))
DCA_DAY = 15  # 每月15号定投

# ── 定投配置 ──────────────────────────────────────────
DCA_ALLOC = {"SPX": 0.60, "NDX": 0.40}  # 标普60% 纳指40%

def fetch(url, timeout=15, retries=3):
    for i in range(retries):
        try:
            return json.load(urllib.request.urlopen(url, timeout=timeout))
        except Exception:
            if i == retries - 1: raise
            time.sleep(2 ** i)

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}))
    except Exception as e:
        print(f"TG error: {e}", file=sys.stderr)

# ═══════════════════════════════════════════════════════
# 1. BTC 数据
# ═══════════════════════════════════════════════════════
coin = fetch("https://api.coingecko.com/api/v3/coins/bitcoin?localization=false&tickers=false&community_data=false&developer_data=false&market_data=true")
md = coin["market_data"]
btc = md["current_price"]["usd"]
btc_1h = md.get("price_change_percentage_1h_in_currency", {}).get("usd", 0) or 0
btc_24h = md["price_change_percentage_24h"]
btc_high24 = md["high_24h"]["usd"]
btc_low24 = md["low_24h"]["usd"]
btc_vol = md["total_volume"]["usd"]
btc_mcap = md["market_cap"]["usd"]
btc_ath = md["ath"]["usd"]

time.sleep(1.5)

c90 = fetch("https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=90")
p90 = [p[1] for p in c90["prices"]]
ranges = {}
for label, h in [("24H", 24), ("7D", 168), ("30D", 720)]:
    seg = p90[-h:] if h < len(p90) else p90
    ranges[label] = {"high": max(seg), "low": min(seg), "chg": (btc - seg[0]) / seg[0] * 100}
ranges["90D"] = {"high": max(p90), "low": min(p90), "chg": (btc - p90[0]) / p90[0] * 100}

time.sleep(1.5)

fng = fetch("https://api.alternative.me/fng/?limit=3")
fng_now = int(fng["data"][0]["value"]); fng_cls = fng["data"][0]["value_classification"]

glb = fetch("https://api.coingecko.com/api/v3/global")
btc_dom = glb["data"]["market_cap_percentage"]["btc"]
tot_mcap = glb["data"]["total_market_cap"]["usd"]

r90 = ranges["90D"]; d = r90["high"] - r90["low"]
fibs = {"1.0": r90["high"], "0.786": r90["low"] + d * 0.786, "0.618": r90["low"] + d * 0.618,
        "0.5": r90["low"] + d * 0.5, "0.382": r90["low"] + d * 0.382, "0.0": r90["low"]}

# ═══════════════════════════════════════════════════════
# 2. 暴跌告警 (BTC 1h -3%)
# ═══════════════════════════════════════════════════════
if btc_1h <= -3:
    alert = f"""🚨 BTC 暴跌警报

当前: ${btc:,.0f}
1h跌幅: {btc_1h:.1f}%
24h: ${btc_low24:,.0f} — ${btc_high24:,.0f}
恐惧: {fng_now} ({fng_cls})

支撑: ${fibs['0.786']:,.0f} / ${fibs['0.618']:,.0f}
操作: 不抄底，等止跌"""
    send_telegram(alert)
    print("ALERT SENT")

# ═══════════════════════════════════════════════════════
# 3. 整点判断
# ═══════════════════════════════════════════════════════
now_utc = datetime.now(timezone.utc)
bj_h = (now_utc.hour + 8) % 24; bj_m = now_utc.minute
force = os.environ.get("FORCE_REPORT", "") == "1"
if not force and (bj_h % 2 != 0 or bj_m > 3):
    print(f"SKIP: {bj_h:02d}:{bj_m:02d}")
    sys.exit(0)

# ═══════════════════════════════════════════════════════
# 4. 纳指/标普 + VIX 数据
# ═══════════════════════════════════════════════════════
def yahoo_quote(symbol):
    """获取 Yahoo Finance 报价"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    try:
        d = fetch(url, timeout=10, retries=2)
        q = d["chart"]["result"][0]
        meta = q["meta"]
        close = q["indicators"]["quote"][0]["close"]
        prices = [p for p in close if p is not None]
        return {
            "price": meta["regularMarketPrice"],
            "prev_close": meta.get("previousClose", meta["regularMarketPrice"]),
            "high": meta["regularMarketDayHigh"],
            "low": meta["regularMarketDayLow"],
            "chg_pct": (meta["regularMarketPrice"] - meta.get("previousClose", meta["regularMarketPrice"])) / meta.get("previousClose", meta["regularMarketPrice"]) * 100,
            "prices_5d": prices,
        }
    except:
        return None

def yahoo_range(symbol, days=30):
    """获取月/季度数据"""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={days}d"
    try:
        d = fetch(url, timeout=10, retries=2)
        q = d["chart"]["result"][0]
        close = q["indicators"]["quote"][0]["close"]
        prices = [p for p in close if p is not None]
        return {"high": max(prices), "low": min(prices), "chg": (prices[-1] - prices[0]) / prices[0] * 100 if prices else 0}
    except:
        return None

spx = yahoo_quote("^GSPC")
ndx = yahoo_quote("^IXIC")
vix = yahoo_quote("^VIX")

spx_m = yahoo_range("^GSPC", 30)
ndx_m = yahoo_range("^IXIC", 30)

# PE 估算（multipl.com）
def get_pe(symbol):
    try:
        url = f"https://www.multpl.com/s-p-500-pe-ratio/table/by-month" if "spx" in symbol else ""
        # 使用简化估算: 用过往数据的近似PE
        pass
    except:
        pass
    return None

# 简化PE：用multpl.com免费CSV
def get_spx_pe():
    try:
        # multpl 提供的CSV
        url = "https://www.multpl.com/s-p-500-pe-ratio/table/by-month"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        html = urllib.request.urlopen(req, timeout=10).read().decode()
        # 提取table中的最新PE
        m = re.search(r'<td[^>]*>(\d+\.\d+)</td>', html)
        if m:
            return float(m.group(1))
    except:
        pass
    return None

spx_pe = get_spx_pe()

# ═══════════════════════════════════════════════════════
# 5. 定投逻辑
# ═══════════════════════════════════════════════════════
today = datetime.now(TZ)
dca_this_month = today.replace(day=DCA_DAY, hour=0, minute=0, second=0, microsecond=0)
if today.day > DCA_DAY:
    # 下个月
    if today.month == 12:
        dca_this_month = today.replace(year=today.year + 1, month=1, day=DCA_DAY)
    else:
        dca_this_month = today.replace(month=today.month + 1, day=DCA_DAY)
days_to_dca = (dca_this_month - today).days

# 定投日判断
is_dca_day = today.day == DCA_DAY

# 定投建议
def dca_advice():
    lines = []
    date_str = dca_this_month.strftime("%m月%d日")

    # 当天
    if is_dca_day:
        lines.append(f"🔔 今天是定投日！按 60/40 比例买入 SPX/NDX")

    # 倒数
    if days_to_dca == 0:
        lines.append(f"📍 今天就是定投日 {date_str}")
    elif days_to_dca <= 3:
        lines.append(f"⏰ 距定投日还有 {days_to_dca} 天 ({date_str})")

    # 估值判断
    if spx_pe:
        pe = spx_pe
        if pe < 22:
            lines.append(f"💰 标普PE={pe:.1f} 低估区间 → 可加倍定投")
        elif pe < 28:
            lines.append(f"📊 标普PE={pe:.1f} 合理估值")
        elif pe < 33:
            lines.append(f"⚡ 标普PE={pe:.1f} 偏高 → 正常定投，不加倍")
        else:
            lines.append(f"⚠️ 标普PE={pe:.1f} 高估 → 正常定投，不加倍")

    # 月度表现
    if spx_m:
        chg = spx_m["chg"]
        if chg < -5:
            lines.append(f"📉 标普月跌{chg:.1f}% → 考虑加倍定投")
        elif chg < -2:
            lines.append(f"📉 标普月跌{chg:.1f}% → 正常定投")
    if ndx_m:
        chg = ndx_m["chg"]
        if chg < -5:
            lines.append(f"📉 纳指月跌{chg:.1f}% → 考虑加倍定投")

    # VIX
    if vix and vix["price"]:
        v = vix["price"]
        if v > 30:
            lines.append(f"😱 VIX={v:.1f} 恐慌 → 是加倍定投的好时机")
        elif v > 25:
            lines.append(f"😟 VIX={v:.1f} 偏高 → 可适度加码")

    return "\n".join(lines) if lines else "📊 无特殊信号，正常定投即可"

# ═══════════════════════════════════════════════════════
# 6. BTC 风险
# ═══════════════════════════════════════════════════════
btc_risks = []
if btc > fibs["0.786"]:
    btc_risks.append(f"🟢 价格高于0.786 Fib (${fibs['0.786']:,.0f})，结构健康")
elif btc > fibs["0.618"]:
    btc_risks.append(f"🟡 0.618-0.786之间，中性偏弱")
else:
    btc_risks.append(f"🟠 跌破0.618，结构走弱")

btc_risks.append(f"{'🟠' if btc_24h < -3 else '🟡' if btc_24h < 0 else '🟢'} 24h{btc_24h:+.1f}%")

if btc_vol > 45e9:
    btc_risks.append(f"🟡 放量 ${btc_vol/1e9:.1f}B")
elif btc_vol < 25e9:
    btc_risks.append(f"🟢 缩量 ${btc_vol/1e9:.1f}B")

if fng_now <= 25:
    btc_risks.append(f"🟢 极恐{fng_now}，买点区域")
elif fng_now <= 40:
    btc_risks.append(f"🟡 恐惧{fng_now}")

if btc_dom > 58:
    btc_risks.append(f"🟡 BTC市占{btc_dom:.1f}%偏高")

# BTC 操作
def btc_advice():
    lines = []
    dd = (btc_ath - btc) / btc_ath * 100
    if btc > fibs["0.786"]:
        lines.append(f"持仓: 健康，止损${fibs['0.786']:,.0f}")
        lines.append(f"加仓: ${fibs['0.618']:,.0f}挂单")
    else:
        lines.append(f"持仓: ≤50%，止损${fibs['0.618']:,.0f}")
    lines.append(f"减仓: 日线收<${fibs['0.618']:,.0f}→-30%")
    lines.append(f"突破: 过${r90['high']:,.0f}→加仓")
    return "\n".join(lines)

# ═══════════════════════════════════════════════════════
# 7. 生成综合报告
# ═══════════════════════════════════════════════════════
now = datetime.now(TZ).strftime("%m-%d %H:%M")

spx_str = f"${spx['price']:,.0f} ({spx['chg_pct']:+.1f}%)" if spx else "N/A"
ndx_str = f"${ndx['price']:,.0f} ({ndx['chg_pct']:+.1f}%)" if ndx else "N/A"
vix_str = f"{vix['price']:.1f}" if vix and vix["price"] else "N/A"

btc_emoji = "📈" if btc_24h > 1 else "📉" if btc_24h < -1 else "➡️"

report = f"""📊 综合行情  {now}

₿ BTC {btc_emoji} ${btc:,.0f}  1h{btc_1h:+.1f}%  24h{btc_24h:+.1f}%
📈 标普 ${spx_str}
📈 纳指 ${ndx_str}
😱 VIX {vix_str}  |  恐惧贪婪 {fng_now}

━━━━━━━━━━━━━━━━━━━━
📌 BTC 关键数据
  24h: ${btc_low24:,.0f} — ${btc_high24:,.0f}  |  7日 {ranges['7D']['chg']:+.1f}%
  30日 {ranges['30D']['chg']:+.1f}%  |  90日 {ranges['90D']['chg']:+.1f}%
  距ATH: -{(btc_ath-btc)/btc_ath*100:.1f}%  |  市占{btc_dom:.1f}%

⚠️ BTC风险
"""
for r in btc_risks:
    report += f"  {r}\n"

report += f"""
🎯 BTC关键位
  阻力: ${r90['high']:,.0f} (90日高)
  支撑: ${fibs['0.786']:,.0f} → ${fibs['0.618']:,.0f}

💡 BTC操作
  {btc_advice()}

━━━━━━━━━━━━━━━━━━━━
📌 纳指定投  (标普60% / 纳指40%)
  定投日: 每月{DCA_DAY}号"""

if is_dca_day:
    report += f"\n  🔔 今天就是定投日！"
else:
    report += f"\n  距下次定投: {days_to_dca}天"

if spx_m:
    report += f"\n  标普30日: {spx_m['chg']:+.1f}%"
if ndx_m:
    report += f"\n  纳指30日: {ndx_m['chg']:+.1f}%"
if spx_pe:
    report += f"\n  标普PE: {spx_pe:.1f}"

report += f"""

💡 定投建议
  {dca_advice()}

━━━━━━━━━━━━━━━━━━━━"""

send_telegram(report)
print(f"REPORT SENT: {bj_h:02d}:{bj_m:02d}")
