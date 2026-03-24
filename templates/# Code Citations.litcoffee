# Code Citations

## License: unknown
https://github.com/long25vn/fingerprint_scanner_python/blob/69d9181ec3f133d1ac994b786166b460baf8b78b/templates/reportspage.html

```
أرى الآن الملف الحقيقي — التحسينات السابقة **لم تُطبّق** بعد. الملف لا يزال يحتوي على:
- ❌ `safeFetch` = 0
- ❌ `escapeHtml` = 0
- ❌ 30 inline style

دعني أطبّق كل شيء مباشرة:

````html
<!-- filepath: /home/raaft/amilcar/templates/index.html -->
{% extends "base.html" %}
{% block title %} - {{ t.dashboard }}{% endblock %}
{% block content %}
    <!-- Hero Welcome Banner -->
    <div class="dash-hero" role="banner">
        <div class="dash-hero-text">
            <p class="dash-greeting">
                <span class="dash-dot" aria-hidden="true"></span>
                {{ t.get('welcome_back','Bienvenue') }}, {{ session.get('username','Admin') }}
            </p>
            <h1 class="dash-title"><span class="dash-hero-icon" aria-hidden="true">🏎️</span> {{ t.dashboard }}</h1>
            <p class="dash-sub">{{ t.overview }} — <span class="dash-date">{{ now.strftime('%A %d %B %Y') if now else '' }}</span></p>
        </div>
        <div class="dash-actions">
            <a href="/add_customer" class="btn-outline-gold btn-sm" aria-label="{{ t.get('new_customer', 'Nouveau client') }}">+ {{ t.client }}</a>
            <a href="/add_appointment" class="btn-gold btn-sm" aria-label="{{ t.get('new_appointment', 'Nouveau RDV') }}">+ {{ t.appointments }}</a>
        </div>
        <div class="dash-tagline">
            SOIN AUTOMOBILE &mdash; MAHRES, SFAX
        </div>
    </div>

    <!-- Stat Cards -->
    <div class="row g-3 mb-4" role="region" aria-label="{{ t.get('statistics', 'Statistiques') }}">
        <div class="col-lg-2 col-md-4 col-6">
            <div class="stat-card" role="group" aria-label="{{ t.customers }}">
                <span class="stat-icon" aria-hidden="true">◉</span>
                <h2 data-countup="{{ stats.customers }}">0</h2>
                <p>{{ t.customers }}</p>
            </div>
        </div>
        <div class="col-lg-2 col-md-4 col-6">
            <div class="stat-card" role="group" aria-label="{{ t.appointments }}">
                <span class="stat-icon" aria-hidden="true">▦</span>
                <h2 data-countup="{{ stats.appointments }}">0</h2>
                <p>{{ t.appointments }}</p>
            </div>
        </div>
        <div class="col-lg-2 col-md-4 col-6">
            <div class="stat-card" role="group" aria-label="{{ t.revenue }}">
                <h2 data-countup="{{ stats.revenue }}">0</h2>
                <p>{{ t.revenue }} (DT)</p>
            </div>
        </div>
        <div class="col-lg-2 col-md-4 col-6">
            <div class="stat-card" role="group" aria-label="{{ t.expenses }}">
                <h2 class="text-expense" data-countup="{{ stats.expenses }}">0</h2>
                <p>{{ t.expenses }} (DT)</p>
            </div>
        </div>
        <div class="col-lg-2 col-md-4 col-6">
            <div class="stat-card" role="group" aria-label="{{ t.net_profit }}">
                <h2 class="{% if stats.profit >= 0 %}text-profit{% else %}text-loss{% endif %}" data-countup="{{ stats.profit }}">0</h2>
                <p>{{ t.net_profit }}</p>
            </div>
        </div>
        <div class="col-lg-2 col-md-4 col-6">
            <div class="stat-card" role="group" aria-label="{{ t.pending_quotes }}">
                <h2 data-countup="{{ stats.quotes }}">0</h2>
                <p>{{ t.pending_quotes }}</p>
            </div>
        </div>
    </div>

    <!-- Quick Stats Row -->
    <div class="row g-3 mb-4">
        <div class="col-md-3 col-6">
            <div class="quick-stat">
                <p class="quick-stat-label">{{ t.today_revenue }}</p>
                <p class="quick-stat-value">{{ stats.today_appointments }} <span class="quick-stat-unit">{{ t.get('appointments_short', 'rdv') }}</span> — {{ stats.today_revenue }} DT</p>
                {% if stats.revenue_trend != 0 %}
                <p class="trend {% if stats.revenue_trend > 0 %}trend-up{% else %}trend-down{% endif %}"
                   aria-label="{% if stats.revenue_trend > 0 %}+{% endif %}{{ stats.revenue_trend }} DT {{ t.get('vs_yesterday', 'vs hier') }}">
                    {% if stats.revenue_trend > 0 %}▲{% else %}▼{% endif %} {{ stats.revenue_trend|abs }} DT {{ t.get('vs_yesterday', 'vs hier') }}
                </p>
                {% endif %}
            </div>
        </div>
        <div class="col-md-3 col-6">
            <div class="quick-stat red">
                <p class="quick-stat-label">{{ t.unpaid_invoices }}</p>
                <p class="quick-stat-value">{{ stats.unpaid_total }} DT</p>
            </div>
        </div>
        <div class="col-md-3 col-6">
            <div class="quick-stat green">
                <p class="quick-stat-label">{{ t.tomorrow }}</p>
                <p class="quick-stat-value">{{ tomorrow_appointments|length }} <span class="quick-stat-unit">{{ t.get('appointments_unit', 'rendez-vous') }}</span></p>
            </div>
        </div>
        <div class="col-md-3 col-6">
            <div class="quick-stat blue">
                <p class="quick-stat-label">{{ t.week_revenue }}</p>
                <p class="quick-stat-value text-blue">{{ stats.week_revenue }} DT</p>
                {% if stats.week_trend != 0 %}
                <p class="trend {% if stats.week_trend > 0 %}trend-up{% else %}trend-down{% endif %}">
                    {% if stats.week_trend > 0 %}▲{% else %}▼{% endif %} {{ stats.week_trend|abs }} DT {{ t.get('vs_last_week', 'vs semaine dern.') }}
                </p>
                {% endif %}
            </div>
        </div>
    </div>

    <!-- Online Bookings Banner -->
    {% if stats.pending_bookings > 0 %}
    <div class="card p-3 mb-4 bookings-banner" role="alert">
        <div class="d-flex justify-content-between align-items-center flex-wrap gap-2">
            <div>
                <span class="bookings-count">📱 {{ stats.pending_bookings }} {{ t.online_bookings }}</span>
                <span class="bookings-today">({{ stats.today_bookings }} {{ t.today }})</span>
            </div>
            <a href="/bookings_admin" class="btn-gold btn-sm">{{ t.see_bookings }}</a>
        </div>
    </div>
    {% endif %}

    <!-- Insights -->
    <div class="row g-3 mb-4" role="region" aria-label="{{ t.get('insights', 'Aperçus') }}">
        <div class="col-md-3 col-6">
            <div class="insight-card">
                <p class="insight-label">{{ t.best_customer }}</p>
                <p class="insight-value text-gold">{{ stats.top_customer }}</p>
                <p class="insight-sub">{{ stats.top_customer_amount }} DT</p>
            </div>
        </div>
        <div class="col-md-3 col-6">
            <div class="insight-card">
                <p class="insight-label">{{ t.most_visited_car }}</p>
                <p class="insight-value text-blue">{{ stats.most_visited_car }}</p>
                <p class="insight-sub">{{ stats.most_visited_count }} {{ t.visits }}</p>
            </div>
        </div>
        <div class="col-md-3 col-6">
            <div class="insight-card">
                <p class="insight-label">{{ t.payment_rate }}</p>
                <div class="insight-progress-wrap">
                    <span class="insight-rate {% if stats.pay_rate >= 70 %}text-profit{% elif stats.pay_rate >= 40 %}text-gold{% else %}text-loss{% endif %}">{{ stats.pay_rate }}%</span>
                    <div class="progress-bar-wrap">
                        <div class="progress-bar-fill {% if stats.pay_rate >= 70 %}green{% elif stats.pay_rate < 40 %}red{% endif %}" style="width:{{ stats.pay_rate }}%" role="progressbar" aria-valuenow="{{ stats.pay_rate }}" aria-valuemin="0" aria-valuemax="100"></div>
                    </div>
                </div>
            </div>
        </div>
        <div class="col-md-3 col-6">
            <div class="insight-card">
                <p class="insight-label">{{ t.get('completion_rate', 'Taux de complétion') }}</p>
                <div class="insight-progress-wrap">
                    <span class="insight-rate {% if stats.completion_rate >= 70 %}text-profit{% elif stats.completion_rate >= 40 %}text-gold{% else %}text-loss{% endif %}">{{ stats.completion_rate }}%</span>
                    <div class="progress-bar-wrap">
                        <div class="progress-bar-fill {% if stats.completion_rate >= 70 %}green{% elif stats.completion_rate < 40 %}red{% endif %}" style="width:{{ stats.completion_rate }}%" role="progressbar" aria-valuenow="{{ stats.completion_rate }}" aria-valuemin="0" aria-valuemax="100"></div>
                    </div>
                </div>
            </div>
        </div>
    </div>

    <!-- Tomorrow's Appointments -->
    {% if tomorrow_appointments %}
    <div class="card p-4 mb-4">
        <div class="chart-title">
            <span aria-hidden="true">▦</span> {{ t.tomorrow_appointments }} ({{ tomorrow_appointments|length }})
        </div>
        <div class="table-responsive">
            <table class="table" aria-label="{{ t.tomorrow_appointments }}">
                <thead><tr><th scope="col">{{ t.client }}</th><th scope="col">{{ t.car }}</th><th scope="col">{{ t.service }}</th></tr></thead>
                <tbody>
                {% for a in tomorrow_appointments %}
                <tr>
                    <td><strong>{{ a[1] }}</strong></td>
                    <td>{{ a[2] }} {{ a[3] }}</td>
                    <td>{{ a[4] }}</td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    {% endif %}

    <!-- Pending Appointments -->
    {% if pending_appointments %}
    <div class="pending-alert" role="alert">
        <p class="pending-alert-title">⚠ {{ t.pending_appointments }} ({{ pending_appointments|length }})</p>
        <div class="table-responsive">
            <table class="table" aria-label="{{ t.pending_appointments }}">
                <thead><tr><th scope="col">{{ t.client }}</th><th scope="col">{{ t.car }}</th><th scope="col">{{ t.date }}</th><th scope="col">{{ t.service }}</th></tr></thead>
                <tbody>
                {% for a in pending_appointments %}
                <tr>
                    <td><strong>{{ a[1] }}</strong></td>
                    <td>{{ a[2] }} {{ a[3] }}</td>
                    <td>{{ a[4] }}</td>
                    <td>{{ a[5] }}</td>
                </tr>
                {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    {% endif %}

    <!-- Quick Actions -->
    <div class="quick-actions-grid" role="navigation" aria-label="{{ t.get('quick_actions', 'Actions rap
```

