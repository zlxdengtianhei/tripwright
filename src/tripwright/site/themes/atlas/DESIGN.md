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
---

# Tripwright·图册

> 状态：现行（web_ui_spec D-14 选定“图册”，11-13）。用在服务生成的分享页：首页、每天一页、实录页。开头的取值表是准的：程序把它导出成样式变量（`tripwright.site.theme`），样式表只经变量取值；表里没有的颜色、字号、圆角、间距，不要用。

## 方案怎样换

页面的构件（类名与结构）由模板与共用样式 `share_static/share.css` 定住，各方案相同。一个方案是 `themes/<名>/` 一个文件夹：一份 `DESIGN.md`（取值，导出成 `:root` 变量）加一份可选的 `theme.css`（少量版式覆盖）。每个方案都要给出共用样式用到的全部变量，缺一个，构建时报错并点名。分享页设置里的 `theme` 选方案，缺省 `atlas`。“线路图”“修订本”两套还没做，要做时照这个文件夹加一个。

## 该与不该

**色：只有一个鲜艳的蓝，它只属于图钉。** 图钉蓝（`pin`）用在：图钉、“怎么走”两端的钉、日期条里“我在这里”的标记、定时的点。链接不用蓝，用墨色加下划线。主按钮用墨色。灰墨（`ink-2`）给次要的字，水色（`water`）给“图所在的面”、表头与“下一个”关键时刻的底，纸白（`paper`）给页头、空心的钉和代码块。没有深色模式：浅色地图的配色只做一套。

**状态：形、填充、文字叠用，不靠颜色。** 实心圆头钉＝要准时；空心圆头钉＝时间可挪；空心方头钉加字母＝几选一（5A、5B）。清单里“怎么走”的时刻，定死的用粗体加一个点，估算的用常规体、没有点。

**改过的地方：划掉与新加。** 行程里真正改过的地方（分享设置里 `changes` 给出、而且原文里真有这几个字的），原来的用删除线加灰墨，新的用水色底加粗。只标原文里有的字，不添字；对不上的不标。

**字：无衬线写字，等宽写数。** 序号、时刻、日期条、班次都用等宽数字。六级：13（图例、图说、钉里的号）、15（次要说明）、16（正文，手机上读得清）、17（小节标题）、20（日页标题，一句很长的路线）、28（首页标题）。

**间距：一套八级。** 4、8、12、16、24、32、48、64。线宽、宽高、定位偏移不算间距。

**圆角：两种（另有 0）。** 4 给面、按钮、方头钉；24 给圆头钉（钉是 28 见方）。没有阴影：层次靠 1 像素的线和水色的底。

**图：线上有底图用真地图，没有就退回位置示意。** 页面先带一张按经纬度摆点的位置示意（内联 SVG，水色网格每格 250 米、500 米比例尺，写明“示意”），所以没网也看得到先后与远近；脚本加载到地图库和底图后，换成真地图，钉的号与形和示意相同。没有坐标的地方不画、不编坐标，图说写明几处没画；一处坐标都没有时，图里只按先后排钉，写明“没有坐标”。

**图在先，但手机上关键时刻更先。** 日页桌面：图固定在左，清单在右（关键时刻、要去的地方、怎么走、折叠）。手机：关键时刻在图前面，图紧挨着它编号的“要去的地方”。

**不要加的：** 标题上方的大写小标签（日期行是内容，用等宽小字）、等大的卡片、粗色边的引用框、胶囊按钮、渐变、阴影、emoji。
