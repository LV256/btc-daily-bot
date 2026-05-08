#!/usr/bin/env python3
"""BTC 行情监控 — 每2h详细报告 + 暴跌3%即时告警"""
import json, os, sys, urllib.request, time
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TZ = timezone(timedelta(hours=8))

def fetch(url, timeout=15, retries=3):
    """带重试的请求"""
    for i in range(retries):
        try:
            return json.load(urllib.request.urlopen(url, timeout=timeout))
        except Exception as e:
            if i == retries - 1:
                raise
            time.sleep(2 ** i)

def send_telegram(text, html=False):
    """发送 Telegram 消息"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
    if html:
        payload["parse_mode"] = "HTML"
    data = json.dumps(payload).encode()
    try:
        resp = json.load(urllib.request.urlopen(
            urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})))
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        print(f"Telegram HTTP {e.code}: {body}", file=sys.stderr)
        # HTML 失败时降级重试
        if html:
            del payload["parse_mode"]
            data = json.dumps(payload).encode()
            try:
                resp = json.load(urllib.request.urlopen(
                    urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})))
            except urllib.error.HTTPError as e2:
                print(f"Telegram retry HTTP {e2.code}: {e2.read().decode()}", file=sys.stderr)
                return False
        else:
            return False
    if not resp.get("ok"):
        print(f"Telegram error: {resp.get('description')}", file=sys.stderr)
        return False
    return True

# ── 数据采集 (减少API调用) ────────────────────────────
coin = fetch("https://api.coingecko.com/api/v3/coins/bitcoin?localization=false&tickers=false&community_data=false&developer_data=false&market_data=true")
md = coin["market_data"]
curr = md["current_price"]["usd"]
chg_1h = md.get("price_change_percentage_1h_in_currency", {}).get("usd", 0) or 0
chg_24h = md["price_change_percentage_24h"]
high_24 = md["high_24h"]["usd"]
low_24 = md["low_24h"]["usd"]
vol_24 = md["total_volume"]["usd"]
mcap = md["market_cap"]["usd"]
ath = md["ath"]["usd"]

time.sleep(1.5)

# 一次拿90天数据，从中提取各周期
c90 = fetch(f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days=90")
prices_90 = [p[1] for p in c90["prices"]]
n = len(prices_90)
ranges = {}
for label, hours in [("24H", 24), ("7D", 168), ("30D", 720)]:
    # 每小时约1个数据点（实际更密），估算分割点
    seg = prices_90[-(hours):] if hours < n else prices_90
    ranges[label] = {
        "high": max(seg), "low": min(seg),
        "chg": (curr - seg[0]) / seg[0] * 100,
        "amp": (max(seg) - min(seg)) / min(seg) * 100,
    }
ranges["90D"] = {
    "high": max(prices_90), "low": min(prices_90),
    "chg": (curr - prices_90[0]) / prices_90[0] * 100,
    "amp": (max(prices_90) - min(prices_90)) / min(prices_90) * 100,
}

time.sleep(1.5)

# 恐惧贪婪
fng = fetch("https://api.alternative.me/fng/?limit=3")
fng_now = int(fng["data"][0]["value"])
fng_cls = fng["data"][0]["value_classification"]
fng_prev = int(fng["data"][1]["value"])

# 全局数据
glb = fetch("https://api.coingecko.com/api/v3/global")
btc_dom = glb["data"]["market_cap_percentage"]["btc"]
tot_mcap = glb["data"]["total_market_cap"]["usd"]
tot_vol = glb["data"]["total_volume"]["usd"]

r90 = ranges["90D"]
fib_diff = r90["high"] - r90["low"]
fibs = {
    "1.0": r90["high"], "0.786": r90["low"] + fib_diff * 0.786,
    "0.618": r90["low"] + fib_diff * 0.618, "0.5": r90["low"] + fib_diff * 0.5,
    "0.382": r90["low"] + fib_diff * 0.382, "0.0": r90["low"],
}

# ── 暴跌检测 ──────────────────────────────────────────
if chg_1h <= -3:
    alert = f"""🚨 <b>BTC 暴跌警报</b> 🚨

