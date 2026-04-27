#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = [
#   "rich>=13.0",
#   "pyyaml>=6.0",
# ]
# ///
"""Phase 3: Report generation for the embedding eval harness.

Renders Rich terminal tables, saves JSON results, and generates
self-contained HTML reports with Chart.js visualizations.
"""

import datetime
import json
import sys
from pathlib import Path
from typing import Any

from rich.table import Table  # type: ignore[import-untyped]

# Ensure sibling scripts are importable
_SCRIPT_DIR = str(Path(__file__).resolve().parent)
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

from embed_eval_common import ComboResult, console  # noqa: E402


# ---------------------------------------------------------------------------
# Display (Rich terminal table)
# ---------------------------------------------------------------------------


def display_results(results: list[ComboResult], top_k: int) -> None:
    """Render a Rich table comparing all model x chunking combinations."""
    if not results:
        console.print("[yellow]No results to display.[/yellow]")
        return

    table = Table(
        title=f"Embedding Evaluation -- Recall & MRR (top_k={top_k})",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Model", style="bold", no_wrap=True)
    table.add_column("Chunking", no_wrap=True)
    table.add_column("R@1", justify="right")
    table.add_column("R@5", justify="right")
    table.add_column(f"R@{top_k}", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("Idx(s)", justify="right", style="dim")
    table.add_column("Q/s", justify="right")
    table.add_column("Chunks", justify="right", style="dim")

    best_mrr = results[0].mrr if results else 0.0
    best_qps = max((r.queries_per_sec for r in results), default=0.0)

    def fmt(v: float) -> str:
        return f"{v:.3f}"

    for res in results:
        model_short = res.model.split("/")[-1]
        mrr_str = fmt(res.mrr)
        qps_str = f"{res.queries_per_sec:.1f}"
        if res.mrr == best_mrr:
            mrr_str = f"[bold green]{mrr_str}[/bold green]"
        if res.queries_per_sec == best_qps:
            qps_str = f"[bold yellow]{qps_str}[/bold yellow]"

        table.add_row(
            model_short,
            res.chunking,
            fmt(res.recall_at_1),
            fmt(res.recall_at_5),
            fmt(res.recall_at_k),
            mrr_str,
            f"{res.index_time_s:.1f}",
            qps_str,
            str(res.chunk_count),
        )

    console.print()
    console.print(table)
    console.print()


# ---------------------------------------------------------------------------
# Save results as JSON
# ---------------------------------------------------------------------------


def save_json_results(
    results: list[ComboResult], output_path: Path, metadata: dict[str, Any]
) -> None:
    """Save evaluation results as JSON."""
    out_data: dict[str, Any] = {
        "metadata": metadata,
        "results": [
            {
                "model": r.model,
                "chunking": r.chunking,
                "recall_at_1": r.recall_at_1,
                "recall_at_5": r.recall_at_5,
                f"recall_at_{r.top_k}": r.recall_at_k,
                "mrr": r.mrr,
                "total_queries": r.total_queries,
                "top_k": r.top_k,
                "index_time_s": round(r.index_time_s, 3),
                "query_time_s": round(r.query_time_s, 3),
                "queries_per_sec": round(r.queries_per_sec, 2),
                "chunk_count": r.chunk_count,
            }
            for r in results
        ],
    }
    output_path.write_text(json.dumps(out_data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# HTML report generation
# ---------------------------------------------------------------------------


def generate_html_report(
    results: list[ComboResult],
    output_path: Path,
    top_k: int,
    metadata: dict[str, Any],
) -> None:
    """Generate a self-contained HTML report with charts and rankings.

    Args:
        results: Sorted ComboResults (best MRR first).
        output_path: Where to write the .html file.
        top_k: The K used for Recall@K.
        metadata: Run parameters dict.
    """
    if not results:
        return

    generated_at = metadata.get("generated_at", datetime.datetime.now().isoformat())
    notes_count = metadata.get("notes_sampled", "?")
    queries_count = metadata.get("total_queries", "?")

    # Prepare chart data
    labels = [f"{r.model.split('/')[-1]}\n{r.chunking}" for r in results]
    labels_js = json.dumps(labels)
    mrr_data = json.dumps([round(r.mrr, 4) for r in results])
    r1_data = json.dumps([round(r.recall_at_1, 4) for r in results])
    r5_data = json.dumps([round(r.recall_at_5, 4) for r in results])
    rk_data = json.dumps([round(r.recall_at_k, 4) for r in results])
    qps_data = json.dumps([round(r.queries_per_sec, 2) for r in results])
    idx_data = json.dumps([round(r.index_time_s, 2) for r in results])

    # Scatter: MRR vs queries_per_sec
    scatter_data = json.dumps(
        [
            {
                "x": round(r.queries_per_sec, 2),
                "y": round(r.mrr, 4),
                "label": f"{r.model.split('/')[-1]}/{r.chunking}",
            }
            for r in results
        ]
    )

    # Medal emojis for top 3
    medals = ["\U0001f947", "\U0001f948", "\U0001f949"]

    # Build rankings cards HTML
    ranking_cards = ""
    for i, res in enumerate(results[:3]):
        medal = medals[i] if i < len(medals) else f"#{i + 1}"
        short = res.model.split("/")[-1]
        ranking_cards += f"""
        <div class="rank-card rank-{i + 1}">
          <div class="medal">{medal}</div>
          <div class="rank-model">{short}</div>
          <div class="rank-chunking">{res.chunking}</div>
          <div class="rank-metrics">
            <span class="metric-pill">MRR <strong>{res.mrr:.3f}</strong></span>
            <span class="metric-pill">R@1 <strong>{res.recall_at_1:.3f}</strong></span>
            <span class="metric-pill">Q/s <strong>{res.queries_per_sec:.1f}</strong></span>
          </div>
        </div>
        """

    # Build full results table HTML
    table_rows = ""
    for i, res in enumerate(results):
        short = res.model.split("/")[-1]
        rank = i + 1
        best_class = " best-row" if i == 0 else ""
        table_rows += f"""
        <tr class="result-row{best_class}">
          <td class="rank">#{rank}</td>
          <td class="model-name" title="{res.model}">{short}</td>
          <td>{res.chunking}</td>
          <td class="metric">{res.recall_at_1:.3f}</td>
          <td class="metric">{res.recall_at_5:.3f}</td>
          <td class="metric">{res.recall_at_k:.3f}</td>
          <td class="metric highlight">{res.mrr:.3f}</td>
          <td class="metric">{res.index_time_s:.1f}s</td>
          <td class="metric">{res.queries_per_sec:.1f}</td>
          <td class="dim">{res.chunk_count:,}</td>
          <td class="dim">{res.total_queries:,}</td>
        </tr>
        """

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Embedding Eval — Parsidion vault</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
  <style>
    :root {{
      --bg: #0d1117;
      --surface: #161b22;
      --surface2: #1c2128;
      --border: #30363d;
      --text: #e6edf3;
      --muted: #8b949e;
      --accent: #58a6ff;
      --green: #3fb950;
      --yellow: #d29922;
      --orange: #db6d28;
      --purple: #bc8cff;
      --gold: #ffd700;
      --silver: #c0c0c0;
      --bronze: #cd7f32;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      font-family: 'JetBrains Mono', 'Fira Code', 'Cascadia Code', monospace;
      background: var(--bg);
      color: var(--text);
      min-height: 100vh;
      padding: 2rem;
    }}
    header {{
      border-bottom: 1px solid var(--border);
      padding-bottom: 1.5rem;
      margin-bottom: 2rem;
    }}
    header h1 {{
      font-size: 1.6rem;
      color: var(--accent);
      letter-spacing: -0.5px;
    }}
    header .subtitle {{
      color: var(--muted);
      font-size: 0.8rem;
      margin-top: 0.4rem;
    }}
    .meta-pills {{
      display: flex;
      gap: 0.75rem;
      margin-top: 0.75rem;
      flex-wrap: wrap;
    }}
    .meta-pill {{
      background: var(--surface2);
      border: 1px solid var(--border);
      border-radius: 4px;
      padding: 0.2rem 0.6rem;
      font-size: 0.75rem;
      color: var(--muted);
    }}
    .meta-pill strong {{ color: var(--text); }}
    section {{ margin-bottom: 2.5rem; }}
    h2 {{
      font-size: 1rem;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: 1px;
      margin-bottom: 1rem;
    }}
    /* Rankings */
    .rankings {{
      display: flex;
      gap: 1rem;
      flex-wrap: wrap;
    }}
    .rank-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem;
      flex: 1;
      min-width: 200px;
    }}
    .rank-1 {{ border-color: var(--gold); }}
    .rank-2 {{ border-color: var(--silver); }}
    .rank-3 {{ border-color: var(--bronze); }}
    .medal {{ font-size: 2rem; margin-bottom: 0.5rem; }}
    .rank-model {{ font-weight: bold; color: var(--accent); font-size: 0.95rem; }}
    .rank-chunking {{ color: var(--muted); font-size: 0.8rem; margin-top: 0.2rem; }}
    .rank-metrics {{ display: flex; gap: 0.5rem; margin-top: 0.75rem; flex-wrap: wrap; }}
    .metric-pill {{
      background: var(--surface2);
      border-radius: 4px;
      padding: 0.2rem 0.5rem;
      font-size: 0.75rem;
      color: var(--muted);
    }}
    .metric-pill strong {{ color: var(--green); }}
    /* Charts */
    .charts-grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
      gap: 1.25rem;
    }}
    .chart-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem;
    }}
    .chart-card h3 {{
      font-size: 0.85rem;
      color: var(--muted);
      margin-bottom: 1rem;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }}
    .chart-wrap {{
      position: relative;
      height: 260px;
    }}
    /* Table */
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid var(--border);
      border-radius: 8px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 0.82rem;
    }}
    th {{
      background: var(--surface2);
      color: var(--muted);
      text-transform: uppercase;
      font-size: 0.7rem;
      letter-spacing: 0.5px;
      padding: 0.6rem 0.75rem;
      border-bottom: 1px solid var(--border);
      text-align: right;
      white-space: nowrap;
    }}
    th:first-child, th:nth-child(2), th:nth-child(3) {{ text-align: left; }}
    td {{
      padding: 0.55rem 0.75rem;
      border-bottom: 1px solid var(--border);
      text-align: right;
    }}
    td:first-child, td:nth-child(2), td:nth-child(3) {{ text-align: left; }}
    tr:last-child td {{ border-bottom: none; }}
    .best-row td {{ background: rgba(63, 185, 80, 0.06); }}
    .rank {{ color: var(--muted); }}
    .model-name {{ color: var(--accent); font-weight: bold; max-width: 200px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }}
    .metric {{ font-variant-numeric: tabular-nums; }}
    .highlight {{ color: var(--green); font-weight: bold; }}
    .dim {{ color: var(--muted); }}
    tr:hover td {{ background: var(--surface2); }}
  </style>
