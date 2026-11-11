## A17：A16 自重放里的两处小毛病（2026-11-03，主会话自重放 a16_self_on_r1、a16_self_off_r1）

依据：两轮自重放的回复原文与工具流。每条有重放形状的坏样本与合法对照（`tests/core/test_a17_fixes.py`、`tests/agents/test_a17_dispatch.py`）；源码退回基线（610d7735）后三条都在坏样本处不过。

- **一、第一段同一行出现两次（`first_part.point_draft`）。**
  - 来源：off 轮第一问，宿主把“最晚几点出发机场”和“怎么走”拆成两个 go 要点，地点都是机场；第一段按要点各出一行，第 1 行与第 4 行相同。on 轮宿主只给了一个 go 要点，没出。
  - 改：同一种类、同一地点（解析出的地点，没解析时是宿主给的 place）的要点合成一行，取排在前面的那个；没有地点的要点不合并（各说各的）。整段再去掉完全相同的行，合并后腾出的行留给硬节点。`turn` 与命令行 `first-part` 都经 `point_draft`，一处改两处管。`first_part.points` 仍每个要点一项，合并的几项 `line` 相同，`answers_each_point` 照旧逐个核。
- **二、没有 MCP 时第一段前的两句英文状态句。**
  - 第一句（19.6 秒，“the points format needs objects, so I'm retrying”）：宿主第一次调 `first-part` 时 `--points` 给的是字符串列表，命令报 pydantic 原文退出 1。改：命令行 `read_points` 把列表里的字符串当成 `{kind: other, said}`（去空白，空串略去，超过 60 字截断，与 `Point.said` 的上限一致）；真格式错（不是 JSON、不是列表、kind 不对、元素不是对象）照旧退出 1，错误末尾加一个可照抄的例子；顺带把整体不是列表时的路径从“points.”改成“points”。字符串要点没有种类，第一段对它们只写“…：第二段给。”，不给存的车；要存的车，宿主得给 kind 与 place（例子里有）。
  - 第二句（38.2 秒，“I'm still checking tonight's actual departures…”）：宿主在后台派出 researcher 之后、第一段之前写的。查了主角色与步骤页，没有一句让它在第一段前说话；有一句让它“以为第一段已经发了”：派单那条要它把“what the first part says of it”交给 researcher，而 D-46 的顺序是先派、再把第一段写成这一步最后的文字。两轮四次派单（on、off 各两问）的 prompt 里都写了“First part already told user”或“The first part said”，当时第一段一次都还没发；派完之后 on 轮没写字、接着自己调了查询，外壳补了一句“没有可见输出”才发第一段；off 轮写了这句状态句，Stop 钩子拦下后才发第一段。
  - 改（主角色，生成的主 SKILL 12,130 → 12,129 字节，一进一出）：派单那条改成交“what the plan stores of it”；第 5 步末句“A draft in your thinking is never shown, nor is a tool result.”换成“The user sees no dispatch, draft in your thinking or tool result.”。researcher 回报格式的 `answer line` 跟着改成“what the plan stored”。新承诺 `dispatch-carries-the-plan`：主 SKILL 要有新说法、不许出现旧说法；`multi-agent-first` 的对应一条换成新句。D-46 的顺序（先在后台派、再发第一段）不变。
- **限制。**
  - 第二句的改法是说明文字，宿主照不照做要看下一次重放；钩子没有改（Stop 钩子已经会拦“I'm still”这类状态句，但拦的时候用户已经看到了）。
  - `turn.next` 里“先把 first_part.text 原样发给用户，再去查”没改：后台派单不算查询（PreToolUse 钩子放行），与 D-46 不冲突。

## A17b：退回 A17 的两处，留下去重（2026-11-03，主会话自重放 a17_self_*）

依据：主会话用 A17 跑的 5 轮自重放，与 A16 的 2 轮比。

- **为什么退。**
  - 第一段前、两段之间多发状态句的问，从 4 问里 1 问变成 10 问里 7 问（“还在核第二段，马上发。”“上面发的第二段已经核过，内容不变，不用再看一遍。”“正在核今晚去机场的车次，核完发第二段。”）。主会话判断最可能来自 A17 改的两句回答层说明；单轮波动也可能，但比例差太大。
  - 没有 MCP 时宿主照旧把 `--points` 写成字符串列表，A17 把它们当 other，第一段只剩“今晚到明天：第二段给。”这类行，答不了所问（a17_self_off_r2、off_r3 第一问）。A16 时报错，宿主改成对象重试，第一段反而是对的。
  - 去重有效：5 轮里没再出现重复行。
- **退回。** 主角色第 5 步末句（“A draft in your thinking is never shown, nor is a tool result.”）、派单那条（“what the first part says of it”）、研究员回报格式的 `answer line`、`promises.py`（`multi-agent-first` 原句，去掉 `dispatch-carries-the-plan`）都回到基线原文，生成件重建；主 SKILL 回到 12,130 字节，`agents/`、`skills/` 与基线无差。`tests/agents/test_a17_dispatch.py` 删去。
- **命令行 `first-part --points`。** 字符串列表照 A16 报 pydantic 原文退出 1，错误末尾保留 A17 加的可照抄的例子（`cli.read_points`）；其他格式错同样带例子。整体不是列表时错误路径写“points”而不是“points.”，保留。
- **留下。** `first_part.point_draft` 的合并与去重（A17 第一条）不变。
- **测试。** `tests/core/test_a17_fixes.py`：`test_points_of_one_kind_to_one_place_share_one_line` 照旧；字符串那条改成 `test_the_command_line_refuses_plain_strings_with_a_right_shape_to_copy`（坏样本：重放的三个字符串，退出 1、信息里有例子；对照：例子换上行程的地点编号后出带存档车的第一段）。源码退回基线后两条都在坏样本处不过。

## S4a：给 agent 的引导按“最差入口”重写（2026-11-03，任务包 4a，调研 td/apps-github.md）

- **为什么。** 原来的引导按 Claude Code 写：默认能派后台子代理，默认能在一轮里先发第一段、再接着核、再发第二段。调研（第一节第 4 条、4.1、5.3）查到的事实是：没有一家应用保证“一次任务中途先发一段再继续”；Claude 聊天、豆包工作没有子代理；豆包工作只连 MCP、不读仓里的文件。server instructions 是 5,715 字节的一段英文连写，步骤和完成条件分不出来。
- **一份核心步骤，同源生成。**
  - 源：`src/tripwright/agents/core.yaml`。8 步，每步一个动作和一句完成条件，完成条件看工具结果或发出的文字就能核对；另有 4 条始终成立的规矩，最后一条是停点。
  - 生成四处：server instructions（3,445 字节，上限 3,600，构建时超了就拒）；`generated/server/steps.json`，`turn` 和命令行 `first-part` 照它返回 `steps`、`always`；主 skill 与 MCP prompt 的“Every message”；`generated/repo/AGENTS.init.md`，init 写成用户仓的 `AGENTS.md`，`CLAUDE.md` 仍是 `@AGENTS.md`。
  - 这个文件不叫 `AGENTS.md`：在本仓里工作的宿主会把它当本仓的说明读。
  - instructions 只放步骤；长规则留在 MCP prompt `tripwright-main`，instructions 最后一句写“写第二段、记录或步骤不清时读它”。只连 MCP 的入口按 instructions、`turn` 的 `steps` 与 `next` 走，`check_answer` 照旧兜底。
  - 承诺里 16 条原来也核 server instructions，现在只核 MCP prompt 和 skill；核 instructions 的只留步骤本身说到的 8 条。新增 3 条，每条核四处：`subagents-optional`、`second-part-in-the-next-reply`、`stop-only-before-what-cannot-be-undone`。`dispatch-before-the-first-part` 改用核心步骤的字句，也核四处。
