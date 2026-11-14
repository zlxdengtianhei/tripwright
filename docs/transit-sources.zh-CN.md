# 给 Transitous 没有的地区接交通源

[English](transit-sources.md)

**简单说。** Tripwright 的时刻表来自 [Transitous](https://transitous.org/)。某个地区 Transitous 没有数据，或者有数据但没有实时，你可以在 Tripwright 旁边自己跑一个 [MOTIS](https://github.com/motis-project/motis)（Transitous 用的就是它），喂这个地区的 GTFS 时刻表，有 GTFS-RT 实时数据就一起喂，再用一个设置告诉 Tripwright 它在哪。之后两端都在这个地区里的查询走你的服务器，别的照旧走 Transitous。不配，什么都不变。

做计划时碰到没接实时源的地区，助手会说一次，问你要不要配，并附上本页链接。你说不配，这趟就不再问。

2026-11-14 用 MOTIS 2.11.4 和亚琛（AVV）的时刻表，在 Mac 的 Docker Desktop 上，按下面两种部署方式都跑过。标了**没测**的步骤没跑过。

## 1. 找源

时刻表要一份 **GTFS**（一个 zip，里面有 `stops.txt`、`stop_times.txt` 等）；实时要一个 **GTFS-RT** 地址，可以没有。MOTIS 也读 SIRI Lite（`protocol: siri`），MOTIS 自己标的是还在开发中（这里**没测**）。

按这个次序找：

1. **Transitous 里是不是已经有了？** [transitous.org/sources](https://transitous.org/sources) 按国家列出每个源、覆盖哪些运营商、带不带实时。在 [Transitous 地图](https://transitous.org/)上，显示成灰色的车次没有实时数据。你的地区已经在里面而且带实时，就什么都不用配。
2. **[Mobility Database](https://mobilitydatabase.org)**（MobilityData 运营）：6000 多个 GTFS、GTFS-RT、GBFS feed，覆盖 99 个以上国家。网站浏览免费，API 要免费账号。每条 feed 记录写明要不要 key（`authentication_type`：0 不要，1 放在网址里，2 放在请求头里），并链接许可（`license_url`）。
3. **[Transitland](https://www.transit.land/)**（Interline 运营）：GTFS 与 GTFS-RT；它的源清单 [Transitland Atlas](https://github.com/transitland/transitland-atlas) 在 GitHub 上公开。它的 API 一律要 key。
4. **该国的国家访问点（NAP）。** 欧盟成员国都必须设一个交通数据的国家访问点（委托条例 (EU) 2017/1926）；[NAPCORE 有清单](https://napcore.eu/description-naps/national-access-point)。多数是目录，链到数据发布方，不直接给下载。
5. **运营方或城市的开放数据门户。** Transitous 给的经验：Google 地图在一个地区显示公交，那里多半有 GTFS。

找到的大致样子（2026-11-14 读）：瑞士 [opentransportdata.swiss](https://opentransportdata.swiss/en/cookbook/timetable-cookbook/gtfs) 有 GTFS 与 GTFS-RT；实时要在平台上申请 key，每个 key 的次数限制它两页写得不一样（[GTFS-RT 页](https://opentransportdata.swiss/en/cookbook/realtime-prediction-cookbook/gtfs-rt)：每分钟 2 次；[限额页](https://opentransportdata.swiss/en/limits-and-costs/)：5 次）；德国 [gtfs.de](https://gtfs.de/en/feeds) 有免费 GTFS（CC BY 4.0，只含未来 30 天）和测试版 GTFS-RT；英格兰、威尔士、苏格兰的巴士在 Bus Open Data Service，免费注册拿 key；日本多数在 ODPT 的 API 后面，免费注册拿 token。中国大陆我们没找到开放的 GTFS：只有地图厂商的接口，要 key、要实名，也塞不进 MOTIS（见第 6 节）。

用一个源之前，核四件事：

| 核什么 | 能用 | 不能用 |
|---|---|---|
| 格式 | GTFS zip；GTFS-RT 的 `.pb` 地址 | 只有 PDF、网页，或只给 App 用的 API（那是下面的第 3 条路） |
| 是否最新 | `feed_info.txt` 或 `calendar.txt` 覆盖你出行的日期 | 日期在出行前就结束了：那几天 MOTIS 查不到车 |
| 许可 | 开放许可（CC BY、ODbL、各国的开放数据许可）；记下它要求的署名 | 没有许可，或条款禁止你这样用 |
| key | 不要 key，或者能自己免费申请 | 要签合同或付费：只有你本人能签 |

key 和合同归你。助手不能替你申请、签约或付款。

## 2. 三条接法

1. **交给 Transitous**（对所有人最好，对你最慢）。Transitous 的源放在 [public-transport/transitous](https://github.com/public-transport/transitous) 仓里，一个地区一份 JSON（`feeds/<ISO 代码>.json`，例：`feeds/at.json`）。加一条（时刻表写 `"type": "http"` 和 GTFS 地址；实时写 `"type": "url"`、`"spec": "gtfs-rt"`），开 PR；仓里的检查会抓取并测这个源，也可以先用 `./src/fetch.py feeds/<代码>.json` 只试一个地区（[做法](https://transitous.org/doc)）。维护者收下之后，所有 Tripwright 都能用，你的部署不用改。文档没写审核要多久（**没测**）。两条限制：要 key 的源，key 要先加密给维护者（`AGE-ENCRYPTED:`，[做法](https://github.com/public-transport/transitous/blob/main/docs/docs/index.md#private-api-keys)）才能加进去，之后 Transitous 拿这个 key 给所有人用，先核 key 的条款允不允许；Transitous 自己的条款对所有用它的人都适用：客户端要开源、带能识别的 User-Agent、不许商用（[API 条款](https://transitous.org/api)）。同时把源加进 [Mobility Database](https://github.com/MobilityData/mobility-database-catalogs)（表单或 PR，它的 README 说一周内回应），别的项目也找得到。
2. **自己跑 MOTIS**（这里的缺省做法，见第 3—5 节）。当天就能用，只对你的部署有效。
3. **接地区自己的 API**（例：奥地利的 Wiener Linien、VAO；没有开放 GTFS 的地区，地图厂商的接口）。就我们查到的，它们的实时是各自的接口，不是 MOTIS 读得了的 GTFS-RT；每个都要在 Tripwright 里另写适配器，有的还要用你的名义签约或实名。现在还没有这类适配器。以你名义申请、条款只许你自己用的 key，留在你自己的服务器上。

## 3. 准备文件

所有东西放一个文件夹 `transit/`，和放 Tripwright 数据的文件夹并排（compose 部署：从 `deploy/` 看是 `../../transit`）。里面放：

- 这个地区的 GTFS zip；
- 覆盖这个地区的 OpenStreetMap 区域文件，`.osm.pbf` 格式（[Geofabrik](https://download.geofabrik.de/) 按国家和地区提供；MOTIS 用它查站名、算步行）；
- `config.yml`：复制 [deploy/transit/config.yml](../deploy/transit/config.yml)，改两个文件名。数据集标签取短的小写词，不带 `_`；
- `sources.yaml`：复制 [deploy/transit/sources.yaml](../deploy/transit/sources.yaml) 并填好（见下）。

测过的例子（亚琛）：

```bash
mkdir transit && cd transit
curl -L -o aachen.osm.pbf https://github.com/motis-project/test-data/raw/aachen/aachen.osm.pbf
curl -L -o avv.gtfs.zip https://opendata.avv.de/current_GTFS/AVV_GTFS_Masten_mit_SPNV.zip
```

`config.yml` 里写 `osm: aachen.osm.pbf`、数据集标签 `avv`、`path: avv.gtfs.zip`。

然后导入一次（我们跑亚琛 5.5 秒、30 MB；德国全境，有一份公开的读数说导入时内存峰值约 7 GB，[出处](https://github.com/helpcode-ai/anythingmcp/blob/main/deploy/motis/Dockerfile)；这里**没测**）：

```bash
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD":/data -w /data ghcr.io/motis-project/motis:2.11.4 /motis import
```

`--user "$(id -u):$(id -g)"` 要留着：导入以你的身份跑，才能往你的文件夹里写 `data/`。不加时导入用镜像自己的用户（`motis`，UID 100）；在装 Docker Engine 的 Linux 服务器上，这个用户写不了你的文件夹，导入停在 `unable to import: basic_ios::clear: iostream error`，也没有 `data/`（2026-11-14 在 Ubuntu 上见到）。服务本身不用加 `--user`：它只读 `data/`。用 `sudo docker` 时 `$(id -u)` 仍是你的，因为 shell 在 `sudo` 跑之前就把它填好了。这条命令原样在 Mac（Docker Desktop）和 Linux 服务器上都跑过。

**`sources.yaml`。** 每个 MOTIS 服务器一条：

```yaml
sources:
  - id: avv
    name: {en: "Aachen AVV (own MOTIS)", zh: "亚琛 AVV（自托管 MOTIS）"}
    api: http://motis:8080/api
    datasets: [avv]
    bbox: [5.95, 50.65, 6.25, 50.85]
    match: [aachen, 亚琛]
    realtime: false
    attribution: "Timetable: Aachener Verkehrsverbund (AVV) open data; map data © OpenStreetMap contributors"
```

- `bbox` 依次是西、南、东、北：这个源**完整**覆盖当地交通的范围。查询两端都在里面才走你的服务器。`python -m tripwright.providers.sources bbox avv.gtfs.zip` 打印一份 GTFS 全部站点的外框；数据伸得更远时要缩小（AVV 的火车开到科隆，外框比亚琛大得多）。
- `match` 写这个源覆盖的行程地区里会出现的词；做计划时据此判断这趟的地区有没有实时。
- `realtime: true` 只在 `config.yml` 里加了能用的 `rt:` 之后才写（真的实时源**没测**）。
- `attribution` 必填：数据许可通常要求署名，回答里会带上。

## 4. 起服务

### 用 `deploy/compose.yaml` 部署的

```bash
cd deploy
echo 'COMPOSE_FILE=compose.yaml:compose.transit.yaml' >> .env
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" motis /motis import    # 第 3 节没导入的话
docker compose up -d
```

[deploy/compose.transit.yaml](../deploy/compose.transit.yaml) 在私有网络上加一个 `motis` 服务，把 `../../transit` 只读挂进 Tripwright，并设好 `TRIPWRIGHT_TRANSIT_SOURCES=/transit/sources.yaml`。要关掉：删掉 `COMPOSE_FILE` 那一行，跑 `docker compose up -d --remove-orphans`（`motis` 容器也一起停掉）。

### 用 SETUP.md 里 `docker run` 部署的

在放 `transit/` 的文件夹里跑：

```bash
docker network create tripwright-transit
docker run -d --name tripwright-motis --network tripwright-transit --network-alias motis --restart unless-stopped \
  -v "$PWD/transit":/data -w /data ghcr.io/motis-project/motis:2.11.4 /motis server /data/data
docker rm -f tripwright
docker run -d --name tripwright --restart unless-stopped --network tripwright-transit -p 127.0.0.1:8000:8000 \
  -v tripwright-data:/data \
  -e TRIPWRIGHT_TRANSIT_SOURCES='[{id: avv, api: "http://motis:8080/api", bbox: [5.95, 50.65, 6.25, 50.85], match: [aachen], attribution: "AVV open data; © OpenStreetMap contributors"}]' \
  tripwright
```

删掉再重建 `tripwright` 容器不丢行程：它们在 `tripwright-data` 卷里。这里设置里直接放列表，不用挂文件。

## 5. 核接上了没有

compose 部署的容器叫 `tripwright-app`，`docker run` 部署的叫 `tripwright`；两种都已带着设置。

```bash
docker exec tripwright python -m tripwright.providers.sources check avv "Aachen Bushof" "Aachen West" \
  --zone Europe/Berlin --when 2026-11-16T09:00
```

我们跑出来是：

```
avv (http://motis:8080/api): Aachen, Bushof [avv_000000100101] -> Aachen West [avv_000000108670], 3 connections from 2026-11-16T09:00:00+01:00
  09:01 -> 09:12  bus 1  (timetable)
  09:07 -> 09:14  bus 73  (timetable)
  09:13 -> 09:20  bus 33  (timetable)
```

再经 Tripwright 本身查一次；结果里的 `evidence.source`，走你的服务器是 `motis:<id>`，否则是 `transitous`：

```bash
docker exec -e HOME=/tmp tripwright tripwright query transit --from "Aachen Bushof" --to "Aachen West" \
  --when 2026-11-16T09:00:00+01:00 --timezone Europe/Berlin
```

查不到车：时刻表可能不覆盖那天（MOTIS 从今天起载入 `num_days` 天），或者名字对不上任何站（用 `tripwright query transit --action stops --text <名字>` 看）。连不上服务器：`docker logs tripwright-motis` 看 MOTIS 起来没有。

## 6. 找不到源的时候

那就没有可接的。有印刷版时刻表的，可以自己把它录成 GTFS，照第 3 节喂进去（**没测**）。Tripwright 在 Transitous 有数据的地方照常回答，说明结果是不是只按时刻表，回答末尾给出查实时晚点、站台和停运的当地 App。查不到是“没查到”，不是“没车”。
