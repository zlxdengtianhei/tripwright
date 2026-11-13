BASE = """<!doctype html>
<html lang="{{ 'zh-CN' if language == 'zh' else 'en' }}" data-language="{{ language }}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>{{ title }} · {{ trip.title }}</title>
<style>{{ style|safe }}</style>
</head>
<body>
<header class="top">
<span class="brand">{{ trip.title }}</span>
{% if not result_page %}
<button type="button" class="navtoggle" data-menu aria-expanded="false">{{ label('菜单','Menu') }}</button>
<nav>
<a href="index.html" data-language-link>{{ label('首页','Home') }}</a>
{% for item in days %}
<a href="{{ item.date }}.html" data-language-link>{{ item.date }}</a>
{% endfor %}
<a href="decisions.html" data-language-link>{{ label('决定','Decisions') }}</a>
{% if memory_sections is defined and memory_sections is not none %}<a href="memory.html" data-language-link>{{ label('记忆','Memory') }}</a>{% endif %}
</nav>
{% endif %}
<div class="language" role="group" aria-label="语言 / Language">
<button type="button" data-language-button="zh">中文</button>
<button type="button" data-language-button="en">EN</button>
</div>
</header>
<main>
<p class="meta">{{ label('版本','Version') }} {{ version.number }} · {{ label('生成于','Generated') }} {{ generated }} · {{ trip.timezone }}</p>
{% block main %}{% endblock %}
</main>
<footer>{{ label('此页展示已提交版本。未核与冲突请在行动前确认。','This page shows the committed version. Review unverified facts and conflicts before acting.') }}<br>{{ label('核验时刻','Checked at') }} {{ checked_at or label('尚无核验记录','No recorded checks') }}</footer>
<script>{{ script|safe }}</script>
</body>
</html>"""