- **子代理可选，D-46 的顺序不变。**
  - 第 3 步写：应用能在后台跑子代理、并在它回报时通知（Claude Code），先派一个 researcher，否则跳过。
  - 第 4 步：派了的，第一段是这一轮最后的文字。
  - 第 5 步：子代理可以替你查；没有就自己一件一件查。
  - 不写“同时查”。原 skill 的“several searches at once”删掉了。
  - Codex 要在 AGENTS.md 里明写才会派，所以 AGENTS.md 加一句“In Codex, spawn `tripwright_researcher` for step 5 when you can run subagents”。
  - 没把顺序改成“先发第一段再派”：D-46 的预检里那样做 8 问都丢了第一段。
- **第二段怎样到用户手里。**
  - 选的做法：在 Claude Code 里，派后台 researcher 的，回报开新一轮；在 Codex 里，同一轮接着做。其他应用，以及不确定的，第一段作这一轮的完整回复，以 `first_part.later`（“要核过的第二段，回我一句“继续”。”）结束。用户下一句以 `points: []` 进 `turn`，结果带 `second_part_due`，宿主照第 5—8 步做，`check_answer` 用那一问的 `request_id`。
  - 服务端认“欠着”的办法：上一问的上下文记了 `request_id`、第一段说了有第二段（`promises_second`），而答案文件（`check_answer` 过核时写、Stop 钩子读的那份）里没有这个 `request_id`。只在 6 小时内认。上一句也这样要过，还指那一问。一句点名了新事（给了要点），就当新问题，不把旧的第二段塞进来。
  - 没用 `clientInfo` 判应用，原因三条：
    - 远程 HTTP 服务是 stateless，SDK 2.3 在旧协议下给工具调用建连接时 `client_info` 是 None（`streamable_http_manager._handle_stateless_request` 调 `Connection.from_envelope(pv, None, None)`），用户手机上的 Claude Code 连的正是这条路；
    - Cowork 和 `claude -p` 也报 `claude-code`，同一个名字背后显示行为不同（Claude 5 模型在工具调用之间的文字可能成为看不到的进度思考块）；
    - 读到它要改 `adapters/mcp.py` 的 turn 签名，超出本包写域。
  - 子代理调研查到的名字：Claude Code `claude-code`，Codex `codex-mcp-client`，Claude 网页与 Cowork 远程连接器 `Anthropic/ClaudeAI`（社区日志），豆包工作没查到。
  - 代价：
    - 豆包工作这类应用里，第二段要等用户回一句，核查在下一轮才开始，用户再等一次核查的时间；
    - 宿主对“自己是不是 Claude Code、Codex”的判断不由服务端核对；
    - 一句“继续”之外又问新事时，旧的第二段不再发。
- **停点。** 只在三处：带 `accept_unverified` 的提交、改用户定为必须的事、发给用户以外的人的东西。其余步骤写明“不用问，接着做”（第 4 条规矩）。
- **没有 MCP 时第一段前的状态句（第 5 条）。**
  - 查法：子代理逐轮读了 a17b、a16、a17 的 off 轮流水（含思考与钩子消息）；我抽查了 a17b_off_r1 第一问。
  - 结果：12 轮里 7 轮有，共 9 句，都在第一段之前；on 轮没有。
  - 9 句里 6 句在同一个位置：后台派出 researcher 之后、第一段之前。
  - 每一轮在发第一段前，都在工具结果里见过第一段的原文成行出现：命令行把它印在 stderr，宿主又常加 `2>&1`。a17b_off_r2 那一轮是宿主自己用 python 印出来的，因为上一问 `2>&1 | json.load` 读坏了。
  - 有两句直说“第一段已经发给你了（上面那段）”“第一段发完了”。
  - 4 句前面紧挨一次 PreToolUse 拒绝，其中两次拒的是 `tripwright … query --help`。拒绝理由的“核查请在后台派 researcher”让它先派，派完写状态句。
  - on 轮的第一段只在 `turn` 的 JSON 里，6 问没有一句状态句。
  - 其余 3 句：A16 的 points 错误没给例子（A17b 已加例子）；A17 的别名查询；A17 的一次核查重试。
  - 来源上的改法：
    - 命令行 `first-part` 只出 JSON（`cli.run_first_part`）；
    - 钩子按分段判查询，带 `--help`、`-h` 的那段不算，经别名的 `query <种类>` 算（`render.check_answer.lookup_command`）。
  - 没改说明措辞，也没改 D-46 的顺序。效果要主会话重放才知道。
- **测试。**
  - `tests/agents/test_s4a_core.py`：
    - `test_server_instructions_hold_every_step_and_its_check_within_the_limit`
    - `test_the_build_refuses_instructions_over_the_limit_and_a_step_without_its_check`
    - `test_the_four_places_carry_the_same_steps`
    - `test_a_changed_step_reaches_every_place_only_through_the_build`
    - `test_codex_is_told_to_spawn_the_researcher_and_no_entry_must_have_subagents`
    - `test_the_main_skill_did_not_grow`
    - `test_sources_load_the_core_from_the_checkout`
  - `tests/core/test_s4a_delivery.py`：
    - `test_a_reply_that_ends_with_the_first_part_gets_the_second_part_on_the_next_message`
    - `test_only_a_message_that_names_nothing_asks_for_the_latest_second_part`
    - `test_one_question_goes_through_from_the_instructions_and_the_tool_results_alone`
    - `test_the_command_line_first_part_carries_the_steps_and_the_line_that_ends_a_reply`
    - `test_the_command_line_prints_the_first_part_only_inside_its_json`
    - `test_reading_the_command_line_help_is_not_a_lookup`
  - 改了的旧测试：
    - `test_agents_prompts`：instructions 的开头；
    - `test_agents_templates`：改看 init 写出的 AGENTS.md，首行带 core.yaml；
    - `test_assemble`：`Sources` 用 `replace` 造；a9 那条不再核 instructions；
    - `test_a10_frame`：`next` 第一句的尾巴改成“再照 steps 往下做”；
    - `tests/agents/conftest.py`：throwaway checkout 也拷 core.yaml 和 templates。
- **主 SKILL。** 12,130 → 12,119 字节。核心步骤换掉了原来的“Every message”和“Check before the second part”两节；“The answer”里与步骤重复的几句收短。

## s4c：分享给朋友的去隐私公开页（D-82、D-83、D-89）

命令 `tripwright --repo <行程仓> share build|publish`（`site/share.py` 出页，`site/publish.py` 推送）。下面是现行的定法。

