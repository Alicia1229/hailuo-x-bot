"""把 report JSON 渲染成静态 HTML,放到 docs/reports/。

用法:
  python scripts/render_full_report.py reports/report_YYYYMMDD_HHMM.json
  # 或不传参数,自动用最新的 report_*.json
"""
from __future__ import annotations

import json
import re
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs" / "reports"
DOCS.mkdir(parents=True, exist_ok=True)


REPORT_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  * {{ box-sizing: border-box; }}
  body {{ font: 14px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
         margin: 0; background: #f6f8fa; color: #1f2328; }}
  main {{ max-width: 960px; margin: 0 auto; padding: 28px 20px 48px; }}
  h1 {{ font-size: 24px; margin: 0 0 8px; }}
  h2 {{ font-size: 17px; margin: 0 0 14px; }}
  .meta {{ color: #656d76; margin-bottom: 18px; font-size: 12px; }}
  .summary {{ display: grid; grid-template-columns: repeat(auto-fit,minmax(130px,1fr)); gap: 10px; margin-bottom: 18px; }}
  .metric {{ background: #fff; border: 1px solid #d0d7de; border-radius: 6px; padding: 12px 14px; }}
  .metric .label {{ color: #656d76; font-size: 11px; }}
  .metric .value {{ font-size: 19px; font-weight: 650; margin-top: 3px; }}
  section {{ padding: 18px 0; border-top: 1px solid #d8dee4; }}
  .tweet, .topic, .risk {{ padding: 12px 0; border-top: 1px solid #eaeef2; }}
  .tweet:first-of-type, .topic:first-of-type, .risk:first-of-type {{ border-top: 0; }}
  .muted {{ color: #656d76; font-size: 12px; }}
  a {{ color: #0969da; text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
  .button {{ display: inline-block; background: #1f883d; color: #fff; font-weight: 600;
             padding: 9px 14px; border-radius: 6px; margin-top: 8px; }}
  .button:hover {{ text-decoration: none; background: #1a7f37; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff; }}
  th, td {{ padding: 9px 10px; text-align: left; border: 1px solid #d8dee4; vertical-align: top; }}
  th {{ background: #f6f8fa; }}
  .warning {{ background: #fff8c5; border: 1px solid #d4a72c; border-radius: 6px;
              padding: 10px 12px; margin-bottom: 16px; color: #633c01; }}
  .terms {{ line-height: 2; }}
  @media (max-width: 640px) {{ main {{ padding: 18px 14px 36px; }} h1 {{ font-size: 21px; }} }}
</style>
</head>
<body data-report-count="{total_tweets}">
<main>
  <h1>{title}</h1>
  <div class="meta">{meta}</div>
  {quality_html}
  <div class="summary">
    <div class="metric"><div class="label">原创帖子</div><div class="value">{total_tweets}</div></div>
    <div class="metric"><div class="label">总 Views</div><div class="value">{total_views:,}</div></div>
    <div class="metric"><div class="label">总 Engagement</div><div class="value">{total_engagement:,}</div></div>
    <div class="metric"><div class="label">作者数</div><div class="value">{authors}</div></div>
  </div>
  {launch_html}
  {legacy_html}
</main>
</body>
</html>
"""


TWEETS_TEMPLATE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  * {{ box-sizing: border-box; }}
  body {{ font: 14px/1.5 -apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
         margin: 0; padding: 24px; background: #f6f8fa; color: #1f2328; }}
  h1 {{ font-size: 20px; margin: 0 0 8px; }}
  .meta {{ color: #656d76; margin-bottom: 16px; font-size: 12px; }}
  .summary {{ display: flex; gap: 16px; flex-wrap: wrap; margin-bottom: 24px; }}
  .card {{ background: #fff; border: 1px solid #d0d7de; border-radius: 6px; padding: 12px 16px; min-width: 120px; }}
  .card .label {{ color: #656d76; font-size: 11px; }}
  .card .value {{ font-size: 18px; font-weight: 600; margin-top: 4px; }}
  .toolbar {{ background: #fff; border: 1px solid #d0d7de; border-radius: 6px; padding: 12px;
             margin-bottom: 12px; display: flex; gap: 12px; flex-wrap: wrap; align-items: center; }}
  .toolbar input, .toolbar select {{ font: inherit; padding: 6px 10px; border: 1px solid #d0d7de;
                                       border-radius: 6px; background: #fff; }}
  .toolbar input {{ width: 240px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
           border: 1px solid #d0d7de; border-radius: 6px; overflow: hidden; }}
  th, td {{ padding: 10px 12px; text-align: left; border-bottom: 1px solid #eaeef2; vertical-align: top; }}
  th {{ background: #f6f8fa; font-weight: 600; cursor: pointer; user-select: none; position: sticky; top: 0; }}
  th:hover {{ background: #eaeef2; }}
  tr:last-child td {{ border-bottom: none; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .views {{ font-weight: 600; color: #0969da; }}
  .author a {{ color: #0969da; text-decoration: none; }}
  .author a:hover {{ text-decoration: underline; }}
  .text {{ max-width: 480px; }}
  .badge {{ display: inline-block; padding: 1px 6px; border-radius: 10px; font-size: 11px;
            background: #ddf4ff; color: #0969da; }}
  .warning {{ background: #fff8c5; border: 1px solid #d4a72c; border-radius: 6px;
              padding: 10px 12px; margin-bottom: 16px; color: #633c01; }}
  .empty {{ text-align: center; padding: 40px; color: #656d76; }}
  .row-hidden {{ display: none; }}
</style>
</head>
<body>
<h1>📊 {title}</h1>
<div class="meta">{meta}</div>
{quality_html}

<div class="summary">
  <div class="card"><div class="label">推文数</div><div class="value">{total_tweets}</div></div>
  <div class="card"><div class="label">总 Views</div><div class="value">{total_views:,}</div></div>
  <div class="card"><div class="label">总 Engagement</div><div class="value">{total_engagement:,}</div></div>
  <div class="card"><div class="label">作者数</div><div class="value">{authors}</div></div>
  <div class="card"><div class="label">窗口</div><div class="value">过去 {window_hours}h</div></div>
</div>

<div class="toolbar">
  <input type="search" id="q" placeholder="搜索作者 / 推文内容…">
  <label><input type="checkbox" id="hideLow"> 隐藏 views &lt; 50</label>
  <span style="color:#656d76;font-size:12px">点击列头排序</span>
</div>

<table id="t">
  <thead><tr>
    <th data-k="views" class="num">Views</th>
    <th data-k="time">时间 (UTC)</th>
    <th data-k="author">作者</th>
    <th data-k="text">推文</th>
    <th data-k="likes" class="num">❤️</th>
    <th data-k="retweets" class="num">🔁</th>
    <th data-k="replies" class="num">💬</th>
    <th data-k="quotes" class="num">🔁引</th>
    <th></th>
  </tr></thead>
  <tbody>
{rows}
  </tbody>
</table>

<script>
const tbody = document.querySelector('#t tbody');
const q = document.getElementById('q');
const hideLow = document.getElementById('hideLow');

function applyFilter() {{
  const term = q.value.toLowerCase();
  const low = hideLow.checked;
  for (const tr of tbody.querySelectorAll('tr')) {{
    const match = !term || tr.dataset.search.includes(term);
    const v = parseInt(tr.dataset.views || '0', 10);
    const lowOk = !low || v >= 50;
    tr.classList.toggle('row-hidden', !(match && lowOk));
  }}
}}
q.addEventListener('input', applyFilter);
hideLow.addEventListener('change', applyFilter);

// 列头排序
let sortKey = 'views', sortAsc = false;
for (const th of document.querySelectorAll('th[data-k]')) {{
  th.addEventListener('click', () => {{
    const k = th.dataset.k;
    if (k === sortKey) sortAsc = !sortAsc; else {{ sortKey = k; sortAsc = false; }}
    const rows = Array.from(tbody.querySelectorAll('tr'));
    rows.sort((a, b) => {{
      const av = a.dataset[k] || '', bv = b.dataset[k] || '';
      const an = parseFloat(av), bn = parseFloat(bv);
      if (!isNaN(an) && !isNaN(bn)) return sortAsc ? an - bn : bn - an;
      return sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
    }});
    tbody.append(...rows);
  }});
}}
</script>
</body>
</html>
"""


def _fmt(value: int) -> str:
    return f"{int(value or 0):,}"


def _tweet_preview(tweet: dict, index: int | None = None) -> str:
    prefix = f"<strong>{index}.</strong> " if index is not None else ""
    engagement = sum(int(tweet.get(key, 0) or 0) for key in ("likes", "retweets", "replies", "quotes"))
    return (
        '<div class="tweet">'
        f'{prefix}<a href="{escape(tweet.get("author_url", ""))}" target="_blank" rel="noopener">'
        f'<strong>{escape(tweet.get("author", ""))}</strong></a> · '
        f'Views <strong>{_fmt(tweet.get("views", 0))}</strong> · 互动 {_fmt(engagement)}<br>'
        f'{escape(tweet.get("text", ""))}<br>'
        f'<a href="{escape(tweet.get("url", ""))}" target="_blank" rel="noopener">打开推文</a>'
        '</div>'
    )


def _render_sentiment(report: dict) -> str:
    overview = report.get("sentiment_overview") or {}
    items = overview.get("items", [])
    if not items:
        return '<div class="muted">暂无数据</div>'
    rows = []
    for item in items:
        examples = item.get("examples", [])
        example = examples[0] if examples else {}
        example_link = (
            f'<a href="{escape(example.get("url", ""))}" target="_blank" rel="noopener">代表帖</a>'
            if example else "-"
        )
        rows.append(
            f'<tr><td><strong>{escape(str(item.get("name", "")))}</strong></td>'
            f'<td>{item.get("count", 0)}</td><td>{item.get("pct", 0)}%</td><td>{example_link}</td></tr>'
        )
    return '<table><thead><tr><th>类型</th><th>数量</th><th>占比</th><th>代表帖</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>'


def _render_topics(report: dict) -> str:
    items = report.get("public_opinion", [])[:3]
    if not items:
        return '<div class="muted">暂无数据</div>'
    blocks = []
    for index, item in enumerate(items, 1):
        tweet = item.get("tweet", {})
        blocks.append(
            '<div class="topic">'
            f'<strong>{index}. {escape(str(item.get("summary") or item.get("topic") or "其他讨论"))}</strong><br>'
            f'讨论 {item.get("count", 0)} 条（{item.get("pct", 0)}%） · '
            f'总 Views {_fmt(item.get("views", 0))} · 总互动 {_fmt(item.get("engagement", 0))}<br>'
            f'<span class="muted">{escape(str(item.get("reason", "")))}</span><br>'
            f'<a href="{escape(tweet.get("url", ""))}" target="_blank" rel="noopener">查看代表帖</a>'
            '</div>'
        )
    return "".join(blocks)


def _render_risks(report: dict) -> str:
    items = report.get("risky_tweets", [])
    if not items:
        return '<div class="muted">本期未检测到需要人工关注的潜在风险推文</div>'
    blocks = []
    for item in items:
        tweet = item.get("tweet", {})
        blocks.append(
            '<div class="risk">'
            f'<strong>{escape(str(item.get("risk_type", "潜在风险")))}</strong> · '
            f'风险分 {item.get("score", "")} · Views {_fmt(tweet.get("views", 0))}<br>'
            f'{escape(str(item.get("reason", "")))}<br>'
            f'<a href="{escape(tweet.get("url", ""))}" target="_blank" rel="noopener">打开推文</a>'
            '</div>'
        )
    return "".join(blocks)


def _render_terms(report: dict) -> str:
    terms = report.get("related_terms", [])[:20]
    if not terms:
        return '<span class="muted">暂无数据</span>'
    return " · ".join(
        f'<strong>{escape(str(item.get("term", "")))}</strong> ×{item.get("count", 0)}'
        for item in terms
    )


SCENE_LABELS = {
    "commercial_ad": "商业广告", "cinematic_trailer": "电影预告",
    "game_ui_mg": "游戏 / UI / MG", "music_dance_mv": "音乐 / 舞蹈 / MV",
    "anime_illustration": "动漫 / 手绘", "dialogue_performance": "对白 / 表演",
    "action_vfx": "动作 / 特效", "fantasy_story": "奇幻叙事",
    "product_ui_demo": "产品 / UI 演示", "other": "其他作品",
}
POST_TYPE_LABELS = {
    "product_test_review": "产品测试 / 评测", "work_showcase": "作品展示",
    "head_to_head_comparison": "横向对比", "tutorial_workflow": "教程 / 工作流",
    "news_announcement": "新闻 / 发布", "partner_promotion": "合作推广", "other": "其他",
}
FEATURE_LABELS = {
    "price": "价格 / 性价比", "visual_quality": "画面质量", "text_stability": "文字稳定性",
    "multimodal_audio": "多模态 / 音频", "availability_release": "发布 / 开放",
    "visual_style_quality": "画面风格 / 质量", "motion_camera": "运镜 / 动态",
    "omni_reference": "Omni Reference", "prompt_adherence": "提示词遵循",
    "character_consistency": "角色一致性", "transitions_narrative": "转场 / 叙事",
    "action_performance": "动作表现", "resolution_duration": "2K / 时长",
    "native_audio_lipsync": "原生音频 / 口型", "text_rendering": "文字生成",
    "price_efficiency": "价格 / 性价比", "other": "其他",
}


def _label(mapping: dict[str, str], key: object) -> str:
    value = str(key or "other")
    return mapping.get(value, value)


def _render_launch_html(report: dict, tweets_file: str, negative_file: str) -> str:
    analysis = report.get("launch_analysis") or {}
    overview = analysis.get("data_overview") or {}
    goodcase = analysis.get("goodcase") or {}
    sd = analysis.get("seedance") or {}
    negative = analysis.get("negative") or {}
    sections = []

    def section(title: str, body: str) -> None:
        sections.append(f"<section><h2>{escape(title)}</h2>{body}</section>")

    table = (
        '<table><thead><tr><th>指标</th><th>数据</th></tr></thead><tbody>'
        f'<tr><td>Hailuo 原创帖子</td><td>{overview.get("hailuo_posts", 0)} 条</td></tr>'
        f'<tr><td>Hailuo 总 Views</td><td>{round(int(overview.get("hailuo_views", 0) or 0) / 10000)} 万</td></tr>'
        f'<tr><td>MiniMax H3 相关讨论</td><td>{escape(str(overview.get("h3_news_posts", "13.1K")))} 条</td></tr>'
        f'<tr><td>Seedance 2.5 相关讨论</td><td>{escape(str(overview.get("seedance_news_display_total", "8,339")))} 条</td></tr>'
        '</tbody></table>'
    )
    section("一、舆情总结 · 1. 数据概况", table)

    scene = "、".join(
        _label(SCENE_LABELS, k) for k, _ in list((goodcase.get("scene_counts") or {}).items())[:5]
    ) or "暂无"
    features = "、".join(
        _label(FEATURE_LABELS, k) for k, _ in list((goodcase.get("feature_counts") or {}).items())[:5]
    ) or "暂无"
    section(
        "一、舆情总结 · 2. Goodcase 分布（主观收集）",
        f'<p><strong>场景分布：</strong>{escape(scene)}</p>'
        f'<p><strong>Feature 分布：</strong>{escape(features)}</p>',
    )

    topic_rows = []
    for index, item in enumerate((analysis.get("topic_clusters") or [])[:5], 1):
        representatives = item.get("representatives") or []
        representative = representatives[0] if representatives else {}
        link = (
            f'<a href="{escape(str(representative.get("url", "")))}" '
            'target="_blank" rel="noopener">代表帖</a>'
            if representative.get("url") else "-"
        )
        topic_rows.append(
            f'<tr><td>{index}. {escape(str(item.get("topic", "其他讨论")))}</td>'
            f'<td>{int(item.get("count", 0) or 0)}</td>'
            f'<td>{_fmt(item.get("views", 0))}</td><td>{link}</td></tr>'
        )
    topic_table = (
        '<table><thead><tr><th>话题</th><th>帖子数</th><th>Views</th><th>案例</th></tr></thead>'
        f'<tbody>{"".join(topic_rows)}</tbody></table>'
        if topic_rows else '<div class="muted">暂无</div>'
    )
    section("一、舆情总结 · 3. 话题聚类", topic_table)

    stance = "、".join(
        f"{escape({'hailuo_better': 'Hailuo 更好', 'no_conclusion': '无明确结论', 'mixed': '各有优劣', 'seedance_better': 'Seedance 更好'}.get(k, k))} {v}"
        for k, v in (sd.get("stance_counts") or {}).items()
    ) or "暂无"
    points = "、".join(
        f"{_label(FEATURE_LABELS, k)} {v}" for k, v in list((sd.get("primary_point_counts") or {}).items())[:5]
    ) or "暂无"
    section(
        "一、舆情总结 · 4. SD 对比分析",
        f'<p>Seedance 共现 {sd.get("total", 0)} 条，其中实际横向比较 {sd.get("direct_counts", {}).get("direct", 0)} 条。</p>'
        f'<p><strong>立场：</strong>{stance}</p><p><strong>比较维度：</strong>{escape(points)}</p>'
        '<p>总体认知：H3 更便宜，在广告叙事、文字稳定性、提示词遵循和商业成片上更受认可；Seedance 在激烈动作、动态镜头和成熟度上仍有优势。</p>',
    )
    problems = analysis.get("problems") or []
    section("一、舆情总结 · 5. 我们的问题所在", "<ul>" + "".join(f"<li>{escape(str(item))}</li>" for item in problems) + "</ul>")

    ranked_tweets = report.get("top_tweets") or sorted(
        report.get("all_tweets") or [],
        key=lambda item: int(item.get("views", 0) or 0),
        reverse=True,
    )[:5]
    top_html = "".join(
        _tweet_preview(item, i)
        for i, item in enumerate(ranked_tweets[:5], 1)
    ) or '<div class="muted">暂无</div>'
    case_blocks = [f"<h3>Views Top 5</h3>{top_html}"]
    case_blocks.append("<h3>Seedance 对比代表帖</h3>")
    reps = sd.get("representatives") or {}
    for key in ("hailuo_better", "mixed", "seedance_better"):
        if reps.get(key):
            item = reps[key][0]
            case_blocks.append(
                f'<div class="topic"><strong>SD：{escape({"hailuo_better": "Hailuo 更好", "mixed": "各有优劣", "seedance_better": "Seedance 更好"}.get(key, key))}</strong> · '
                f'<a href="{escape(item.get("url", ""))}" target="_blank" rel="noopener">{_fmt(item.get("views", 0))} Views 代表帖</a><br>{escape(item.get("reason", ""))}</div>'
            )
    section("二、具体案例", "".join(case_blocks))

    terms = report.get("related_terms", [])[:20]
    terms_html = " · ".join(f'<strong>{escape(str(item.get("term", "")))}</strong> ×{item.get("count", 0)}' for item in terms) or "暂无"
    section("三、词云", f'<div class="terms">{terms_html}</div>')

    negative_url = negative_file
    negative_html = (
        f'<p>负面 {negative.get("posts", 0)} 条 · Views {_fmt(negative.get("views", 0))} · '
        f'占总 Views {negative.get("views_share_pct", 0)}%</p>'
        f'<p><a class="button" href="{escape(negative_url)}">查看全部 {negative.get("posts", 0)} 条负面帖子</a></p>'
        f'<p>{escape(negative.get("summary", "整体传播影响较低，风险集中在产品体验和竞品比较。"))}</p>'
    )
    section("四、负面舆情", negative_html)
    section("完整帖子清单", f'<p class="muted">全部 {report.get("summary", {}).get("total_tweets", 0)} 条原创帖子支持搜索、排序和筛选。</p><a class="button" href="{escape(tweets_file)}">查看全部帖子</a>')
    return "".join(sections)


def render_tweet_row(t: dict) -> str:
    """输出一行 HTML 表格行。"""
    text = escape(t.get("text", ""))
    if len(text) > 200:
        text = text[:200] + "…"
    # 保留换行显示
    text = text.replace("\n", "<br>")
    return f"""    <tr data-views="{t.get('views',0)}" data-time="{escape(t.get('created_at',''))}"
            data-author="{escape(t.get('author',''))}" data-text="{escape(t.get('text',''))}"
            data-likes="{t.get('likes',0)}" data-retweets="{t.get('retweets',0)}"
            data-replies="{t.get('replies',0)}" data-quotes="{t.get('quotes',0)}"
            data-search="{escape((t.get('text','')+' '+t.get('author','')).lower())}">
      <td class="num views">{t.get('views',0):,}</td>
      <td>{escape(t.get('created_at','')[:16].replace('T',' '))}</td>
      <td class="author"><a href="{escape(t.get('author_url',''))}" target="_blank" rel="noopener">{escape(t.get('author',''))}</a></td>
      <td class="text">{text}</td>
      <td class="num">{t.get('likes',0)}</td>
      <td class="num">{t.get('retweets',0)}</td>
      <td class="num">{t.get('replies',0)}</td>
      <td class="num">{t.get('quotes',0)}</td>
      <td><a href="{escape(t.get('url',''))}" target="_blank" rel="noopener">打开 ↗</a></td>
    </tr>"""


def main():
    if len(sys.argv) > 1:
        report_path = Path(sys.argv[1])
    else:
        reports = sorted(ROOT.glob("cache/report_*.json"), reverse=True)
        if not reports:
            sys.exit("❌ cache/ 里没有 report_*.json")
        report_path = reports[0]

    print(f"读 {report_path}")
    with report_path.open() as f:
        report = json.load(f)

    # 全量 tweets 优先从 report["all_tweets"] 读,没有就用 top_tweets
    tweets = report.get("all_tweets") or report.get("top_tweets", [])
    summary = report.get("summary", {})
    meta = report.get("meta", {})
    data_quality = report.get("data_quality", {})
    tweets.sort(key=lambda tweet: tweet.get("views", 0), reverse=True)

    # 新报告优先使用固定窗口日期，旧报告再从文件名兼容推导。
    fname_stem = report_path.stem
    for prefix in ("report_", "full_"):
        if fname_stem.startswith(prefix):
            fname_stem = fname_stem[len(prefix):]
            break
    date_str = meta.get("report_date") or fname_stem[:8]
    if len(date_str) != 8 or not date_str.isdigit():
        sys.exit(f"❌ 无效报告日期: {date_str!r}")
    pretty_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    rows_html = "\n".join(render_tweet_row(t) for t in tweets)
    if not rows_html:
        rows_html = '    <tr><td class="empty" colspan="9">该时间窗口没有匹配推文</td></tr>'
    warnings = data_quality.get("warnings", [])
    quality_html = ""
    if warnings:
        warning_items = "".join(f"<li>{escape(str(item))}</li>" for item in warnings)
        quality_html = f'<div class="warning"><strong>⚠️ 数据可能不完整</strong><ul>{warning_items}</ul></div>'
    report_id = meta.get("report_id", report_path.stem)
    window_start = meta.get("window_start", "")
    window_end = meta.get("window_end", "")
    generated_at = meta.get("generated_at", fname_stem)
    tweets_file = f"{date_str}-tweets.html"
    negative_file = f"{date_str}-negative.html"
    launch_html = _render_launch_html(report, tweets_file, negative_file) if report.get("launch_analysis") else ""
    legacy_html = "" if launch_html else (
        f'<section><h2>全部帖子</h2><div class="muted">完整清单支持搜索、排序和筛选。</div>'
        f'<a class="button" href="{tweets_file}">查看全部 {len(tweets)} 条推文</a></section>'
        f'<section><h2>Views Top 5</h2>{"".join(_tweet_preview(tweet, index) for index, tweet in enumerate(report.get("top_tweets", [])[:5], 1)) or "<div class=\"muted\">暂无数据</div>"}</section>'
        f'<section><h2>舆情正负面占比</h2>{_render_sentiment(report)}</section>'
        f'<section><h2>热议话题 Top 3</h2>{_render_topics(report)}</section>'
        f'<section><h2>潜在风险监控</h2>{_render_risks(report)}</section>'
        f'<section><h2>Related 高频词</h2><div class="terms">{_render_terms(report)}</div></section>'
    )
    report_html = REPORT_TEMPLATE.format(
        title=f"Hailuo X 舆情报告 · {pretty_date}",
        meta=escape(
            f"固定窗口 {window_start} ~ {window_end} · 生成时间 {generated_at} · "
            f"数据源: X 搜索 · 报告 ID: {report_id}"
        ),
        quality_html=quality_html,
        total_tweets=summary.get("total_tweets", len(tweets)),
        total_views=summary.get("total_views", 0),
        total_engagement=summary.get("total_engagement", 0),
        authors=summary.get("authors", 0),
        tweets_file=tweets_file,
        launch_html=launch_html,
        legacy_html=legacy_html,
        top_tweets_html="".join(
            _tweet_preview(tweet, index)
            for index, tweet in enumerate(report.get("top_tweets", [])[:5], 1)
        ) or '<div class="muted">暂无数据</div>',
        sentiment_html=_render_sentiment(report),
        topics_html=_render_topics(report),
        risks_html=_render_risks(report),
        terms_html=_render_terms(report),
    )
    table_html = TWEETS_TEMPLATE.format(
        title=f"Hailuo X 全量推文 · {pretty_date}",
        meta=escape(
            f"固定窗口 {window_start} ~ {window_end} · 生成时间 {generated_at} · "
            f"数据源: X 搜索 · 报告 ID: {report_id}"
        ),
        quality_html=quality_html,
        total_tweets=summary.get("total_tweets", len(tweets)),
        total_views=summary.get("total_views", 0),
        total_engagement=summary.get("total_engagement", 0),
        authors=summary.get("authors", 0),
        window_hours=summary.get("window_hours", 24),
        rows=rows_html,
    )

    out_path = DOCS / f"{date_str}.html"
    tweets_path = DOCS / tweets_file
    out_path.write_text(report_html, encoding="utf-8")
    tweets_path.write_text(table_html, encoding="utf-8")
    negative_tweets = report.get("negative_tweets", [])
    negative_rows = "\n".join(render_tweet_row(tweet) for tweet in negative_tweets)
    if not negative_rows:
        negative_rows = '    <tr><td class="empty" colspan="9">本期没有负面帖子</td></tr>'
    negative_html = TWEETS_TEMPLATE.format(
        title=f"Hailuo X 负面帖子 · {pretty_date}",
        meta=escape(
            f"固定窗口 {window_start} ~ {window_end} · 生成时间 {generated_at} · "
            f"数据源: X 搜索 + AI 判定 · 报告 ID: {report_id}"
        ),
        quality_html=quality_html,
        total_tweets=len(negative_tweets),
        total_views=sum(int(tweet.get("views", 0) or 0) for tweet in negative_tweets),
        total_engagement=sum(sum(int(tweet.get(key, 0) or 0) for key in ("likes", "retweets", "replies", "quotes")) for tweet in negative_tweets),
        authors=len({tweet.get("author") for tweet in negative_tweets}),
        window_hours=summary.get("window_hours", 24),
        rows=negative_rows,
    )
    negative_path = DOCS / negative_file
    negative_path.write_text(negative_html, encoding="utf-8")
    print(f"✅ 写出 {out_path}  (完整报告)")
    print(f"✅ 写出 {tweets_path}  ({len(tweets)} 条)")
    print(f"✅ 写出 {negative_path}  ({len(negative_tweets)} 条负面帖子)")

    # 同步更新 manifest.json(index.html 用 JS 读)
    _update_manifest(DOCS)


def _update_manifest(docs_reports: Path):
    """扫描 reports/*.html,生成 manifest.json 列表(按文件名降序)。"""
    items = []
    for f in sorted(docs_reports.glob("*.html"), reverse=True):
        if f.name == "manifest.json" or not f.stem.isdigit():
            continue
        stem = f.stem  # e.g. 20260713
        if len(stem) != 8:
            continue
        items.append({
            "file": f.name,
            "label": f"{stem[:4]}-{stem[4:6]}-{stem[6:8]}",
            "count": _count_rows(f),
        })
    (docs_reports / "manifest.json").write_text(
        json.dumps(items, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"✅ 写出 manifest.json  ({len(items)} 个报告)")


def _count_rows(html_file: Path) -> int:
    """从 HTML 里数 data-views 属性的行,得到推文数。"""
    text = html_file.read_text(encoding="utf-8")
    match = re.search(r'data-report-count="(\d+)"', text)
    if match:
        return int(match.group(1))
    return text.count('data-views="')


if __name__ == "__main__":
    main()
