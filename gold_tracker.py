#!/usr/bin/env python3
"""金店黄金价格追踪器。

数据源:cngold.org 后台接口(网友报价,仅供参考)。
- update  首次运行抓取全量历史(从 config.start_date 起),之后每次运行只增量补新到今天。
- chart   读取已存数据,生成多店对比走势图 output/trend.html。
- 直接运行(无参数)= 先 update 再 chart。

无第三方依赖,仅用标准库 + 系统 curl。
"""
import argparse
import csv
import datetime as dt
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(ROOT, "data")
OUT_DIR = os.path.join(ROOT, "output")
API = "https://www.cngold.org/sgapp/price/gold"
# 交易所金价基准:金融界历史 K 线接口(上海金交所),取日收盘价 q2,单位元/克
JIJINHAO = "https://api.jijinhao.com/quoteCenter/history.htm"
JIJINHAO_REFERER = "https://quote.cngold.org/"
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TREND = {"1": "跌", "2": "涨", "3": "平"}


def load_config():
    with open(os.path.join(ROOT, "config.json"), encoding="utf-8") as f:
        return json.load(f)


def http_get(url, tries=4, referer=None):
    """用 curl 拉取(比 urllib 在该站更稳),带重试。"""
    last = None
    cmd = ["curl", "-s", "--max-time", "30", "-A", UA]
    if referer:
        cmd += ["-e", referer]
    for t in range(tries):
        r = subprocess.run(cmd + [url], capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout
        last = f"rc={r.returncode} err={r.stderr[:120]}"
        time.sleep(1.5 * (t + 1))
    raise RuntimeError(f"请求失败: {url}\n{last}")


def parse_jsonp(raw):
    """接口返回形如 `var json = {...};`,剥壳成对象。"""
    m = re.search(r"var\s+json\s*=\s*(.*?);?\s*$", raw, re.S)
    if not m:
        raise ValueError("无法解析返回内容: " + raw[:200])
    return json.loads(m.group(1).rstrip().rstrip(";"))


def resolve_product_id(brand_id, product_name):
    """各金店的 productId 不同,按品种名解析出该店对应的黄金 productId。"""
    url = f"{API}/varieties.do?brandId={brand_id}&variable=json"
    d = parse_jsonp(http_get(url))
    items = d.get("data", []) if isinstance(d, dict) else []
    for p in items:                       # 精确匹配
        if p["name"] == product_name:
            return p["id"], p["name"]
    for p in items:                       # 退而求其次:含"黄金"
        if "黄金" in p["name"]:
            return p["id"], p["name"]
    for p in items:                       # 再退:含"足金"
        if "足金" in p["name"]:
            return p["id"], p["name"]
    raise RuntimeError(f"品牌 {brand_id} 未找到黄金相关品种,可选: {[p['name'] for p in items]}")


def fetch_range(brand_id, product_id, start, end, page_size=200):
    """抓 [start, end] 区间某店某品种的全部日报价,返回 {date: row}。"""
    rows = {}
    url0 = (f"{API}/pageData.do?currentPage=0&pageSize={page_size}"
            f"&startTime={start}&endTime={end}&brandId={brand_id}"
            f"&productId={product_id}&variable=json")
    first = parse_jsonp(http_get(url0))[0]["data"][0]
    pages = int(first["totalPage"])

    def collect(infos):
        for r in infos:
            rows[r["updateTime"]] = {
                "date": r["updateTime"],
                "price": r["price"],
                "unit": r["priceUnit"],
                "trend": TREND.get(r.get("raiseDownType"), r.get("raiseDownType", "")),
            }

    collect(first["infos"])
    for p in range(1, pages):
        url = (f"{API}/pageData.do?currentPage={p}&pageSize={page_size}"
               f"&startTime={start}&endTime={end}&brandId={brand_id}"
               f"&productId={product_id}&variable=json")
        collect(parse_jsonp(http_get(url))[0]["data"][0]["infos"])
        time.sleep(0.3)
    return rows


def fetch_baseline(code, start, page_size=500, max_pages=30):
    """抓交易所金价日线(金融界接口),取收盘价 q2。翻页直到覆盖 start。"""
    rows = {}
    for page in range(1, max_pages + 1):
        url = f"{JIJINHAO}?code={code}&style=3&pageSize={page_size}&currentPage={page}"
        raw = http_get(url, referer=JIJINHAO_REFERER)
        m = re.search(r"quote_json\s*=\s*(\{.*\})\s*;?\s*$", raw, re.S)
        if not m:
            break
        d = json.loads(m.group(1))
        data = d.get("data") or []
        if not data:
            break
        unit = d.get("unit", "元/克")
        oldest = "9999"
        for r in data:
            date = dt.datetime.fromtimestamp(r["time"] / 1000).strftime("%Y-%m-%d")
            oldest = min(oldest, date)
            if date >= start:
                rows[date] = {"date": date, "price": str(r["q2"]), "unit": unit, "trend": ""}
        if oldest < start:            # 本页已翻过起始日,停
            break
        time.sleep(0.3)
    # 按收盘价前后比较补 trend(涨/跌/平)
    ordered = sorted(rows.values(), key=lambda x: x["date"])
    prev = None
    for r in ordered:
        cur = float(r["price"])
        if prev is not None:
            r["trend"] = "涨" if cur > prev else "跌" if cur < prev else "平"
        prev = cur
    return rows


def brand_csv(name):
    return os.path.join(DATA_DIR, f"{name}.csv")


def read_existing(name):
    path = brand_csv(name)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        return {r["date"]: r for r in csv.DictReader(f)}


def write_brand(name, rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    data = sorted(rows.values(), key=lambda x: x["date"])
    with open(brand_csv(name), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["date", "price", "unit", "trend"])
        w.writeheader()
        w.writerows(data)
    return data


def cmd_update(cfg):
    today = dt.date.today().isoformat()
    product_name = cfg["product_name"]
    print(f"更新到 {today} · 品种「{product_name}」· 共 {len(cfg['brands'])} 家金店\n")
    for b in cfg["brands"]:
        name, bid = b["name"], b["brandId"]
        want = b.get("product_name", product_name)  # 允许按店覆盖品种名
        try:
            pid, pname = resolve_product_id(bid, want)
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            continue
        existing = read_existing(name)
        # 增量:从已存最新日期起(重抓当天以吸收修正);无历史则从配置起始日
        start = max(existing) if existing else cfg["start_date"]
        try:
            fresh = fetch_range(bid, pid, start, today)
        except Exception as e:
            print(f"  ✗ {name}: 抓取失败 {e}")
            continue
        before = len(existing)
        existing.update(fresh)
        data = write_brand(name, existing)
        added = len(data) - before
        span = f"{data[0]['date']}~{data[-1]['date']}" if data else "空"
        print(f"  ✓ {name}(pid={pid},{pname}): 共 {len(data)} 天 [{span}] 本次新增/更新 {len(fresh)},净增 {added}")

    for b in cfg.get("baselines", []):
        name, code = b["name"], b["jijinhao_code"]
        existing = read_existing(name)
        start = max(existing) if existing else cfg["start_date"]
        try:
            fresh = fetch_baseline(code, start)
        except Exception as e:
            print(f"  ✗ {name}(基准): 抓取失败 {e}")
            continue
        before = len(existing)
        existing.update(fresh)
        data = write_brand(name, existing)
        added = len(data) - before
        span = f"{data[0]['date']}~{data[-1]['date']}" if data else "空"
        print(f"  ✓ {name}(基准,{code}): 共 {len(data)} 天 [{span}] 本次新增/更新 {len(fresh)},净增 {added}")

    print("\n数据目录:", DATA_DIR)


def cmd_chart(cfg):
    os.makedirs(OUT_DIR, exist_ok=True)
    baseline_names = {b["name"] for b in cfg.get("baselines", [])}
    entries = [(b["name"], False) for b in cfg["brands"]]
    entries += [(b["name"], True) for b in cfg.get("baselines", [])]

    series, all_dates, combined = [], set(), []
    for name, is_baseline in entries:
        rows = read_existing(name)
        if not rows:
            continue
        for d, r in rows.items():
            combined.append({"date": d, "brand": name, "price": r["price"],
                             "unit": r.get("unit", ""), "trend": r.get("trend", "")})
        series.append((name, {d: float(r["price"]) for d, r in rows.items()}, is_baseline))
        all_dates.update(rows)
    if not series:
        print("没有数据,请先运行:python gold_tracker.py update")
        return
    dates = sorted(all_dates)

    # 取第一条 baseline 作为交易所金价基准,用于算价差/溢价
    ref, ref_name = {}, None
    for name, dmap, is_baseline in series:
        if is_baseline:
            ref_name, ref = name, dmap
            break

    def premium(dmap, d):  # 店价 - 当日交易所金价
        return round(dmap[d] - ref[d], 2) if (ref and d in ref and d in dmap) else None

    # 每日金店(非基准)最高/最低价 -> {date: [高店, 高价, 低店, 低价]}
    minmax = {}
    for d in dates:
        vals = [(nm, dm[d]) for nm, dm, isb in series if not isb and d in dm]
        if vals:
            hi = max(vals, key=lambda x: x[1])
            lo = min(vals, key=lambda x: x[1])
            minmax[d] = [hi[0], hi[1], lo[0], lo[1]]

    # 合并长表,加 baseline(交易所金价)/ premium(价差)两列
    combined.sort(key=lambda x: (x["date"], x["brand"]))
    dmap_by_name = {nm: dm for nm, dm, _ in series}
    for row in combined:
        d, nm = row["date"], row["brand"]
        row["baseline"] = ref.get(d, "") if ref else ""
        prem = premium(dmap_by_name[nm], d)
        row["premium"] = "" if prem is None else prem
    with open(os.path.join(OUT_DIR, "all_brands.csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=["date", "brand", "price", "unit", "trend", "baseline", "premium"])
        w.writeheader()
        w.writerows(combined)

    def style(s, is_baseline):
        if is_baseline:  # 基准线:浅金加粗虚线,压在最上层(适配深色底)
            s["lineStyle"] = {"color": "#f0e6cf", "width": 2, "type": "dashed"}
            s["z"] = 10
            s["emphasis"] = {"focus": "series"}
        return s

    es, es_prem = [], []  # 价格模式 / 溢价模式(店价-基准)
    for name, dmap, is_baseline in series:
        es.append(style({"name": name, "type": "line", "showSymbol": False, "smooth": True,
                         "connectNulls": True, "data": [dmap.get(d, None) for d in dates]}, is_baseline))
        es_prem.append(style({"name": name, "type": "line", "showSymbol": False, "smooth": True,
                              "connectNulls": True, "data": [premium(dmap, d) for d in dates]}, is_baseline))

    chart_cfg = cfg.get("chart", {})
    default_visible = set(chart_cfg.get("default_visible", ["周大福"])) | baseline_names
    range_months = int(chart_cfg.get("default_range_months", 12))
    html = _chart_html(dates, es, es_prem, default_visible, range_months, minmax, ref_name)
    out = os.path.join(OUT_DIR, "trend.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"走势图: {out}")
    print(f"合并数据: {os.path.join(OUT_DIR, 'all_brands.csv')}  ({len(combined)} 行)")
    return out


def _chart_html(dates, series, series_prem, default_visible, range_months, minmax, ref_name):
    names = [s["name"] for s in series]
    selected = {n: (n in default_visible) for n in names}
    has_prem = bool(ref_name)
    # 默认时间窗:最后一天往前推 range_months 个月;0=全部
    if range_months and len(dates) > 1:
        last = dt.date.fromisoformat(dates[-1])
        y, m = last.year, last.month - range_months
        while m <= 0:
            m += 12
            y -= 1
        cutoff = f"{y:04d}-{m:02d}-{last.day:02d}"
        start_value = next((d for d in dates if d >= cutoff), dates[0])
    else:
        start_value = dates[0]

    palette = ["#e6b450", "#e07a5f", "#6cb6a6", "#7f9cd4", "#d98cae",
               "#b0b56a", "#63b0d6", "#c98f5a", "#a98cc8"]
    mode_bar = f"""    <div class="grp"><span class="seg-label">模式</span>
      <div class="seg" id="segMode">
        <button onclick="setMode('price',this)" class="on">价格</button>
        <button onclick="setMode('prem',this)">溢价 · 较{ref_name}</button>
      </div>
    </div>""" if has_prem else ""

    return f"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>黄金价格指数 · 金店 vs 交易所</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Manrope:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/echarts@5.4.3/dist/echarts.min.js"></script>
<style>
:root{{
  --gold:#d9a441; --gold-lt:#e8cd94; --ink:#f0ebdf; --muted:#9a8f7d; --faint:#6d6454;
  --panel-line:#2c261d;
  --mono:"IBM Plex Mono",ui-monospace,monospace; --ui:"Manrope",system-ui,sans-serif;
  --display:"Fraunces",Georgia,serif;
}}
*{{box-sizing:border-box}}
body{{margin:0;min-height:100vh;font-family:var(--ui);color:var(--ink);
  background:
    radial-gradient(1200px 620px at 12% -12%, rgba(217,164,65,.11), transparent 58%),
    radial-gradient(1000px 700px at 102% -6%, rgba(120,150,212,.06), transparent 55%),
    linear-gradient(180deg,#100e0b 0%,#0b0a08 100%);}}
body::before{{content:"";position:fixed;inset:0;pointer-events:none;z-index:0;opacity:.04;
  background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.8' numOctaves='2'/%3E%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)'/%3E%3C/svg%3E");}}
.wrap{{position:relative;z-index:1;max-width:1200px;margin:0 auto;padding:34px 22px 26px}}
.eyebrow{{font-family:var(--mono);font-size:11px;letter-spacing:.3em;text-transform:uppercase;
  color:var(--gold);opacity:.85;display:flex;align-items:center;gap:9px}}
.eyebrow::before{{content:"";width:22px;height:1px;background:linear-gradient(90deg,var(--gold),transparent)}}
h1{{font-family:var(--display);font-weight:600;font-size:clamp(26px,3.4vw,38px);line-height:1.05;
  margin:10px 0 6px;letter-spacing:.2px}}
h1 em{{font-style:italic;color:var(--gold-lt)}}
.meta{{font-family:var(--mono);font-size:12px;color:var(--muted);letter-spacing:.02em}}
.card{{position:relative;margin-top:22px;padding:16px 18px 8px;border:1px solid var(--panel-line);
  border-radius:18px;background:linear-gradient(180deg,rgba(27,23,18,.72),rgba(18,15,11,.72));
  box-shadow:0 24px 70px rgba(0,0,0,.5),inset 0 1px 0 rgba(255,255,255,.03);
  backdrop-filter:blur(4px)}}
.controls{{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:14px;
  padding:4px 2px 12px}}
.grp{{display:inline-flex;align-items:center;gap:9px}}
.seg-label{{font-family:var(--mono);font-size:10px;letter-spacing:.22em;text-transform:uppercase;color:var(--faint)}}
.seg{{display:inline-flex;background:rgba(0,0,0,.32);border:1px solid var(--panel-line);
  border-radius:12px;padding:3px}}
.seg button{{font-family:var(--ui);font-size:13px;font-weight:600;color:var(--muted);background:transparent;
  border:0;padding:7px 16px;border-radius:9px;cursor:pointer;transition:color .18s,background .18s,box-shadow .18s;
  letter-spacing:.01em;white-space:nowrap}}
.seg button:hover{{color:var(--ink)}}
.seg button.on{{background:linear-gradient(180deg,#e9bd57,#d59b34);color:#1c1608;
  box-shadow:0 2px 12px rgba(217,164,65,.3),inset 0 1px 0 rgba(255,255,255,.35)}}
.hint{{font-family:var(--mono);font-size:11px;color:var(--faint);padding:0 2px 6px;letter-spacing:.02em}}
.hint b{{color:var(--gold);font-weight:500}}
#c{{width:100%;height:64vh;min-height:430px}}
.note{{text-align:center;color:var(--faint);font-size:11.5px;font-family:var(--mono);
  padding:16px 0 0;letter-spacing:.02em}}
.tp{{font-family:var(--mono)}}
.tp .row{{display:flex;justify-content:space-between;gap:18px;line-height:1.7}}
.tp .hd{{color:var(--gold-lt);font-weight:500;margin-bottom:3px;border-bottom:1px solid rgba(217,164,65,.25);padding-bottom:3px}}
.tp .sep{{margin:5px 0 4px;border-top:1px dashed rgba(255,255,255,.14)}}
.tp .hi{{color:#f0a58c}} .tp .lo{{color:#8fc3e6}}
</style></head>
<body>
<div class="wrap">
  <div class="eyebrow">GOLD PRICE INDEX · 上海金交所基准</div>
  <h1>金店黄金 <em>vs</em> 交易所金价</h1>
  <div class="meta">元 / 克 · 日频 · {dates[0]} — {dates[-1]}</div>

  <div class="card">
    <div class="controls">
      <div class="grp"><span class="seg-label">范围</span>
        <div class="seg" id="segRange">
          <button onclick="setRange(1,this)">近1月</button>
          <button onclick="setRange(12,this)" class="on">近1年</button>
          <button onclick="setRange(0,this)">全部</button>
        </div>
      </div>
{mode_bar}
    </div>
    <div class="hint">默认显示 <b>周大福</b> + 交易所基准 · 点上方图例可开关其他金店 · 悬停查看当日全网最高 / 最低</div>
    <div id="c"></div>
  </div>
  <div class="note">数据来源:cngold.org 网友金店报价 + 金融界上海金交所行情 · 与门店实际有差异,仅供参考</div>
</div>
<script>
var DATES={json.dumps(dates)};
var SERIES_PRICE={json.dumps(series, ensure_ascii=False)};
var SERIES_PREM={json.dumps(series_prem, ensure_ascii=False)};
var MINMAX={json.dumps(minmax, ensure_ascii=False)};
var PALETTE={json.dumps(palette)};
var MODE='price';
var ch=echarts.init(document.getElementById('c'),null,{{renderer:'canvas'}});
function tip(ps){{
  if(!ps||!ps.length) return '';
  var d=ps[0].axisValue;
  var h='<div class="tp"><div class="hd">'+d+(MODE==='prem'?' · 溢价(元/克)':'')+'</div>';
  ps.forEach(function(p){{ if(p.data!=null)
    h+='<div class="row"><span>'+p.marker+' '+p.seriesName+'</span><b>'+p.data+'</b></div>'; }});
  var mm=MINMAX[d];
  if(mm) h+='<div class="sep"></div>'+
    '<div class="row hi"><span>▲ 当日最高 · '+mm[0]+'</span><b>'+mm[1]+'</b></div>'+
    '<div class="row lo"><span>▼ 当日最低 · '+mm[2]+'</span><b>'+mm[3]+'</b></div>';
  return h+'</div>';
}}
ch.setOption({{
 color:PALETTE,
 textStyle:{{fontFamily:'"IBM Plex Mono","Manrope",monospace',color:'#9a8f7d'}},
 tooltip:{{trigger:'axis',formatter:tip,
   backgroundColor:'rgba(16,14,11,.94)',borderColor:'rgba(217,164,65,.35)',borderWidth:1,
   padding:[9,11],textStyle:{{color:'#f0ebdf',fontSize:12}},
   axisPointer:{{type:'line',lineStyle:{{color:'rgba(217,164,65,.4)',type:'dashed'}}}},
   extraCssText:'backdrop-filter:blur(10px);border-radius:11px;box-shadow:0 12px 40px rgba(0,0,0,.55);'}},
 legend:{{data:{json.dumps(names, ensure_ascii=False)},selected:{json.dumps(selected, ensure_ascii=False)},
   top:6,type:'scroll',icon:'roundRect',itemWidth:16,itemHeight:3,itemGap:16,
   textStyle:{{color:'#b8ad99',fontFamily:'Manrope',fontSize:12}},inactiveColor:'#4a4335',
   pageIconColor:'#d9a441',pageIconInactiveColor:'#4a4335',pageTextStyle:{{color:'#9a8f7d'}}}},
 grid:{{left:56,right:26,top:52,bottom:62}},
 xAxis:{{type:'category',data:DATES,boundaryGap:false,
   axisLine:{{lineStyle:{{color:'#3a3327'}}}},axisTick:{{show:false}},
   axisLabel:{{color:'#8a8072',fontSize:11}},
   splitLine:{{show:false}}}},
 yAxis:{{type:'value',scale:true,name:'元/克',
   nameTextStyle:{{color:'#8a8072',fontSize:11,padding:[0,0,6,0]}},
   axisLabel:{{color:'#8a8072',fontSize:11}},axisLine:{{show:false}},axisTick:{{show:false}},
   splitLine:{{lineStyle:{{color:'rgba(240,230,207,.06)'}}}}}},
 dataZoom:[{{type:'inside',startValue:'{start_value}'}},
   {{type:'slider',bottom:14,height:18,startValue:'{start_value}',
     borderColor:'transparent',backgroundColor:'rgba(255,255,255,.03)',
     fillerColor:'rgba(217,164,65,.14)',
     handleStyle:{{color:'#d9a441',borderColor:'#d9a441'}},
     moveHandleStyle:{{color:'#d9a441'}},
     dataBackground:{{lineStyle:{{color:'#4a4335'}},areaStyle:{{color:'rgba(217,164,65,.07)'}}}},
     selectedDataBackground:{{lineStyle:{{color:'#d9a441'}},areaStyle:{{color:'rgba(217,164,65,.16)'}}}},
     textStyle:{{color:'#8a8072',fontFamily:'IBM Plex Mono',fontSize:10}}}}],
 series:SERIES_PRICE
}});
function setRange(months,btn){{
  document.querySelectorAll('#segRange button').forEach(function(b){{b.classList.remove('on')}});
  if(btn) btn.classList.add('on');
  var last=DATES[DATES.length-1], sv=DATES[0];
  if(months>0){{
    var p=last.split('-'), d=new Date(+p[0],+p[1]-1,+p[2]);
    d.setMonth(d.getMonth()-months);
    var cut=d.getFullYear()+'-'+('0'+(d.getMonth()+1)).slice(-2)+'-'+('0'+d.getDate()).slice(-2);
    for(var i=0;i<DATES.length;i++){{if(DATES[i]>=cut){{sv=DATES[i];break;}}}}
  }}
  ch.dispatchAction({{type:'dataZoom',startValue:sv,endValue:last}});
}}
function setMode(m,btn){{
  MODE=m;
  document.querySelectorAll('#segMode button').forEach(function(b){{b.classList.remove('on')}});
  if(btn) btn.classList.add('on');
  ch.setOption({{series: m==='price'?SERIES_PRICE:SERIES_PREM,
                 yAxis:{{name: m==='price'?'元/克':'溢价 元/克'}}}});
}}
window.addEventListener('resize',function(){{ch.resize()}});
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser(description="金店黄金价格追踪器")
    ap.add_argument("cmd", nargs="?", default="all",
                    choices=["update", "chart", "all"],
                    help="update=抓取/增量更新;chart=生成走势图;all=两者(默认)")
    args = ap.parse_args()
    cfg = load_config()
    if args.cmd in ("update", "all"):
        cmd_update(cfg)
    if args.cmd in ("chart", "all"):
        cmd_chart(cfg)


if __name__ == "__main__":
    sys.exit(main())