- **出什么。**
  - 只出首页和每天一页，来源是**已提交的计划**：有远端读 `origin/<branch>`，没有读本地分支，与服务的 `effective` 一致。还没推到远端的本地提交不出页。
  - 每天一页写：日期与星期、标题、区域、日落、过夜处（住处只写城区）、要准时的时刻和说明、当天说明、每套安排（第一套是推荐）。
  - 每段写：时间、类型、标题、内容、“怎么走”（`mode`／`line`／步行分钟）、备注、当地名＋地图链接、备选地点；另有按时段的候选地点和下雨的安排。
  - 不写：出处、把握度、核验、营业时间、价格字段、地点的 reason／notes、App 提示；决定、记忆、原话、证据也不读。
- **代号。**
  - 所有人编成 Traveler A–Z／旅伴 A–Z。顺序是 `trip.yaml` 的 `people`，再接 `profile/people.yaml` 里其余的人。
  - 用户本人也编代号：记录里没有字段标明哪一个是用户本人。
  - 名字本身是代称、后面带编号的（“队友 1”“同行人 A”）只替换，不当私密词挡。单独一个代词（“我”）不动。
- **住处。**
  - 怎样认：地点 `kind` 属于 hotel、hostel、apartment、airbnb、guesthouse、pension、lodging、accommodation、stay、home、住宿、住处、酒店、青旅、公寓、民宿、家（不分大小写）。
  - 写成“住在 <城区>（酒店）／Staying in <district> (hotel)”，没有地图链接。
  - 城区按序取：地点的 `district`；当地名里“邮编＋城镇”那一段（如 1070 Wien）；`area`。括号里的类型按 kind 写，默认“酒店”。
  - 当私密词的有：名字、当地名、地址前两段（名字、街道门牌）；名字里长度至少 4 的拉丁字母词，但泛称（Hotel、Hostel、Haus 等）和在别的公开地点、区域名里出现过的除外；小数至少 4 位的坐标。
  - 城区、城市、国家不算私密。
- **航班号。**
  - 规则：两位航司代码＋1—4 位数字，中文改成“航班”，英文改成“flight”。
  - 不算航班号的：三个字母开头的（REX、RJX、VAB）；字母加数字开头、后面只跟一位数字、中间又没有空格的（N25、S45）；时刻、小数；数字后跟单位的（站、路、楼、min、′ 等）；列车类别和常见缩写（IC、EC、RJ、RE、EN、NJ、EG、OG、UG、XL 等）。
  - flight 类 booking 原文里出现的号码一律算航班号。
- **电话。**
  - 算电话的：带 + 或 00 的国际号码，至少 8 位数字；0 开头、中间有分隔的本地号码，至少 7 位数字；中国手机号。
  - 记在非住宿地点上的号码是商家公开电话，放行；住处的号码照挡。
- **订单。**
  - 原有规则之外，认这些标签：Buchungsnummer、confirmation/reservation/order/ticket number、订单编号、取票码、票号等。
  - booking 里键名像订单号的字段（reference、confirmation、pnr、order、voucher 等），值带数字、长度至少 5 的，原值当私密词。
- **英文。**
  - 程序生成的字是双语的。用户写的中文从 `<行程仓>/.tripwright/share/en.yaml` 取英文（Git 忽略）。它的键是已换好代号的中文，所以文件里不会有真名。
  - `share build --todo` 把还没英文的句子加进去、值留空，agent 填好再出一次。
  - 没有英文的照原文显示，标“(中文)”。
  - 页面默认英文（`<html lang="en">`），右上角 EN／中文 切换，也认 `?lang=`。
- **扫描与换上。**
  - 先写到输出目录旁的临时目录，扫描过了再用一次 rename 整体换上。扫不过就删掉临时目录，原输出不动。
  - 已有目录不是分享页（没有 `.tripwright-share` 标记）时拒绝覆盖。
  - 扫描逐个文件，先解 HTML 实体与 URL 编码再扫。类别：email、order-like identifier、flight number、phone number、person name、lodging、private word。只报“类别 → 页名”，不报原值。
- **发布。**
  - 第一次必须带 `--create`。依次做：`gh repo create <名> --public`、克隆、提交并推送、`gh api -X POST repos/<o>/<r>/pages`（legacy，main，`/`），再读 `html_url`。
  - 之后每次：fetch；把副本整个换成网页；再扫一遍副本；提交、推送。内容没变就不提交。
  - 第二道扫描不过时，先把副本 reset、clean，再报错，线上保持上一版。
  - 提交者固定为 `tripwright <share@tripwright.invalid>`，日期用 UTC。
  - 仓名：没给 `--name` 时用 `trip-<趟 id>`。`--private` 只在付费计划可用（网页本身仍公开）。
  - 公开仓里只有几页 HTML、`.nojekyll`、`robots.txt`（Disallow）、`.tripwright-share` 标记；每页加 `noindex`。
- **退出码与提醒。**
  - 退出码：0 成功，1 出错，2 扫描没过、没推。
  - 被挡时在行程仓索引留一条 `share_blocked` notice（只写类别和页名），下一次 `turn` 带出；下一次发布成功时关掉（CONTRACTS 3.9）。
- **本机状态。** 都在 `<行程仓>/.tripwright/share/`：
  - `config.json`：仓名与网址，有它就表示这趟开了分享页；
  - `site/`：最近一次干净的出页；
  - `public/`：推送用的副本；
  - `last.json`：最近一次结果，只记类别；
  - `en.yaml`。
- **谁来触发。**
  - 提交生效后由主 agent 跑 `share publish`：主角色第 6 步，S4a2。
  - 服务端不推公开仓：它没有那个仓的凭据。远程部署时 `share_blocked` 只在跑命令的那台机器的索引里。

## S4a2：分享页接进引导与约定，later 一行过核（2026-11-03）

- **提交后重出分享页（D-89）。**
  - 写在第 6 步“Update the site”的 With tools 里，进 skill 的 `references/single-agent.md` 和 MCP prompt；主 SKILL 读表那一行加“or the share page after a commit”，instructions 的读那句加“the share page after a commit”。
  - 内容：
    - 行程仓有 `.tripwright/share/config.json` 时，写入 `effective` 后跑 `tripwright share publish`。
    - 退出 2 是扫描没过、没推，下一次 `turn` 带 `share_blocked`；照类别经 `propose`、`commit` 改记录，不把原值写进回答或记录，再发布一次。
    - 第一次建公开仓（`--create`）是对外发布，要先停下问用户：说清 GitHub Free 只能从公开仓发 Pages、仓里只有网页、仓名是什么，用户同意再建。
  - 核心步骤不加步。停点那条规矩从“anything sent to someone other than the user”改成“anything sent or published to others”，公开网页也算在内，不点具体命令。
  - 承诺 `main-agent-never-runs-the-command-line` 放行 `share publish`：它在提交之后跑，不查任何东西。新承诺 `share-page-after-commit` 核第 6 步的这几句。
