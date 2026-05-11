#!/usr/bin/env python3
"""BTC + 纳指定投 综合监控 — 每2h报告 + 暴跌告警"""
import json, os, sys, urllib.request, time, re
from datetime import datetime, timezone, timedelta

TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
TZ = timezone(timedelta(hours=8))
DCA_DAY = 21  # 每月21号定投

# ── 定投配置 ──────────────────────────────────────────
DCA_ALLOC = {"SPX": 0.60, "NDX": 0.40}  # 标普60% 纳指40%

def fetch(url, timeout=15, retries=3, headers=None):
    for i in range(retries):
        try:
            req = urllib.request.Request(url)
            if headers:
                for k, v in headers.items():
                    req.add_header(k, v)
            return json.load(urllib.request.urlopen(req, timeout=timeout))
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
fng_prev = int(fng["data"][1]["value"])

glb = fetch("https://api.coingecko.com/api/v3/global")
btc_dom = glb["data"]["market_cap_percentage"]["btc"]
tot_mcap = glb["data"]["total_market_cap"]["usd"]

r90 = ranges["90D"]; d = r90["high"] - r90["low"]
fibs = {"1.0": r90["high"], "0.786": r90["low"] + d * 0.786, "0.618": r90["low"] + d * 0.618,
        "0.5": r90["low"] + d * 0.5, "0.382": r90["low"] + d * 0.382, "0.0": r90["low"]}

# ═══════════════════════════════════════════════════════
# 1.5 ETF 资金流 (Farside via Jina AI)
# ═══════════════════════════════════════════════════════
def fetch_etf_flow():
    """抓取 Farside BTC ETF 资金流，返回最近5天 + 汇总"""
    try:
        url = "https://r.jina.ai/https://farside.co.uk/btc/"
        req = urllib.request.Request(url, headers={"Accept": "text/markdown", "User-Agent": "Mozilla/5.0"})
        md = urllib.request.urlopen(req, timeout=20).read().decode()
    except Exception as e:
        print(f"ETF fetch error: {e}", file=sys.stderr)
        return None

    # 解析 Markdown 表格: | 20 Apr 2026 | 256.0 | (6.6) | ... | 238.4 |
    rows = []
    in_table = False
    for line in md.split("\n"):
        line = line.strip()
        if "| Total |" in line and "IBIT" not in line:
            in_table = False
            continue
        if "IBIT | FBTC | BITB" in line:
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("|"):
            continue
        cols = [c.strip() for c in line.split("|")]
        # 格式: 空 | 日期 | IBIT | FBTC | ... | Total | 空
        if len(cols) < 3:
            continue
        date_cell = cols[1]
        # 匹配日期格式
        m = re.match(r'(\d{1,2} \w{3} \d{4})', date_cell)
        if not m:
            continue
        date_str = m.group(1)
        # 最后一列为 Total
        total_cell = cols[-2]  # 倒数第二列（最后一列为空）
        # Farside 用 () 表示负数: (268.5) → -268.5
        total_cell = total_cell.replace("(", "-").replace(")", "")
        try:
            total = float(total_cell.replace(",", ""))
        except ValueError:
            total = 0.0  # 当天数据未出（"-"）

        # 各基金明细
        funds = {}
        fund_names = ["IBIT", "FBTC", "BITB", "ARKB", "BTCO", "EZBC", "BRRR", "HODL", "BTCW", "MSBT", "GBTC", "BTC"]
        for i, name in enumerate(fund_names):
            try:
                val = float(cols[i + 2].replace(",", ""))
            except (ValueError, IndexError):
                val = 0.0
            funds[name] = val

        rows.append({"date": date_str, "total": total, "funds": funds})

    if not rows:
        return None

    # 最近5天
    recent = rows[-5:] if len(rows) >= 5 else rows

    # 5日累计
    total_5d = sum(r["total"] for r in recent)

    # 连续流入/流出天数
    streak = 0
    for r in reversed(rows):
        if r["total"] > 0:
            if streak <= 0:
                streak = 1 if streak == 0 else streak
            else:
                streak += 1
        elif r["total"] < 0:
            if streak >= 0:
                streak = -1 if streak == 0 else streak
            else:
                streak -= 1
        if r["total"] == 0:
            continue
        if abs(streak) > 1:
            # 检查是否真正连续
            pass

    # 重新计算连续方向
    direction = ""
    inflow_days = 0
    outflow_days = 0
    for r in rows[-10:]:
        if r["total"] > 0:
            inflow_days += 1
            outflow_days = 0
        elif r["total"] < 0:
            outflow_days += 1
            inflow_days = 0
    if outflow_days >= 2:
        direction = f"连续{outflow_days}日流出⚠️"
    elif inflow_days >= 3:
        direction = f"连续{inflow_days}日流入✅"
    else:
        direction = "方向不定"

    return {
        "recent": recent,
        "total_5d": total_5d,
        "direction": direction,
        "latest": rows[-1] if rows else None,
    }