当前: <b>${curr:,.0f}</b>
1小时跌幅: <b>{chg_1h:.1f}%</b>
24h 高低: ${low_24:,.0f} — ${high_24:,.0f}
恐惧贪婪: {fng_now} ({fng_cls})

━━━━━━━━━━━━━━━━━━━━
⚠️ 关键支撑
  ${fibs['0.786']:,.0f} (0.786 Fib)
  ${ranges['7D']['low']:,.0f} (7日低点)
  ${fibs['0.618']:,.0f} (0.618 Fib)

🟡 操作: 不抄底，等止跌信号
  跌破 ${fibs['0.786']:,.0f} → 减仓
  跌破 ${fibs['0.618']:,.0f} → 再减
━━━━━━━━━━━━━━━━━━━━"""
    send_telegram(alert, html=True)
    print("ALERT SENT: 3% drop detected")

# ── 整点判断 ──────────────────────────────────────────
now_utc = datetime.now(timezone.utc)
bj_h = (now_utc.hour + 8) % 24
bj_m = now_utc.minute

# 仅在偶数整点±3分钟推送详细报告 (手动触发 skip 此检查)
force = os.environ.get("FORCE_REPORT", "") == "1"
if not force and (bj_h % 2 != 0 or bj_m > 3):
    print(f"SKIP: {bj_h:02d}:{bj_m:02d}")
    sys.exit(0)

# ── 风险评估 ──────────────────────────────────────────
risks = []
if curr > fibs["0.786"]:
    risks.append(("🟢", f"价格结构健康，高于 0.786 Fib (${fibs['0.786']:,.0f})。趋势偏多"))
elif curr > fibs["0.618"]:
    risks.append(("🟡", f"价格在 0.618-0.786 之间，中性偏弱。跌破 ${fibs['0.618']:,.0f} 结构恶化"))
elif curr > fibs["0.5"]:
    risks.append(("🟠", f"价格跌破 0.618，结构走弱。${fibs['0.5']:,.0f} 是最后防线"))
else:
    risks.append(("🔴", f"价格跌破 0.5 Fib，结构严重受损"))

if chg_24h > 3:
    risks.append(("🟢", f"24h 涨幅 {chg_24h:.1f}%，短期动能强"))
elif chg_24h > 0:
    risks.append(("🟢", f"24h 微涨 {chg_24h:.1f}%，中性偏多"))
elif chg_24h > -3:
    risks.append(("🟡", f"24h 下跌 {chg_24h:.1f}%，正常回调"))
else:
    risks.append(("🟠", f"24h 跌超 {chg_24h:.1f}%，空头占优"))

if vol_24 > 45e9:
    risks.append(("🟡", f"放量 ${vol_24/1e9:.1f}B，下跌是出货信号"))
elif vol_24 > 25e9:
    risks.append(("🟢", f"成交量正常 ${vol_24/1e9:.1f}B"))
else:
    risks.append(("🟢", f"缩量 ${vol_24/1e9:.1f}B，抛压有限"))

if fng_now <= 25:
    risks.append(("🟢", f"极度恐惧 ({fng_now})，历史买点区域，但不等于马上见底"))
elif fng_now <= 40:
    risks.append(("🟡", f"恐惧区间 ({fng_now})，市场谨慎"))
elif fng_now <= 60:
    risks.append(("🟢", f"中性 ({fng_now})"))
else:
    risks.append(("🟡", f"贪婪 ({fng_now})，注意过热"))
if fng_now < fng_prev - 10:
    risks.append(("🟠", f"情绪恶化: {fng_prev}→{fng_now}"))

if btc_dom > 58:
    risks.append(("🟡", f"BTC 市占率 {btc_dom:.1f}% 偏高，山寨失血"))

# ── 操作建议 ──────────────────────────────────────────
def trading_advice():
    lines = []
    dd_ath = (ath - curr) / ath * 100
    if curr > fibs["0.786"]:
        lines.append(f"📌 持仓: 结构健康。止损上移至 ${fibs['0.786']:,.0f}")
    elif curr > fibs["0.618"]:
        lines.append(f"📌 持仓: 中性偏弱，≤50% 仓位。止损 ${fibs['0.618']:,.0f}")
    elif curr > fibs["0.5"]:
        lines.append(f"📌 持仓: 走弱，≤30% 仓位。止损 ${fibs['0.5']:,.0f}")
    else:
        lines.append(f"📌 持仓: 观望。${fibs['0.0']:,.0f} 抄底区")
    if curr < fibs["0.618"]:
        lines.append(f"💰 加仓挂单: ${fibs['0.5']:,.0f} / ${fibs['0.0']:,.0f}")
    elif curr < fibs["0.786"]:
        lines.append(f"💰 加仓挂单: ${fibs['0.618']:,.0f}")
    lines.append(f"🛑 减仓: 日线收 < ${fibs['0.618']:,.0f} → -30%")
    lines.append(f"🛑 止损: 日线收 < ${fibs['0.5']:,.0f} → -50%")
    lines.append(f"🚀 突破: 放量过 ${r90['high']:,.0f} → 加仓")
    lines.append(f"📅 定投: ATH回撤 {dd_ath:.0f}%，合理区间")
    return "\n".join(lines)

# ── 生成报告 ──────────────────────────────────────────
now = datetime.now(TZ).strftime("%m-%d %H:%M")
emoji = "📈" if chg_24h > 1 else "📉" if chg_24h < -1 else "➡️"

report = f"""📊 <b>BTC 行情</b>  {now}