MACROS = """
{% macro checks(items) %}{% for check in items %}
<div class="check {{ check.status }}">
<span class="badge {{ check.status }}">{{ status_label(check.status) }}</span> {{ check.text }}{% if check.status != 'passed' %}
<p>
<b>{{ label('后果','Consequence') }}:</b> {{ check.consequence or label('可行性尚未证实，可能无法按计划完成。','Feasibility remains unverified; the plan may not work.') }}</p>
<p>
<b>{{ label('替代走法','Fallback') }}:</b> {{ check.alternative or label('行动前核实，保留较简单的备选。','Confirm before acting and retain a simpler fallback.') }}</p>
{% endif %}
</div>
{% endfor %}{% endmacro %}
{% macro place(place) %}
<span class="place-name">{{ place.name_local }}</span>
<button type="button" class="copy" data-copy="{{ place.name_local }}">{{ label('复制当地名','Copy local name') }}</button>
<span class="place-note">{{ place.name }}</span>
{% if place.reason %}
<p>{{ place.reason }}</p>
{% endif %}{% if place.map_url %}
<a href="{{ place.map_url }}" target="_blank" rel="noopener noreferrer">{{ label('地图搜索','Map search') }}</a>
{% endif %}{% endmacro %}
{% macro day_content(day) %}
<h1>{{ day.date }} · {{ day.title }}</h1>
<p class="muted">{{ day.area }} · {{ day.pace }}{% if day.sunset %} · {{ label('日落','Sunset') }} {{ day.sunset }}{% endif %}
</p>
<section class="callout">
<h2>{{ label('速览','At a glance') }}</h2>
{% if day.notes %}
<p>{{ day.notes }}</p>
{% endif %}{% for anchor in day.anchors %}
<p class="time">{{ anchor.time }} {{ anchor.text }} · {{ label('最晚出发','Latest departure') }} {{ anchor.latest_departure or label('未核','Unverified') }}</p>
{% endfor %}{% if day.plans %}
<p>{{ label('推荐','Recommended') }}: {{ day.plans[0].name }}</p>
{% for track in day.plans[0].tracks %}
<p>{{ people(track.subject) }}: {% for block in track.blocks %}{{ block.start }} {{ block.title }}{% if not loop.last %} → {% endif %}{% endfor %}
</p>
{% endfor %}{% endif %}
</section>
<div class="foldbar">
<button type="button" data-fold="open">{{ label('全部展开','Expand all') }}</button>
<button type="button" data-fold="close">{{ label('全部收起','Collapse all') }}</button>
</div>
<details class="fold" open>
<summary>
<h2>{{ label('硬节点','Hard nodes') }}</h2>
</summary>
{% for anchor in day.anchors %}
<article class="card">
<h3>{{ anchor.time }} {{ anchor.text }}</h3>
<p>{{ people(anchor.subject) }} · {{ label('余量（分钟）','Buffer (minutes)') }} {{ anchor.buffer_minutes }}</p>
<p>
<b>{{ label('最晚出发','Latest departure') }}:</b> {{ anchor.latest_departure or label('未核','Unverified') }}</p>
<p>{{ label('错过的后果','If missed') }}: {{ anchor.consequence }}</p>
<p>{{ label('替代走法','Fallback') }}: {{ anchor.alternative }}</p>{{ checks(anchor.verification) }}</article>
{% else %}
<p class="empty">{{ label('没有已记录的硬节点。','No recorded hard nodes.') }}</p>
{% endfor %}
</details>
<details class="fold" open id="timeline">
<summary>
<h2>{{ label('一天的时间轴','Day timeline') }}</h2>
</summary>
{% for plan in day.plans %}
<h3>{{ plan.name }} {% if loop.first %}
<span class="badge">{{ label('推荐','Recommended') }}</span>
{% endif %}
</h3>
<div class="tracks">
{% for track in plan.tracks %}
<section>
<h4>{{ people(track.subject) }}</h4>
<ol class="timeline">
{% for block in track.blocks %}
<li>
<span class="time">{{ block.start }}–{{ block.end or block.start }}</span> · {{ block.kind }}<h4>{{ block.title }}</h4>
<p>{{ label('排程把握','Schedule certainty') }}: {{ block.certainty }}</p>
{% if block.place_view %}{{ place(block.place_view) }}{% endif %}{% if block.text %}
<p>{{ block.text }}</p>
{% endif %}{% if block.app %}
<p>
<b>App:</b> {{ block.app }}</p>
{% endif %}{% if block.note %}
<p>{{ block.note }}</p>
{% endif %}{% if block.source %}
<p>{{ label('出处','Source') }}: {{ block.source }}</p>
{% endif %}{{ checks(block.verification) }}{% if block.option_views %}
<p>{{ label('备选','Alternatives') }}:</p>
{% for alternative in block.option_views %}{{ place(alternative) }}{% endfor %}{% endif %}
</li>
{% endfor %}
</ol>
</section>
{% endfor %}
</div>
{% endfor %}
</details>
<details class="fold" open>
<summary>
<h2>{{ label('地点与地图','Places and maps') }}</h2>
</summary>
<div class="cards">
{% for item in day.places %}
<article class="card">{{ place(item) }}{% if item.hours %}
<p>{{ label('营业资料（以核验结论为准）','Opening information (see checks)') }}: {{ item.hours }}</p>
{% endif %}{{ checks(item.verification) }}</article>
{% else %}
<p class="empty">{{ label('没有地点资料。','No place information.') }}</p>
{% endfor %}
</div>
</details>
<details class="fold" open>
<summary>
<h2>{{ label('按时段的候选','Candidates by time slot') }}</h2>
</summary>
{% for slot, choices in day.candidate_views.items() %}
<h3>{{ slot }}</h3>
{% for item in choices %}
<article class="card">{{ place(item) }}{{ checks(item.verification) }}</article>
{% endfor %}{% else %}
<p class="empty">{{ label('尚无额外候选。','No additional candidates recorded.') }}</p>
{% endfor %}
</details>
<details class="fold" open>
<summary>
<h2>{{ label('下雨怎么办','Rain plan') }}</h2>
</summary>
<p>{{ day.rain_plan or label('尚未记录雨天备选；天气不适时先确认。','No rain alternative recorded; confirm before weather-sensitive activities.') }}</p>
</details>
<details class="fold" open>
<summary>
<h2>{{ label('交通花费','Transport costs') }}</h2>
</summary>
{% for fare in day.fares %}
<article class="card">
<h3>{{ fare.segment }}</h3>
<p>{{ fare.ticket }} · {{ people(fare.subject) }}</p>
<p>{{ label('每人','Per person') }}: {{ fare.amount_per_person if fare.amount_per_person is not none else label('金额未知，未计入合计','Unknown amount; excluded from totals') }} {{ fare.currency or '' }}{% if fare.estimated %} · {{ label('估算','Estimate') }}{% endif %}
</p>
{% if fare.alternative or fare.included_in %}
<p>{{ label('备选或已包含，不重复计费','Alternative or included; excluded from totals') }} {{ fare.included_in or '' }}</p>
{% endif %}{{ checks(fare.verification) }}</article>
{% else %}
<p class="empty">{{ label('未记录票价，不代表免费。','No fares recorded; this does not mean travel is free.') }}</p>
{% endfor %}{% for person, currencies in day.totals.items() %}
<p>
<b>{{ label('已知金额合计','Known cost total') }} · {{ people([person]) }}:</b> {% for currency, amount in currencies.items() %}{{ currency }} {{ amount }} {% endfor %}
</p>
{% endfor %}
<p class="muted">{{ label('各币种分开合计。未知金额不计入，估算仍是估算。','Currencies are totalled separately. Unknown amounts are excluded; estimates remain estimates.') }}</p>
</details>
<details class="fold" open>
<summary>
<h2>{{ label('本日核验结论','Day verification') }}</h2>
</summary>{{ checks(day.verification) }}{% if not day.verification %}
<p>{{ label('尚无核验记录，不代表已核。','No recorded checks; this does not mean verified.') }}</p>
{% endif %}
</details>
{% endmacro %}
"""