etf = fetch_etf_flow()

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
# ── 自愈：检测调度空窗 ─────────────────────────────────
STATE_FILE = os.path.join(os.path.dirname(__file__) or ".", "report_state.json")
last_ts = 0
try:
    with open(STATE_FILE) as f:
        s = json.load(f)
        last_ts = s.get("last_report_ts", 0)
except Exception:
    pass

# ═══════════════════════════════════════════════════════
# 2.5 支撑/阻力位逼近告警 (距关键 Fib <3%)
# ═══════════════════════════════════════════════════════
FIB_THRESHOLD = 0.03  # 3% 触发告警
FIB_RESET = 0.05      # 价格偏离 >5% 才允许重新告警同一 level

fib_alert_state = s.get("fib_alerts", {}) if 's' in dir() and isinstance(s, dict) else {}

fib_hit = []
for lvl_name, lvl_price in [("1.0", fibs["1.0"]), ("0.786", fibs["0.786"]),
                              ("0.618", fibs["0.618"]), ("0.5", fibs["0.5"])]:
    dist_pct = abs(btc - lvl_price) / lvl_price
    if dist_pct < FIB_THRESHOLD:
        direction = "阻力" if btc < lvl_price else "支撑"
        last_price = fib_alert_state.get(lvl_name)
        # 去重：同一 level 且价格未明显变动 → 跳过
        if last_price and abs(btc - last_price) / last_price < FIB_RESET:
            continue
        fib_hit.append((lvl_name, lvl_price, dist_pct, direction))
        fib_alert_state[lvl_name] = btc

if fib_hit:
    lines = ["⚡ BTC 关键位逼近\n"]
    for lvl_name, lvl_price, dist_pct, direction in fib_hit:
        emo = "🔺 阻力" if direction == "阻力" else "🔻 支撑"
        lines.append(f"{emo}: ${lvl_price:,.0f} (Fib {lvl_name})  距 {dist_pct*100:.1f}%")
    lines.append(f"\n当前: ${btc:,.0f}  24h {btc_24h:+.1f}%  恐惧: {fng_now}")
    lines.append(f"\n—— Hermes · BTC Monitor")
    send_telegram("\n".join(lines))
    print(f"FIB ALERT SENT: {len(fib_hit)} levels")

    # 更新 state（后续会统一保存）
    # fib_alert_state 会被嵌入到 state dict 中一起写入
    if 's' not in dir() or not isinstance(s, dict):
        s = {}
    s['fib_alerts'] = fib_alert_state

gap_min = (time.time() - last_ts) / 60 if last_ts else 999
heal = gap_min > 25  # 超过25分钟没推 → 调度空窗 → 自愈补推

if not force and not heal and (bj_h % 2 != 0 or bj_m > 3):
    print(f"SKIP: {bj_h:02d}:{bj_m:02d}")
    sys.exit(0)
if heal:
    print(f"HEAL: 上次推送 {gap_min:.0f} 分钟前，自愈补推")

