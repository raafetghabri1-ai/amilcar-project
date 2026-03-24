"""Test all non-parameterized GET routes for 500 errors."""


def test_no_500_errors(admin_client, app):
    """No route should return 500 (server error)."""
    errors = []
    for rule in app.url_map.iter_rules():
        if 'GET' not in rule.methods:
            continue
        if '<' in rule.rule or rule.endpoint == 'static':
            continue
        resp = admin_client.get(rule.rule)
        if resp.status_code == 500:
            errors.append(f"{rule.rule} ({rule.endpoint})")
    assert not errors, f"Routes returning 500: {errors}"


def test_route_count(app):
    """App should have 400+ routes."""
    rules = list(app.url_map.iter_rules())
    assert len(rules) >= 400, f"Expected 400+ routes, got {len(rules)}"


def test_no_duplicate_endpoints(app):
    """No unintentional duplicate endpoint names (allow known aliases)."""
    known_aliases = {'reports_bp.export_monthly_report_excel', 'exports_bp.export_monthly_report_excel', 'healthz'}
    endpoints = {}
    for rule in app.url_map.iter_rules():
        if rule.endpoint in endpoints:
            endpoints[rule.endpoint].append(rule.rule)
        else:
            endpoints[rule.endpoint] = [rule.rule]
    dupes = {k: v for k, v in endpoints.items() if len(v) > 1 and k not in known_aliases}
    assert not dupes, f"Duplicate endpoints: {dupes}"
