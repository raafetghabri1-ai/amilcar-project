"""
AMILCAR — Email Helper Module
Reusable SMTP send + HTML template builder
"""
import smtplib
import logging
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.application import MIMEApplication
from database.db import get_db

_log = logging.getLogger('amilcar.email')


def get_smtp_settings():
    """Return dict of SMTP settings from DB. Checks both key naming conventions."""
    with get_db() as conn:
        rows = conn.execute("SELECT key, value FROM settings WHERE key LIKE 'smtp_%'").fetchall()
    s = {r[0]: r[1] for r in rows}
    return {
        'host': s.get('smtp_host') or s.get('smtp_server', ''),
        'port': int(s.get('smtp_port', '587') or '587'),
        'user': s.get('smtp_user') or s.get('smtp_email', ''),
        'password': s.get('smtp_pass') or s.get('smtp_password', ''),
        'from_name': s.get('smtp_from') or s.get('smtp_from_name', 'AMILCAR'),
    }


def send_email(to_email, subject, html_body, attachments=None, customer_id=None):
    """Send an email via SMTP. Returns True on success, False on failure.
    attachments: list of (filename, bytes_data, subtype) tuples
    """
    cfg = get_smtp_settings()
    if not cfg['host'] or not cfg['user']:
        _log.warning('SMTP not configured, skipping email to %s', to_email)
        return False

    msg = MIMEMultipart()
    msg['From'] = f"{cfg['from_name']} <{cfg['user']}>"
    msg['To'] = to_email
    msg['Subject'] = subject
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    if attachments:
        for fname, data, subtype in attachments:
            part = MIMEApplication(data, _subtype=subtype)
            part.add_header('Content-Disposition', 'attachment', filename=fname)
            msg.attach(part)

    status = 'failed'
    try:
        server = smtplib.SMTP(cfg['host'], cfg['port'], timeout=15)
        server.starttls()
        server.login(cfg['user'], cfg['password'])
        server.send_message(msg)
        server.quit()
        status = 'sent'
        _log.info('Email sent to %s: %s', to_email, subject)
    except Exception as e:
        _log.error('Email failed to %s: %s', to_email, e)

    # Log to email_log
    try:
        with get_db() as conn:
            conn.execute(
                "INSERT INTO email_log (customer_id, to_email, subject, body, status) VALUES (?,?,?,?,?)",
                (customer_id, to_email, subject, html_body[:500], status))
            conn.commit()
    except Exception:
        pass

    return status == 'sent'


def test_smtp_connection():
    """Test SMTP connection. Returns (success: bool, message: str)."""
    cfg = get_smtp_settings()
    if not cfg['host'] or not cfg['user']:
        return False, "SMTP non configuré"
    try:
        server = smtplib.SMTP(cfg['host'], cfg['port'], timeout=10)
        server.starttls()
        server.login(cfg['user'], cfg['password'])
        server.quit()
        return True, "Connexion SMTP réussie"
    except Exception as e:
        return False, f"Erreur: {str(e)}"


def get_setting_value(key, default=''):
    """Get a single setting value."""
    with get_db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


# ─── HTML Email Templates ───

def _base_template(content, shop_name='AMILCAR'):
    """Wrap content in a styled HTML email template."""
    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="margin:0;padding:0;background:#0a0a0a;font-family:Arial,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0a0a0a;padding:20px 0">
<tr><td align="center">
<table width="560" cellpadding="0" cellspacing="0" style="background:#111;border-radius:12px;border:1px solid #222;overflow:hidden">
  <tr><td style="background:linear-gradient(135deg,#D4AF37,#B8962E);padding:20px;text-align:center">
    <h1 style="margin:0;color:#09090b;font-size:22px;letter-spacing:1px">{shop_name}</h1>
  </td></tr>
  <tr><td style="padding:28px 32px;color:#e0e0e0;font-size:14px;line-height:1.7">
    {content}
  </td></tr>
  <tr><td style="padding:16px 32px;border-top:1px solid #222;text-align:center;color:#666;font-size:11px">
    &copy; {shop_name} &mdash; Centre d'entretien automobile premium
  </td></tr>
</table>
</td></tr></table>
</body></html>"""


def build_payment_receipt_email(invoice_data, shop_name='AMILCAR'):
    """Build HTML email for payment receipt.
    invoice_data: dict with keys: id, customer_name, amount, paid_amount, service, date, car, payment_method
    """
    content = f"""
    <h2 style="color:#D4AF37;margin:0 0 16px">Reçu de Paiement</h2>
    <p>Bonjour <strong>{invoice_data['customer_name']}</strong>,</p>
    <p>Nous confirmons la réception de votre paiement :</p>
    <table width="100%" style="margin:16px 0;border-collapse:collapse">
      <tr><td style="padding:8px 12px;border:1px solid #333;color:#999">Facture N°</td>
          <td style="padding:8px 12px;border:1px solid #333;color:#D4AF37;font-weight:bold">#{invoice_data['id']}</td></tr>
      <tr><td style="padding:8px 12px;border:1px solid #333;color:#999">Service</td>
          <td style="padding:8px 12px;border:1px solid #333">{invoice_data['service']}</td></tr>
      <tr><td style="padding:8px 12px;border:1px solid #333;color:#999">Véhicule</td>
          <td style="padding:8px 12px;border:1px solid #333">{invoice_data['car']}</td></tr>
      <tr><td style="padding:8px 12px;border:1px solid #333;color:#999">Montant total</td>
          <td style="padding:8px 12px;border:1px solid #333">{invoice_data['amount']:.2f} TND</td></tr>
      <tr><td style="padding:8px 12px;border:1px solid #333;color:#999">Montant payé</td>
          <td style="padding:8px 12px;border:1px solid #333;color:#4ade80;font-weight:bold">{invoice_data['paid_amount']:.2f} TND</td></tr>
      <tr><td style="padding:8px 12px;border:1px solid #333;color:#999">Mode</td>
          <td style="padding:8px 12px;border:1px solid #333">{invoice_data['payment_method']}</td></tr>
    </table>
    <p>Merci pour votre confiance !</p>"""
    return _base_template(content, shop_name)


def build_reminder_email(appt_data, shop_name='AMILCAR'):
    """Build HTML email for appointment reminder.
    appt_data: dict with keys: customer_name, date, time, service, car
    """
    time_str = f" à <strong>{appt_data['time']}</strong>" if appt_data.get('time') else ""
    content = f"""
    <h2 style="color:#D4AF37;margin:0 0 16px">Rappel de Rendez-vous</h2>
    <p>Bonjour <strong>{appt_data['customer_name']}</strong>,</p>
    <p>Nous vous rappelons votre rendez-vous demain{time_str} :</p>
    <table width="100%" style="margin:16px 0;border-collapse:collapse">
      <tr><td style="padding:8px 12px;border:1px solid #333;color:#999">Date</td>
          <td style="padding:8px 12px;border:1px solid #333;color:#D4AF37;font-weight:bold">{appt_data['date']}</td></tr>
      <tr><td style="padding:8px 12px;border:1px solid #333;color:#999">Service</td>
          <td style="padding:8px 12px;border:1px solid #333">{appt_data['service']}</td></tr>
      <tr><td style="padding:8px 12px;border:1px solid #333;color:#999">Véhicule</td>
          <td style="padding:8px 12px;border:1px solid #333">{appt_data['car']}</td></tr>
    </table>
    <p>À bientôt chez {shop_name} !</p>"""
    return _base_template(content, shop_name)