# ═══════════════════════════════════════════════════════
# 4. 纳指/标普 + VIX 数据
# ═══════════════════════════════════════════════════════
def stock_quote(symbol):
    """获取股票报价，优先 Yahoo，失败则用其他源"""
    # Yahoo Finance v8
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=5d"
    try:
        d = fetch(url, timeout=10, retries=2, headers={"User-Agent": "Mozilla/5.0"})
        q = d["chart"]["result"][0]
        meta = q["meta"]
        close = q["indicators"]["quote"][0]["close"]
        prices = [p for p in close if p is not None]
        # 从历史价格计算涨跌（比 previousClose 更可靠，对指数有效）
        if prices and len(prices) >= 2 and prices[-2] and prices[-2] > 0:
            prev_close = prices[-2]
            chg_pct = (meta["regularMarketPrice"] - prev_close) / prev_close * 100
        else:
            prev = meta.get("previousClose")
            if not prev:
                prev = meta.get("regularMarketPreviousClose")
            if not prev:
                prev = meta.get("regularMarketPrice")
            chg_pct = (meta["regularMarketPrice"] - prev) / prev * 100 if prev and prev > 0 else 0
        return {
            "price": meta["regularMarketPrice"],
            "prev_close": prev_close if prices and len(prices) >= 2 else (meta.get("previousClose") or meta.get("regularMarketPrice")),
            "high": meta.get("regularMarketDayHigh"),
            "low": meta.get("regularMarketDayLow"),
            "chg_pct": chg_pct,
            "prices_5d": prices,
        }
    except Exception as e:
        print(f"Yahoo {symbol}: {e}", file=sys.stderr)

    # 备用: Alphavantage demo key
    try:
        av_symbol = {"^GSPC": "SPY", "^IXIC": "QQQ", "^VIX": "VIXY"}.get(symbol, symbol)
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={av_symbol}&apikey=demo"
        d = fetch(url, timeout=10, retries=1)
        q = d.get("Global Quote", {})
        if q:
            price = float(q.get("05. price", 0))
            prev = float(q.get("08. previous close", price))
            change = float(q.get("09. change", 0))
            chg_pct = float(q.get("10. change percent", "0%").replace("%", ""))
            return {
                "price": price, "prev_close": prev,
                "high": float(q.get("03. high", 0)),
                "low": float(q.get("04. low", 0)),
                "chg_pct": chg_pct,
                "prices_5d": [price],
            }
    except Exception as e2:
        print(f"AlphaVantage {symbol}: {e2}", file=sys.stderr)

    return None

def stock_range(symbol, days=30):
    """获取历史数据"""
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={days}d"
    try:
        d = fetch(url, timeout=10, retries=2, headers={"User-Agent": "Mozilla/5.0"})
        q = d["chart"]["result"][0]
        close = q["indicators"]["quote"][0]["close"]
        prices = [p for p in close if p is not None]
        if prices:
            return {"high": max(prices), "low": min(prices), "chg": (prices[-1] - prices[0]) / prices[0] * 100}
    except:
        pass
    return None

spx = stock_quote("^GSPC")
ndx = stock_quote("^IXIC")
vix = stock_quote("^VIX")

spx_m = stock_range("^GSPC", 30)
ndx_m = stock_range("^IXIC", 30)

print(f"SPX: {spx['price'] if spx else 'N/A'}, NDX: {ndx['price'] if ndx else 'N/A'}, VIX: {vix['price'] if vix else 'N/A'}")

# ═══════════════════════════════════════════════════════
# 熔断预警: VIX飙升 + 美债暴跌 + 黄金流动性危机
# ═══════════════════════════════════════════════════════
tnx = stock_quote("^TNX")      # 10年期美债收益率
gold = stock_quote("GC=F")     # 黄金期货

macro_state = s.get("macro_alerts", {}) if 's' in dir() and isinstance(s, dict) else {}

alerts_macro = []

# ── VIX 突破阈值 ──
if vix:
    v = vix["price"]
    for th in [30, 40, 50, 60]:
        if v > th:
            last = macro_state.get(f"vix_{th}")
            if not last or abs(v - last) / last > 0.12:
                alerts_macro.append(f"😱 VIX 突破 {th}：{v:.1f}")
                macro_state[f"vix_{th}"] = v

    # VIX 单日跳涨 >30%
    chg = vix.get("chg_pct", 0)
    if chg > 30:
        last = macro_state.get("vix_surge")
        if not last or abs(v - last) / last > 0.20:
            alerts_macro.append(f"⚡ VIX 单日暴涨 {chg:.0f}%：{v:.1f}")
            macro_state["vix_surge"] = v