- **接缝约定。** `share_blocked` 写进 CONTRACTS 3.9 和 schema（`$defs/share_blocked_notice`；`$defs/notice` 成为冲突通知与它二选一）。
- **later 一行。**
  - 第一段以 `first_part.later` 结尾时，`check_answer` 不把那一行算正文，Stop 钩子让这一轮结束（原来按 D-48 推第二段）。
  - `later` 之后还有字报 `ends_with_later`。第一段里的状态句报 `no_status_line`，规则见 `check_answer.STATUS_LINE`，服务端自己的“没核：……”行除外。Stop 钩子遇到这两种情况拦下。
  - `first_part.LATER` 是这一行的唯一出处，服务端和检查共用。
- **测试。**
  - `tests/agents/test_s4a2_share_guidance.py::test_after_a_commit_the_share_page_is_published_and_the_first_public_repository_waits_for_the_user`
  - `tests/site/test_s4a2_share_notice.py::test_a_blocked_share_page_leaves_a_contract_notice_without_values_until_a_clean_publish`
  - `tests/core/test_s4a2_later.py::test_a_first_part_may_end_the_reply_with_later_and_nothing_after_it`
  - 第 4 条的坏样本与对照都在最后这一条里。
  - 改了的旧测试：`test_a14_fixes` 的状态句坏样本现在也报 `no_status_line`。
- **主 SKILL。** 12,119 → 12,108 字节：“写法（对话里的回答）：”那一行标签删了，读表多半行。
## s4b：工具注解、交通查询跨进程共享、check 与 status 只读（2026-11-03）

依据：主会话 s4b 任务包第 1—3 条。

- **工具注解按实情标（`adapters/mcp.py`）。** ChatGPT 按 `readOnlyHint` 判断读写，没有这个注解的算写，Pro 只能用读取类工具。
  - 逐个工具在带远端的行程仓上调用，比较调用前后的索引库、工作区、HEAD 和远端 main，结果是：只有 `state`（各个 view）和 `explain` 什么都不改。
  - `query` 原来标成只读，但它的大多数 kind 都写：`transit`（含 `stops`）、`latest_departure`、`weather`、`geocode`、`nearby`、`saved`、`web_evidence` 每次都新记一条证据，提交并推送；`plan_day` 排一个作业；`check_answer` 的第二段通过时写本机的 `.tripwright/answers.json`（不提交）。只有 `opening`、`open_places` 和第一段的 `check_answer` 什么都不写。
  - 改：`query` 标为写（`readOnlyHint: false`、`destructiveHint: false`、`idempotentHint: false`、`openWorldHint: true`）；`commit` 换掉当前计划、`result` 的 cancel 停掉作业，这两个标 `destructiveHint: true`；`turn`、`propose` 不变。这一批不拆 `query`。
  - 代价：ChatGPT Pro 只剩 `state`、`explain`，不能查交通；Codex 对 `query` 要先批准（`codex exec` 不批准就拒，`default_tools_approval_mode = "approve"` 可放行）。
  - 测试：`tests/repo/test_s4b_tool_annotations.py`。
- **交通查询跨进程共享（`providers/common.py` 的 `SharedLookups`，`providers/transitous.py`）。**
  - 来源：11-03 02:1x 四轮自重放并行，每轮一个本地 stdio 服务，同样的查询各发一遍。本机共享的 720 次/24 小时计数用满（`ROUTING_BUDGET_EXCEEDED`），其余排队超时。
  - 改：上游的 JSON 回答按请求（路径加参数）存进共享目录（`TRIPWRIGHT_TRANSITOUS_SHARED`，默认在计数文件旁的 `transitous-shared/`），在有效期内各进程直接用。
    - 同一请求只有一个进程去取，其余等它的回答。
    - 所有进程的请求间隔至少 0.5 秒，同时最多 2 个。
    - 429 和 502—504 先停 1 秒、3 秒再试（`Retry-After` 不超过 10 秒时按它），每次重试都计入 720。
    - 锁都是不阻塞地取、取不到就短睡再试，被取消的查询不会留下锁；目录写不了就关掉共享，查询照常。
  - 有效期沿用各查询原来的内存缓存：路线 30 分钟，整天时刻表 6 小时，站名 7 天，步行时间 30 天。用的回答保留原来的抓取时刻（`fetched_at`、`valid_until` 按它算），不当成新的。`fresh` 的查询（存的计划事实复用前重查，D-57）只认这次查询开始之后取到的回答。
  - 测试：`tests/providers/test_s4b_shared_lookups.py`。
- **`check`、`status` 只读（`cli.read_repository`、`committer.read_status`、`Store(read_only=True)`）。**
  - 来源：命令行打开行程仓时先 `attach`：索引不在 HEAD 时重建，并把 HEAD 上比远端多出的带 `Tripwright-Op` 的提交恢复成待推的写入；命令结束时 `committer.stop()` 收手改、提交并推一次。11-03 08:08 在 my-travel 副本上跑 `check`，就这样把一个还没核完的合并提交推上了远端 main。远端已经前移时这次推送被拒，那条路不再重试。
  - 改：`check` 只用 `Repository` 读提交；`status` 以只读方式打开索引库，不重建、不 fetch、不 push、不提交，输出补上 `fetched: false`、`index_head`、`index_at_head` 和一句说明。同步只在 `sync` 和写入时做。
  - 测试：`tests/repo/test_s4b_check_reads_only.py`。

## S4e：Codex 也在下一轮出第二段，later 一行认字不认引号（2026-11-03，主会话；任务 D-93）

- **为什么：** 合并版（8dda6d99）上两轮合成数据干跑。Codex（gpt-6-luna）发完第一段就停、没出第二段，第一段前还多一句开场话：D-88 的回头条件。Claude Code 不给子代理那轮把 later 一行抄成英文引号，`check_answer`、Stop 钩子与重放脚本按原句逐字比，没认出，重放就没补发“继续”。
- **改了：**
  - `agents/core.yaml` 第 4 步：没有后台子代理的应用（含 Codex）都以 `first_part.later` 结束这一轮，下一句出第二段；第 1 步加“写任何文字之前先调 `turn`”。生成文件随之重出（instructions、steps、主 SKILL、AGENTS.init）。
  - `render/check_answer.py`：`is_later` 按字比，引号、空格与句末标点不算；四处原来的 `in LATER_LINES` 都换成它。测试 `tests/core/test_s4a2_later.py` 加一条：英文引号、直角引号、去掉句号都算结束，后面跟状态句照旧拦。
  - 钩子：用户回“继续”的那条消息算同一问的后半（`answers_later`：上一条真消息之后的最后一段文字以 later 一行结束）。first-part 钩子放行这时的查询；Stop 钩子把第一段当已发，没出第二段就催第二段。来源：answer-v4 上 Claude Code 不给子代理的干跑，“继续”之后 first-part 钩子拦了查询，宿主写了一段解释又重发第一段。测试同一文件加一条，带对照：上一段没以 later 结束时，新消息的查询照旧等第一段。
- **没改：** 第一段的内容与 `first_part.text`、`second_part_due` 的判法。

## S4h-a：服务端核这一轮的参数与形状（2026-11-03，阶段 4 出口判不合格，任务 D-96）

