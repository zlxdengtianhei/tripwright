---
version: alpha
name: Tripwright·图册
description: Tripwright 分享页（服务按一次行程生成的首页与日页）的设计取值。方向“图册”：行程是在地上走出来的，图与清单用同一套图钉编号。
colors:
  ground: "#F4F5F3"
  paper: "#FFFFFF"
  water: "#DCE7EE"
  line: "#C3CDD4"
  ink: "#1B2430"
  ink-2: "#55606B"
  pin: "#2B64CC"
  struck: "#A33A2C"
  added: "#FBEDB1"
typography:
  label:
    fontFamily: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif
    fontSize: 13px
    fontWeight: 400
    lineHeight: 1.6
  body:
    fontFamily: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif
    fontSize: 15px
    fontWeight: 400
    lineHeight: 1.6
  body-day:
    fontFamily: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif
    fontSize: 16px
    fontWeight: 400
    lineHeight: 1.6
  heading-3:
    fontFamily: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif
    fontSize: 17px
    fontWeight: 700
    lineHeight: 1.3
  heading-2:
    fontFamily: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif
    fontSize: 20px
    fontWeight: 700
    lineHeight: 1.3
  heading-1:
    fontFamily: system-ui, -apple-system, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif
    fontSize: 28px
    fontWeight: 700
    lineHeight: 1.3
  figure:
    fontFamily: ui-monospace, "SF Mono", "DejaVu Sans Mono", "Noto Sans Mono CJK SC", monospace
    fontSize: 13px
    fontWeight: 700
    lineHeight: 1
    fontFeature: '"tnum"'
  time:
    fontFamily: ui-monospace, "SF Mono", "DejaVu Sans Mono", "Noto Sans Mono CJK SC", monospace
    fontSize: 16px
    fontWeight: 700
    lineHeight: 1.6
    fontFeature: '"tnum"'
rounded:
  none: 0px
  panel: 4px
  pin: 24px
spacing:
  step-1: 4px
  step-2: 8px
  step-3: 12px
  step-4: 16px
  step-5: 24px
  step-6: 32px
  step-7: 48px
  step-8: 64px
components:
  page:
    backgroundColor: "{colors.ground}"
    textColor: "{colors.ink}"
    typography: "{typography.body}"
  map-plate:
    backgroundColor: "{colors.water}"
    rounded: "{rounded.panel}"
    padding: 12px
  pin-fixed:
    backgroundColor: "{colors.pin}"
    textColor: "{colors.paper}"
    rounded: "{rounded.pin}"
    typography: "{typography.figure}"
  pin-visit:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.pin}"
    rounded: "{rounded.pin}"
    typography: "{typography.figure}"
  pin-option:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.pin}"
    rounded: "{rounded.panel}"
    typography: "{typography.figure}"
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.panel}"
  button:
    backgroundColor: "{colors.paper}"
    textColor: "{colors.ink}"
    rounded: "{rounded.panel}"
  rule:
    backgroundColor: "{colors.line}"
  note:
    textColor: "{colors.ink-2}"
  revision-old:
    textColor: "{colors.ink-2}"
  revision-old-line:
    backgroundColor: "{colors.struck}"
  revision-new:
    backgroundColor: "{colors.added}"
    textColor: "{colors.ink}"
---

# Tripwright·图册

> 状态：现行（web_ui_spec D-14 选定“图册”，11-13；取值与“修订记号”“地图”两节对齐定稿第 1 版，`wus-twf` 11-13 03:52 交回）。用在服务生成的分享页：首页、每天一页、实录页。开头的取值表是准的：程序把它导出成样式变量（`tripwright.site.theme`），样式表只经变量取值；表里没有的颜色、字号、圆角、间距，不要用。

## 方案怎样换

页面的构件（类名与结构）由模板与共用样式 `share_static/share.css` 定住，各方案相同。一个方案是 `themes/<名>/` 一个文件夹：一份 `DESIGN.md`（取值，导出成 `:root` 变量）加一份可选的 `theme.css`（少量版式覆盖）。每个方案都要给出共用样式用到的全部变量，缺一个，构建时报错并点名。分享页设置里的 `theme` 选方案，缺省 `atlas`。“线路图”“修订本”两套还没做，要做时照这个文件夹加一个。

## 该与不该

