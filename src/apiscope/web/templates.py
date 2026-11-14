"""Jinja2 HTML templates and rendering environment for ApiScope Web Dashboard.

Provides autoescaped HTML5 templates with developer-tool styling, Chart.js trend
visualizations, Expected vs. Actual failure diagnostics, Guided Workflow visualizer,
Project Intelligence, Test Planning & Review, Execution Previews, and Trust Reports.
"""

from __future__ import annotations

import json

from jinja2 import DictLoader, Environment, select_autoescape

_BASE_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}ApiScope Dashboard{% endblock %}</title>
  <style>
    :root {
      --primary: #2563eb;
      --primary-hover: #1d4ed8;
      --bg: #f8fafc;
      --surface: #ffffff;
      --text: #0f172a;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --badge-pass-bg: #dcfce7;
      --badge-pass-text: #166534;
      --badge-fail-bg: #fee2e2;
      --badge-fail-text: #991b1b;
      --badge-warn-bg: #fef3c7;
      --badge-warn-text: #92400e;
      --badge-skip-bg: #f1f5f9;
      --badge-skip-text: #475569;
      --badge-info-bg: #eff6ff;
      --badge-info-text: #1e40af;
      --badge-ai-bg: #f3e8ff;
      --badge-ai-text: #6b21a8;
    }
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
        Roboto, Helvetica, Arial, sans-serif;
      background-color: var(--bg);
      color: var(--text);
      line-height: 1.5;
    }
    .navbar {
      background: var(--surface);
      border-bottom: 1px solid var(--border);
      padding: 0.75rem 2rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
      position: sticky;
      top: 0;
      z-index: 100;
    }
    .navbar-brand {
      font-size: 1.25rem;
      font-weight: 700;
      color: var(--primary);
      text-decoration: none;
      display: flex;
      align-items: center;
      gap: 0.5rem;
    }
    .navbar-brand span { color: var(--text); }
    .nav-links {
      display: flex;
      gap: 0.5rem;
      list-style: none;
      flex-wrap: wrap;
    }
    .nav-links a {
      color: var(--text-muted);
      text-decoration: none;
      font-size: 0.85rem;
      font-weight: 500;
      padding: 0.35rem 0.65rem;
      border-radius: 6px;
      transition: all 0.15s ease;
      white-space: nowrap;
    }
    .nav-links a:hover, .nav-links a.active {
      color: var(--primary);
      background-color: #eff6ff;
    }
    .status-pill {
      display: inline-flex;
      align-items: center;
      gap: 0.35rem;
      font-size: 0.75rem;
      font-weight: 600;
      padding: 0.2rem 0.6rem;
      border-radius: 9999px;
      border: 1px solid #cbd5e1;
      background: #f8fafc;
      color: #334155;
    }
    .status-pill-safe {
      background: #f0fdf4;
      border-color: #bbf7d0;
      color: #166534;
    }
    .status-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #22c55e;
    }
    .container {
      max-width: 1200px;
      margin: 1.5rem auto;
      padding: 0 1.25rem;
    }
    .card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.5rem;
      margin-bottom: 1.5rem;
      box-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05);
    }
    .card-title {
      font-size: 1.15rem;
      font-weight: 600;
      margin-bottom: 1rem;
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .stat-card {
      background: var(--bg);
      border: 1px solid var(--border);
      border-radius: 6px;
      padding: 1rem;
      text-align: center;
    }
    .stat-value {
      font-size: 1.75rem;
      font-weight: 700;
      color: var(--text);
    }
    .stat-label {
      font-size: 0.8rem;
      color: var(--text-muted);
      text-transform: uppercase;
      font-weight: 600;
      margin-top: 0.25rem;
    }
    .badge {
      display: inline-block;
      padding: 0.25rem 0.5rem;
      border-radius: 4px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
    }
    .badge-passed { background: var(--badge-pass-bg); color: var(--badge-pass-text); }
    .badge-failed { background: var(--badge-fail-bg); color: var(--badge-fail-text); }
    .badge-warning { background: var(--badge-warn-bg); color: var(--badge-warn-text); }
    .badge-skipped { background: var(--badge-skip-bg); color: var(--badge-skip-text); }
    .badge-error { background: #fee2e2; color: #991b1b; }
    .badge-pass { background: var(--badge-pass-bg); color: var(--badge-pass-text); }
    .badge-fail { background: var(--badge-fail-bg); color: var(--badge-fail-text); }
    .badge-warn { background: var(--badge-warn-bg); color: var(--badge-warn-text); }
    .badge-skip { background: var(--badge-skip-bg); color: var(--badge-skip-text); }
    .badge-info { background: var(--badge-info-bg); color: var(--badge-info-text); }
    .badge-ai { background: var(--badge-ai-bg); color: var(--badge-ai-text); }
    .badge-blocked { background: #fee2e2; color: #991b1b; }
    .badge-untested { background: #f1f5f9; color: #64748b; }
    .badge-get { background: #dbeafe; color: #1e40af; font-weight: 700; }
    .badge-post { background: #dcfce7; color: #166534; font-weight: 700; }
    .badge-put { background: #fef3c7; color: #92400e; font-weight: 700; }
    .badge-delete { background: #fee2e2; color: #991b1b; font-weight: 700; }
    .badge-patch { background: #f3e8ff; color: #6b21a8; font-weight: 700; }
    .table-container {
      overflow-x: auto;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 0.9rem;
      text-align: left;
    }
    th, td {
      padding: 0.75rem 1rem;
      border-bottom: 1px solid var(--border);
    }
    th {
      background-color: var(--bg);
      color: var(--text-muted);
      font-weight: 600;
      text-transform: uppercase;
      font-size: 0.75rem;
      letter-spacing: 0.05em;
    }
    tr:hover { background-color: #f1f5f9; }
    .btn {
      display: inline-block;
      padding: 0.5rem 1rem;
      font-size: 0.875rem;
      font-weight: 500;
      border-radius: 6px;
      text-decoration: none;
      cursor: pointer;
      border: 1px solid transparent;
      transition: all 0.15s ease;
    }
    .btn-primary { background: var(--primary); color: white; }
    .btn-primary:hover { background: var(--primary-hover); }
    .btn-outline {
      background: transparent;
      border-color: var(--border);
      color: var(--text);
    }
    .btn-outline:hover { background: var(--bg); }
    .btn-secondary { background: #f1f5f9; color: var(--text); border-color: var(--border); }
    .btn-secondary:hover { background: #e2e8f0; }
    .btn-danger { background: #ef4444; color: white; }
    .btn-danger:hover { background: #dc2626; }
    .btn-sm { padding: 0.25rem 0.5rem; font-size: 0.75rem; }
    .empty-state {
      text-align: center;
      padding: 3rem 1rem;
      color: var(--text-muted);
      font-size: 1rem;
    }
    .charts-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(450px, 1fr));
      gap: 1.5rem;
      margin-bottom: 1.5rem;
    }
    .chart-container {
      position: relative;
      width: 100%;
      height: 250px;
    }
    .form-group { margin-bottom: 1rem; }
    .form-label {
      display: block;
      font-size: 0.85rem;
      font-weight: 500;
      margin-bottom: 0.25rem;
      color: var(--text);
    }
    .form-input {
      width: 100%;
      padding: 0.5rem 0.75rem;
      border: 1px solid var(--border);
      border-radius: 6px;
      font-size: 0.9rem;
    }
    .form-input:focus {
      outline: none;
      border-color: var(--primary);
      box-shadow: 0 0 0 2px rgba(37, 99, 235, 0.1);
    }
    .form-control {
      width: 100%;
      padding: 0.45rem 0.65rem;
      font-size: 0.85rem;
      border: 1px solid var(--border);
      border-radius: 6px;
      background: var(--surface);
      color: var(--text);
    }
    .form-checkbox { margin-right: 0.4rem; vertical-align: middle; }
    .form-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1rem;
    }
    pre.code-block {
      background: #1e293b;
      color: #f8fafc;
      padding: 0.75rem 1rem;
      border-radius: 6px;
      font-family: monospace;
      font-size: 0.85rem;
      overflow-x: auto;
    }
    .alert {
      padding: 0.75rem 1rem;
      border-radius: 6px;
      font-size: 0.85rem;
      margin-bottom: 1rem;
      border: 1px solid transparent;
    }
    .alert-info { background: #eff6ff; border-color: #bfdbfe; color: #1e40af; }
    .alert-warning { background: #fef3c7; border-color: #fde68a; color: #92400e; }
    .alert-danger { background: #fee2e2; border-color: #fecaca; color: #991b1b; }
    .alert-success { background: #dcfce7; border-color: #bbf7d0; color: #166534; }
    .progress-bar {
      height: 8px;
      background: #e2e8f0;
      border-radius: 4px;
      overflow: hidden;
      margin-top: 0.35rem;
    }
    .progress-fill {
      height: 100%;
      background: var(--primary);
      border-radius: 4px;
    }
    footer {
      text-align: center;
      padding: 2rem;
      color: var(--text-muted);
      font-size: 0.85rem;
      border-top: 1px solid var(--border);
      margin-top: 4rem;
    }
  </style>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
</head>
<body>
  <nav class="navbar">
    <a href="/runs" class="navbar-brand">
      ApiScope <span>Dashboard</span>
    </a>
    <ul class="nav-links">
      <li><a href="/" class="{% if active_tab == 'overview' %}active{% endif %}">Overview</a></li>
      <li><a href="/guided" class="{% if active_tab == 'guided' %}active{% endif %}">Guided Workflow</a></li>
      <li><a href="/intelligence" class="{% if active_tab == 'intelligence' %}active{% endif %}">Project Intelligence</a></li>
      <li><a href="/planning" class="{% if active_tab == 'planning' %}active{% endif %}">Test Planning</a></li>
      <li><a href="/execution" class="{% if active_tab == 'execution' %}active{% endif %}">Execution Preview</a></li>
      <li><a href="/trust" class="{% if active_tab == 'trust' %}active{% endif %}">Trust &amp; Diagnostics</a></li>
      <li><a href="/runs" class="{% if active_tab == 'runs' %}active{% endif %}">Runs &amp; Trends</a></li>
      <li><a href="/health" class="{% if active_tab == 'health' %}active{% endif %}">Module Health</a></li>
      <li><a href="/zombies" class="{% if active_tab == 'zombies' %}active{% endif %}">Zombie &amp; Silent Failures</a></li>
    </ul>
    <div style="display:flex; align-items:center; gap:0.5rem;">
      <span class="status-pill status-pill-safe">
        <span class="status-dot"></span> OFFLINE / READ-ONLY
      </span>
    </div>
  </nav>
  <main class="container">
    {% block content %}{% endblock %}
  </main>
  {% block scripts %}{% endblock %}
  <footer>
    ApiScope &bull; Automated API Testing, Health Monitoring &amp; Trust Diagnostics
  </footer>
</body>
</html>
"""

_OVERVIEW_TEMPLATE = """{% extends "base.html" %}
{% block title %}Overview &bull; ApiScope{% endblock %}
{% block content %}
<div style="margin-bottom:1.25rem; display:flex; justify-content:space-between; align-items:flex-end;">
  <div>
    <h1 style="font-size:1.5rem; font-weight:700;">{{ data.project_name }} <span style="font-size:0.95rem; color:var(--text-muted); font-weight:normal;">({{ data.api_version }})</span></h1>
    <p style="color:var(--text-muted); font-size:0.85rem;">Target: <code>{{ data.target_origin }}</code> &bull; Env: <strong>{{ data.environment_name }}</strong> &bull; Policy: <span class="badge badge-passed">READ_ONLY / SAFE</span></p>
  </div>
  <div>
    <a href="/guided" class="btn btn-primary">Guided Workflow &rarr;</a>
  </div>
</div>

<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-value">{{ data.total_documented }}</div>
    <div class="stat-label">Documented Operations</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#2563eb;">{{ data.attempted_ops }}</div>
    <div class="stat-label">Exercised Operations</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#166534;">{{ data.passed_ops }}</div>
    <div class="stat-label">Passed Operations</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#b91c1c;">{{ data.failed_ops }}</div>
    <div class="stat-label">Failed Operations</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#64748b;">{{ data.blocked_ops }}</div>
    <div class="stat-label">Blocked Mutations</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#92400e;">{{ data.untested_ops }}</div>
    <div class="stat-label">Untested Operations</div>
  </div>
</div>

<div style="display:grid; grid-template-columns: 2fr 1fr; gap:1.25rem; margin-bottom:1.25rem;">
  <div class="card" style="margin-bottom:0;">
    <div class="card-title">Operation Coverage Intelligence</div>
    <div style="margin-bottom:1rem;">
      <div style="display:flex; justify-content:space-between; font-size:0.8rem; font-weight:600;">
        <span>Read-Only Operations ({{ data.attempted_ops }} / {{ data.readonly_total }})</span>
        <span>{{ data.readonly_pct }}%</span>
      </div>
      <div class="progress-bar"><div class="progress-fill" style="width: {{ data.readonly_pct }}%; background:#10b981;"></div></div>
    </div>
    <div style="margin-bottom:1rem;">
      <div style="display:flex; justify-content:space-between; font-size:0.8rem; font-weight:600;">
        <span>Mutating Operations (0 / {{ data.mutation_total }} - Fenced by Policy)</span>
        <span>{{ data.mutation_pct }}%</span>
      </div>
      <div class="progress-bar"><div class="progress-fill" style="width: {{ data.mutation_pct }}%; background:#ef4444;"></div></div>
    </div>
    <div>
      <div style="display:flex; justify-content:space-between; font-size:0.8rem; font-weight:600;">
        <span>Overall Verified Coverage ({{ data.attempted_ops }} / {{ data.total_documented }})</span>
        <span>{{ data.overall_pct }}%</span>
      </div>
      <div class="progress-bar"><div class="progress-fill" style="width: {{ data.overall_pct }}%;"></div></div>
    </div>
  </div>

  <div class="card" style="margin-bottom:0;">
    <div class="card-title">Coverage Breakdown</div>
    <div style="max-height: 180px; position:relative; display:flex; justify-content:center;">
      <canvas id="coverageDoughnutChart"></canvas>
    </div>
  </div>
</div>

{% if data.outstanding_reviews > 0 %}
<div class="alert alert-warning" style="display:flex; justify-content:space-between; align-items:center;">
  <div>
    <strong>Human Review Required:</strong> {{ data.outstanding_reviews }} proposed test scenario(s) require human authorization before execution.
  </div>
  <a href="/planning" class="btn btn-secondary btn-sm">Review Scenarios &rarr;</a>
</div>
{% endif %}

{% if data.issue_summaries %}
<div class="card">
  <div class="card-title">Evidence-Backed Diagnostic Summaries</div>
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Category</th>
          <th>Severity</th>
          <th>Count</th>
          <th>Description</th>
        </tr>
      </thead>
      <tbody>
        {% for issue in data.issue_summaries %}
        <tr>
          <td><strong>{{ issue.category }}</strong></td>
          <td><span class="badge {% if issue.severity == 'HIGH' %}badge-fail{% elif issue.severity == 'WARNING' %}badge-warn{% else %}badge-info{% endif %}">{{ issue.severity }}</span></td>
          <td>{{ issue.count }}</td>
          <td>{{ issue.description }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endif %}

<div class="card">
  <div class="card-title">
    <span>Recent Test Execution Runs</span>
    <a href="/runs" class="btn btn-secondary btn-sm">View Runs History &rarr;</a>
  </div>
  {% if not data.runs %}
  <div class="empty-state">
    No test runs recorded yet. Use the Guided Analysis or Trigger API to execute a test suite.
  </div>
  {% else %}
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Run ID</th>
          <th>Environment</th>
          <th>Total Cases</th>
          <th>Passed</th>
          <th>Failed</th>
          <th>Duration</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {% for r in data.runs %}
        <tr>
          <td><code>{{ r.run_id }}</code></td>
          <td>{{ r.environment_name }}</td>
          <td>{{ r.total_tests }}</td>
          <td><span class="badge badge-pass">{{ r.passed_tests }}</span></td>
          <td><span class="badge {% if r.failed_tests > 0 %}badge-fail{% else %}badge-skip{% endif %}">{{ r.failed_tests }}</span></td>
          <td>{{ "%.1f"|format(r.duration_ms) }} ms</td>
          <td>
            <a href="/runs/{{ r.run_id }}" class="btn btn-secondary btn-sm">View Details</a>
            <a href="/runs/{{ r.run_id }}/download?format=json" class="btn btn-secondary btn-sm">JSON</a>
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>
{% endblock %}

{% block scripts %}
<script>
  if (window.Chart) {
    const ctx = document.getElementById('coverageDoughnutChart');
    if (ctx) {
      new Chart(ctx, {
        type: 'doughnut',
        data: {
          labels: ['Passed GETs', 'Blocked Mutations', 'Untested GETs'],
          datasets: [{
            data: [{{ data.passed_ops }}, {{ data.blocked_ops }}, {{ data.untested_ops }}],
            backgroundColor: ['#10b981', '#cbd5e1', '#f59e0b'],
            borderWidth: 1
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } }
          }
        }
      });
    }
  }
</script>
{% endblock %}
"""

_GUIDED_TEMPLATE = """{% extends "base.html" %}
{% block title %}Guided Workflow &bull; ApiScope{% endblock %}
{% block content %}
<div style="margin-bottom:1.25rem;">
  <h1 style="font-size:1.5rem; font-weight:700;">Guided Project Testing Workflow</h1>
  <p style="color:var(--text-muted); font-size:0.85rem;">11-stage autonomous orchestration pipeline with exact-origin authorization and execution safety policies.</p>
</div>

<div class="card">
  <div class="card-title">Configure Analysis &amp; Execution Preview</div>
  <form method="POST" action="/api/guided/run">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap:1rem; margin-bottom:1rem;">
      <div class="form-group">
        <label class="form-label">OpenAPI Spec Path</label>
        <input type="text" name="spec_path" class="form-control" value="{{ data.spec_path }}" placeholder="e.g. stockpile_tests/stockpile_openapi.yaml" required>
      </div>
      <div class="form-group">
        <label class="form-label">Source Repo Path (Optional)</label>
        <input type="text" name="repo_path" class="form-control" placeholder="e.g. ../StockPile">
      </div>
      <div class="form-group">
        <label class="form-label">Target Origin</label>
        <input type="text" name="target_origin" class="form-control" value="{{ data.target_origin }}" placeholder="http://localhost:8080">
      </div>
      <div class="form-group">
        <label class="form-label">Environment Name</label>
        <input type="text" name="environment_name" class="form-control" value="{{ data.environment_name }}">
      </div>
    </div>

    <div class="alert alert-info" style="font-size:0.8rem; margin-bottom:1rem;">
      <strong>Execution Safety Gate:</strong> Mutating operations are strictly fenced. To enable mutations, both boxes below must be explicitly checked.
    </div>

    <div style="display:flex; gap:1.5rem; flex-wrap:wrap; margin-bottom:1rem;">
      <label style="font-size:0.85rem; font-weight:500;">
        <input type="checkbox" name="allow_mutation" value="true" class="form-checkbox">
        Allow Mutating Operations (<code>--allow-mutation</code>)
      </label>
      <label style="font-size:0.85rem; font-weight:500;">
        <input type="checkbox" name="confirm_isolated_env" value="true" class="form-checkbox">
        Confirm Isolated Test Environment (<code>--confirm-isolated-env</code>)
      </label>
    </div>

    <button type="submit" class="btn btn-primary">Run Offline Analysis &amp; Preview</button>
  </form>
</div>

<div class="card">
  <div class="card-title">Workflow Lifecycle Stages</div>
  <div style="display:flex; flex-direction:column; gap:0.75rem;">
    {% for s in data.stages %}
    <div style="border:1px solid var(--border); border-radius:6px; padding:0.85rem; background: {% if s.status == 'COMPLETED' %}#f8fafc{% elif s.status == 'COMPLETED_WITH_WARNINGS' %}#fffbeb{% else %}var(--surface){% endif %};">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.35rem;">
        <div>
          <strong style="font-size:0.95rem;">{{ s.title }}</strong>
          <span style="font-size:0.75rem; color:var(--text-muted); margin-left:0.5rem;">({{ "%.1f"|format(s.duration_ms) }} ms)</span>
        </div>
        <span class="badge {% if s.status == 'COMPLETED' %}badge-pass{% elif s.status == 'COMPLETED_WITH_WARNINGS' %}badge-warn{% elif s.status == 'NOT_STARTED' %}badge-skip{% else %}badge-fail{% endif %}">
          {{ s.status }}
        </span>
      </div>
      <p style="font-size:0.85rem; color:var(--text-muted); margin-bottom:0.35rem;">{{ s.summary }}</p>

      {% if s.warnings %}
      <div style="font-size:0.8rem; color:#b45309; margin-bottom:0.35rem;">
        <strong>Warnings:</strong>
        <ul style="padding-left:1.25rem;">
          {% for w in s.warnings %}
          <li>{{ w }}</li>
          {% endfor %}
        </ul>
      </div>
      {% endif %}

      {% if s.artifacts %}
      <div style="font-size:0.75rem;">
        <strong>Artifacts:</strong>
        {% for a in s.artifacts %}
        <a href="/artifacts/{{ a }}" class="btn btn-secondary btn-sm" style="padding:0.15rem 0.4rem; font-size:0.7rem; margin-left:0.25rem;">{{ a }}</a>
        {% endfor %}
      </div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
</div>
{% endblock %}
"""

_INTELLIGENCE_TEMPLATE = """{% extends "base.html" %}
{% block title %}Project Intelligence &bull; ApiScope{% endblock %}
{% block content %}
<div style="margin-bottom:1.25rem;">
  <h1 style="font-size:1.5rem; font-weight:700;">Project Intelligence</h1>
  <p style="color:var(--text-muted); font-size:0.85rem;">Detected modules, domain entities, and endpoint inventory with AST confidence provenance.</p>
</div>

<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-value">{{ data.total_modules }}</div>
    <div class="stat-label">Discovered Modules</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{{ data.total_entities }}</div>
    <div class="stat-label">Domain Entities</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">{{ data.total_endpoints }}</div>
    <div class="stat-label">Total Endpoints</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="font-size:1.15rem;">{{ data.source_language }}</div>
    <div class="stat-label">Source Language</div>
  </div>
</div>

<div class="card">
  <div class="card-title">Discovered Modules</div>
  <div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr)); gap:0.75rem;">
    {% for m in data.modules %}
    <div style="border:1px solid var(--border); border-radius:6px; padding:0.75rem; background:var(--bg);">
      <strong style="font-size:0.9rem;">{{ m.name }}</strong>
      <div style="font-size:0.75rem; color:var(--text-muted); margin-top:0.25rem;">
        {{ m.endpoints_count }} endpoints &bull; {{ m.entities_count }} entities
      </div>
    </div>
    {% endfor %}
  </div>
</div>

<div class="card">
  <div class="card-title">Endpoint Inventory</div>
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Method</th>
          <th>Path</th>
          <th>Module</th>
          <th>Operation ID</th>
          <th>Provenance</th>
          <th>AST Confidence</th>
        </tr>
      </thead>
      <tbody>
        {% for ep in data.endpoints %}
        <tr>
          <td><span class="badge badge-{{ ep.method|lower }}">{{ ep.method }}</span></td>
          <td><code>{{ ep.path }}</code></td>
          <td><strong>{{ ep.module }}</strong></td>
          <td>{{ ep.operation_id or '-' }}</td>
          <td><span class="badge badge-info">{{ ep.provenance }}</span></td>
          <td>{{ "%.2f"|format(ep.ast_confidence) }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>

<div class="card">
  <div class="card-title">Source vs. Spec Reconciliation &amp; Limitations</div>
  {% if data.discrepancies %}
  <div class="alert alert-warning" style="margin-bottom:0.75rem;">
    <strong>Discrepancies Noted:</strong>
    <ul style="padding-left:1.25rem; font-size:0.8rem; margin-top:0.25rem;">
      {% for d in data.discrepancies %}
      <li>{{ d }}</li>
      {% endfor %}
    </ul>
  </div>
  {% endif %}
  <div style="font-size:0.85rem; color:var(--text-muted);">
    <strong>Project Understanding Limitations:</strong>
    <ul style="padding-left:1.25rem; margin-top:0.25rem;">
      {% for lim in data.limitations %}
      <li>{{ lim }}</li>
      {% endfor %}
    </ul>
  </div>
</div>
{% endblock %}
"""

_PLANNING_TEMPLATE = """{% extends "base.html" %}
{% block title %}Test Planning &bull; ApiScope{% endblock %}
{% block content %}
<div style="margin-bottom:1.25rem;">
  <h1 style="font-size:1.5rem; font-weight:700;">Test Planning &amp; Review</h1>
  <p style="color:var(--text-muted); font-size:0.85rem;">Synthesized test scenarios, oracle provenance, fixture prerequisites, and human authorization.</p>
</div>

<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-value">{{ data.total_scenarios }}</div>
    <div class="stat-label">Total Scenarios</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#166534;">{{ data.approved_count }}</div>
    <div class="stat-label">Approved</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#b45309;">{{ data.proposed_count }}</div>
    <div class="stat-label">Pending Review</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#2563eb;">{{ data.summary.scenarios_by_origin.DETERMINISTIC or data.total_scenarios }}</div>
    <div class="stat-label">Deterministic</div>
  </div>
</div>

{% if data.proposed_count > 0 %}
<div class="alert alert-warning" style="display:flex; justify-content:space-between; align-items:center;">
  <div>
    <strong>Human Review Required:</strong> {{ data.proposed_count }} scenario(s) are in PROPOSED status and require human review.
  </div>
  <form method="POST" action="/api/planning/approve" style="margin:0;">
    <input type="hidden" name="csrf_token" value="{{ csrf_token }}">
    <button type="submit" class="btn btn-primary btn-sm">Approve Proposed Scenarios</button>
  </form>
</div>
{% endif %}

{% if data.missing_prerequisites %}
<div class="card">
  <div class="card-title">Fixture Prerequisites &amp; Unresolved Parameters</div>
  <p style="font-size:0.8rem; color:var(--text-muted); margin-bottom:0.5rem;">The following parameter fixtures require concrete values or stateful setup workflows:</p>
  <ul style="font-size:0.8rem; padding-left:1.25rem; color:#b45309;">
    {% for prereq in data.missing_prerequisites %}
    <li><code>{{ prereq }}</code></li>
    {% endfor %}
  </ul>
</div>
{% endif %}

<div class="card">
  <div class="card-title">Planned Scenarios</div>
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Scenario ID</th>
          <th>Name</th>
          <th>Category</th>
          <th>Priority</th>
          <th>Origin</th>
          <th>Status</th>
          <th>Oracle Provenance</th>
        </tr>
      </thead>
      <tbody>
        {% for sc in data.scenarios %}
        <tr>
          <td><code>{{ sc.scenario_id }}</code></td>
          <td><strong>{{ sc.name }}</strong></td>
          <td><span class="badge badge-info">{{ sc.category }}</span></td>
          <td><span class="badge {% if sc.priority == 'HIGH' %}badge-fail{% else %}badge-skip{% endif %}">{{ sc.priority }}</span></td>
          <td><span class="badge {% if sc.origin == 'AI_PROPOSED' %}badge-ai{% else %}badge-info{% endif %}">{{ sc.origin }}</span></td>
          <td><span class="badge {% if sc.status == 'APPROVED' %}badge-pass{% else %}badge-warn{% endif %}">{{ sc.status }}</span></td>
          <td>{{ sc.oracle_provenance }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
"""

_EXECUTION_TEMPLATE = """{% extends "base.html" %}
{% block title %}Execution Preview &bull; ApiScope{% endblock %}
{% block content %}
<div style="margin-bottom:1.25rem;">
  <h1 style="font-size:1.5rem; font-weight:700;">Execution Preview &amp; Run Results</h1>
  <p style="color:var(--text-muted); font-size:0.85rem;">Safety fencing, dry-run breakdown, and recorded execution cases with Expected vs. Actual diagnostics.</p>
</div>

<div class="card">
  <div class="card-title">Execution Safety Fence Status</div>
  <div class="alert alert-info" style="margin-bottom:1rem;">
    <strong>Policy Enforcement:</strong> {{ data.preview_status }}
  </div>
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-value" style="color:#10b981;">{{ data.readonly_count }}</div>
      <div class="stat-label">Read-Only GETs (Authorized)</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" style="color:#ef4444;">{{ data.mutating_count }}</div>
      <div class="stat-label">Mutations (Fenced)</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{{ data.total_cases }}</div>
      <div class="stat-label">Recorded Test Cases</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" style="color:#166534;">{{ data.passed_cases }}</div>
      <div class="stat-label">Passed Cases</div>
    </div>
  </div>
</div>

<div class="card">
  <div class="card-title">Recorded Test Execution Cases</div>
  {% if not data.cases %}
  <div class="empty-state">No execution cases recorded.</div>
  {% else %}
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Status</th>
          <th>Method</th>
          <th>Endpoint / Case Name</th>
          <th>HTTP Status</th>
          <th>Duration</th>
          <th>Assertions</th>
        </tr>
      </thead>
      <tbody>
        {% for c in data.cases %}
        <tr>
          <td><span class="badge {% if c.status == 'PASSED' %}badge-pass{% else %}badge-fail{% endif %}">{{ c.status }}</span></td>
          <td><span class="badge badge-{{ c.method|lower }}">{{ c.method }}</span></td>
          <td><code>{{ c.url }}</code><br><small style="color:var(--text-muted);">{{ c.name }}</small></td>
          <td><strong>{{ c.status_code }}</strong></td>
          <td>{{ c.elapsed_ms }} ms</td>
          <td>
            {% for a in c.assertions %}
            <div style="font-size:0.75rem; color:{% if a.passed %}#166534{% else %}#991b1b{% endif %};">
              {% if a.passed %}&check;{% else %}&cross;{% endif %} {{ a.type or a.rule_type }}: {{ a.message or 'Matched' }}
            </div>
            {% endfor %}
            {% if c.error_message %}
            <div style="font-size:0.75rem; color:#991b1b; margin-top:0.25rem;">
              <strong>Error:</strong> {{ c.error_message }}
            </div>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% endif %}
</div>
{% endblock %}
"""

_TRUST_TEMPLATE = """{% extends "base.html" %}
{% block title %}Trust &bull; ApiScope{% endblock %}
{% block content %}
<div style="margin-bottom:1.25rem;">
  <h1 style="font-size:1.5rem; font-weight:700;">Trust Assessment &amp; Defensible Diagnostics</h1>
  <p style="color:var(--text-muted); font-size:0.85rem;">11 defensible diagnostic categories separating true application bugs from test defects and unverified assumptions.</p>
</div>

<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-value" style="color:#2563eb;">{{ data.deduplicated_findings_count }}</div>
    <div class="stat-label">Deduplicated Findings</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#166534;">{{ data.verdict_counts.PASS_WITHIN_VERIFIED_COVERAGE or 0 }}</div>
    <div class="stat-label">Verified Passes</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#64748b;">{{ data.verdict_counts.NOT_TESTED or 0 }}</div>
    <div class="stat-label">Safely Untested</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#b45309;">{{ data.verdict_counts.FIXTURE_OR_DEPENDENCY_ISSUE or 0 }}</div>
    <div class="stat-label">Fixture Dependencies</div>
  </div>
</div>

<div class="card">
  <div class="card-title">Diagnostic Findings &amp; Evidence Audit</div>
  {% for f in data.findings %}
  <div style="border:1px solid var(--border); border-radius:6px; padding:1rem; margin-bottom:1rem; background:var(--bg);">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.5rem;">
      <div>
        <span class="badge badge-info">{{ f.verdict }}</span>
        <span class="badge {% if f.severity == 'HIGH' or f.severity == 'CRITICAL' %}badge-fail{% elif f.severity == 'MEDIUM' %}badge-warn{% else %}badge-skip{% endif %}" style="margin-left:0.35rem;">{{ f.severity }}</span>
        <strong style="font-size:0.95rem; margin-left:0.5rem;">{{ f.route_template }}</strong>
      </div>
      <code style="font-size:0.75rem;">{{ f.finding_id }}</code>
    </div>

    <div style="margin-bottom:0.5rem; font-size:0.85rem;">
      <strong>Observation:</strong> {{ f.what_happened }}
    </div>

    <div style="margin-bottom:0.5rem; font-size:0.85rem; color:var(--text-muted);">
      <strong>Reasoning / Cause:</strong> {{ f.why_reported }}
    </div>

    {% if f.supporting_evidence %}
    <div style="margin-bottom:0.5rem; font-size:0.8rem;">
      <strong>Supporting Evidence:</strong>
      <ul style="padding-left:1.25rem;">
        {% for ev in f.supporting_evidence %}
        <li>{{ ev }}</li>
        {% endfor %}
      </ul>
    </div>
    {% endif %}

    {% if f.recommended_action %}
    <div style="font-size:0.8rem; color:#166534;">
      <strong>Recommended Action:</strong> {{ f.recommended_action }}
    </div>
    {% endif %}

    {% if f.what_remains_untested %}
    <div style="font-size:0.75rem; color:var(--text-muted); margin-top:0.35rem;">
      <strong>Remains Untested:</strong> {{ f.what_remains_untested|join(', ') }}
    </div>
    {% endif %}
  </div>
  {% endfor %}
</div>

<div class="card">
  <div class="card-title">Latency Drift Diagnostics</div>
  {% if not data.has_baseline_latency %}
  <div class="empty-state" style="padding:1rem;">
    Insufficient baseline samples to evaluate latency drift (minimum 5 samples required).
  </div>
  {% else %}
  <p style="font-size:0.85rem;">Baseline P95: <strong>{{ data.baseline_p95_ms }} ms</strong></p>
  {% endif %}
</div>

<div class="card">
  <div class="card-title">Evidence Limitations &amp; Verification Gaps</div>
  <div style="font-size:0.85rem; color:var(--text-muted);">
    <strong>Limitations:</strong>
    <ul style="padding-left:1.25rem; margin-top:0.25rem; margin-bottom:0.75rem;">
      {% for lim in data.evidence_limitations %}
      <li>{{ lim }}</li>
      {% endfor %}
    </ul>
    <strong>Remaining Gaps:</strong>
    <ul style="padding-left:1.25rem; margin-top:0.25rem;">
      {% for gap in data.remaining_verification_gaps %}
      <li>{{ gap }}</li>
      {% endfor %}
    </ul>
  </div>
</div>
{% endblock %}
"""

_RUNS_TEMPLATE = """{% extends "base.html" %}
{% block title %}Runs &amp; Trends &bull; ApiScope{% endblock %}
{% block content %}
<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-value">{{ total_runs }}</div>
    <div class="stat-label">Total Test Runs</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">
      {% if latest_run %}
        {{ latest_run.passed_tests }}/{{ latest_run.total_tests }}
      {% else %}-{% endif %}
    </div>
    <div class="stat-label">Latest Run Passed</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">
      {% if latest_run and latest_run.total_tests > 0 %}
        {{ "%.1f"|format(latest_run.passed_tests / latest_run.total_tests * 100) }}%
      {% else %}-{% endif %}
    </div>
    <div class="stat-label">Latest Pass Rate</div>
  </div>
  <div class="stat-card">
    <div class="stat-value">
      {% if latest_run %}
        {{ "%.1f"|format(latest_run.duration_ms) }} ms
      {% else %}-{% endif %}
    </div>
    <div class="stat-label">Latest Duration</div>
  </div>
</div>

<div class="card">
  <div class="card-title">Trigger Test Suite Run</div>
  <form id="trigger-form">
    <div class="form-row">
      <div class="form-group">
        <label class="form-label" for="suite-path">Suite Path (required)</label>
        <input class="form-input" type="text" id="suite-path" name="suite_path"
               placeholder="e.g. examples/m4_chaining_suite.yaml" required>
      </div>
      <div class="form-group">
        <label class="form-label" for="env-file">Environment File (optional)</label>
        <input class="form-input" type="text" id="env-file" name="env_file"
               placeholder="e.g. examples/m2_environment_example.yaml">
      </div>
      <div class="form-group">
        <label class="form-label" for="module-filter">Module Filter (optional)</label>
        <input class="form-input" type="text" id="module-filter" name="module"
               placeholder="e.g. orders">
      </div>
    </div>
    <div style="display:flex; justify-content:space-between; align-items:center;">
      <button type="button" class="btn btn-primary" onclick="submitRun()">
        Run Suite
      </button>
      <div id="trigger-msg" style="font-size:0.9rem; font-weight:500;"></div>
    </div>
  </form>
</div>

{% if trends and trends.points %}
<div class="card">
  <div class="card-title">Historical Execution Trends</div>
  <div class="charts-row">
    <div class="stat-card">
      <div class="card-title" style="font-size:0.95rem;">Pass Rate History (%)</div>
      <div class="chart-container">
        <canvas id="passRateChart"></canvas>
      </div>
    </div>
    <div class="stat-card">
      <div class="card-title" style="font-size:0.95rem;">Latency Metrics (ms)</div>
      <div class="chart-container">
        <canvas id="latencyChart"></canvas>
      </div>
    </div>
  </div>
  <div class="table-container" style="margin-top:1rem;">
    <table>
      <thead>
        <tr>
          <th>Run ID</th>
          <th>Started At</th>
          <th>Pass Rate</th>
          <th>Avg Latency</th>
          <th>p95 Latency</th>
        </tr>
      </thead>
      <tbody>
        {% for pt in trends.points %}
        <tr>
          <td><a href="/runs/{{ pt.run_id }}">{{ pt.run_id[:8] }}...</a></td>
          <td>{{ pt.started_at[:19] }}</td>
          <td>{{ "%.1f"|format(pt.pass_rate_pct) }}%</td>
          <td>{{ "%.1f"|format(pt.avg_elapsed_ms) }} ms</td>
          <td>{{ "%.1f"|format(pt.p95_elapsed_ms) }} ms</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endif %}

<div class="card">
  <div class="card-title">Test Run History</div>
  {% if not runs %}
    <div class="empty-state">No test runs recorded yet</div>
  {% else %}
    <div class="table-container">
      <table>
        <thead>
          <tr>
            <th>Run ID</th>
            <th>Environment</th>
            <th>Started At</th>
            <th>Total</th>
            <th>Passed</th>
            <th>Failed</th>
            <th>Errors</th>
            <th>Skipped</th>
            <th>Duration</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {% for r in runs %}
          <tr>
            <td>
              <a href="/runs/{{ r.run_id }}"
                 style="font-weight:600; color:var(--primary); text-decoration:none;">
                {{ r.run_id[:8] }}...
              </a>
            </td>
            <td>{{ r.environment_name or "default" }}</td>
            <td>{{ r.started_at[:19] }}</td>
            <td><strong>{{ r.total_tests }}</strong></td>
            <td><span class="badge badge-passed">{{ r.passed_tests }}</span></td>
            <td>
              {% if r.failed_tests > 0 %}
                <span class="badge badge-failed">{{ r.failed_tests }}</span>
              {% else %}0{% endif %}
            </td>
            <td>
              {% if r.error_tests > 0 %}
                <span class="badge badge-error">{{ r.error_tests }}</span>
              {% else %}0{% endif %}
            </td>
            <td>
              {% if r.skipped_tests > 0 %}
                <span class="badge badge-skipped">{{ r.skipped_tests }}</span>
              {% else %}0{% endif %}
            </td>
            <td>{{ "%.1f"|format(r.duration_ms) }} ms</td>
            <td>
              <a href="/runs/{{ r.run_id }}"
                 class="btn btn-outline btn-sm">View</a>
              <a href="/runs/{{ r.run_id }}/download?format=json"
                 class="btn btn-outline btn-sm" title="JSON">JSON</a>
              <a href="/runs/{{ r.run_id }}/download?format=junit"
                 class="btn btn-outline btn-sm" title="JUnit">JUnit</a>
              <a href="/runs/{{ r.run_id }}/download?format=html"
                 class="btn btn-outline btn-sm" title="HTML">HTML</a>
            </td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% endif %}
</div>

{% if trends and trends.points %}
<script type="application/json" id="trends-data">
{{ trends_json|safe }}
</script>
<script>
document.addEventListener("DOMContentLoaded", function() {
  const dataElem = document.getElementById("trends-data");
  if (!dataElem || typeof Chart === "undefined") return;
  try {
    const data = JSON.parse(dataElem.textContent);
    const points = data.points || [];
    if (points.length === 0) return;
    const labels = points.map(p => p.run_id.substring(0, 8));
    const passRates = points.map(p => p.pass_rate_pct);
    const avgLats = points.map(p => p.avg_elapsed_ms);
    const p95Lats = points.map(p => p.p95_elapsed_ms);

    const ctxPass = document.getElementById("passRateChart");
    if (ctxPass) {
      new Chart(ctxPass, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [{
            label: 'Pass Rate (%)',
            data: passRates,
            borderColor: '#10b981',
            backgroundColor: 'rgba(16, 185, 129, 0.1)',
            fill: true,
            tension: 0.2
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: { y: { min: 0, max: 100 } }
        }
      });
    }

    const ctxLat = document.getElementById("latencyChart");
    if (ctxLat) {
      new Chart(ctxLat, {
        type: 'line',
        data: {
          labels: labels,
          datasets: [
            {
              label: 'Avg Latency (ms)',
              data: avgLats,
              borderColor: '#3b82f6',
              tension: 0.2
            },
            {
              label: 'p95 Latency (ms)',
              data: p95Lats,
              borderColor: '#f59e0b',
              tension: 0.2
            }
          ]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          scales: { y: { beginAtZero: true } }
        }
      });
    }
  } catch(e) {
    console.error("Failed to render charts:", e);
  }
});
</script>
{% endif %}

<script>
function submitRun() {
  const suitePath = document.getElementById("suite-path").value.trim();
  const envFile = document.getElementById("env-file").value.trim() || null;
  const moduleFilter = document.getElementById("module-filter").value.trim() || null;
  const statusMsg = document.getElementById("trigger-msg");

  if (!suitePath) {
    statusMsg.style.color = "red";
    statusMsg.textContent = "Suite path is required.";
    return;
  }

  statusMsg.style.color = "var(--text-muted)";
  statusMsg.textContent = "Executing suite...";

  fetch('/api/runs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      suite_path: suitePath,
      env_file: envFile,
      module: moduleFilter
    })
  })
  .then(resp => resp.json().then(data => ({ status: resp.status, data })))
  .then(({ status, data }) => {
    if (status === 201 || status === 200) {
      statusMsg.style.color = "green";
      statusMsg.textContent = "Success! Redirecting to run...";
      setTimeout(() => { window.location.href = `/runs/${data.run_id}`; }, 500);
    } else {
      statusMsg.style.color = "red";
      statusMsg.textContent = "Error: " + (data.error || "Failed to trigger run");
    }
  })
  .catch(err => {
    statusMsg.style.color = "red";
    statusMsg.textContent = "Request error: " + err;
  });
}
</script>
{% endblock %}
"""

_RUN_DETAIL_TEMPLATE = """{% extends "base.html" %}
{% block title %}Run Detail &bull; {{ run.run_id[:8] }} &bull; ApiScope{% endblock %}
{% block content %}
<div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:1.5rem;">
  <div>
    <h1 style="font-size:1.5rem; font-weight:700;">
      Run Detail: <code>{{ run.run_id }}</code>
    </h1>
    <p style="color:var(--text-muted); font-size:0.9rem;">
      Environment: <strong>{{ run.environment_name or "default" }}</strong> &bull;
      Started: {{ run.started_at.isoformat()[:19] }} &bull;
      Duration: {{ "%.1f"|format(run.duration_ms) }} ms
    </p>
  </div>
  <div style="display:flex; gap:0.5rem;">
    <a href="/runs/{{ run.run_id }}/download?format=json"
       class="btn btn-outline btn-sm">Download JSON</a>
    <a href="/runs/{{ run.run_id }}/download?format=junit"
       class="btn btn-outline btn-sm">Download JUnit</a>
    <a href="/runs/{{ run.run_id }}/download?format=html"
       class="btn btn-outline btn-sm">Download HTML</a>
  </div>
</div>

<div class="stats-grid">
  <div class="stat-card">
    <div class="stat-value">{{ run.total_tests }}</div>
    <div class="stat-label">Total Tests</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#166534;">{{ run.passed_tests }}</div>
    <div class="stat-label">Passed</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#991b1b;">{{ run.failed_tests }}</div>
    <div class="stat-label">Failed</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#b91c1c;">{{ run.error_tests }}</div>
    <div class="stat-label">Errors</div>
  </div>
  <div class="stat-card">
    <div class="stat-value" style="color:#475569;">{{ run.skipped_tests }}</div>
    <div class="stat-label">Skipped</div>
  </div>
</div>

<div class="card">
  <div class="card-title">Test Cases ({{ run.results|length }})</div>
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Module</th>
          <th>Test Name</th>
          <th>Method</th>
          <th>URL</th>
          <th>Status</th>
          <th>Status Code</th>
          <th>Latency</th>
        </tr>
      </thead>
      <tbody>
        {% for case in run.results %}
        <tr>
          <td><strong>{{ case.module }}</strong></td>
          <td>{{ case.test_name }}</td>
          <td><code>{{ case.method }}</code></td>
          <td><code style="word-break:break-all;">{{ case.url }}</code></td>
          <td>
            <span class="badge badge-{{ case.status.value.lower() }}">
              {{ case.status.value }}
            </span>
          </td>
          <td>{{ case.status_code or "-" }}</td>
          <td>
            {% if case.elapsed_ms %}
              {{ "%.1f"|format(case.elapsed_ms) }} ms
            {% else %}-{% endif %}
          </td>
        </tr>
        {% if case.status.value != 'PASSED' or case.assertion_results or case.extracted_variables or case.error_message or case.skip_reason %}
        <tr>
          <td colspan="7" style="background:#f8fafc; padding:1rem 1.5rem;">
            {% if case.error_message %}
            <div style="color:#991b1b; font-weight:600; margin-bottom:0.5rem;">
              Error: {{ case.error_message }}
            </div>
            {% endif %}
            {% if case.skip_reason %}
            <div style="color:#64748b; font-weight:500; margin-bottom:0.5rem;">
              Skipped: {{ case.skip_reason }}
            </div>
            {% endif %}

            {% if case.assertion_results %}
            <div style="font-weight:600; font-size:0.85rem; margin-top:0.5rem; margin-bottom:0.25rem;">
              Assertions:
            </div>
            <ul style="list-style:none; padding-left:0; font-size:0.85rem;">
              {% for a in case.assertion_results %}
              <li style="margin-bottom:0.4rem; padding:0.4rem 0.6rem; border-radius:4px; background:{% if a.passed %}#f0fdf4{% else %}#fef2f2{% endif %}; border:1px solid {% if a.passed %}#bbf7d0{% else %}#fecaca{% endif %};">
                <span class="badge {% if a.passed %}badge-passed{% else %}badge-failed{% endif %}"
                      style="margin-right:0.5rem;">
                  {% if a.passed %}PASS{% else %}FAIL{% endif %}
                </span>
                <strong>[{{ a.rule_type or a.assertion_type }}]</strong> {{ a.message }}
                {% if not a.passed and (a.expected is not none or a.actual is not none) %}
                <div style="margin-top:0.25rem; font-family:monospace; font-size:0.8rem; color:#1e293b;">
                  Expected: <code>{{ a.expected }}</code> | Actual: <code>{{ a.actual }}</code>
                </div>
                {% endif %}
              </li>
              {% endfor %}
            </ul>
            {% endif %}

            {% if case.extracted_variables %}
            <div style="font-weight:600; font-size:0.85rem; margin-top:0.5rem; margin-bottom:0.25rem;">
              Extracted Variables:
            </div>
            <pre class="code-block">{{ case.extracted_variables|tojson(indent=2) }}</pre>
            {% endif %}
          </td>
        </tr>
        {% endif %}
        {% endfor %}
      </tbody>
    </table>
  </div>
</div>
{% endblock %}
"""

_HEALTH_TEMPLATE = """{% extends "base.html" %}
{% block title %}Module Health Verdict &bull; ApiScope{% endblock %}
{% block content %}
<div style="margin-bottom:1.5rem;">
  <h1 style="font-size:1.5rem; font-weight:700;">Module Health Verdict</h1>
  <p style="color:var(--text-muted); font-size:0.9rem;">
    Evidence-based evaluation of API module health across test coverage, assertions,
    latency drift, and silent failure signals.
  </p>
</div>

{% if not reports %}
<div class="card">
  <div class="empty-state">No test runs available to evaluate module health</div>
</div>
{% else %}
  {% for r in reports %}
  <div class="card">
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:0.75rem;">
      <h2 style="font-size:1.25rem; font-weight:700;">Module: {{ r.module }}</h2>
      <span class="badge badge-{{ r.verdict.value.lower().replace(' ', '-') }}"
            style="font-size:0.85rem; padding:0.35rem 0.75rem;">
        {{ r.verdict.value }}
      </span>
    </div>

    <p style="font-size:0.95rem; font-weight:500; margin-bottom:1rem; color:var(--text);">
      {{ r.summary }}
    </p>

    <div style="margin-bottom:1rem;">
      <div style="display:flex; justify-content:space-between; font-size:0.85rem; margin-bottom:0.25rem;">
        <span>
          Endpoint Coverage: <strong>{{ "%.1f"|format(r.endpoint_coverage_pct) }}%</strong>
          ({{ r.spec_endpoints_tested }}/{{ r.spec_endpoints_total }} endpoints)
        </span>
        <span>Threshold: {{ "%.1f"|format(r.coverage_threshold_pct) }}%</span>
      </div>
      <div style="background:#e2e8f0; height:8px; border-radius:4px; overflow:hidden;">
        <div style="background:{% if r.endpoint_coverage_pct >= r.coverage_threshold_pct %}#10b981{% else %}#ef4444{% endif %}; width:{{ r.endpoint_coverage_pct }}%; height:100%;"></div>
      </div>
      {% if r.untested_endpoints %}
      <div style="font-size:0.8rem; color:#991b1b; margin-top:0.35rem;">
        Untested Endpoints: {{ r.untested_endpoints|join(', ') }}
      </div>
      {% endif %}
    </div>

    <div class="stats-grid" style="margin-bottom:1rem;">
      <div class="stat-card" style="padding:0.75rem;">
        <div class="stat-value" style="font-size:1.25rem;">
          {{ "%.1f"|format(r.functional_stats.pass_rate_pct) }}%
        </div>
        <div class="stat-label">Functional</div>
      </div>
      <div class="stat-card" style="padding:0.75rem;">
        <div class="stat-value" style="font-size:1.25rem;">
          {{ "%.1f"|format(r.contract_stats.pass_rate_pct) }}%
        </div>
        <div class="stat-label">Contract</div>
      </div>
      <div class="stat-card" style="padding:0.75rem;">
        <div class="stat-value" style="font-size:1.25rem;">
          {{ "%.1f"|format(r.negative_boundary_stats.pass_rate_pct) }}%
        </div>
        <div class="stat-label">Boundary/Negative</div>
      </div>
      <div class="stat-card" style="padding:0.75rem;">
        <div class="stat-value" style="font-size:1.25rem;">
          {{ "%.1f"|format(r.auth_stats.pass_rate_pct) }}%
        </div>
        <div class="stat-label">Auth</div>
      </div>
    </div>

    {% if r.evidence %}
    <div style="margin-bottom:0.75rem;">
      <div style="font-size:0.85rem; font-weight:600; color:var(--text-muted); margin-bottom:0.25rem;">
        EVIDENCE:
      </div>
      <ul style="font-size:0.85rem; padding-left:1.25rem; color:var(--text);">
        {% for ev in r.evidence %}
        <li>{{ ev }}</li>
        {% endfor %}
      </ul>
    </div>
    {% endif %}

    {% if r.warnings %}
    <div style="margin-bottom:0.75rem;">
      <div style="font-size:0.85rem; font-weight:600; color:#b45309; margin-bottom:0.25rem;">
        WARNINGS:
      </div>
      <ul style="font-size:0.85rem; padding-left:1.25rem; color:#b45309;">
        {% for w in r.warnings %}
        <li>{{ w }}</li>
        {% endfor %}
      </ul>
    </div>
    {% endif %}

    {% if r.failures %}
    <div>
      <div style="font-size:0.85rem; font-weight:600; color:#b91c1c; margin-bottom:0.25rem;">
        FAILURES:
      </div>
      <ul style="font-size:0.85rem; padding-left:1.25rem; color:#b91c1c;">
        {% for f in r.failures %}
        <li>{{ f }}</li>
        {% endfor %}
      </ul>
    </div>
    {% endif %}
  </div>
  {% endfor %}
{% endif %}
{% endblock %}
"""

_ZOMBIES_TEMPLATE = """{% extends "base.html" %}
{% block title %}Zombie &amp; Silent Failures &bull; ApiScope{% endblock %}
{% block content %}
<div style="margin-bottom:1.5rem;">
  <h1 style="font-size:1.5rem; font-weight:700;">Zombie &amp; Silent Failure Analysis</h1>
  <p style="color:var(--text-muted); font-size:0.9rem;">
    Detection of unused, undocumented, untested, and deprecated-but-called endpoints (M9),
    plus silent failure signals S1–S4 (M10).
  </p>
</div>

{% if not zombie_report and not silent_report %}
<div class="card">
  <div class="empty-state">
    Zombie detection and log analysis are not configured. Run the server with
    <code>--spec</code> and <code>--access-log</code> / <code>--app-log</code>
    to enable this view.
  </div>
</div>
{% else %}

{% if zombie_report %}
<div class="card">
  <div class="card-title">Zombie API Findings (Total: {{ zombie_report.total_findings }})</div>
  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-value" style="color:#b45309;">
        {{ zombie_report.counts_by_class["UNUSED"] }}
      </div>
      <div class="stat-label">UNUSED</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" style="color:#b91c1c;">
        {{ zombie_report.counts_by_class["UNDOCUMENTED"] }}
      </div>
      <div class="stat-label">UNDOCUMENTED</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" style="color:#475569;">
        {{ zombie_report.counts_by_class["UNTESTED"] }}
      </div>
      <div class="stat-label">UNTESTED</div>
    </div>
    <div class="stat-card">
      <div class="stat-value" style="color:#b45309;">
        {{ zombie_report.counts_by_class["DEPRECATED-BUT-CALLED"] }}
      </div>
      <div class="stat-label">DEPRECATED-BUT-CALLED</div>
    </div>
  </div>

  {% if zombie_report.findings %}
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Classification</th>
          <th>Module</th>
          <th>Method</th>
          <th>Path</th>
          <th>Calls</th>
          <th>Last Seen</th>
          <th>Details</th>
        </tr>
      </thead>
      <tbody>
        {% for f in zombie_report.findings %}
        <tr>
          <td><span class="badge badge-warning">{{ f.classification.value }}</span></td>
          <td><strong>{{ f.module }}</strong></td>
          <td><code>{{ f.method }}</code></td>
          <td><code>{{ f.normalized_path }}</code></td>
          <td>{{ f.call_count }}</td>
          <td>{{ f.last_seen or "-" }}</td>
          <td>{{ f.details }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty-state" style="padding:1.5rem;">No zombie endpoints detected.</div>
  {% endif %}
</div>
{% endif %}

{% if silent_report %}
<div class="card">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
    <div class="card-title" style="margin-bottom:0;">Silent Failure Signal Analysis</div>
    <span class="badge {% if silent_report.has_silent_failures %}badge-warning{% else %}badge-passed{% endif %}"
          style="font-size:0.85rem; padding:0.35rem 0.75rem;">
      {{ silent_report.verdict_banner }}
    </span>
  </div>

  <div class="stats-grid">
    <div class="stat-card">
      <div class="stat-value">{{ silent_report.counts_by_type["S1_LOG_ERROR"] }}</div>
      <div class="stat-label">S1: App Log Errors</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{{ silent_report.counts_by_type["S2_DEPENDENCY_PROBE"] }}</div>
      <div class="stat-label">S2: Degraded Probes</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{{ silent_report.counts_by_type["S3_LATENCY_DRIFT"] }}</div>
      <div class="stat-label">S3: Latency Drift</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{{ silent_report.counts_by_type["S4_SUSPICIOUS_2XX"] }}</div>
      <div class="stat-label">S4: Suspicious 2xx</div>
    </div>
  </div>

  {% if silent_report.signals %}
  <div class="table-container">
    <table>
      <thead>
        <tr>
          <th>Signal</th>
          <th>Module</th>
          <th>Source / Endpoint</th>
          <th>Description</th>
          <th>Evidence</th>
        </tr>
      </thead>
      <tbody>
        {% for s in silent_report.signals %}
        <tr>
          <td><span class="badge badge-warning">{{ s.signal_type.value }}</span></td>
          <td><strong>{{ s.module }}</strong></td>
          <td><code>{{ s.endpoint_or_source }}</code></td>
          <td>{{ s.description }}</td>
          <td><code style="word-break:break-all;">{{ s.evidence }}</code></td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
  {% else %}
  <div class="empty-state" style="padding:1.5rem;">
    No silent failures detected across Signals S1–S4.
  </div>
  {% endif %}
</div>
{% endif %}

{% endif %}
{% endblock %}
"""

_ERROR_TEMPLATE = """{% extends "base.html" %}
{% block title %}Error {{ status_code }} &bull; ApiScope{% endblock %}
{% block content %}
<div class="card" style="text-align:center; padding:3rem 1rem;">
  <h1 style="font-size:3rem; font-weight:800; color:#ef4444; margin-bottom:0.5rem;">
    {{ status_code }}
  </h1>
  <div style="font-size:1.25rem; font-weight:600; margin-bottom:1rem; color:var(--text);">
    {{ error_title }}
  </div>
  <p style="color:var(--text-muted); max-width:500px; margin:0 auto 1.5rem auto;">
    {{ error_message }}
  </p>
  <a href="/runs" class="btn btn-primary">Return to Runs &amp; Trends</a>
</div>
{% endblock %}
"""


def create_jinja_environment() -> Environment:
    """Create and return a Jinja2 Environment with autoescape enabled.

    Uses DictLoader to bundle all HTML5 templates safely without external
    filesystem dependencies or package data distribution concerns.
    """
    loader = DictLoader(
        {
            "base.html": _BASE_TEMPLATE,
            "overview.html": _OVERVIEW_TEMPLATE,
            "guided.html": _GUIDED_TEMPLATE,
            "intelligence.html": _INTELLIGENCE_TEMPLATE,
            "planning.html": _PLANNING_TEMPLATE,
            "execution.html": _EXECUTION_TEMPLATE,
            "trust.html": _TRUST_TEMPLATE,
            "runs.html": _RUNS_TEMPLATE,
            "run_detail.html": _RUN_DETAIL_TEMPLATE,
            "health.html": _HEALTH_TEMPLATE,
            "zombies.html": _ZOMBIES_TEMPLATE,
            "error.html": _ERROR_TEMPLATE,
        }
    )
    env = Environment(
        loader=loader,
        autoescape=select_autoescape(["html", "xml"]),
    )
    env.filters["tojson"] = lambda v, indent=None: json.dumps(v, indent=indent)
    return env