依据：阶段 4 出口判词（6 轮重放，0 轮合格；共同前提 A：宿主没照指令做，check_answer 照样放行；前提 B：“继续”那一轮不带原问题的 points）与 codex_r1、ns_on_r1、mcponly_r1、ns_off_r1 的工具记录。做法：错的参数在服务端拒收或认对，不再只靠 `next` 里的说明。每条有重放形状的坏样本与对照（`tests/core/test_s4h_server.py`）；源码退回基线（9eb3334）后 8 条测试都在坏样本处不过。

- **一、quick 场景问最晚时，查询不收硬节点（`Tripwright._keep_frame`）。**
  - 来源：codex_r1 第 1 问。`turn` 的 `next` 写了“不给 anchor_id、by、board_at”，宿主第一次照做，第二次自己加了计划目标 05:45 排队的 `anchor_id`，查成明早的框；第二段主选改成“明早 04:56 出门”，丢了存的“机场过夜”。
  - 改：`turn` 的上下文记 `frame {scenario, asks_latest}`。最近一次 `turn`（30 分钟内，“继续”那一轮带原问题的）是 quick 或 status、`first_part.asks_latest` 为真时，`latest_departure` 带 `anchor_id` 拒收；`by`、`board_at` 的时刻正好是某个硬节点当天的时刻，也拒收（同一个硬节点换个字段）。报错写明“只给 origin 和 destination”，结果里有今晚最后一班和之后要守的时间（`hard_node`）。
  - 为什么拒收、不忽略：约定里错的参数一律“报错并给改法”（A9 上车时刻当 `by`、A11 网上做的锚点）；照服务的框静默改查，宿主拿到的不是它要的，又不一定读说明。拒收不花查询额度。
  - 放行：用户自己说的时刻（不是任何硬节点的）照常按 `by` 查；plan 等其他场景、`turn` 30 分钟之后照常。
- **二、`turn` 的 `place` 和 `said` 对不上时拒收；认不出就明说（`packs.place_conflicts`、`packs.aliased`、`Tripwright._checked_places`、`_unknown_places`）。**
  - 来源一：ns_on_r1 第 2 问。用户说“Block Factory 还是什么的那个”，宿主把 at 的 place 填成计划里的另一家岩馆，第一段写“你在 <计划的岩馆>（你说的“…Block Factory…”）”，末班也从那里算。
  - 改：at、go、do 要点的 place 认出一处存的地点，said 按同样的规则（包含存的名字、拉丁字母相似度、下面的类别词）认出另一处时，`turn` 在收件之前拒收：`Invalid points: points.N: place … is the stored …, but its said “…” names the stored …: send place …`。什么都没记，同一个 `request_id` 可以再发。命令行 `first-part` 同样拒（退出 1）。said 认不出、认出两处或认出同一处，都不算冲突。
  - 来源二：mcponly_r1 第 1 问。宿主把 go 的 place 填成英文 “airport”，行程里存的是“维也纳机场 / Flughafen Wien”，什么都没认出，第一段只有“几点出发要查，第二段给”，没给计划里的车。
  - 改：`resolve_place` 最后按类别词认（`PLACE_WORDS`：airport、机场、Flughafen、aéroport…；station、火车站、Hauptbahnhof…）：先取 kind 就是这一类的唯一一处，否则取短名（中文名“（”前、原文名地址前）里有这一类词的唯一一处。短名不含括号里的说明和地址，所以“汉堡王（机场到达大厅）”不算机场。两处都像就不认。
  - 还认不出（不是存的地点，也不是坐标）：要点记 `unknown`（at 记在 `points.at.unknown`），`next` 开头写一句：哪些没认出、第一段没给它们存的车和时刻、也没拿计划里的地点顶替，存的地点 ID（前 8 个）可以改填，用同一个 `request_id` 再调。不拒收：用户可以在任何没存的地方。
- **三、“继续”那一轮带上原问题（`Tripwright._unanswered`、`_second_part_due`）。**
  - 来源：mcponly_r1 第 2 问。“继续”的 `turn` 回了 `second_part_due`，之后 `latest_departure` 仍从岩馆算，没按原问题的下一步（先去车站吃）。没走“继续”的 sub_on、codex 起点对了。
  - 真因：`packs.next_step` 取最近一份带 `points` 的上下文。“继续”那一轮的上下文存的是 `point_record(None)`（`given: false`，没有 `at`、`next`），比原问题的新，`next_step` 就取了它，什么都不改。
  - 改：带 `second_part_due` 的那一轮，上下文的 `points` 和 `frame` 照抄原问题的；`second_part_due` 的判法不变。之后的查询从原问题的下一步算，第一条的框也照原问题。再回一句“继续”还指同一问，也带它。
  - 顺带：同一时刻写的两份上下文，`next_step` 和 `_keep_frame` 取后写的那份（原来 `max` 取先写的；冻住钟的测试里会碰上）。
- **四、存的关门时刻可以写进第二段（`render.check_answer.stored_times`；`query.LAST_SAY` 的 closing）。**
  - 来源：判词“跨轮的毛病”第二点。第 1 问各轮把服务的“22:00 关门出来，走到 … 坐 22:16 的 5B”写进标“核过”的行，`checked_has_evidence` 以“时刻 22:00 不在这一趟工具查到的结果里”拦下，修法是“改成网上说法”；宿主删了时刻，“最晚几点出发”只剩车站上车时刻。
  - 查清：拦的是 `evidence_problems`。22:00 来自起点存的 `opening_hours`（`origin_closes`），不在任何查询结果里；服务自己的句子没说它是存的。
  - 改：服务句子写“22:00 关门出来（按存的营业时间）”。`checked_has_evidence` 不把“自己带标的时刻”算作核过的说法：时刻后面、下一个时刻或分句结束之前（括号里的标注到右括号）有“存的、存档、没核”之一（英文 stored、archived、not checked），这个时刻不要证据。没带标、又正好是这一问查询起点存的关门时刻（`frame.closes`），照样拦，修法改成“照 latest_departure 的句子写“22:00 关门出来（按存的营业时间）”，时刻留着”。`leave_before_closing` 的修法同步加标。
  - 没带“没核”：起点的营业时间可能有网页出处（`hours_source`），这次查询没核它，存的是实情；“存的”已经是标。
- **五、第一段“（20:51 查过）”早于 21:01 的问（`providers/common.cached`、`storage.pinned_since`）。**
  - 真因（查实）：不是 UTC 显示。ns_on_r1 第 1 问的查询在服务钟 21:04:45—21:05 做，证据 `fetched_at` 是 19:51:35Z，即 20:51 CET，比查询早 14 分钟；按 UTC 显示会是 20:05。差的 14 分钟正好是墙钟上 sub_on_r1 做同一查询（13:33:36Z）到 ns_on 做它（13:47:15Z）的间隔。s4b 的共享答案按墙钟上的年龄倒推抓取时刻（`fetched = 服务钟 − (墙钟现在 − 墙钟抓取)`）；重放把每一轮的服务钟钉在 21:01，别的轮在更早的墙钟时刻抓的答案，就被放到这一轮第一问之前。mcponly 的 20:35（早约 27 分钟）、ns_off 的 20:46（早约 19 分钟）同理，往回推都落在墙钟上 ns_on 第 1 问发查询的 13:47Z。
  - 改：钉住的钟只从钉住那一刻起和墙钟同步（`storage.pinned_since`：`TRIPWRIGHT_NOW_SET_AT`，否则进程第一次读钟的墙钟时刻）。钉住之前抓的共享答案，按这次查询的时刻算；钉住之后抓的照 s4b 按年龄倒推。没钉钟时不变：别的进程 10 分钟前抓的答案，照旧说 10 分钟前，`valid_until` 也照旧。
  - 为什么不改显示：没钉钟时，显示的就是数据真实的抓取时刻，可以早于这一问（同一台机器上别的会话 30 分钟内查过同一程），这是实情。错只在重放：别的轮在另一条钉住的时间线上抓的，放到这一轮里没有意义。