# ── 美债收益率暴跌 ──
if tnx:
    yld = tnx["price"]
    chg = tnx.get("chg_pct", 0)
    if chg < -5:
        last = macro_state.get("tnx_drop")
        if not last or abs(yld - last) / last > 0.15:
            alerts_macro.append(f"📉 10Y 美债收益率暴跌 {chg:.1f}%：{yld:.3f}%")
            macro_state["tnx_drop"] = yld

# ── 黄金流动性危机 (VIX>25 且黄金跌 >2%) ──
if gold and vix:
    g_chg = gold.get("chg_pct", 0)
    if g_chg < -2 and vix["price"] > 25:
        last = macro_state.get("gold_crisis")
        gp = gold["price"]
        if not last or abs(gp - last) / last > 0.05:
            alerts_macro.append(
                f"⚠️ 流动性危机信号\\n"
                f"  VIX: {vix['price']:.1f}  黄金: {g_chg:+.1f}%\\n"
                f"  恐慌中抛售黄金 → 全市场筹现金"
            )
            macro_state["gold_crisis"] = gp

# ── 发送 ──
if alerts_macro:
    lines = ["🚨 熔断预警\n"]
    lines.extend(alerts_macro)
    if spx:
        lines.append(f"\n标普: ${spx['price']:,.0f} ({spx['chg_pct']:+.1f}%)")
    if ndx:
        lines.append(f"纳指: ${ndx['price']:,.0f} ({ndx['chg_pct']:+.1f}%)")
    lines.append(f"\n—— Hermes · 熔断预警")
    send_telegram("\n".join(lines))
    print(f"MACRO ALERT SENT: {len(alerts_macro)} signals")

# 保存到 state dict
if 's' in dir() and isinstance(s, dict):
    s["macro_alerts"] = macro_state

# PE 获取 (Yahoo Finance ETF PE)
def get_etf_pe(symbol):
    """通过 Yahoo Finance quoteSummary 获取 ETF 的 trailingPE"""
    try:
        url = f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=defaultKeyStatistics"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        d = json.load(urllib.request.urlopen(req, timeout=10))
        stats = d["quoteSummary"]["result"][0]["defaultKeyStatistics"]
        pe = stats.get("trailingPE") or stats.get("forwardPE")
        if pe and pe.get("raw"):
            return pe["raw"]
    except Exception as e:
        print(f"Yahoo PE {symbol}: {e}", file=sys.stderr)

    # fallback: 用 multpl.com (已被 JS 渲染，大概率失败)
    try:
        alt = {"SPY": "s-p-500-pe-ratio", "QQQ": "nasdaq-100-pe-ratio"}.get(symbol)
        if alt:
            url = f"https://www.multpl.com/{alt}/table/by-month"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            html = urllib.request.urlopen(req, timeout=10).read().decode()
            m = re.search(r'<td[^>]*>(\\d+\\.\\d+)</td>', html)
            if m:
                return float(m.group(1))
            print(f"multpl {symbol}: no match")
    except Exception as e:
        print(f"multpl {symbol}: {e}")

    # 最终 fallback (标普 PE ~27.5, 纳指通常高15-20%)
    if symbol == "SPY":
        return 27.5
    elif symbol == "QQQ":
        return 32.0
    return None