</head>
<body>

<header>
  <h1>Embedding Evaluation — Parsidion vault</h1>
  <div class="subtitle">Model x Chunking benchmark using Claude-generated ground truth</div>
  <div class="meta-pills">
    <span class="meta-pill">Generated <strong>{generated_at[:19]}</strong></span>
    <span class="meta-pill">Notes sampled <strong>{notes_count}</strong></span>
    <span class="meta-pill">Total queries <strong>{queries_count}</strong></span>
    <span class="meta-pill">top_k <strong>{top_k}</strong></span>
    <span class="meta-pill">Combos <strong>{len(results)}</strong></span>
  </div>
</header>

<section>
  <h2>Top Rankings</h2>
  <div class="rankings">
    {ranking_cards}
  </div>
</section>

<section>
  <h2>Charts</h2>
  <div class="charts-grid">
    <div class="chart-card">
      <h3>MRR (Mean Reciprocal Rank)</h3>
      <div class="chart-wrap"><canvas id="mrrChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Recall@1</h3>
      <div class="chart-wrap"><canvas id="r1Chart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Recall@5</h3>
      <div class="chart-wrap"><canvas id="r5Chart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Recall@{top_k}</h3>
      <div class="chart-wrap"><canvas id="rkChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Speed — Queries / Second</h3>
      <div class="chart-wrap"><canvas id="qpsChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Index Time (seconds)</h3>
      <div class="chart-wrap"><canvas id="idxChart"></canvas></div>
    </div>
    <div class="chart-card">
      <h3>Quality vs Speed (MRR vs Q/s)</h3>
      <div class="chart-wrap"><canvas id="scatterChart"></canvas></div>
    </div>
  </div>
