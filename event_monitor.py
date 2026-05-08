#!/usr/bin/env python3
"""FOMC/CPI/非农 日历提醒 + 资产配置偏离告警"""
import json, os, sys, urllib.request, time
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TZ = timezone(timedelta(hours=8))

# ═══════════════════════════════════════════════════════
# 2026 年 FOMC / CPI / 非农 日历
# ═══════════════════════════════════════════════════════
EVENTS = {
    "2026-05-13": ("📊 CPI 消费者物价指数", "公布日"),
    "2026-05-15": ("📊 非农就业 NFP", "公布日"),  # 通常5月第一个周五调整
    "2026-06-05": ("📊 非农就业 NFP", "公布日"),
    "2026-06-10": ("📊 CPI 消费者物价指数", "公布日"),
    "2026-06-16": ("🏛 FOMC 美联储议息", "第一天"),
    "2026-06-17": ("🏛 FOMC 美联储议息", "决议日 ⚡"),
    "2026-07-03": ("📊 非农就业 NFP", "公布日"),
    "2026-07-14": ("📊 CPI 消费者物价指数", "公布日"),
    "2026-07-28": ("🏛 FOMC 美联储议息", "第一天"),
    "2026-07-29": ("🏛 FOMC 美联储议息", "决议日 ⚡"),
    "2026-08-07": ("📊 非农就业 NFP", "公布日"),
    "2026-08-12": ("📊 CPI 消费者物价指数", "公布日"),
    "2026-09-04": ("📊 非农就业 NFP", "公布日"),
    "2026-09-11": ("📊 CPI 消费者物价指数", "公布日"),
    "2026-09-15": ("🏛 FOMC 美联储议息", "第一天"),
    "2026-09-16": ("🏛 FOMC 美联储议息", "决议日 ⚡"),
    "2026-10-02": ("📊 非农就业 NFP", "公布日"),
    "2026-10-14": ("📊 CPI 消费者物价指数", "公布日"),
    "2026-11-03": ("🏛 FOMC 美联储议息", "第一天"),
    "2026-11-04": ("🏛 FOMC 美联储议息", "决议日 ⚡"),
    "2026-11-06": ("📊 非农就业 NFP", "公布日"),
    "2026-11-12": ("📊 CPI 消费者物价指数", "公布日"),
    "2026-12-04": ("📊 非农就业 NFP", "公布日"),
    "2026-12-10": ("📊 CPI 消费者物价指数", "公布日"),
    "2026-12-15": ("🏛 FOMC 美联储议息", "第一天"),
    "2026-12-16": ("🏛 FOMC 美联储议息", "决议日 ⚡"),
}

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    data = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}))
    except Exception as e:
        print(f"TG error: {e}", file=sys.stderr)

# ═══════════════════════════════════════════════════════
# 1. 日历提醒: 明天有事件
# ═══════════════════════════════════════════════════════
today = datetime.now(TZ).strftime("%Y-%m-%d")
tomorrow = (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
alerts = []

if tomorrow in EVENTS:
    name, stage = EVENTS[tomorrow]
    alerts.append(f"📅 明日事件提醒\n\n{name}\n{stage}\n"
                  f"日期: {tomorrow}\n\n⚠️ 市场可能出现大幅波动")

if today in EVENTS:
    name, stage = EVENTS[today]
    if "决议日" in stage:
        alerts.append(f"⚡ 今日 FOMC 决议日!\n\n{name} - {stage}\n"
                      f"凌晨2点(美东)公布利率决议\n关注BTC和美股波动")

for alert in alerts:
    send_telegram(f"{alert}\n\n—— Hermes · 事件日历")
    print(f"CALENDAR ALERT: {alert[:50]}")

# ═══════════════════════════════════════════════════════
# 2. 资产配置偏离检查 (标普 vs 纳指 30日表现差 >5%)
# ═══════════════════════════════════════════════════════
def fetch(url, timeout=10, retries=2):
    for i in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            return json.load(urllib.request.urlopen(req, timeout=timeout))
        except:
            if i == retries - 1: raise
            time.sleep(2)

def get_monthly_chg(symbol):
    try:
        url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=30d"
        d = fetch(url, timeout=10, retries=2)
        prices = [p for p in d["chart"]["result"][0]["indicators"]["quote"][0]["close"] if p is not None]
        if prices:
            return (prices[-1] - prices[0]) / prices[0] * 100
    except:
        pass
    return None

spx_chg = get_monthly_chg("^GSPC")
ndx_chg = get_monthly_chg("^IXIC")

if spx_chg and ndx_chg:
    diff = abs(spx_chg - ndx_chg)
    print(f"SPX 30d: {spx_chg:+.1f}%, NDX 30d: {ndx_chg:+.1f}%, diff: {diff:.1f}%")
    if diff > 5:
        target_spx = 0.60
        target_ndx = 0.40
        # 估算当前比例
        current_spx = 1 + spx_chg / 100
        current_ndx = 1 + ndx_chg / 100
        total = current_spx * 0.6 + current_ndx * 0.4
        actual_spx = current_spx * 0.6 / total * 100
        actual_ndx = current_ndx * 0.4 / total * 100
        
        msg = f"""⚖️ 资产配置偏离告警

  标普 30日: {spx_chg:+.1f}%
  纳指 30日: {ndx_chg:+.1f}%
  偏离度: {diff:.1f}%

  当前比例: 标普 {actual_spx:.0f}% / 纳指 {actual_ndx:.0f}%
  目标比例: 标普 {target_spx*100:.0f}% / 纳指 {target_ndx*100:.0f}%
  
  💡 下次定投时调整分配，卖强补弱

  —— Hermes · 资产配置"""
        send_telegram(msg)
        print("REBALANCE ALERT SENT")
else:
    print("SPX/NDX data unavailable for rebalance check")
