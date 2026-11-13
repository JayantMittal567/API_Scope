"""Jinja2 HTML templates and rendering environment for ApiScope Web Dashboard.

Provides autoescaped HTML5 templates with responsive styling, Chart.js trend
visualizations, Expected vs. Actual failure diagnostics, and status badges.
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
      padding: 1rem 2rem;
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
      gap: 1rem;
      list-style: none;
    }
    .nav-links a {
      color: var(--text-muted);
      text-decoration: none;
      font-size: 0.95rem;
      font-weight: 500;
      padding: 0.5rem 0.75rem;
      border-radius: 6px;
      transition: all 0.15s ease;
    }
    .nav-links a:hover, .nav-links a.active {
      color: var(--primary);
      background-color: #eff6ff;
    }
    .container {
      max-width: 1200px;
      margin: 2rem auto;
      padding: 0 1.5rem;
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
      color: var(--text);
    }
    .stats-grid {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 1rem;
      margin-bottom: 1.5rem;
    }
    .stat-card {
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 1.25rem;
      text-align: center;
    }
    .stat-value {
      font-size: 1.75rem;
      font-weight: 700;
      color: var(--primary);
    }
    .stat-label {
      font-size: 0.85rem;
      color: var(--text-muted);
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-top: 0.25rem;
    }
    .badge {
      display: inline-block;
      padding: 0.2rem 0.55rem;
      border-radius: 9999px;
      font-size: 0.75rem;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.025em;
    }
    .badge-passed, .badge-healthy {
      background: var(--badge-pass-bg);
      color: var(--badge-pass-text);
    }
    .badge-failed, .badge-unhealthy {
      background: var(--badge-fail-bg);
      color: var(--badge-fail-text);
    }
    .badge-error { background: #fee2e2; color: #b91c1c; }
    .badge-skipped, .badge-incomplete {
      background: var(--badge-skip-bg);
      color: var(--badge-skip-text);
    }
    .badge-warning, .badge-healthy-with-warnings {
      background: var(--badge-warn-bg);
      color: var(--badge-warn-text);
    }
    .table-container { overflow-x: auto; }
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
      background: #f8fafc;
      font-weight: 600;
      color: var(--text-muted);
      font-size: 0.8rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }
    tr:hover td { background-color: #f8fafc; }
    .btn {
      display: inline-block;
      padding: 0.5rem 1rem;
      font-size: 0.875rem;
      font-weight: 500;
      border-radius: 6px;
      text-decoration: none;
      cursor: pointer;
      border: 1px solid transparent;
      transition: background-color 0.15s ease;
    }
    .btn-primary { background: var(--primary); color: #fff; }
    .btn-primary:hover { background: var(--primary-hover); }
    .btn-outline {
      background: transparent;
      border-color: var(--border);
      color: var(--text);
    }
    .btn-outline:hover { background: #f1f5f9; }
    .btn-sm { padding: 0.25rem 0.5rem; font-size: 0.8rem; }
    .empty-state {
      padding: 3rem 1rem;
      text-align: center;
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
      <li>
        <a href="/runs" class="{% if active_tab == 'runs' %}active{% endif %}">
          Runs &amp; Trends
        </a>
      </li>
      <li>
        <a href="/health" class="{% if active_tab == 'health' %}active{% endif %}">
          Module Health
        </a>
      </li>
      <li>
        <a href="/zombies" class="{% if active_tab == 'zombies' %}active{% endif %}">
          Zombie &amp; Silent Failures
        </a>
      </li>
    </ul>
  </nav>
  <main class="container">
    {% block content %}{% endblock %}
  </main>
  <footer>
    ApiScope &bull; Automated API Testing, Health Monitoring &amp; Analysis
  </footer>
</body>
</html>
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
<div style="display:flex; justify-content:space-between; align-items:flex-start;
  margin-bottom:1.5rem;">
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
        {% if case.status.value != 'PASSED' or case.assertion_results or
          case.extracted_variables or case.error_message or case.skip_reason %}
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
            <div style="font-weight:600; font-size:0.85rem; margin-top:0.5rem;
              margin-bottom:0.25rem;">
              Assertions:
            </div>
            <ul style="list-style:none; padding-left:0; font-size:0.85rem;">
              {% for a in case.assertion_results %}
              <li style="margin-bottom:0.4rem; padding:0.4rem 0.6rem;
                border-radius:4px; background:{% if a.passed %}#f0fdf4{% else
                %}#fef2f2{% endif %}; border:1px solid {% if a.passed %}#bbf7d0{%
                else %}#fecaca{% endif %};">
                <span class="badge {% if a.passed %}badge-passed{% else
                  %}badge-failed{% endif %}"
                      style="margin-right:0.5rem;">
                  {% if a.passed %}PASS{% else %}FAIL{% endif %}
                </span>
                <strong>[{{ a.rule_type or a.assertion_type }}]</strong> {{ a.message }}
                {% if not a.passed and (a.expected is not none or a.actual is not
                  none) %}
                <div style="margin-top:0.25rem; font-family:monospace;
                  font-size:0.8rem; color:#1e293b;">
                  Expected: <code>{{ a.expected }}</code> | Actual: <code>{{
                    a.actual }}</code>
                </div>
                {% endif %}
              </li>
              {% endfor %}
            </ul>
            {% endif %}

            {% if case.extracted_variables %}
            <div style="font-weight:600; font-size:0.85rem; margin-top:0.5rem;
              margin-bottom:0.25rem;">
              Extracted Variables:
            </div>
            <pre class="code-block">{{ case.extracted_variables|tojson(indent=2)
              }}</pre>
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
    <div style="display:flex; justify-content:space-between; align-items:center;
      margin-bottom:0.75rem;">
      <h2 style="font-size:1.25rem; font-weight:700;">Module: {{ r.module }}</h2>
      <span class="badge badge-{{ r.verdict.value.lower().replace(' ', '-') }}"
            style="font-size:0.85rem; padding:0.35rem 0.75rem;">
        {{ r.verdict.value }}
      </span>
    </div>

    <p style="font-size:0.95rem; font-weight:500; margin-bottom:1rem;
      color:var(--text);">
      {{ r.summary }}
    </p>

    <div style="margin-bottom:1rem;">
      <div style="display:flex; justify-content:space-between; font-size:0.85rem;
        margin-bottom:0.25rem;">
        <span>
          Endpoint Coverage: <strong>{{ "%.1f"|format(r.endpoint_coverage_pct)
            }}%</strong>
          ({{ r.spec_endpoints_tested }}/{{ r.spec_endpoints_total }} endpoints)
        </span>
        <span>Threshold: {{ "%.1f"|format(r.coverage_threshold_pct) }}%</span>
      </div>
      <div style="background:#e2e8f0; height:8px; border-radius:4px; overflow:hidden;">
        <div style="background:{% if r.endpoint_coverage_pct >=
          r.coverage_threshold_pct %}#10b981{% else %}#ef4444{% endif %}; width:{{
          r.endpoint_coverage_pct }}%; height:100%;"></div>
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
      <div style="font-size:0.85rem; font-weight:600; color:var(--text-muted);
        margin-bottom:0.25rem;">
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
      <div style="font-size:0.85rem; font-weight:600; color:#b45309;
        margin-bottom:0.25rem;">
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
      <div style="font-size:0.85rem; font-weight:600; color:#b91c1c;
        margin-bottom:0.25rem;">
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
  <h1 style="font-size:1.5rem;">Zombie &amp; Silent Failure Analysis</h1>
  <p style="color:var(--text-muted); font-size:0.9rem;">
    Detection of unused, undocumented, untested, and deprecated-but-called endpoints
      (M9),
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
  <div class="card-title">Zombie API Findings (Total: {{
    zombie_report.total_findings }})</div>
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
  <div style="display:flex; justify-content:space-between; align-items:center;
    margin-bottom:1rem;">
    <div class="card-title" style="margin-bottom:0;">Silent Failure Signal
      Analysis</div>
    <span class="badge {% if silent_report.has_silent_failures %}badge-warning{%
      else %}badge-passed{% endif %}"
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
      <div class="stat-value">{{ silent_report.counts_by_type["S2_DEPENDENCY_PROBE"]
        }}</div>
      <div class="stat-label">S2: Degraded Probes</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{{ silent_report.counts_by_type["S3_LATENCY_DRIFT"]
        }}</div>
      <div class="stat-label">S3: Latency Drift</div>
    </div>
    <div class="stat-card">
      <div class="stat-value">{{ silent_report.counts_by_type["S4_SUSPICIOUS_2XX"]
        }}</div>
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
  <div style="font-size:1.25rem; font-weight:600; margin-bottom:1rem;
    color:var(--text);">
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