INDEX = """{% extends 'base' %}{% import 'macros' as m with context %}{% block main %}
<h1>{{ trip.title }}</h1>
<p>{{ trip.start }} → {{ trip.end }}</p>
<h2>{{ label('今天与明天','Today and tomorrow') }}</h2>
<div class="cards">
{% for title, day in nearby %}
<article class="card daycard">
<h3>{{ title }}</h3>
{% if day %}
<a href="{{ day.date }}.html" data-language-link>{{ day.date }} · {{ day.title }}</a>
{% for anchor in day.anchors %}
<p>{{ anchor.time }} {{ anchor.text }}</p>
{% endfor %}{% else %}
<p>{{ label('这天没有已提交的行程。','No committed itinerary for this date.') }}</p>
{% endif %}
</article>
{% endfor %}
</div>
<h2>{{ label('每天','Every day') }}</h2>
<ul class="daylist">
{% for day in days %}
<li>
<a href="{{ day.date }}.html" data-language-link>{{ day.date }} · {{ day.title }}</a> · {{ day.area }}</li>
{% endfor %}
</ul>
<section class="fold">
<h2>{{ label('要提前做的','Prepare ahead') }}</h2>
{% for booking in actions %}
<article class="card">
<h3>{{ booking.what }}</h3>
<p>{{ booking.due or booking.date }} · {{ booking.status }}</p>
<p>{{ booking.why_now }}</p>
{% if booking.if_missed %}
<p>{{ label('后果','Consequence') }}: {{ booking.if_missed }}</p>
{% endif %}
</article>
{% else %}
<p>{{ label('没有已记录的待办。','No recorded pending actions.') }}</p>
{% endfor %}
</section>
<section class="fold">
<h2>{{ label('待你定','Pending decisions') }}</h2>
{% for question in undecided %}
<article class="card">
<h3>{{ question.question or question.text }}</h3>
<p>{{ question.options|join(' · ') }}</p>
</article>
{% else %}
<p>{{ label('没有已记录的待定问题。','No recorded undecided questions.') }}</p>
{% endfor %}
<a href="decisions.html" data-language-link>{{ label('全部决定','All decisions') }}</a>
</section>
<section class="fold">
<h2>{{ label('待核与冲突','Unverified facts and conflicts') }}</h2>{{ m.checks(attention) }}{% for check in pending %}
<p>{{ check.text or check.question }} · {{ check.consequence or label('后果尚未记录，行动前确认。','Consequences not recorded; confirm before acting.') }}</p>
{% endfor %}
</section>
{% endblock %}"""

DAY = """{% extends 'base' %}{% import 'macros' as m with context %}{% block main %}{{ m.day_content(day) }}<nav class="daynavs">
{% if previous %}
<a href="{{ previous.date }}.html" data-language-link>← {{ previous.date }}</a>
{% endif %}{% if following %}
<a href="{{ following.date }}.html" data-language-link>{{ following.date }} →</a>
{% endif %}
</nav>
{% endblock %}"""

