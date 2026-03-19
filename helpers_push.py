"""
AMILCAR — Web Push Notification Helper
Sends VAPID-authenticated push notifications to subscribed browsers.
"""
import json
from helpers_logging import get_logger

_log = get_logger('push')


def _get_vapid_keys(conn):
    """Get VAPID keys from settings table."""
    keys = {}
    for row in conn.execute(
        "SELECT key, value FROM settings WHERE key IN ('vapid_public','vapid_private','vapid_email')"
    ).fetchall():
        keys[row[0]] = row[1]
    if not keys.get('vapid_public') or not keys.get('vapid_private') or not keys.get('vapid_email'):
        return None
    return keys


def _get_subscriptions(conn):
    """Get all stored push subscriptions."""
    rows = conn.execute(
        "SELECT value FROM settings WHERE key LIKE 'push_sub_%'"
    ).fetchall()
    subs = []
    for row in rows:
        try:
            sub = json.loads(row[0].replace("'", '"'))
            if sub.get('endpoint'):
                subs.append(sub)
        except (json.JSONDecodeError, AttributeError):
            continue
    return subs


def send_push(conn, title, body, url='/', icon='/static/logo.png'):
    """Send push notification to all subscribed browsers.
    
    Returns (sent_count, error_count).
    """
    keys = _get_vapid_keys(conn)
    if not keys:
        _log.debug('Push skipped — no VAPID keys configured')
        return 0, 0

    subs = _get_subscriptions(conn)
    if not subs:
        _log.debug('Push skipped — no subscriptions')
        return 0, 0

    try:
        from pywebpush import webpush, WebPushException
    except ImportError:
        _log.warning('pywebpush not installed')
        return 0, 0

    payload = json.dumps({
        'title': title,
        'body': body,
        'icon': icon,
        'url': url,
    })
    vapid_claims = {
        'sub': f"mailto:{keys['vapid_email']}",
    }

    sent, errors = 0, 0
    expired_endpoints = []
    for sub in subs:
        try:
            webpush(
                subscription_info=sub,
                data=payload,
                vapid_private_key=keys['vapid_private'],
                vapid_claims=vapid_claims,
                timeout=5,
            )
            sent += 1
        except WebPushException as e:
            if '410' in str(e) or '404' in str(e):
                expired_endpoints.append(sub.get('endpoint', '')[:50])
            else:
                _log.warning('Push error: %s', e)
            errors += 1
        except Exception as e:
            _log.warning('Push unexpected error: %s', e)
            errors += 1

    # Clean up expired subscriptions
    for ep in expired_endpoints:
        conn.execute("DELETE FROM settings WHERE key = ?", (f'push_sub_{ep}',))
    if expired_endpoints:
        conn.commit()
        _log.info('Cleaned %d expired push subscriptions', len(expired_endpoints))

    _log.info('Push sent: %d ok, %d errors (title=%s)', sent, errors, title)
    return sent, errors