</section>

<section>
  <h2>Full Results</h2>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Rank</th>
          <th>Model</th>
          <th>Chunking</th>
          <th>R@1</th>
          <th>R@5</th>
          <th>R@{top_k}</th>
          <th>MRR</th>
          <th>Idx Time</th>
          <th>Q/s</th>
          <th>Chunks</th>
          <th>Queries</th>
        </tr>
      </thead>
      <tbody>
        {table_rows}
      </tbody>
    </table>
  </div>
</section>

<script>
const LABELS = {labels_js};
const palette = [
  '#58a6ff','#3fb950','#d29922','#f78166','#bc8cff',
  '#79c0ff','#56d364','#e3b341','#ffa198','#d2a8ff',
];

function barConfig(label, data, color) {{
  return {{
    type: 'bar',
    data: {{
      labels: LABELS,
      datasets: [{{ label, data, backgroundColor: color + 'bb', borderColor: color, borderWidth: 1 }}]
    }},
    options: {{
      indexAxis: 'y',
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{ legend: {{ display: false }} }},
      scales: {{
        x: {{ grid: {{ color: '#30363d' }}, ticks: {{ color: '#8b949e', font: {{ size: 10 }} }} }},
        y: {{ grid: {{ color: '#30363d' }}, ticks: {{ color: '#e6edf3', font: {{ size: 9 }} }} }}
      }}
    }}
  }};
}}