DECISIONS = """{% extends 'base' %}{% block main %}
<h1>{{ label('决定','Decisions') }}</h1>
{% for decision in decisions %}
<article class="card">
<h2>{{ decision.question }}</h2>
<dl>
<dt>{{ label('选项','Options') }}</dt>
<dd>{{ decision.options|join(' · ') }}</dd>
<dt>{{ label('选了哪支','Choice') }}</dt>
<dd>{{ decision.choice }}</dd>
<dt>{{ label('谁定','Decided by') }}</dt>
<dd>{{ decision.decided_by }}</dd>
<dt>{{ label('理由','Reason') }}</dt>
<dd>{{ decision.reason or label('未记录','Not recorded') }}</dd>
<dt>{{ label('回头条件','Revisit when') }}</dt>
<dd>{{ decision.revisit_when or label('未记录','Not recorded') }}</dd>
<dt>{{ label('出处','Provenance') }}</dt>
<dd>{{ decision.source }}</dd>
</dl>
</article>
{% else %}
<p>{{ label('没有已记录的决定。','No recorded decisions.') }}</p>
{% endfor %}{% endblock %}"""

RESULT = """{% extends 'base' %}
{% import 'macros' as m with context %}
{% block main %}
<h1>{{ label('手机结果页','Trip results') }}</h1>
<section class="fold">
<h2>{{ label('第一段材料','First response material') }}</h2>
<p class="muted">{{ label('这里展示服务保存的回答材料；宿主实际写出的对话未保存在本服务。','These are the materials saved by the service; the host-written conversation is not stored here.') }}</p>
{% if first %}
<p>{{ label('材料时刻','Material time') }}: {{ first.created_at }} · {{ label('基于版本','Based on version') }} {{ first.base_version }}</p>
{% if first.base_version != version.number %}
<p class="unverified">{{ label('材料来自旧版本，请以下方当前版本为准。','These materials belong to an older version; refer to the current version below.') }}</p>
{% endif %}
{% if first.text %}
<p>{{ label(first.outline.zh, first.outline.en) }}</p>
<h3>{{ label('给宿主的约束包','Constraint pack given to the host') }}</h3>
{{ first.text }}
{% else %}<p class="unverified">{{ label('这条材料没有存约束包（从仓重建的运行库不带它）。','This material has no stored pack (a store rebuilt from the repository has none).') }}</p>{% endif %}
{% else %}<p>{{ label('尚无第一段材料。','No first response material yet.') }}</p>{% endif %}
</section>
<section class="fold">
<h2>{{ label('第二段：先看差异','Second response: differences first') }}</h2>
{% if pending %}
{% if job and job.status == 'failed' %}
<p class="conflict">{{ label('核验失败，请在对话中重试。','Verification failed; retry through the conversation.') }}</p>
{% elif job and job.status == 'stale' %}
<p class="unverified">{{ label('核验任务已过期，请在对话中重新发起。','The check job is stale; request a new check through the conversation.') }}</p>
{% else %}
<p>{{ label('核验中','Verification pending') }} · {{ job.status if job else label('尚无核验任务','No check job recorded') }}</p>
{% endif %}
<p>{{ label('预计','Expected') }}: {{ job.eta if job and job.eta else label('尚未提供完成时刻。重排设计目标约 8 分钟，并非服务保证；请稍后刷新。','No completion time supplied. The replan design target is about 8 minutes, not a service guarantee; refresh later.') }}</p>
{% endif %}
{% if second %}
{% if second.historical %}<h3>{{ label('最近一次已有结果（先前任务）','Most recent available result (earlier job)') }}</h3>{% endif %}
<p>{{ label('基于版本','Based on version') }} {{ second.base_version }} · {{ second.state }} · {{ second.generated_at }}</p>
{% if second.stale %}<p class="unverified">{{ label('此结果已过期，不代表当前版本核验通过。','This result is stale and does not verify the current version.') }}</p>{% endif %}
<p class="result-material">{{ second.value.differences or label('尚无差异说明。','No difference summary recorded.') }}</p>
{{ m.checks(second.checks) }}
{% if second.days %}
<h3>{{ label('本次核验的完整候选日程','Complete candidate itinerary checked in this result') }}</h3>
{% for day in second.days %}{{ m.day_content(day) }}{% endfor %}
{% endif %}
{% endif %}
</section>
<section>
<h2>{{ label('当前版本：今天','Current version: today') }}</h2>
{% if not today %}<p>{{ label('今天没有已提交的行程。','No committed itinerary for today.') }}</p>
{% if upcoming %}<h3>{{ label('下一天行程','Next itinerary day') }}</h3>{% endif %}{% endif %}
{% if today_text %}{{ today_text }}{% endif %}
</section>
{% endblock %}"""