spx_pe = get_etf_pe("SPY")
ndx_pe = get_etf_pe("QQQ")
print(f"PE: SPX={spx_pe} NDX={ndx_pe}")

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

    # ═══════ 定投金额 ═══════
    pe_spx = spx_pe or 27.5
    pe_ndx = ndx_pe or 32.0

    # 标普 PE 决定定投总额
    if pe_spx < 20:
        dca_amount = 6000; pe_level = "低估"
    elif pe_spx < 28:
        dca_amount = 3000; pe_level = "正常"
    elif pe_spx < 33:
        dca_amount = 3000; pe_level = "偏高"
    else:
        dca_amount = 1500; pe_level = "极端高估"

    spx_part = int(dca_amount * 0.6)
    ndx_part = int(dca_amount * 0.4)

    # 定投日
    if is_dca_day:
        lines.append(f"🔔 今天是定投日！")
    else:
        lines.append(f"📍 距下次定投 {days_to_dca} 天 ({date_str})")

    lines.append(f"")
    lines.append(f"💰 定投金额 (月投 ¥{dca_amount:,})")
    lines.append(f"  标普500 (60%): ¥{spx_part:,}  |  PE {pe_spx:.1f} ({pe_level})")
    lines.append(f"  纳指100 (40%): ¥{ndx_part:,}  |  PE {pe_ndx:.1f} {'偏高' if pe_ndx > 30 else '正常' if pe_ndx > 25 else '偏低'})")

    # ═══════ 仓位建议 ═══════
    if vix and vix["price"]:
        v = vix["price"]
        if v > 50:
            position = "30% 仓位"
            reason = "VIX 极高，仅留底仓"
        elif v > 40:
            position = "50% 仓位"
            reason = "VIX 恐慌，已减仓避险"
        elif v > 30:
            position = "70% 仓位"
            reason = "VIX 偏高，适度降仓"
        else:
            position = "100% 仓位"
            reason = "VIX 正常，满仓运行"

        if gold and gold.get("chg_pct", 0) < -2 and v > 25:
            position = "30% 仓位"; reason += " ⚠️ 流动性危机信号"

        lines.append(f"")
        lines.append(f"📊 仓位建议: {position}")
        lines.append(f"   {reason}")
    else:
        lines.append(f"")
        lines.append(f"📊 仓位建议: 100% (VIX 正常)")

    # ═══════ 止盈 ═══════
    if pe_spx > 40:
        lines.append(f"")
        lines.append(f"🎯 止盈: PE={pe:.1f} 极端 → 卖出总仓位 20%, 锁定利润")
    elif pe > 35:
        lines.append(f"")
        lines.append(f"🎯 止盈: PE={pe:.1f} 过高 → 卖出总仓位 10%")

    # ═══════ 加仓信号 ═══════
    bonus = 0
    if spx_m and spx_m["chg"] < -10:
        bonus += 3000
        lines.append(f"")
        lines.append(f"📉 标普月跌{spx_m['chg']:.1f}% → 额外追加 ¥3,000")
    if ndx_m and ndx_m["chg"] < -10:
        bonus += 3000
        lines.append(f"📉 纳指月跌{ndx_m['chg']:.1f}% → 额外追加 ¥3,000")
    if vix and vix["price"] and vix["price"] > 25:
        if vix["price"] > 40:
            lines.append(f"⚡ VIX={vix['price']:.1f} 极度恐慌 → 是加仓好时机")

    if bonus > 0:
        lines.append(f"   本月额外加仓合计: ¥{bonus:,}")

    # ═══════ 定投日当天总结 ═══════
    if is_dca_day:
        total = dca_amount + bonus
        lines.append(f"")
        lines.append(f"📌 今日操作汇总:")
        lines.append(f"   定投: ¥{dca_amount:,} | 额外: ¥{bonus:,} | 合计: ¥{total:,}")
        lines.append(f"   买入: 标普 ¥{int(total*0.6):,} + 纳指 ¥{int(total*0.4):,}")

    return "\n".join(lines) if lines else "📊 无特殊信号，正常定投即可"

# ═══════════════════════════════════════════════════════
# 6. BTC 风险评估 (完整版)
# ═══════════════════════════════════════════════════════
btc_risks = []
if btc > fibs["0.786"]:
    btc_risks.append(("🟢", f"价格结构健康，高于 0.786 Fib (${fibs['0.786']:,.0f})。趋势偏多"))
elif btc > fibs["0.618"]:
    btc_risks.append(("🟡", f"价格在 0.618-0.786 之间，中性偏弱。跌破 ${fibs['0.618']:,.0f} 结构恶化"))
elif btc > fibs["0.5"]:
    btc_risks.append(("🟠", f"价格跌破 0.618，结构走弱。${fibs['0.5']:,.0f} 是最后防线"))
else:
    btc_risks.append(("🔴", f"价格跌破 0.5 Fib，结构严重受损"))

if btc_24h > 3:
    btc_risks.append(("🟢", f"24h 涨幅 {btc_24h:.1f}%，短期动能强"))
