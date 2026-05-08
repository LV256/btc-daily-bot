#!/usr/bin/env python3
"""BTC 每日行情报告 → Telegram 推送"""
import json, os, sys, urllib.request
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

# ── 数据采集 ──────────────────────────────────────────
def fetch(url, timeout=15):
    return json.load(urllib.request.urlopen(url, timeout=timeout))

def get_btc_data():
    """抓取所有需要的 BTC 数据"""
    # 当前价格 + 24h
    coin = fetch("https://api.coingecko.com/api/v3/coins/bitcoin?localization=false&tickers=false&community_data=false&developer_data=false&market_data=true")
    md = coin["market_data"]
    curr = md["current_price"]["usd"]
    high24 = md["high_24h"]["usd"]
    low24 = md["low_24h"]["usd"]
    chg24 = md["price_change_percentage_24h"]
    vol24 = md["total_volume"]["usd"]
    mcap = md["market_cap"]["usd"]
    ath = md["ath"]["usd"]
    dd_ath = (ath - curr) / ath * 100

    # 7D / 30D / 90D
    ranges = {}
    for label, days in [("7D", 7), ("30D", 30), ("90D", 90)]:
        chart = fetch(f"https://api.coingecko.com/api/v3/coins/bitcoin/market_chart?vs_currency=usd&days={days}")
        prices = [p[1] for p in chart["prices"]]
        ranges[label] = {
            "high": max(prices),
            "low": min(prices),
            "chg": (curr - prices[0]) / prices[0] * 100,
            "amp": (max(prices) - min(prices)) / min(prices) * 100,
        }

    # 恐惧贪婪
    fng_data = fetch("https://api.alternative.me/fng/?limit=3")
    fng_now = int(fng_data["data"][0]["value"])
    fng_class = fng_data["data"][0]["value_classification"]
    fng_prev = int(fng_data["data"][1]["value"])

    # 市占率
    global_data = fetch("https://api.coingecko.com/api/v3/global")
    btc_dom = global_data["data"]["market_cap_percentage"]["btc"]
    total_mcap = global_data["data"]["total_market_cap"]["usd"]

    # 斐波那契
    r90 = ranges["90D"]
    diff = r90["high"] - r90["low"]
    fibs = {
        "1.0 (顶)": r90["high"],
        "0.786": r90["low"] + diff * 0.786,
        "0.618": r90["low"] + diff * 0.618,
        "0.5": r90["low"] + diff * 0.5,
        "0.382": r90["low"] + diff * 0.382,
        "0.0 (底)": r90["low"],
    }

    return {
        "curr": curr, "high24": high24, "low24": low24,
        "chg24": chg24, "vol24": vol24, "mcap": mcap,
        "ath": ath, "dd_ath": dd_ath,
        "ranges": ranges, "fng": fng_now, "fng_class": fng_class,
        "fng_prev": fng_prev, "btc_dom": btc_dom, "total_mcap": total_mcap,
        "fibs": fibs,
    }

# ── 风险判断 ──────────────────────────────────────────
def assess_risk(d):
    """基于数据给出风险等级"""
    risks = []
    curr = d["curr"]
    fibs = d["fibs"]

    # 斐波那契位置
    if curr < fibs["0.618"]:
        risks.append(("🔴", "价格低于 0.618 斐波那契位，结构偏弱"))
    elif curr < fibs["0.786"]:
        risks.append(("🟡", "价格在 0.618-0.786 之间，短线承压"))
    else:
        risks.append(("🟢", "价格高于 0.786，结构健康"))

    # 恐惧贪婪
    if d["fng"] <= 25:
        risks.append(("🟢", f"极度恐惧 ({d['fng']})，历史买点区域"))
    elif d["fng"] <= 40:
        risks.append(("🟡", f"恐惧区间 ({d['fng']})，谨慎偏弱"))
    else:
        risks.append(("🟢", f"情绪中性/贪婪 ({d['fng']})"))

    # 24h 涨跌
    if d["chg24"] < -5:
        risks.append(("🔴", f"24h 暴跌 {d['chg24']:.1f}%，注意风险"))
    elif d["chg24"] < -2:
        risks.append(("🟡", f"24h 下跌 {d['chg24']:.1f}%，正常回调"))

    return risks