MEMORY = """{% extends 'base' %}{% block main %}
<h1>{{ label('记忆（长期内容）','Memory (long-term content)') }}</h1>
<p>{{ label(memory_summary.zh, memory_summary.en) }}</p>
{% if memory_pending %}<p>{{ label('待确认的：回“沿用”或“这趟不用”，记进这一趟。','For each waiting entry, reply "keep" or "not this trip".') }}</p>{% endif %}
{% for section in memory_sections %}
<section class="card">
<h2>{{ label(section.zh.title, section.en.title) }}</h2>
{% for zh, en in section.pairs %}
<div class="memory {{ zh.status }}">
<p><span class="badge {{ zh.status }}">{{ label(zh.status_label, en.status_label) }}</span> {{ zh.words }}</p>
<p class="meta">{% if zh.subject_ids %}{{ people(zh.subject_ids) }} · {% endif %}{{ label(zh.origin, en.origin) }} · {{ label(zh.used, en.used) }}{% if zh.basis %} · {{ label(zh.basis, en.basis) }}{% endif %}</p>
</div>
{% endfor %}
</section>
{% else %}
<p>{{ label('还没有长期内容。','No long-term content yet.') }}</p>
{% endfor %}{% endblock %}"""


# The page friends read (`tripwright.site.share`): a home page and one page per day, English first, nothing else.
SHARE_BASE = """<!doctype html>
<html lang="en" data-language="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex, nofollow">
<title>{{ title.en }}</title>
<script>(function(){try{var q=new URLSearchParams(location.search).get("lang"),l=q||localStorage.getItem("tw-lang");if(l==="zh"||l==="en"){document.documentElement.dataset.language=l;document.documentElement.lang=l==="zh"?"zh-CN":"en"}}catch(e){}})()</script>
<style>{{ style|safe }}</style>
</head>
<body data-zone="{{ zone }}" data-theme="{{ theme }}">
<header class="top">
<div class="bar wrap">
<a class="brand" href="index.html" data-lang-link>{{ t(trip_title) }}</a>
<div class="lang" role="group" aria-label="Language / 语言"><button type="button" data-lang-button="en">EN</button><button type="button" data-lang-button="zh">中文</button></div>
</div>
<nav class="days" aria-label="Days">
<a href="index.html" data-lang-link{% if current == 'index' %} aria-current="page"{% endif %}>{{ label('首页','Home') }}</a>
{% if log %}<a href="log.html" data-lang-link{% if current == 'log' %} aria-current="page"{% endif %}>{{ label('实录','Trip log') }}</a>{% endif %}
{% for item in days %}<a href="{{ item.date }}.html" data-lang-link{% if current == item.date %} aria-current="page"><svg class="here" width="12" height="17" viewBox="-12 -35 24 37" aria-hidden="true"><path d="M0,0 C-4,-8 -12,-12 -12,-22 A12,12 0 1 1 12,-22 C12,-12 4,-8 0,0Z"/></svg>{% else %}>{% endif %}{{ item.chip }}</a>
{% endfor %}
</nav>
</header>
<main class="wrap">
{% block main %}{% endblock %}
</main>
<footer class="wrap">{{ label('分享给朋友看的行程概览：人名用代号，住处只写城区。时刻与安排以出行当天为准。','A shared overview of the plan: people appear as codes and places to stay only by district. Times and plans may still change.') }}{% if untranslated %}<br>{{ label('部分内容只有中文。','Some texts are in Chinese only.') }}{% endif %}<br>{{ label('更新于','Updated') }} {{ updated }}</footer>
<script>{{ script|safe }}</script>
</body>
</html>"""

SHARE_FIGURE = """{% macro figure(fig, name) %}{% if fig %}<figure class="plate" data-map="{{ name }}">
{{ fig.svg }}
<div class="livemap" hidden></div>
{% if fig.legend %}<ul class="key"><li><span class="pin mini fixed"></span>{{ label('要准时','Fixed time') }}</li><li><span class="pin mini visit"></span>{{ label('时间可挪','Time can shift') }}</li><li><span class="pin mini opt"></span>{{ label('几选一','One of several') }}</li></ul>{% endif %}
<figcaption class="note">{% if fig.square %}<span class="sketch-note">{{ label('位置示意：按经纬度摆放，没有底图；网格每格 ' ~ fig.square ~ '。','Position sketch from coordinates, no base map; grid squares are ' ~ fig.square ~ '.') }}</span>{% else %}<span class="sketch-note">{{ label('没有坐标：钉只按先后排，不表示位置。','No coordinates: the pins stand in order only, not where they are.') }}</span>{% endif %}{% if fig.missing %} {{ label(fig.missing ~ ' 处没有坐标，没画在图上。', fig.missing ~ (' place has no coordinates and is not drawn.' if fig.missing == 1 else ' places have no coordinates and are not drawn.')) }}{% endif %} <span class="live-note" hidden>{{ label('地图底图：OpenStreetMap。钉的号同清单。','Base map: OpenStreetMap. Pin numbers as in the lists.') }}</span></figcaption>
</figure>
{% if fig.live %}<script type="application/json" id="{{ name }}">{{ fig.json|safe }}</script>{% endif %}{% endif %}{% endmacro %}"""

