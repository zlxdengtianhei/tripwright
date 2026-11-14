# 数据来源与限制

**默认真实数据来自 Transitous、Open-Meteo 和 Nominatim。** 交通主要是时刻表，覆盖与日期都有边界；没有查到某班车不等于没有车。能查明的稳定事实由 agent 查好，无法取得的实时信息才在回答末尾给出 App、搜索内容、操作与链接。

**English.** Tripwright uses Transitous for transport, Open-Meteo for weather, and Nominatim for geocoding. Transport results are timetable-based unless a leg is explicitly marked as live. Coverage and validity checks are partial, not worldwide guarantees. Where Transitous has no data or no real time for a region, a deployment can run its own MOTIS with that region's GTFS and GTFS-RT ([transit-sources.md](transit-sources.md)); Wiener Linien and VAO adapters are not implemented. Provider terms apply independently of the project's MIT license.

MCP 服务只有设置 `TRIPWRIGHT_PROVIDERS=live` 才使用这些真实来源；未设置时是虚构数据。命令行 `tripwright query` 默认选择 `live`。所有结果都应保留来源与是否虚构的说明。

## Transitous：交通时刻表

[Transitous](https://transitous.org/) 汇集开放交通数据，公共 API 不需要密钥。Tripwright 使用它查询车次、发到时刻与换乘，把每一程和查询窗口写成服务端句子，供宿主照抄。车次带有明确实时标记时按标记说明；否则写“按时刻表，不是实时”，不承诺晚点、站台或临时停运信息完整。

### 公共 API 的条款与本项目实现

部署前阅读 [Transitous API 条款](https://transitous.org/api/)。按本项目记录的条款，公共服务供开放源码、非商业、轻量使用；多用户、自动化或更大量调用应先与维护者商量。需要商业或大量使用时，另行取得安排或部署自己的数据服务，不能从 MIT 代码许可推出公共 API 的使用权。

本项目的真实网络客户端：

- 默认在**滚动 24 小时内最多发出 720 次路线请求**，包括 `/plan`、`/one-to-many`、`/one-to-all`；不是每天零点清零。
- 通过共用文件保存路线请求计数，默认 `~/.cache/tripwright/transitous-routing.log`，可用 `TRIPWRIGHT_TRANSITOUS_USAGE` 指定位置；同一用户的进程共享该文件。macOS／Linux 使用文件锁，Windows 没有同等锁保证。
- 地名查询不计入这 720 次。一次用户提问可能触发多次路线请求，不能把 720 次当成 720 次提问，也不承诺“一次排程最多两次”。
- 达到限制时返回 `ROUTING_BUDGET_EXCEEDED`。`TRIPWRIGHT_TRANSITOUS_ROUTING_PER_DAY` 可改本地上限，但改变配置不会放宽上游条款。
- 请求携带应用名、版本与项目地址作为 User-Agent。`TRIPWRIGHT_USER_AGENT` 可替换为部署者自己的识别与联系信息。

展示交通结果时保留 [Transitous 数据来源链接](https://transitous.org/sources/)与 **© OpenStreetMap contributors**。具体数据集仍有各自许可。

### 覆盖与有效期

覆盖说明来自仓内的 `transitous_coverage.json`，由 Transitous 全部数据集清单生成；当前快照读取于 2026-11-01，包含 143 份清单。它是随项目维护的快照，不是每次提问都会自动刷新的全球覆盖保证。

- 各地区站点按来源标识归到数据集；能查到的线路不限于奥地利。
- **日期规则目前只有奥地利一条。** 已知年度数据按 12 月第二个星期六结束；例如当前 2026 年数据从 2026-12-13 起可能需要新时刻表。这个日期不是全球固定换季日，数据更新后也会改变。查询触及未覆盖日期时会给出说明，不说“没车”。
- **地区缺口目前只画出了克恩顿本地巴士。** 当端点落在该区域时，会说明本地巴士未覆盖；这不表示火车也没有。
- 奥地利及克恩顿区域轮廓是近似值，边界附近可能识别不准。其他国家或地区的缺口、有效期可能尚未识别出来；没有提示不等于完整覆盖。
- 路由返回的是找到的走法，并非穷尽所有班次。只有查询范围与后续班次有支持时，才可说“最晚”“末班”或“之后没有”；一次空结果不能支持这些结论。

维护者可以用 `python -m tripwright.providers.coverage refresh <数据集清单检出目录>/feeds --commit <提交号>` 更新快照。只更新代码或重新查询，不会自动取得新的清单快照。

## 能给什么，剩下的在哪里查

| 事项 | Tripwright 当前能给什么 | 拿不到时怎样处理 |
| --- | --- | --- |
| 线路、目的地、按时刻表的车次和换乘 | 在查询窗口与数据覆盖内直接查好，逐程写准 | 覆盖不足如实说明，不把可查事实全部推给用户再核 |
| 晚点、站台、临时停运 | 仅保留上游明确提供的标记，不保证完整实时覆盖 | 末尾给运营方 App 的搜法与链接；奥地利常用 ÖBB App，德国 DB Navigator，瑞士 SBB Mobile |
| 换季后的日期、缺失数据集 | 已知时写出有效期或覆盖限制 | 去运营方 App／官网查目标日期；结果为空不等于没有车 |
| 餐馆、场馆的营业规则 | 保存的地点资料；宿主读取店家／场馆页面后记下原句与来源 | 找不到可靠页面就说明未知，提供已核实的官网或询问方式 |
| 订位、余位、门票库存、按需巴士预约 | 没有通用的预订或库存接口 | 去服务商的官方预约入口，或询问工作人员；不编余位、电话或操作按钮 |
| 票价与票能否使用 | 保存的票务资料、可核实的运营方规则 | 不承诺完整实时票价，必要时去运营方购票入口查询 |

工具的 `how_to_check` 把软件名、搜索词、出发时间、步骤和链接与交通事实分开。回答在最末一节“怎么查、怎么问”使用这些内容。需要问人时先给一句起草邀请；用户要了，再提供英语与当地语言，可按需加其他语言。用户在 App 或现场看到更新后，可以告诉 agent，由它按有来源的实况记录。

## 天气与地名

**天气：** [Open-Meteo](https://open-meteo.com/) 提供温度、降雨概率、风速等预报，按 CC BY 4.0 署名；免费 API 的用途与限额见[服务条款](https://open-meteo.com/en/terms)。本实现给今天／明天的预报设 3 小时缓存有效期、后天 6 小时、更远日期 12 小时。这是本地刷新策略，不是天气在这些时段内必定不变；官方预警仍应看当地气象机构。

**地名：** Nominatim 使用 OpenStreetMap 数据，保留 © OpenStreetMap contributors 与 [ODbL 署名](https://www.openstreetmap.org/copyright)。公共接口要求识别用的 User-Agent、缓存与不超过每秒一次请求；本实现单实例串行并至少间隔一秒。多实例部署还需控制合计用量。详见 [Nominatim 使用政策](https://operations.osmfoundation.org/policies/nominatim/)。它用于地名解析，不是自动补全或批量抓取入口。

## 额外实时来源

Transitous 没有某地区的数据或没有实时时，部署可以自己跑一个 MOTIS，喂这个地区的 GTFS 与 GTFS-RT，用 `TRIPWRIGHT_TRANSIT_SOURCES` 指过去；两端都在它范围里的查询走它，其余照旧走 Transitous。做法见 [transit-sources.zh-CN.md](transit-sources.zh-CN.md)。做计划时，没接实时源的地区会被点名问一次要不要配，附上这份做法。

Wiener Linien 与 VAO 自己的接口不是 GTFS-RT，MOTIS 读不了；当前没有对应适配器，也没有设置页可粘贴密钥。服务可以询问并记录用户对来源的选择，但记录选择不等于已完成连接。

- **Wiener Linien：** 可作为维也纳的额外实时来源候选；本项目目前不提供“说要就开”的功能，接入前再核接口、覆盖与条款。
- **VAO：** 如需接入，由用户本人向服务商确认合同、资格、接口和申请凭据；agent 不代用户签约。当前未验证它在本项目中的实时覆盖、请求额度或开通时长，不作承诺。
- **其他地区：** 有公开 GTFS-RT 的，照上面的做法自己接；不能据此宣称已有全球实时数据。

Google 数据 API 和非官方 HAFAS 接口当前没有接入。回答中的 Google 地图导航链接只是链接，不表示调用了 Google 的数据接口。

## 署名

- [Transitous 数据来源](https://transitous.org/sources/)。
- [© OpenStreetMap contributors](https://www.openstreetmap.org/copyright)，用于交通与地名数据。
- [Open-Meteo](https://open-meteo.com/)，天气数据 CC BY 4.0。

额外来源接入后，再按其实际条款补充署名与使用边界。