- **测试。** `tests/core/test_s4h_server.py`：
  - `test_a_quick_latest_question_refuses_a_lookup_that_names_a_hard_node`
  - `test_a_place_the_users_words_contradict_is_refused_before_anything_is_recorded`
  - `test_a_word_for_a_kind_of_place_names_the_one_stored_place_of_that_kind`
  - `test_a_place_not_recognized_is_said_and_never_taken_for_the_plans`
  - `test_the_lookups_after_go_on_start_where_the_continued_question_goes_next`
  - `test_the_stored_closing_time_may_stand_on_a_checked_line_when_it_says_so`
  - `test_an_answer_another_replay_fetched_earlier_is_not_dated_before_this_replays_question`
  - `test_on_a_pinned_clock_an_answer_fetched_after_the_pin_keeps_its_age`
  - 改了的旧测试：
    - `test_a16_fixes` 第 3 条：关门句与 `leave_before_closing` 的修法加“（按存的营业时间）”；
    - `test_a12_first_part` 的 a12b 一条：原话里有“机场”时，现在按类别词认出存的机场（不拿原话当地名的保证不变）；“几处都像或都不像就不写地名”改用不含类别词的原话核，另加一条“place 填‘维也纳’、原话说机场”认出机场。
- **限制。**
  - 第一条只看最近一次 `turn`：宿主不调 `turn` 就查，判不出。用户自己说的时刻正好是某个硬节点的时刻时，`by` 也被拒；这时服务的框已经给了它（`hard_node` 或“你计划留的余量”那行）。
  - 第一条没做判词机制补充 1 的另一半（第二段主选的车不在今晚的框里、计划存着机场过夜时报出）：框错的查询拒收之后，第二段拿不到明早的框。
  - 第二条的冲突只认得出“said 里像另一处存的地点”。said 什么地点都没说、宿主填了计划里的地点（假设用户在那），服务端分不出。
  - 类别词只有机场和火车站两类；地铁站、公交站名太多，不收。
  - 第四条只放行带标的存的时刻；宿主把存的时刻写成“核过”而不带标，照旧拦。

## S4h-b：命令行少拦人、分享页住处段只说先后、工具连得慢时先等（2026-11-03，主会话 191cd345）

依据：阶段 4 出口判词（第 9 次质量判）前提 C、机制补充 4 与 6、出口第 17 条“几点到住处”段；坏样本 runs/s4_ns_off_r1。S4h-a 同步改服务端文件（service.py、query.py、render/），互不相碰。

- **命令行少拦人（`cli.py`）。**
  - `first-part --points` 收宿主自己写的 kind 近义词（S4h-b：deadline、route、time、latest_departure、transit、departure → go；food、meal → eat；event、activity → do；place、where → at；大小写与空白照收，`place: ""` 当没给），stderr 出一行“read kind 'x' as 'y'”说明收成了什么；stdout 仍只有 JSON（S4a 不变）。认不出的 kind 仍退出 1，报错里除原有示例外，再给一条改好的完整命令（`try: tripwright [--db|--repo] first-part --text … --points '[…]'`，未知 kind 换成 other、空 place 去掉），照粘贴即可运行。坏样本：off_r1 第一问宿主填 departure/route/time，连报三次错、第一段拖晚。
  - `query check_answer` 没带 `--request-id`（也没带 `--question`）时，CLI 取同一会话最近一次 turn 的 request_id（事件表里最近收到的非 system 消息；与服务的 30 分钟回退同一“同时刻取后录”规则），stderr 写明用的哪一条与收到时间；一条 turn 都没有时报错退出 1，报错里给带 `--request-id` 的完整命令例子（并提 `--question`）。给了 `--question` 时不自动取（那是 D-48 设计的“turn 没记下时给原话”路径）。坏样本：off_r1 两问的 check_answer 没带 request_id，回“本问原话不明，吃的检查没做”照旧放行，过的不是这一问的检查。
  - 测试：`tests/core/test_s4hb_cli.py`（16 例，改前全红）；`test_a17_fixes` 一行随 read_points 新签名（返回 (points, 说明)）更新。
- **分享页构建不出住处进出的时刻（`site/privacy.py`、`site/share.py`、`site/templates.py`）。**
  - 规则：时间线里起点或终点是住处的那一段，不出分钟级时刻（HH:MM，含全角冒号）、步行分钟、到站时刻，也不出 “from your door”“N minute walk”“门口/楼下” 这类和住处连用的说法；只说先后（“回住处 / Back to the lodging”+线路）。公共地点之间的时刻照留。
  - 判“这段贴着住处”：块的 place 是住处；或块是无 place 的移动块（或 kind travel），而相邻块的 place 是住处、或它自己的 text/mode/note 提到住处（重写后的“住处/our stay”）、或它是当日基于住处的第一个移动块。带公共 place 的块（参观、吃饭）不算，即使它的 note 拿住处当地标。
  - 实现：`privacy.py` 加 `MINUTE_TIME`、`WALK_MINUTES`、`AT_THE_DOOR`、`STAY_WORDS` 与 `stay_segment_dirty`（中英一起查；提到住处的字句再带裸“N 分钟”也算）；`share.py` 的 `block_view` 对这种段把 start/end 置空、walk_minutes 不出、text/mode/note/title 里有这些内容的整句不出现（两语言任一脏即整句不出）、mode 被丢弃时线路（line）仍保留、标题退到“回住处/Back to the lodging”或“从住处出发/From the lodging”；模板的时间跨越加空守卫。
  - 测试：`tests/site/test_share.py` 用合成计划（住处→某站→景点、景点→住处）核中英两种输出（改前红），加一条对照（干净的“回 住处 放行李”照旧可见）。
- **工具连得慢时不转命令行（`agents/core.yaml` 第 1 步）。**
  - 第 1 步末尾加一句：“While Tripwright's tools are still connecting, retry every few seconds (up to about 30) before using the command line; never write about connections.”。生成物重出（instructions 3,585/3,600 字节、steps.json、主 SKILL、MCP prompt、AGENTS.init），`build --check` 过。主 SKILL 的字节钉因这句从 12,171/12,130/12,119 提到 12,225（三处守护测试各注一句 S4h-b 后改钉）。
  - 测试：`tests/agents/test_s4hb_core.py`（2 例，改前红）。
- **契约：** `docs/CONTRACTS.md` 3 节加一组 S4h-b（命令行两条与分享页一条；无形状变化，机读件与样本不动）。

## S4h2：第 1 问两段走法对上；“按时刻表”一句由检查要求（2026-11-03，S4h 自重放，任务 D-97）