SHARE_INDEX = """{% extends 'base' %}{% import 'figure' as f with context %}{% block main %}
<section class="hero">
<p class="kicker">{{ t(kicker) }}</p>
<h1>{{ t(trip_title) }}</h1>
{% if intro %}<p class="lede">{{ t(intro) }}</p>{% endif %}
{% if travelers %}<p class="muted small">{{ label('同行','Travelling') }}: {{ t(travelers) }}</p>{% endif %}
<p class="btnrow"><a class="btn primary" href="#days">{{ label('看每一天','See the days') }}</a><a class="btn" href="{{ days[0].date }}.html" data-lang-link>{{ label('从第 1 天开始 →','Start with day 1 →') }}</a>{% if log %}<a class="btn" href="log.html" data-lang-link>{{ label('路上的实录 →','Trip log →') }}</a>{% endif %}</p>
</section>
{% if route|length > 1 or figure %}
<div class="spread{% if figure %} with-figure{% endif %}">
{% if route|length > 1 %}
<section class="blk">
<h2 id="route">{{ label('路线','The route') }}</h2>
<ol class="route">
{% for stop in route %}<li class="stop"><span class="pin visit">{{ stop.pin }}</span>{% if stop.href %}<a href="{{ stop.href }}" data-lang-link>{% else %}<div>{% endif %}<b>{{ t(stop.name) }}</b>{% if stop.note %}<span class="note">{{ t(stop.note) }}</span>{% endif %}{% if stop.href %}</a>{% else %}</div>{% endif %}</li>
{% if stop.leg %}<li class="leg">{{ t(stop.leg) }}</li>{% endif %}
{% endfor %}
</ol>
</section>
{% endif %}
{% if figure %}<div class="figcol">
{{ f.figure(figure, 'map-all') }}
<p class="muted small">{{ label('钉上的号是路线的次序；虚线只表示先后，不是真实路线。点一下钉打开那天。','The numbers on the pins are the order of the route; the dashed line shows only the order, not the real route. Tap a pin to open its day.') }}</p>
</div>{% endif %}
</div>
{% endif %}
<section class="blk">
<h2 id="days">{{ label('每天','Day by day') }}</h2>
<p class="muted small">{{ label('点一行看那天的完整安排。','Each row opens the plan for that day.') }}</p>
<ol class="rows">
{% for day in days %}
<li><a href="{{ day.date }}.html" data-lang-link><span class="dd"><b>{{ t(day.label) }}</b>{{ t(day.short) }}</span><span class="dt">{% if day.head %}<b>{{ t(day.head) }}</b> · {{ t(day.rest) }}{% else %}{{ t(day.title) }}{% endif %}</span>{% if day.stay %}<span class="ds">{{ label('住处','Stay') }}: {{ t(day.stay) }}</span>{% endif %}</a></li>
{% endfor %}
</ol>
</section>
{% endblock %}"""