# ── 关键价位 ──────────────────────────────────────────
def key_levels(d):
    curr = d["curr"]
    r7 = d["ranges"]["7D"]
    fibs = d["fibs"]

    support = [
        f"${fibs['0.786']:,.0f} (0.786 Fib)",
        f"${r7['low']:,.0f} (7日低点)",
        f"${fibs['0.618']:,.0f} (0.618 Fib)",
    ]
    resistance = [
        f"${r7['high']:,.0f} (7日高点 / 90日顶)",
        f"${d['ath'] * 0.7:,.0f} (0.7 ATH)",
        f"${d['ath']:,.0f} (ATH)",
    ]
    return support, resistance

# ── 生成报告 ──────────────────────────────────────────
def build_report(d):
    curr = d["curr"]
    r = d["ranges"]
    fibs = d["fibs"]
    risks = assess_risk(d)
    support, resistance = key_levels(d)

    # 情绪 emoji
    emoji = "📈" if d["chg24"] > 1 else "📉" if d["chg24"] < -1 else "➡️"

    tz = timezone(timedelta(hours=8))
    now = datetime.now(tz).strftime("%Y.%m.%d %H:%M")

    report = f"""📊 BTC 每日行情  {now}

{emoji} ${curr:,.0f}  (24h {d['chg24']:+.1f}%)

━━━━━━━━━━━━━━━━━━━━
📌 24小时
  最高:  ${d['high24']:,.0f}
  最低:  ${d['low24']:,.0f}
  成交:  ${d['vol24']/1e9:.1f}B
  市值:  ${d['mcap']/1e12:.2f}T

📌 区间表现
  7日:   ${r['7D']['low']:,.0f} — ${r['7D']['high']:,.0f}  ({r['7D']['chg']:+.1f}%)
  30日:  ${r['30D']['low']:,.0f} — ${r['30D']['high']:,.0f}  ({r['30D']['chg']:+.1f}%)
  90日:  ${r['90D']['low']:,.0f} — ${r['90D']['high']:,.0f}  ({r['90D']['chg']:+.1f}%)

📌 情绪
  恐惧贪婪:  {d['fng']} ({d['fng_class']})
  BTC 市占:  {d['btc_dom']:.1f}%
  距 ATH:    -{d['dd_ath']:.1f}%

━━━━━━━━━━━━━━━━━━━━
🎯 关键价位 (90日 Fib)
  阻力:  {resistance[0]}
          {resistance[1]}

  支撑:  {support[0]}
          {support[1]}
          {support[2]}

━━━━━━━━━━━━━━━━━━━━
⚠️ 风险评估
"""

    for icon, desc in risks:
        report += f"  {icon} {desc}\n"

    # 一句话
    report += f"""
━━━━━━━━━━━━━━━━━━━━
💡 操作建议
"""

    if curr > fibs["0.786"]:
        if d["chg24"] > 0:
            report += "  结构健康，多头趋势。$82,500 突破加仓，$78,300 止损。"
        else:
            report += "  仍在 0.786 上方但短线回调。$78,300 是本周关键防线。"
    elif curr > fibs["0.618"]:
        report += "  短期承压，$75,000-78,000 是定投区间。$75k 以下止损。"
    else:
        report += "  ⚠️ 跌破 0.618，结构走弱。建议减仓观望，不抄底。"

    return report

# ── 推送到 Telegram ───────────────────────────────────
def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    resp = json.load(urllib.request.urlopen(req))
    if not resp.get("ok"):
        print(f"Telegram error: {resp}", file=sys.stderr)
        sys.exit(1)
    print("OK: sent to Telegram")

# ── Main ──────────────────────────────────────────────
if __name__ == "__main__":
    try:
        data = get_btc_data()
        report = build_report(data)
        print(report)
        print("\n" + "=" * 50)
        send_telegram(report)
    except Exception as e:
        # Send error to Telegram too
        err_msg = f"❌ BTC 报告生成失败\n{type(e).__name__}: {e}"
        try:
            send_telegram(err_msg)
        except:
            pass
        print(err_msg, file=sys.stderr)
        sys.exit(1)
