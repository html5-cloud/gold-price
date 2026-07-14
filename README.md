# 金店黄金价格追踪器

记录国内主流金店(周大福、老凤祥、周生生、中国黄金等)的**每日黄金价格**,支持增量更新和多店对比走势图。数据从 **2020-01-01** 起。

## 用法

```bash
cd ~/projects/gold-price-tracker

python3 gold_tracker.py            # 默认:先增量更新数据,再生成走势图
python3 gold_tracker.py update     # 只抓数据(首次=全量回溯2020,之后=只补新到今天)
python3 gold_tracker.py chart      # 只用已存数据重新生成走势图
```

无需安装任何依赖(纯 Python 标准库 + 系统 `curl`)。走势图用到 echarts,打开 HTML 需联网加载一次。

## 走势图交互

- **时间范围**:最近1月 / 最近1年(默认)/ 全部,一键切换;也可拖底部 slider 自定义。
- **价格 / 溢价切换**:「价格」看各店绝对价;「溢价(较T+D)」把每条线换成 **店价 − 当日交易所金价**,基准线落到 0,直接看品牌溢价随时间怎么变。
- **图例**:默认只显示 周大福 + 交易所基准,顶部点其他金店名即可加显/隐藏。
- **悬停 tooltip**:除当前显示的线,还会给出**当日全部 9 家金店的最高价 / 最低价**(哪家、多少),不受当前显示了哪几条线影响。

## 每日自动更新(可选)

手动:每天跑一次 `python3 gold_tracker.py` 即可增量补当天。
自动:加一条 crontab(每天 20:00):

```
0 20 * * * cd ~/projects/gold-price-tracker && /usr/bin/python3 gold_tracker.py >> update.log 2>&1
```

## 部署到 GitHub Pages(push 自动部署 + 每日自动更新)

已内置 `.github/workflows/deploy.yml`。推到 GitHub 后,**每次 push、每天定时、以及手动触发**都会:抓最新金价 → 重新生成走势图 → 把新数据提交回仓库 → 发布到 GitHub Pages。

**首次一次性设置(在 GitHub 仓库页面):**
1. Settings → Pages → Build and deployment → Source 选 **GitHub Actions**。
2. Settings → Actions → General → Workflow permissions 选 **Read and write permissions**(否则每日更新无法把数据提交回仓库)。

**推送代码:**
```bash
cd ~/projects/gold-price-tracker
git init && git add . && git commit -m "init: 金价追踪器"
git branch -M main
git remote add origin git@github.com:<你的用户名>/<仓库名>.git
git push -u origin main
```
push 后到 Actions 页看部署进度,完成后访问 `https://<用户名>.github.io/<仓库名>/`。

> 说明:仓库只保存 `data/`(小体积 CSV 价格历史);`output/` 的 HTML 每次在云端重新生成、不进仓库,避免仓库膨胀。云端定时抓取用的是 GitHub 海外服务器,偶尔可能抓不到 cngold/金融界,此时会自动用仓库已有数据兜底部署,不会中断。

## 目录结构

```
gold-price-tracker/
├── config.json          # 追踪的金店列表、起始日期、品种名(黄金价格)
├── gold_tracker.py      # 抓取 + 生成走势图
├── data/                # 每家金店一个 CSV(date, price, unit, trend)
│   ├── 周大福.csv
│   └── ...
├── output/
│   ├── trend.html       # 多店对比走势图(交互:缩放/悬停/图例/价格·溢价切换)
│   └── all_brands.csv   # 合并长表(date, brand, price, unit, trend, baseline, premium)
└── README.md
```

## 增删金店

改 `config.json` 的 `brands` 列表即可。`brandId` 从 cngold 的金店列表接口获得:

```bash
curl -s -A Mozilla "https://www.cngold.org/sgapp/enterPrise/brand.do?brandId=&variable=json"
```

注意:各金店内部的"黄金价格"`productId` 不同,脚本会按 `product_name` 自动解析,配置里**只需填 brandId**。

## 数据说明

- 来源:cngold.org 后台接口(`/sgapp/price/gold/pageData.do`),为**网友提供的金店报价**,与门店实际成交价有差异,仅供参考、看趋势。
- 单位:元/克。`trend` 为当日涨/跌/平。
- 同一天可能有重复报价,已按日期去重(保留最后一条)。
- **交易所金价基准线**:`config.json` 的 `baselines`,数据源为金融界(jijinhao)历史 K 线接口,取上海金交所**黄金T+D 日收盘价**(元/克)。走势图里用**黑色加粗虚线**画出,金店价与它的差就是"品牌溢价"。可再加别的基准(如 Au99.99),填对应 `jijinhao_code` 即可。
- 注意:交易所金价只有交易日有数据(周末/节假日为空),金店报价基本每天都有。