SHARE_LOG = """{% extends 'base' %}{% block main %}
<p class="kicker">{{ t(trip_title) }}</p>
<h1>{{ label('旅行实录','Trip log') }}</h1>
<p class="lede">{{ t(log.intro) }}</p>
{% if log.origin %}<section class="card log-origin">{{ t(log.origin) }}</section>{% endif %}
<ol class="rows">
{% for piece in log.pieces %}<li><a href="#{{ piece.id }}"><span class="dd">{{ loop.index }}</span><span class="dt"><b>{{ t(piece.title) }}</b></span><span class="ds">{{ t(piece.when) }}</span></a></li>
{% endfor %}
</ol>
{% for piece in log.pieces %}
<section class="card log-piece" id="{{ piece.id }}">
<h2>{{ loop.index }}. {{ t(piece.title) }}</h2>
<p class="meta">{{ t(piece.when) }}</p>
<h3>{{ label('我在路上说的','What I said') }}</h3>
{% for saying in piece.said %}{% if saying.retold %}<p class="retold">{% if saying.time %}<span class="time">{{ saying.time }}</span> {% endif %}{{ t(saying.text) }} <span class="tag">{{ label('转述','Retold') }}</span></p>
{% else %}<blockquote>{% if saying.time %}<span class="time">{{ saying.time }}</span> {% endif %}{{ t(saying.text) }}</blockquote>
{% endif %}{% endfor %}
<h3>{{ label('当时怎样处理','How it was handled then') }}</h3>
<p>{{ t(piece.then) }}</p>
<h3>{{ label('tripwright 现在','tripwright now') }}{% if piece.match %} <span class="tag">{{ t(piece.match) }}</span>{% endif %}</h3>
<p>{{ t(piece.became) }}</p>
{% if piece.replay %}<details class="fold"><summary>{{ label('把同一句话再交给 tripwright','The same message, given to tripwright again') }}</summary>
<div class="in">
<p class="muted small">{{ t(piece.replay.setup) }}</p>
<blockquote>{{ t(piece.replay.asked) }}</blockquote>
{% for part in piece.replay.parts %}<h4>{{ t(part.label) }}</h4>
<div class="log-reply">{{ t(part.text) }}</div>
{% endfor %}
{% if piece.replay.note %}<h4>{{ label('我们的评注','Our note') }}</h4>
<p>{{ t(piece.replay.note) }}</p>{% endif %}
</div>
</details>{% endif %}
</section>
{% endfor %}
{% endblock %}"""

