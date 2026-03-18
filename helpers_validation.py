"""
AMILCAR — Input Validation & Sanitization
==========================================
Central validation layer for all user inputs.
Used by route handlers to validate form data before DB operations.
"""
import re
from datetime import datetime, date


# ─── Patterns ───
_PHONE_RE = re.compile(r'^\+?\d{7,15}$')
_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$')
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_TIME_RE = re.compile(r'^\d{2}:\d{2}$')
_PLATE_RE = re.compile(r'^[A-Za-z0-9\- ]{2,15}$')


class ValidationError(Exception):
    """Raised when input validation fails."""
    def __init__(self, field, message):
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class Validator:
    """Collects validation errors for a form submission."""

    def __init__(self):
        self.errors = []

    @property
    def ok(self):
        return len(self.errors) == 0

    def error(self, field, message):
        self.errors.append({'field': field, 'message': message})

    def first_error(self):
        return self.errors[0]['message'] if self.errors else None

    def all_messages(self):
        return [e['message'] for e in self.errors]

    # ─── Basic types ───

    def require(self, value, field_name, label=None):
        """Ensure value is not empty."""
        label = label or field_name
        if value is None or str(value).strip() == '':
            self.error(field_name, f"{label} est requis")
            return ''
        return str(value).strip()

    def require_int(self, value, field_name, label=None, min_val=None, max_val=None):
        """Parse and validate integer."""
        label = label or field_name
        if value is None or str(value).strip() == '':
            self.error(field_name, f"{label} est requis")
            return 0
        try:
            n = int(value)
        except (ValueError, TypeError):
            self.error(field_name, f"{label} doit être un nombre entier")
            return 0
        if min_val is not None and n < min_val:
            self.error(field_name, f"{label} minimum : {min_val}")
            return min_val
        if max_val is not None and n > max_val:
            self.error(field_name, f"{label} maximum : {max_val}")
            return max_val
        return n

    def require_float(self, value, field_name, label=None, min_val=None, max_val=None):
        """Parse and validate decimal number."""
        label = label or field_name
        if value is None or str(value).strip() == '':
            self.error(field_name, f"{label} est requis")
            return 0.0
        try:
            n = float(str(value).replace(',', '.'))
        except (ValueError, TypeError):
            self.error(field_name, f"{label} doit être un nombre")
            return 0.0
        if min_val is not None and n < min_val:
            self.error(field_name, f"{label} minimum : {min_val}")
            return float(min_val)
        if max_val is not None and n > max_val:
            self.error(field_name, f"{label} maximum : {max_val}")
            return float(max_val)
        return n

    def optional_float(self, value, field_name, label=None, min_val=0, max_val=None):
        """Parse optional decimal — returns 0.0 if empty."""
        if value is None or str(value).strip() == '':
            return 0.0
        return self.require_float(value, field_name, label, min_val, max_val)

    # ─── String validation ───

    def string(self, value, field_name, label=None, min_len=0, max_len=500):
        """Validate string length."""
        label = label or field_name
        s = str(value).strip() if value else ''
        if min_len > 0 and len(s) < min_len:
            self.error(field_name, f"{label} doit contenir au moins {min_len} caractères")
        if len(s) > max_len:
            self.error(field_name, f"{label} ne doit pas dépasser {max_len} caractères")
            s = s[:max_len]
        return s

    def safe_text(self, value, field_name, label=None, max_len=2000):
        """Sanitize free text — strip HTML tags."""
        s = self.string(value, field_name, label, max_len=max_len)
        s = re.sub(r'<[^>]+>', '', s)
        return s

    # ─── Specific formats ───

    def phone(self, value, field_name='phone', label='Téléphone'):
        """Validate phone number format."""
        if value is None or str(value).strip() == '':
            return ''
        cleaned = re.sub(r'[\s\-\.\(\)]', '', str(value).strip())
        if not _PHONE_RE.match(cleaned):
            self.error(field_name, f"{label} invalide (7-15 chiffres)")
            return cleaned
        return cleaned

    def email(self, value, field_name='email', label='Email'):
        """Validate email format."""
        if value is None or str(value).strip() == '':
            return ''
        e = str(value).strip().lower()
        if not _EMAIL_RE.match(e):
            self.error(field_name, f"{label} invalide")
            return e
        return e

    def date_str(self, value, field_name='date', label='Date'):
        """Validate YYYY-MM-DD date format."""
        label = label or field_name
        if value is None or str(value).strip() == '':
            self.error(field_name, f"{label} est requis")
            return ''
        s = str(value).strip()
        if not _DATE_RE.match(s):
            self.error(field_name, f"{label} format invalide (AAAA-MM-JJ)")
            return s
        try:
            datetime.strptime(s, '%Y-%m-%d')
        except ValueError:
            self.error(field_name, f"{label} date invalide")
        return s

    def time_str(self, value, field_name='time', label='Heure'):
        """Validate HH:MM time format (optional)."""
        if value is None or str(value).strip() == '':
            return ''
        s = str(value).strip()
        if not _TIME_RE.match(s):
            self.error(field_name, f"{label} format invalide (HH:MM)")
        return s

    def plate(self, value, field_name='plate', label='Immatriculation'):
        """Validate license plate."""
        if value is None or str(value).strip() == '':
            return ''
        s = str(value).strip().upper()
        if not _PLATE_RE.match(s):
            self.error(field_name, f"{label} invalide")
        return s

    def amount(self, value, field_name='amount', label='Montant'):
        """Validate monetary amount."""
        return self.require_float(value, field_name, label, min_val=0, max_val=999999.99)

    def optional_amount(self, value, field_name='amount', label='Montant'):
        """Validate optional monetary amount."""
        return self.optional_float(value, field_name, label, min_val=0, max_val=999999.99)

    def choice(self, value, choices, field_name, label=None):
        """Validate value is in allowed choices."""
        label = label or field_name
        s = str(value).strip() if value else ''
        if s not in choices:
            self.error(field_name, f"{label} invalide")
            return choices[0] if choices else ''
        return s