new Chart(document.getElementById('mrrChart'), barConfig('MRR', {mrr_data}, '#3fb950'));
new Chart(document.getElementById('r1Chart'),  barConfig('R@1', {r1_data}, '#58a6ff'));
new Chart(document.getElementById('r5Chart'),  barConfig('R@5', {r5_data}, '#79c0ff'));
new Chart(document.getElementById('rkChart'),  barConfig('R@{top_k}', {rk_data}, '#bc8cff'));
new Chart(document.getElementById('qpsChart'), barConfig('Q/s', {qps_data}, '#d29922'));
new Chart(document.getElementById('idxChart'), barConfig('Idx(s)', {idx_data}, '#f78166'));

// Scatter: quality vs speed
const scatterRaw = {scatter_data};
new Chart(document.getElementById('scatterChart'), {{
  type: 'scatter',
  data: {{
    datasets: [{{
      label: 'Model x Chunking',
      data: scatterRaw,
      backgroundColor: scatterRaw.map((_, i) => palette[i % palette.length] + 'cc'),
      pointRadius: 8,
      pointHoverRadius: 10,
    }}]
  }},
  options: {{
    responsive: true,
    maintainAspectRatio: false,
    plugins: {{
      legend: {{ display: false }},
      tooltip: {{
        callbacks: {{
          label: ctx => `${{ctx.raw.label}}: MRR=${{ctx.raw.y.toFixed(3)}} Q/s=${{ctx.raw.x.toFixed(1)}}`
        }}
      }}
    }},
    scales: {{
      x: {{ title: {{ display: true, text: 'Queries / Second', color: '#8b949e' }}, grid: {{ color: '#30363d' }}, ticks: {{ color: '#8b949e' }} }},
      y: {{ title: {{ display: true, text: 'MRR', color: '#8b949e' }}, grid: {{ color: '#30363d' }}, ticks: {{ color: '#8b949e' }} }}
    }}
  }}
}});
</script>
</body>
</html>"""

    output_path.write_text(html, encoding="utf-8")