{emoji} <b>${curr:,.0f}</b>  1h {chg_1h:+.1f}%  24h {chg_24h:+.1f}%

━━━━━━━━━━━━━━━━━━━━
📌 价格区间
  24h:  ${low_24:,.0f} — ${high_24:,.0f}
  7日:  ${ranges['7D']['low']:,.0f} — ${ranges['7D']['high']:,.0f}  ({ranges['7D']['chg']:+.1f}%)
  30日: ${ranges['30D']['low']:,.0f} — ${ranges['30D']['high']:,.0f}  ({ranges['30D']['chg']:+.1f}%)
  90日: ${ranges['90D']['low']:,.0f} — ${ranges['90D']['high']:,.0f}  ({ranges['90D']['chg']:+.1f}%)

📌 市场数据
  成交量:    ${vol_24/1e9:.1f}B
  BTC 市值:  ${mcap/1e12:.2f}T / 总 ${tot_mcap/1e12:.2f}T
  BTC 市占:  {btc_dom:.1f}%
  恐惧贪婪:  {fng_now} ({fng_cls})  {'↑' if fng_now > fng_prev else '↓'}
  距 ATH:    -{(ath - curr) / ath * 100:.1f}%

━━━━━━━━━━━━━━━━━━━━
⚠️ <b>风险预警</b>
"""

for icon, desc in risks[:5]:
    report += f"  {icon} {desc}\n"

report += f"""
━━━━━━━━━━━━━━━━━━━━
🎯 <b>重点关注</b>
  🔼 阻力: ${r90['high']:,.0f} (90日高 / 多空分界)
      突破 → ${ath * 0.7:,.0f} → ${ath:,.0f}
  🔽 支撑: ${fibs['0.786']:,.0f} (0.786) → ${fibs['0.618']:,.0f} (0.618)
"""

if chg_24h < 0 and vol_24 < 35e9:
    report += f"  📊 缩量下跌，空头力度有限\n"
elif chg_24h < 0 and vol_24 > 45e9:
    report += f"  📊 ⚠️ 放量下跌，警惕出货\n"
elif chg_24h > 0 and vol_24 > 45e9:
    report += f"  📊 放量上涨，买盘积极\n"

if fng_now <= 30:
    report += f"  🧠 恐惧 {fng_now}，接近极恐，关注情绪底\n"
elif fng_prev - fng_now >= 5:
    report += f"  🧠 情绪恶化 ({fng_prev}→{fng_now})，防恐慌\n"

report += f"""
━━━━━━━━━━━━━━━━━━━━
💡 <b>操作建议</b>

{trading_advice()}
━━━━━━━━━━━━━━━━━━━━"""

send_telegram(report, html=True)
print(f"REPORT SENT: {bj_h:02d}:{bj_m:02d}")