elif btc_24h > 0:
    btc_risks.append(("🟢", f"24h 微涨 {btc_24h:.1f}%，中性偏多"))
elif btc_24h > -3:
    btc_risks.append(("🟡", f"24h 下跌 {btc_24h:.1f}%，正常回调"))
else:
    btc_risks.append(("🟠", f"24h 跌超 {btc_24h:.1f}%，空头占优"))

if btc_vol > 45e9:
    btc_risks.append(("🟡", f"放量 ${btc_vol/1e9:.1f}B，下跌是出货信号"))
elif btc_vol > 25e9:
    btc_risks.append(("🟢", f"成交量正常 ${btc_vol/1e9:.1f}B"))
else:
    btc_risks.append(("🟢", f"缩量 ${btc_vol/1e9:.1f}B，抛压有限"))

if fng_now <= 25:
    btc_risks.append(("🟢", f"极度恐惧 ({fng_now})，历史买点区域，但不等于马上见底"))
elif fng_now <= 40:
    btc_risks.append(("🟡", f"恐惧区间 ({fng_now})，市场谨慎"))
elif fng_now <= 60:
    btc_risks.append(("🟢", f"中性 ({fng_now})"))
else:
    btc_risks.append(("🟡", f"贪婪 ({fng_now})，注意过热"))
if fng_now < fng_prev - 10:
    btc_risks.append(("🟠", f"情绪恶化: {fng_prev}→{fng_now}"))

if btc_dom > 58:
    btc_risks.append(("🟡", f"BTC 市占率 {btc_dom:.1f}% 偏高，山寨失血"))

# BTC 操作建议
def btc_advice():
    lines = []
    dd_ath = (btc_ath - btc) / btc_ath * 100
    if btc > fibs["0.786"]:
        lines.append(f"📌 持仓: 结构健康。止损上移至 ${fibs['0.786']:,.0f}")
    elif btc > fibs["0.618"]:
        lines.append(f"📌 持仓: 中性偏弱，≤50% 仓位。止损 ${fibs['0.618']:,.0f}")
    elif btc > fibs["0.5"]:
        lines.append(f"📌 持仓: 走弱，≤30% 仓位。止损 ${fibs['0.5']:,.0f}")
    else:
        lines.append(f"📌 持仓: 观望。${fibs['0.0']:,.0f} 抄底区")
    if btc < fibs["0.618"]:
        lines.append(f"💰 加仓挂单: ${fibs['0.5']:,.0f} / ${fibs['0.0']:,.0f}")
    elif btc < fibs["0.786"]:
        lines.append(f"💰 加仓挂单: ${fibs['0.618']:,.0f}")
    lines.append(f"🛑 减仓: 日线收 < ${fibs['0.618']:,.0f} → -30%")
    lines.append(f"🛑 止损: 日线收 < ${fibs['0.5']:,.0f} → -50%")
    lines.append(f"🚀 突破: 放量过 ${r90['high']:,.0f} → 加仓")
    lines.append(f"📅 BTC定投: ATH回撤 {dd_ath:.0f}%，合理区间")
    return "\n".join(lines)

# ═══════════════════════════════════════════════════════
# 7. 生成综合报告
# ═══════════════════════════════════════════════════════
now = datetime.now(TZ).strftime("%m-%d %H:%M")
emoji = "📈" if btc_24h > 1 else "📉" if btc_24h < -1 else "➡️"

spx_str = f"${spx['price']:,.0f} ({spx['chg_pct']:+.1f}%)" if spx else "N/A"
ndx_str = f"${ndx['price']:,.0f} ({ndx['chg_pct']:+.1f}%)" if ndx else "N/A"

