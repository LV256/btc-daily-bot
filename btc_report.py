#!/usr/bin/env python3
"""BTC 行情监控 — 每2h详细报告 + 暴跌3%即时告警"""
import json, os, sys, urllib.request
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TZ = timezone(timedelta(hours=8))

def fetch(url, timeout=15):
    return json.load(urllib.request.urlopen(url, timeout=timeout))

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}).encode()
    urllib.request.urlopen(urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}))

# ── 数据 ──────────────────────────────────────────────
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

ranges = {}
for label, days in [("24H", 1), ("7D", 7), ("30D", 30), ("90D", 90)]:
    c = fetch(f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days={days}")
    prices = [p[1] for p in c["prices"]]
    ranges[label] = {"high": max(prices), "low": min(prices),
                     "chg": (curr - prices[0]) / prices[0] * 100,
                     "amp": (max(prices) - min(prices)) / min(prices) * 100}

fng = fetch("https://api.alternative.me/fng/?limit=3")
fng_now = int(fng["data"][0]["value"])
fng_cls = fng["data"][0]["value_classification"]
fng_prev = int(fng["data"][1]["value"])

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

# ── 暴跌检测 (1h变化超过-3%) ──────────────────────────
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
    send_telegram(alert)
    print("ALERT SENT: 3% drop detected")

# ── 每2小时详细报告 ──────────────────────────────────
# 北京时间偶数整点触发 (0,2,4,6,8,10,12,14,16,18,20,22)
# GitHub cron: */120 分钟 近似2h，靠脚本判断
now_utc = datetime.now(timezone.utc)
beijing_hour = (now_utc.hour + 8) % 24
beijing_min = now_utc.minute

# 仅在整点±3分钟内推送（cron 5分钟一次，这个窗口正好命中一次）
if beijing_min > 3:
    print(f"SKIP: {beijing_hour:02d}:{beijing_min:02d} 非整点窗口，跳过详细报告")
    sys.exit(0)

# ── 风险评估矩阵 ──────────────────────────────────────
risks = []
# 1. 价格结构
if curr > fibs["0.786"]:
    risks.append(("🟢", f"价格结构健康，高于 0.786 Fib (${fibs['0.786']:,.0f})。趋势偏多"))
elif curr > fibs["0.618"]:
    risks.append(("🟡", f"价格在 0.618-0.786 之间，中性偏弱。若跌破 ${fibs['0.618']:,.0f} 结构恶化"))
elif curr > fibs["0.5"]:
    risks.append(("🟠", f"价格跌破 0.618，结构走弱。${fibs['0.5']:,.0f} (0.5) 是最后的防线"))
else:
    risks.append(("🔴", f"价格跌破 0.5 Fib，结构严重受损。需要重新评估底部"))

# 2. 短期动量
if chg_24h > 3:
    risks.append(("🟢", f"24h 涨幅 {chg_24h:.1f}%，短期动能强"))
elif chg_24h > 0:
    risks.append(("🟢", f"24h 微涨 {chg_24h:.1f}%，方向中性偏多"))
elif chg_24h > -3:
    risks.append(("🟡", f"24h 下跌 {chg_24h:.1f}%，正常回调范围"))
else:
    risks.append(("🟠", f"24h 跌超 {chg_24h:.1f}%，短期空头占优，需警惕"))

# 3. 成交量
vol_avg_7d = ranges["7D"]["amp"]  # approximate
if vol_24 > 50e9:
    risks.append(("🟡", f"放量 ${vol_24/1e9:.1f}B，若下跌则为出货信号"))
elif vol_24 > 25e9:
    risks.append(("🟢", f"成交量正常 ${vol_24/1e9:.1f}B"))
else:
    risks.append(("🟢", f"缩量 ${vol_24/1e9:.1f}B，抛压不大"))

# 4. 情绪
if fng_now <= 25:
    risks.append(("🟢", f"极度恐惧 ({fng_now}) — 历史上是买点区域，但不等于马上见底"))
elif fng_now <= 40:
    risks.append(("🟡", f"恐惧区间 ({fng_now}) — 市场谨慎，反弹持续性存疑"))
elif fng_now <= 60:
    risks.append(("🟢", f"中性 ({fng_now}) — 情绪正常"))
else:
    risks.append(("🟡", f"贪婪 ({fng_now}) — 注意过热"))
# 趋势变化
if fng_now < fng_prev - 10:
    risks.append(("🟠", f"情绪恶化: {fng_prev}→{fng_now}，短期内谨慎"))

# 5. BTC 市占率
if btc_dom > 58:
    risks.append(("🟡", f"BTC 市占率 {btc_dom:.1f}% 偏高，山寨币失血，市场风险偏好低"))

# ── 操作建议 ──────────────────────────────────────────
def trading_advice():
    lines = []
    dd_ath = (ath - curr) / ath * 100

    # 持仓建议
    if curr > fibs["0.786"]:
        lines.append(f"📌 持仓: 结构健康，继续持有。止损上移至 ${fibs['0.786']:,.0f}")
    elif curr > fibs["0.618"]:
        lines.append(f"📌 持仓: 中性偏弱，仓位不超过 50%。止损 ${fibs['0.618']:,.0f}")
    elif curr > fibs["0.5"]:
        lines.append(f"📌 持仓: 结构走弱，仓位控制在 30%。止损 ${fibs['0.5']:,.0f}")
    else:
        lines.append(f"📌 持仓: 严重走弱，观望或轻仓。${fibs['0.0']:,.0f} 抄底区")

    # 加仓条件
    if curr < fibs["0.618"]:
        lines.append(f"💰 加仓: ${fibs['0.5']:,.0f} 和 ${fibs['0.0']:,.0f} 分批挂单")
    elif curr < fibs["0.786"]:
        lines.append(f"💰 加仓: ${fibs['0.618']:,.0f} 挂单，不要追高")

    # 减仓/止损
    lines.append(f"🛑 减仓: 日线收盘跌破 ${fibs['0.618']:,.0f} → 减仓 30%")
    lines.append(f"🛑 止损: 日线收盘跌破 ${fibs['0.5']:,.0f} → 减仓 50%")

    # 突破追入
    lines.append(f"🚀 追入: 放量突破 ${r90['high']:,.0f} (90日高) → 趋势确认，可加仓")

    # 定投者提示
    lines.append(f"📅 定投: 当前距 ATH 回撤 {dd_ath:.0f}%，属于合理定投区间")

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
  成交:  ${vol_24/1e9:.1f}B
  市值:  ${mcap/1e12:.2f}T / 总 ${tot_mcap/1e12:.2f}T
  BTC 市占: {btc_dom:.1f}%
  恐惧贪婪: {fng_now} ({fng_cls})  {'↑' if fng_now > fng_prev else '↓'}
  距 ATH:  -{(ath - curr) / ath * 100:.1f}%

━━━━━━━━━━━━━━━━━━━━
⚠️ <b>风险预警</b>
"""

for icon, desc in risks[:5]:
    report += f"  {icon} {desc}\n"

report += f"""
━━━━━━━━━━━━━━━━━━━━
🎯 <b>重点关注</b>
"""

# 关键位
report += f"  🔼 阻力: ${r90['high']:,.0f} (90日高 / 多空分界)\n"
report += f"      突破 → 趋势转强，目标 ${ath * 0.7:,.0f} → ${ath:,.0f}\n"
report += f"  🔽 支撑: ${fibs['0.786']:,.0f} (0.786) → ${fibs['0.618']:,.0f} (0.618)\n"

# 量价关系
if chg_24h < 0 and vol_24 < 35e9:
    report += f"  📊 缩量下跌，空头力度有限\n"
elif chg_24h < 0 and vol_24 > 45e9:
    report += f"  📊 ⚠️ 放量下跌，出货信号\n"
elif chg_24h > 0 and vol_24 > 45e9:
    report += f"  📊 放量上涨，买盘积极\n"

# 情绪
if fng_now <= 30:
    report += f"  🧠 恐惧指数 {fng_now}，接近极端恐惧，关注是否出现情绪底\n"
elif fng_prev - fng_now >= 5:
    report += f"  🧠 情绪恶化中 ({fng_prev}→{fng_now})，警惕恐慌蔓延\n"

report += f"""
━━━━━━━━━━━━━━━━━━━━
💡 <b>操作建议</b>

{trading_advice()}
━━━━━━━━━━━━━━━━━━━━"""

send_telegram(report)
print(f"REPORT SENT: {beijing_hour:02d}:{beijing_min:02d}")