SHARE_DAY = """{% extends 'base' %}{% import 'figure' as f with context %}
{% macro pin(p, mini=false) %}{% if p %}<span class="pin {{ p.shape }}{% if mini %} mini{% endif %}">{{ p.label }}</span>{% endif %}{% endmacro %}
{% macro place(item, named=true) %}{% if item.stay %}<span class="pl">{{ t(item.stay) }}</span>{% else %}<span class="pl">{% if named and item.pin %}{{ pin(item.pin) }}{% endif %}<span class="pb">{% if named %}<b>{% if item.name %}{{ t(item.name) }}{% else %}{{ item.local }}{% endif %}</b>{% if item.name %}<small>{{ item.local }}</small>{% endif %}{% else %}<small>{{ item.local }}</small>{% endif %} <a class="gmap" href="{{ item.map_url }}" target="_blank" rel="noopener noreferrer">{{ label('地图','Map') }} ↗</a></span></span>{% endif %}{% endmacro %}
{% block main %}
<p class="crumb"><a href="index.html" data-lang-link>← {{ label('总览','Overview') }}</a></p>
<p class="kicker">{{ t(day.label) }} · {{ t(day.short) }}</p>
<h1 class="daytitle">{{ t(day.title, day.marks) }}</h1>
<p class="meta">{% if day.sunset %}{{ label('日落','Sunset') }} {{ day.sunset }}{% endif %}{% if day.stay %}{% if day.sunset %} · {% endif %}{{ label('住处','Stay') }}: {{ t(day.stay) }}{% endif %}</p>
<div class="day{% if day.figure %} with-figure{% endif %}">
{% if day.anchors %}
<section class="blk keytimes">
<h2>{{ label('关键时刻','Key times') }}</h2>
<ul class="glist anchors">
{% for anchor in day.anchors %}<li data-at="{{ anchor.at }}" data-date="{{ day.date }}"><span class="t">{{ anchor.time }}</span><span class="tx">{{ t(anchor.text, day.marks) }}<span class="tag">{{ label('已过','Past') }}</span></span></li>
{% endfor %}
</ul>
{% if day.changed %}<p class="revkey"><del>{{ label('划掉的','Struck out') }}</del> {{ label('是原来的安排，','was the plan before; ') }}<ins>{{ label('加底色的','highlighted') }}</ins> {{ label('是改后的。','is what replaced it.') }}</p>{% endif %}
</section>
{% endif %}
{% if day.figure %}<div class="figcol">{{ f.figure(day.figure, 'map-day') }}</div>{% endif %}
<div class="listcol">
{% if day.stops %}
<section class="blk">
<h2>{{ label('要去的地方','Places to go') }}</h2>
<ul class="glist stops">
{% for stop in day.stops %}<li class="{{ stop.kind }} {{ stop.certainty }}"><span class="t">{{ stop.start }}{% if stop.end %}<i>–{{ stop.end }}</i>{% endif %}</span><span class="tx">{% for item in stop.spots %}{% if not loop.first %}<span class="or">{{ label('或 ','or ') }}</span>{% endif %}{{ place(item) }}{% endfor %}{% if stop.who %}<span class="who">{{ t(stop.who) }}</span>{% endif %}</span></li>
{% endfor %}
</ul>
</section>
{% endif %}
{% if day.legs %}
<section class="blk">
<h2>{{ label('怎么走','Getting around') }}</h2>
<ul class="glist legs">
{% for leg in day.legs %}<li class="{{ leg.certainty }}"><span class="t">{{ leg.start }}{% if leg.end %}<i>–{{ leg.end }}</i>{% endif %}</span><span class="tx">{% if leg.ends and (leg.ends[0] or leg.ends[1]) %}<span class="seg">{{ pin(leg.ends[0], true) }}<i></i>{{ pin(leg.ends[1], true) }}</span>{% endif %}<b>{{ t(leg.title, day.marks) }}</b>{% if leg.who %}<span class="who">{{ t(leg.who) }}</span>{% endif %}{% if leg.how %}<span class="how">{{ t(leg.how, day.marks) }}</span>{% endif %}{% if leg.note %}<span class="how">{{ t(leg.note, day.marks) }}</span>{% endif %}</span></li>
{% endfor %}
</ul>
<p class="muted small"><span class="legs-key">●</span> {{ label('定时：时刻表上的车或已订好的。','fixed: on a timetable or booked.') }}</p>
</section>
{% endif %}
{% if day.notes or day.rain %}
<details class="fold"><summary><h2>{{ label('说明与下雨怎么办','Notes and rain') }}</h2></summary>
<div class="in">
{% if day.notes %}<p>{{ t(day.notes, day.marks) }}</p>{% endif %}
{% if day.rain %}<p><b>{{ label('下雨的话：','If it rains: ') }}</b>{{ t(day.rain, day.marks) }}</p>{% endif %}
</div>
</details>
{% endif %}
{% if day.plans %}
<details class="fold"><summary><h2>{{ label('这天的完整时间轴','The whole day, hour by hour') }}</h2></summary>
<div class="in">
{% for plan in day.plans %}
<p class="plan">{% if day.plans|length > 1 %}{% if loop.first %}{{ label('主方案：','Main plan: ') }}{% else %}{{ label('另一套：','Alternative: ') }}{% endif %}{% endif %}{{ t(plan.name, day.marks) }}</p>
{% if plan.note %}<p class="muted">{{ t(plan.note, day.marks) }}</p>{% endif %}
{% for track in plan.tracks %}
<div class="track">
{% if plan.tracks|length > 1 %}<h3>{{ t(track.who) }}</h3>{% endif %}
<ol class="timeline">
{% for block in track.blocks %}
<li class="{{ block.key }} {{ block.certainty }}">
<span class="time">{{ block.start }}{% if block.end and block.end != block.start %}–{{ block.end }}{% endif %}</span><span class="what">{{ t(block.kind) }}</span>
<h4>{{ t(block.title, day.marks) }}</h4>
{% if block.text %}<p>{{ t(block.text, day.marks) }}</p>{% endif %}
{% if block.how %}<p class="how">{{ t(block.how, day.marks) }}</p>{% endif %}
{% if block.note %}<p>{{ t(block.note, day.marks) }}</p>{% endif %}
{% if block.place and not block.place.stay %}<p>{{ place(block.place, false) }}</p>{% endif %}
{% if block.options %}<p class="muted small">{{ label('可选：','Options: ') }}</p>{% for item in block.options %}<p>{{ place(item) }}</p>{% endfor %}{% endif %}
</li>
{% endfor %}
</ol>
</div>
{% endfor %}
{% endfor %}
{% if day.candidates %}
<h3>{{ label('也可以去','Other places for this day') }}</h3>
{% for slot in day.candidates %}<p class="plan">{{ t(slot.name) }}</p>{% for item in slot.places %}<p>{{ place(item) }}</p>{% endfor %}
{% endfor %}
{% endif %}
</div>
</details>
{% endif %}
</div>
</div>
<nav class="daynavs">
{% if previous %}<a href="{{ previous.date }}.html" data-lang-link>← {{ t(previous.short) }}</a>{% endif %}
{% if following %}<a class="next" href="{{ following.date }}.html" data-lang-link>{{ t(following.short) }} →</a>{% endif %}
</nav>
{% endblock %}"""