report = f"""📊 BTC 行情  {now}

{emoji} ${btc:,.0f}  1h {btc_1h:+.1f}%  24h {btc_24h:+.1f}%

━━━━━━━━━━━━━━━━━━━━
📌 价格区间
  24h:  ${btc_low24:,.0f} — ${btc_high24:,.0f}
  7日:  ${ranges['7D']['low']:,.0f} — ${ranges['7D']['high']:,.0f}  ({ranges['7D']['chg']:+.1f}%)
  30日: ${ranges['30D']['low']:,.0f} — ${ranges['30D']['high']:,.0f}  ({ranges['30D']['chg']:+.1f}%)
  90日: ${ranges['90D']['low']:,.0f} — ${ranges['90D']['high']:,.0f}  ({ranges['90D']['chg']:+.1f}%)

📌 市场数据
  成交量:    ${btc_vol/1e9:.1f}B
  BTC 市值:  ${btc_mcap/1e12:.2f}T / 总 ${tot_mcap/1e12:.2f}T
  BTC 市占:  {btc_dom:.1f}%
  恐惧贪婪:  {fng_now} ({fng_cls})  {'↑' if fng_now > fng_prev else '↓'}
  距 ATH:    -{(btc_ath - btc) / btc_ath * 100:.1f}%
"""

# ETF 资金流
if etf and etf.get("recent"):
    report += f"""
📌 BTC ETF 资金流 (US$M)
  状态:    {etf['direction']}
  5日累计: {etf['total_5d']:+.1f}M
"""
    for r in reversed(etf["recent"]):
        flag = "🔴" if r["total"] < -50 else "🟢" if r["total"] > 50 else "➖"
        report += f"  {flag} {r['date']:>6}: {r['total']:+.1f}M"
        # 显示流入/流出最大的基金
        funds = r.get("funds", {})
        if funds:
            inflows = [(k, v) for k, v in funds.items() if v > 10]
            outflows = [(k, v) for k, v in funds.items() if v < -10]
            if outflows:
                top_out = min(outflows, key=lambda x: x[1])
                report += f"  ({top_out[0]} {top_out[1]:.0f}M)"
            if inflows:
                top_in = max(inflows, key=lambda x: x[1])
                report += f"  ({top_in[0]} +{top_in[1]:.0f}M)"
        report += "\n"

report += f"""
━━━━━━━━━━━━━━━━━━━━
⚠️ 风险预警
"""
# ETF 风险
if etf and etf.get("recent"):
    recent = etf["recent"]
    # 连续方向
    out_streak = 0; in_streak = 0
    for r in reversed(recent):
        if r["total"] < 0: out_streak += 1; break
        elif r["total"] > 0: in_streak += 1; break
        else: break
    etf_risks = []
    if out_streak > 0:
        if etf["total_5d"] < -500:
            etf_risks.append(("🔴", "ETF 5日流出大幅，机构出逃"))
        elif etf["total_5d"] < -100:
            etf_risks.append(("🟠", f"ETF 流出 {etf['total_5d']:+.0f}M/5日，关注是否持续"))
        else:
            etf_risks.append(("🟡", "ETF 小幅流出，暂不恐慌"))
    elif in_streak > 0:
        if etf["total_5d"] > 1000:
            etf_risks.append(("🟢", f"ETF 5日大幅流入 {etf['total_5d']:+.0f}M，机构抢筹"))
        elif etf["total_5d"] > 200:
            etf_risks.append(("🟢", f"ETF 持续流入 {etf['total_5d']:+.0f}M/5日"))
        else:
            etf_risks.append(("🟢", "ETF 小幅净流入"))
    # 添加到风险列表
    for icon, desc in etf_risks:
        report += f"  {icon} ETF: {desc}\n"

report += "\n"
for icon, desc in btc_risks[:5]:
    report += f"  {icon} {desc}\n"

report += f"""
━━━━━━━━━━━━━━━━━━━━
🎯 重点关注
  🔼 阻力: ${r90['high']:,.0f} (90日高 / 多空分界)
      突破 → ${btc_ath * 0.7:,.0f} → ${btc_ath:,.0f}
  🔽 支撑: ${fibs['0.786']:,.0f} (0.786) → ${fibs['0.618']:,.0f} (0.618)
"""

if btc_24h < 0 and btc_vol < 35e9:
    report += f"  📊 缩量下跌，空头力度有限\n"
elif btc_24h < 0 and btc_vol > 45e9:
    report += f"  📊 ⚠️ 放量下跌，警惕出货\n"
elif btc_24h > 0 and btc_vol > 45e9:
    report += f"  📊 放量上涨，买盘积极\n"

if fng_now <= 30:
    report += f"  🧠 恐惧 {fng_now}，接近极恐，关注情绪底\n"