依据：主会话 S4h 自重放（`evidence/s4h_selfreplay_20261103.md`）“还在出现的四类”第 2、3 类；坏样本 `s4h_mcponly_r1/turn1.reply.md`（sub_on、ns_on、维也纳 codex 同形）与 `s4h_ns_off_r1/turn1.reply.md`、`turn2.reply.md`。每条有坏样本与对照（`tests/core/test_s4h2_server.py`）；源码退回 3f24c99 后 3 条都在坏样本处不过。

- **一、计划那班车的句子说计划的走法还用不用（`query.plan_rides`、`planned_leave`；`LAST_SAY` 的 `plan_way`、`plan_way_missing`）。**
  - 来源：第一段照计划写“21:45 从岩馆出门，22:13 到总站，坐 22:42 RJX”（`packs.planned_way`，存档没核）；第二段的主选是 `latest_departure` 的“计划里的 22:42 RJX 19959 赶得上：22:00 关门出来（按存的营业时间），走到 Brigittaplatz 坐 22:16 的 5B”。两条都赶得上，可句子没交代第一段那条。两边各取各的：第一段取计划存的交通块，第二段取查到的最晚一条。
  - 改：`plan_rides` 拿到查询起点。计划里有从这个起点到那班车上车站、当天的交通块（取最晚开始的那块），它的出门时刻还没过、又和查到的最晚出门差 5 分钟以上时，句子后面接一句：
    - 查到的连接里有在它前后 5 分钟出门、也坐这班车的：“；计划的 21:45 出门那条（存的走法）也赶得上，想早走就照它”；
    - 没有：“；计划的 21:45 出门那条（存的走法）这次没在结果里，照这条走”。
  - 为什么接在这句上：这句就是第二段的主选句，宿主照抄；关系写在同一句里，宿主删不掉一半。主选仍是最晚那条，用户问的是“最晚几点出发”。
  - 为什么标“存的走法”：21:45 是计划存的出门时刻，查到的连接出门时刻可能差一两分钟；带标后，宿主把这句放进标“核过”的行，`checked_has_evidence` 不要求 21:45 有证据（S4h-a 的 `stored_times`）。
- **二、`check_answer` 第二段加 `first_times_followed`（`render.check_answer.followed_problems`；`first_line_times` 从回放检查的 `first_line_problems` 抽出共用）。**
  - 带 `request_id` 时，取那一问 `turn` 存的第一段原文（`first_text`；“继续”那一轮取被续那一问的）。第一行里第一个时刻（不算钟点和“HH:MM 查过”）是这一段要用户照着做的时刻，比如出门、上车。第二段既没再写它，也没有一行说改了（“第一段”“改成”“换成”“改为”），就报，修法给“照 latest_departure 的句子写第一段那条还用不用”或“和第一段不同：…”。
  - 没有那一问的第一段（不带 `request_id`、只走命令行 `first-part`）时不核。
  - 为什么只看第一个时刻、不照回放检查要每个时刻都再写：回放检查的那一项在自重放里误报和真错混在一起（自核记录“没核到的”）；到站时刻 22:13 不再写不算错，出门时刻没交代才是两套走法。
- **三、`check_answer` 第二段加 `timetable_said`（`timetable_problems`）。**
  - 来源：只走命令行的一轮，宿主自己写第二段，没写“按时刻表，不是实时”，`check_answer --question` 照样放行；走 MCP 的几轮是照抄了服务的 `note` 才有。
  - 改：第二段有一行（不是标题）带交通词、又写了这一趟查询（`transit`、`latest_departure`）里的时刻，而全文没有“按时刻表”“不是实时”“非实时”“是实时的”（英文 timetable, not real time、not live 一类）时，报出，修法给 `note` 的原句。MCP 与命令行走同一个 `check_answer`。
  - 只认这几种说法，不认单独的“实时”“时刻表”：服务的“怎么查”一句里就有“实时晚点、站台…时刻表里没有”“看实时时刻”，不能算说了。
- **测试。** `tests/core/test_s4h2_server.py`：
  - `test_the_plans_train_sentence_says_whether_the_plans_own_way_still_holds`
  - `test_a_second_part_that_drops_the_first_parts_time_without_saying_so_is_refused`
  - `test_a_second_part_with_looked_up_rides_says_they_are_the_timetables`
  - 改了的旧测试：
    - 第二段样本补上时刻表一句：`test_a10_frame.second_part`、`test_a8_transit_facts.second` 末尾加一行；`test_a6_check_answer.GOOD_SECOND` 和两处现写的样本（`test_a6` 的窗口一条、`test_a7_candidates_places` 的大巴一条）并进原有一行，不加行，免得改动后面行号；
    - 计划那班车的句子后面多了“；计划的 21:45 出门那条（存的走法）这次没在结果里，照这条走”：`test_a16_fixes` 第 3 条两处、`test_s4h_server` 第 4 条。这些行程的计划里有 21:45 从岩馆去总站的块，假数据里只有一条接驳。
- **限制。**
  - 第一条只在计划存了从查询起点出发的交通块时说；第一段若写的是别的（只有“计划里是今晚 22:42 在 X 坐 Y”），句子不变，第二条照样核第一个时刻。
  - 第二条只核第一行第一个时刻；宿主把它写进别的意思（例如“21:45 之前别走”）也算提到了。
  - 第三条不核说的是否和数据一致（数据是实时的却写“按时刻表”）。

## S4h3：命令行取最近一次 turn 只认最近 30 分钟（2026-11-03，主会话 191cd345）

依据：S4h2 自重放 s4h2_ns_off_r1。只走命令行那一轮，这一问没有 turn（宿主用的是命令行的 first-part，它不记），命令行的 `query check_answer` 不带 `--request-id`、`--question`，S4h-b 的“取最近一次 turn”取到了评测仓里 10-31 导入时的 turn，于是拿一个没人问过的问题核这份草稿：吃的检查没跑，宿主发出了没有具名吃饭地点的第二段。

- **改（`cli.py` 的 `latest_turn`）：** 只取最近 `RECENT_QUESTION_MINUTES`（30 分钟，服务自己的“最近一条用户消息”回退用的同一个窗口）内收到的非 system 消息；没有就照旧报错，报错里给带 `--request-id` 的命令与 `--question`。
- **测试：** `tests/core/test_s4hb_cli.py::test_check_answer_never_takes_a_turn_of_another_hour`（只有 21:01 的 seed turn、钟到 23:30：退出 1；旧代码上这条红）。

## S5：方法论 v0.5 三点（2026-11-03，worker s5-impl；主会话 191cd345 派）

依据：用户 Q42 的 R-147—R-149（`METHOD.md` v0.5 第 2.9、3.2、6.1 节）；现状见 s5 盘点（`gap-inventory.md`）。约定见 `docs/CONTRACTS.md` 3.10（v2.0）。

