"""把 report JSON 渲染成静态 HTML,放到 docs/reports/。

用法:
  python scripts/render_full_report.py reports/report_YYYYMMDD_HHMM.json
  # 或不传参数,自动用最新的 report_*.json
"""
from __future__ import annotations

import json
import sys
from html import escape
from pathlib import Path

ROOT = Path(__file__).parent.parent
DOCS = ROOT / "docs" / "reports"
DOCS.mkdir(parents=True, exist_ok=True)


HTML_TEMPLATE = """<!doctype html>
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
  .empty {{ text-align: center; padding: 40px; color: #656d76; }}
  .row-hidden {{ display: none; }}
</style>
</head>
<body>
<h1>📊 {title}</h1>
<div class="meta">{meta}</div>

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

    if not tweets:
        sys.exit("❌ report 里没有推文数据")

    # 文件名用报告里的时间戳,或 fallback 到文件名
    fname_stem = report_path.stem
    for prefix in ("report_", "full_"):
        if fname_stem.startswith(prefix):
            fname_stem = fname_stem[len(prefix):]
            break
    date_str = fname_stem[:8]  # YYYYMMDD
    pretty_date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"

    rows_html = "\n".join(render_tweet_row(t) for t in tweets)
    html = HTML_TEMPLATE.format(
        title=f"Hailuo X 全量推文 · {pretty_date}",
        meta=f"生成时间 {fname_stem} · 数据源: X 搜索 · 报告 ID: {report_path.stem}",
        total_tweets=summary.get("total_tweets", len(tweets)),
        total_views=summary.get("total_views", 0),
        total_engagement=summary.get("total_engagement", 0),
        authors=summary.get("authors", 0),
        window_hours=summary.get("window_hours", 24),
        rows=rows_html,
    )

    out_path = DOCS / f"{date_str}.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"✅ 写出 {out_path}  ({len(tweets)} 条)")

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
    return text.count('data-views="')


if __name__ == "__main__":
    main()