elif fng_prev - fng_now >= 5:
    report += f"  🧠 情绪恶化 ({fng_prev}→{fng_now})，防恐慌\n"

report += f"""
━━━━━━━━━━━━━━━━━━━━
💡 BTC 操作建议

{btc_advice()}

━━━━━━━━━━━━━━━━━━━━
📈 纳指定投  (标普60% / 纳指40%)

  定投日: 每月{DCA_DAY}号"""

if is_dca_day:
    report += f"\n  🔔 今天就是定投日！"
else:
    report += f"\n  距下次定投: {days_to_dca}天"

report += f"""

📌 指数行情
  标普500: {spx_str} | 30日 {spx_m['chg']:+.1f}%""" if spx_m else ""

if spx and spx.get("high") and spx.get("low"):
    report += f" | 今高 {spx['high']:,.0f} 今低 {spx['low']:,.0f}"

report += f"\n  纳斯达克: {ndx_str}"

if ndx_m:
    report += f" | 30日 {ndx_m['chg']:+.1f}%"

if ndx and ndx.get("high") and ndx.get("low"):
    report += f" | 今高 {ndx['high']:,.0f} 今低 {ndx['low']:,.0f}"

if vix and vix["price"]:
    report += f"\n  VIX: {vix['price']:.1f}"
if spx_pe:
    report += f"\n  标普PE: {spx_pe:.1f}"
if ndx_pe:
    report += f"  纳指PE: {ndx_pe:.1f}"

report += f"""

⚠️ 定投风险
"""

# DCA risk warnings
dca_risks = []
if spx_m and spx_m["chg"] < -5:
    dca_risks.append(f"🟠 标普月跌{spx_m['chg']:.1f}%，大幅回撤中")
elif spx_m and spx_m["chg"] < -2:
    dca_risks.append(f"🟡 标普月跌{spx_m['chg']:.1f}%，小幅回撤")
elif spx_m and spx_m["chg"] > 4:
    dca_risks.append(f"🟢 标普月涨{spx_m['chg']:.1f}%，趋势向好")
else:
    dca_risks.append(f"🟢 标普月{'涨' if spx_m and spx_m['chg'] > 0 else '跌'}{spx_m['chg'] if spx_m else 0:+.1f}%，正常波动")

if ndx_m and ndx_m["chg"] < -5:
    dca_risks.append(f"🟠 纳指月跌{ndx_m['chg']:.1f}%，科技股承压")
elif ndx_m and ndx_m["chg"] < -2:
    dca_risks.append(f"🟡 纳指月跌{ndx_m['chg']:.1f}%，小幅回撤")

if vix and vix["price"]:
    v = vix["price"]
    if v > 30:
        dca_risks.append(f"🔴 VIX={v:.1f} 极度恐慌，机会区间")
    elif v > 25:
        dca_risks.append(f"🟠 VIX={v:.1f} 偏高，恐慌中")
    elif v > 20:
        dca_risks.append(f"🟡 VIX={v:.1f} 略高")
    else:
        dca_risks.append(f"🟢 VIX={v:.1f} 平稳")

if spx_pe:
    pe = spx_pe
    if pe < 22:
        dca_risks.append(f"🟢 PE={pe:.1f} 低估")
    elif pe < 28:
        dca_risks.append(f"🟡 PE={pe:.1f} 合理偏高")
    elif pe < 33:
        dca_risks.append(f"🟠 PE={pe:.1f} 高估")
    else:
        dca_risks.append(f"🔴 PE={pe:.1f} 极端高估")

for r in dca_risks:
    report += f"  {r}\n"

report += f"""
💡 定投建议
  {dca_advice()}

━━━━━━━━━━━━━━━━━━━━
—— Hermes · 每日自动推送 ──"""

send_telegram(report)
print(f"REPORT SENT: {bj_h:02d}:{bj_m:02d}")
# 写入状态（fib_alerts + 自愈时间戳）
try:
    s['last_report_ts'] = time.time()
    s['last_report_time'] = datetime.now(TZ).isoformat()
    with open(STATE_FILE, "w") as f:
        json.dump(s, f)
except Exception:
    pass