- **一、agent 的不取代用户的（`ledger.apply`；METHOD 2.9、4）。**
  - 来源：盘点 1.1 的探针。同 key、同人、同范围时，后到的 `agent_inference` 条目把用户明说的条目改成 `superseded`（`ledger.py` 原 1043—1046 行，取代时不看来源）。
  - 改：两层按 `source.basis` 分。用户层是 `user_explicit`、`direction_set`（`consider.USER_LAYER`），其余是 agent 层。同一对象（同 key 或声明、人与范围相交）上，agent 层的新条目不取代、不作例外、不使用户条目待定（旧条是 must 时也不开问题）。两条都 `active`，agent 那条记 `relations.conflicts_with_user`。同一条消息里的一对（同批，或先记 agent 的、后记用户的）同样处理。用户层的新条目照旧取代用户层与 agent 层的旧条目，“用户自己改口”不受影响。
  - 连带改：跨层的一对不再记 `possible_contradiction_with`。不改的话，agent 每发回一条推断，就和同一消息里用户的每条各生成一个“可能矛盾”的问题，进窄包的必需问题组，快问包更易超 1,800。
  - 不改：“定了基本不变”的种类照旧先走 `stable_refusal`（agent 推断碰上就进 `not_recorded`，问用户），比“并存”更严。实况（facts）的取代照旧不看来源：查询得到的新实况常该盖过用户早先说的，要分层得另定规则。
- **二、`consider` 每条标来源层，截断先留用户的（`consider.build`、`consider.layer`；METHOD 3.2）。**
  - 改：每条加 `layer` 短词：用户说的、我的推断、我的默认、我定的、计划里的。产品地区表的条目写“我的默认”。agent 条目带 `conflicts_with_user`。
  - 截断：必须项与用户层条目不计入条数上限、永不截；其余（agent 的、计划的、地区表的）按种类次序填到上限。这照 METHOD 3.2“用户层全量加这一范围最多若干条”。没取“按层排序、仍按上限硬截”，因为用户层自己超上限时，用户的偏好仍会因为种类次序排在用户规矩后面被截。
  - `next`：列出 agent 层条目时加一句“不说成用户要的”，有冲突时加一句“照用户说的”。`more_how` 指向 `explain(record_id)`。
  - 窄包与它的 1,800 预算不动：来源层只加在 `consider` 上，包的渲染没改。
- **三、按编号取单条（`service.explain`、`service._record_card`）。**
  - 改：扩 `explain`，不加工具，工具仍七个。`record_id` 是账本记录（条目、实况、决定、待核、待定问题）时，结果加 `record`：原文与引句、来源层与 basis、原话出处（`said`，取自 `packs.message`，长期条目导入的也有）、状态、被谁取代、取代链末端现在生效的那条（`current`）、关系、让给它的 agent 条目（`conflicted_by`）。
  - `version_usage` 不给 `version` 时只回最新一版。每版只带这一条的用法、那版约束包带没带它（`carried`）、点名它的变更行，不带整份约束包。不是账本记录的编号照旧。
  - 只经 MCP、不读仓文件，远程同样可用。
- **四、云端写回（`agents/core.yaml` 第 6 步、`service.turn`、`ledger.offered`、`usage.account_usages`、`packs.compile_pack`、`Store.attempt`；METHOD 6.1 第 3 步）。**
  - 第 6 步改为“用户这句定了或改了的，和你推断或选了的（basis `agent_inference`），放进同一份 `propose` 再 `commit`；两者之间不另记”。这句随生成物进 instructions、`steps`、主 SKILL 与 AGENTS.md。
  - 为了守住字节上限，同时删了别处的字：开头一句、“停下问用户”一句、读长规则一句的括注（保留“the share page after a commit”）。另外删了主 SKILL 读表两行与第 3 步注里“it reads the candidate itself, so”。
  - 字节数：instructions 3,586（≤3,600，`build --check` 过）；主 SKILL 12,217（≤12,225）。
  - `turn` 加 `write_back {say, record}`：一句提醒和要发回的原形（来源是本问事件号，basis `agent_inference`，`quote` 空）。不占 instructions 的字节。
  - agent 层的软条目（agent 层且 `prefer`／`optional`，`ledger.offered`）不是必需条目。计划提案的 `usages` 里可不给（给了照常核）；宽包标 `non_required`，不进 `dispositions`，宽包仍不因预算省记录。窄包里它们是第三种非必需记录（CONTRACTS 3.1），放不下时先整批走，再轮到用户条目缩成编号行；两条都是这种条目的可能矛盾问题同样非必需；它们不进偏好次序的配对。must、forbid 无论谁记都必需。
  - 为什么窄包也要改：写回的推断按范围进窄包。实测“用户 3 条偏好、agent 发回 2 条推断”的快问包：不改时 1,892 token，用户的条目只剩编号；改后 1,626，用户的原文都在。基线是 1,775，那时推断还取代了用户的“轻松一点”。
  - `turn.record` 出错不拒整条 `turn`：形状错、多给了 `decisions` 一类、账本拒，都照收原话、照出第一段。`record` 整份不记：新加的 `Store.attempt` 是存档点，回滚不留半条；`_record` 里原来那次嵌套写入也改用它。`recorded.not_recorded` 加 `{part: "record", records, reason, fix}`，`reason` 是原拒绝原文，`fix` 说改好后放进这一问的 `propose`；`next` 加一句。
- **测试。** `tests/core/test_s5_layers.py`（14 条；每条先在改前的代码上红，记录见 REPLY）。
- **改了的旧测试（都因第一、二、四条的语义变化，测试原意保留）：**
  - `test_ledger.py` 三条、`test_b3a_regressions.py::test_preview_does_not_mutate_relations_or_duplicate_supersedes`：取代、例外、撞 must 的新条目原来没填 basis（默认 `agent_inference`），而它们是用户的话。现在补上 `basis: user_explicit`。
  - `test_m1_consider.py`：“常变的被取代并说出来”原来用 agent 推断去取代用户的话。现在先断言推断不取代，再用用户的话取代。“长列表截到上限”改用 agent 的推断条目，因为用户的条目不再截。
  - 坏 `record` 原来整条拒 `turn`。下面这些现在断言原话已收，同一句拒绝原文出现在 `recorded.not_recorded[].reason`：`test_b3g_admission.py`（10 个参数）、`test_b3g_mcp.py`、`test_a5_cli_and_stdio.py`、`test_a5_latest_and_evidence.py`、`test_b3f_three_paths.py::test_reports_and_corrections_go_through_propose_not_turn`、`tests/repo/test_memory.py::test_the_users_answer_is_recorded_through_turn_and_a_not_used_entry_stops_applying`。
  - `tests/contracts/contract_checks.py::non_required` 加第三种：窄包里 agent 层的软条目。
- **限制。**
  - 没填 basis 的条目算 agent 层。宿主若把用户的话记成不填 basis，这些条目会被标“我的推断”，挡不住 agent 推断的覆盖，也不再是宽包必需。提醒写在 `write_back` 和 `turn` 的工具说明里，服务端不核。
  - 同一 `request_id` 不能带改好的 `record` 重发（会报复用），只能放进 `propose`。没有提案的快问，写回就多一次只改账本的 `propose`＋`commit`，在第一段之后、第二段之前。
  - 包里的“细节”行仍是行程仓路径（盘点 tw-6），云端读细节要靠 `explain(record_id)` 与 `state(view='day')`。