**色：只有一个鲜艳的蓝，它只属于图钉。** 图钉蓝（`pin`）用在：图钉、“怎么走”两端的钉、日期条里“我在这里”的标记、定时的点。链接不用蓝，用墨色加下划线。主按钮用墨色。灰墨（`ink-2`）给次要的字，水色（`water`）给“图所在的面”、表头与“下一个”关键时刻的底，纸白（`paper`）给页头、空心的钉和代码块。没有深色模式：浅色地图的配色只做一套。

**状态：形、填充、文字叠用，不靠颜色。** 实心圆头钉＝要准时；空心圆头钉＝时间可挪；空心方头钉加字母＝几选一（5A、5B）。清单里“怎么走”的时刻，定死的用粗体加一个点，估算的用常规体、没有点。

**修订记号：只给真正改过的地方。** 原定的划掉（灰墨字，2 像素的砖红删除线 `struck`），现在的标成新加（浅黄底 `added`，2 像素墨色下划线，加粗）。删除线与下划线是形，灰与加粗是字重，句子里本来就有“原定的……改坐这一班”是文字：转成灰度仍分得清。砖红、浅黄只给修订，不进别的状态；它们比图钉蓝暗淡、面积小，蓝仍是页上唯一抢眼的颜色。用 HTML 的 `<del>`、`<ins>`（类 `tw-rev-old`、`tw-rev-new`；含改动的那一行加 `tw-revised`），两种语言都标，读屏能读出“删除”“插入”。哪里改过来自分享设置的 `changes`（{date, was, now}），只标原文里真有的字，不添字；对不上的不标。

**字：无衬线写字，等宽写数。** 序号、时刻、日期条、班次都用等宽数字。六级：13（图例、图说、钉里的号）、15（次要说明）、16（正文，手机上读得清）、17（小节标题）、20（日页标题，一句很长的路线）、28（首页标题）。

**间距：一套八级。** 4、8、12、16、24、32、48、64。线宽、宽高、定位偏移不算间距。

**圆角：两种（另有 0）。** 4 给面、按钮、方头钉；24 给圆头钉（钉是 28 见方）。没有阴影：层次靠 1 像素的线和水色的底。

**图在先，手机上时刻在先。** 日页桌面：图固定在左，清单在右（关键时刻、要去的地方、怎么走、折叠）。手机：先“关键时刻”，再位置图，图紧接在它编号的“要去的地方”正上方，然后“怎么走”与折叠的两块。理由：在路上的人打开手机，第一问是“几点要到哪”；图与清单同号，所以两者要挨着。页里的先后本来就是这个次序（关键时刻、图、清单），桌面的两栏靠网格摆放，所以读屏与打印照手机的先后。

**不要加的：** 标题上方的大写小标签（日期行是内容，用等宽小字）、等大的卡片、粗色边的引用框、胶囊按钮、渐变、阴影、emoji。

## 地图

**线上有底图时，位置图换成真地图；钉与编号照旧。** 位置示意（`plate` 里的内联 SVG）是没有底图时的退路，也是页面打开那一刻先看到的。

- 有底图、点有坐标：脚本加载 Leaflet 与 OpenStreetMap 的底图后，图版（`plate`，水色的面、1 像素线、4 圆角）里换成真地图；图钉照本表的三种形（实心圆＝要准时、空心圆＝可挪、空心方加字母＝几选一），号与清单、“怎么走”行首、图例同一套。底图压成灰、调淡，让图钉蓝仍是图上唯一鲜艳的颜色。线：实线表先后，虚线表几选一，颜色照 `ink`、`ink-2`。图说换成“底图来自 OpenStreetMap”。
- 没有坐标的点：不编坐标，不画；清单里照旧有号，“怎么走”那一程只有一端有钉，图说写明几处没画。
- 一个点都没有坐标：不加载地图，图里只按先后排钉，写明“没有坐标：钉只按先后排，不表示位置”。
- 底图加载不了（离线、超时、被拦）：地图库没加载到就一直是位置示意（按经纬度摆点，网格每格 250 米起、随范围放大，比例尺，不画河与街），图说写“位置示意：按经纬度摆放，没有底图”；库加载到了、8 秒内一张底图都没到，也退回位置示意。不留“地图加载中……”在那里转。
- 同一处去两次（同一坐标两个号）：钉并排画，点与线仍在真实位置。
- 手机上图版的位置照“图在先，手机上时刻在先”一条：图在“要去的地方”正上方。
- 和定稿第 1 版的差：OpenStreetMap 的标准底图关不掉自带的兴趣点标记，只能整体压灰调淡；要关掉得换一家可配样式的底图（要 key 或自建瓦片），这一版没做。
