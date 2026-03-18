"""Tests for helpers_validation.py — Central Input Validation."""
import pytest
from helpers_validation import Validator


class TestValidator:
    def setup_method(self):
        self.v = Validator()

    # ── require ──
    def test_require_ok(self):
        assert self.v.require('hello', 'name') == 'hello'
        assert self.v.ok

    def test_require_empty(self):
        self.v.require('', 'name', 'Nom')
        assert not self.v.ok
        assert 'Nom est requis' in self.v.first_error()

    def test_require_none(self):
        self.v.require(None, 'name')
        assert not self.v.ok

    def test_require_strips_whitespace(self):
        assert self.v.require('  hello  ', 'x') == 'hello'

    # ── require_int ──
    def test_require_int_ok(self):
        assert self.v.require_int('42', 'id') == 42
        assert self.v.ok

    def test_require_int_invalid(self):
        self.v.require_int('abc', 'id', 'ID')
        assert not self.v.ok
        assert 'nombre entier' in self.v.first_error()

    def test_require_int_min(self):
        result = self.v.require_int('0', 'qty', 'Quantité', min_val=1)
        assert not self.v.ok
        assert result == 1

    def test_require_int_max(self):
        result = self.v.require_int('999', 'qty', min_val=1, max_val=100)
        assert not self.v.ok
        assert result == 100

    def test_require_int_empty(self):
        self.v.require_int('', 'id', 'ID')
        assert not self.v.ok

    # ── require_float ──
    def test_require_float_ok(self):
        assert self.v.require_float('3.14', 'price') == 3.14
        assert self.v.ok

    def test_require_float_comma(self):
        assert self.v.require_float('3,14', 'price') == 3.14

    def test_require_float_invalid(self):
        self.v.require_float('abc', 'price', 'Prix')
        assert not self.v.ok

    def test_require_float_min(self):
        result = self.v.require_float('-5', 'price', min_val=0)
        assert not self.v.ok
        assert result == 0.0

    def test_optional_float_empty(self):
        assert self.v.optional_float('', 'discount') == 0.0
        assert self.v.ok

    def test_optional_float_valid(self):
        assert self.v.optional_float('10.5', 'discount') == 10.5
        assert self.v.ok

    # ── string ──
    def test_string_ok(self):
        assert self.v.string('hello', 'name') == 'hello'
        assert self.v.ok

    def test_string_too_short(self):
        self.v.string('a', 'name', 'Nom', min_len=2)
        assert not self.v.ok

    def test_string_too_long(self):
        result = self.v.string('x' * 600, 'name', max_len=500)
        assert len(result) == 500

    def test_safe_text_strips_html(self):
        result = self.v.safe_text('<script>alert("xss")</script>hello', 'notes')
        assert '<script>' not in result
        assert 'hello' in result

    # ── phone ──
    def test_phone_valid(self):
        assert self.v.phone('+21612345678') == '+21612345678'
        assert self.v.ok

    def test_phone_with_spaces(self):
        assert self.v.phone('+216 12 345 678') == '+21612345678'
        assert self.v.ok

    def test_phone_too_short(self):
        self.v.phone('123')
        assert not self.v.ok

    def test_phone_empty_ok(self):
        assert self.v.phone('') == ''
        assert self.v.ok

    def test_phone_letters(self):
        self.v.phone('abcdefgh')
        assert not self.v.ok

    # ── email ──
    def test_email_valid(self):
        assert self.v.email('test@example.com') == 'test@example.com'
        assert self.v.ok

    def test_email_uppercase(self):
        assert self.v.email('Test@Example.COM') == 'test@example.com'

    def test_email_invalid(self):
        self.v.email('not-an-email')
        assert not self.v.ok

    def test_email_empty_ok(self):
        assert self.v.email('') == ''
        assert self.v.ok

    # ── date_str ──
    def test_date_valid(self):
        assert self.v.date_str('2026-03-18') == '2026-03-18'
        assert self.v.ok

    def test_date_invalid_format(self):
        self.v.date_str('18/03/2026')
        assert not self.v.ok

    def test_date_invalid_day(self):
        self.v.date_str('2026-02-30')
        assert not self.v.ok

    def test_date_empty(self):
        self.v.date_str('')
        assert not self.v.ok

    # ── time_str ──
    def test_time_valid(self):
        assert self.v.time_str('14:30') == '14:30'
        assert self.v.ok

    def test_time_empty_ok(self):
        assert self.v.time_str('') == ''
        assert self.v.ok

    def test_time_invalid(self):
        self.v.time_str('2pm')
        assert not self.v.ok

    # ── plate ──
    def test_plate_valid(self):
        assert self.v.plate('123 TU 4567') == '123 TU 4567'
        assert self.v.ok

    def test_plate_empty_ok(self):
        assert self.v.plate('') == ''
        assert self.v.ok

    # ── amount ──
    def test_amount_valid(self):
        assert self.v.amount('150.50') == 150.50
        assert self.v.ok

    def test_amount_negative(self):
        self.v.amount('-10')
        assert not self.v.ok

    def test_amount_too_large(self):
        self.v.amount('1000000')
        assert not self.v.ok

    # ── choice ──
    def test_choice_valid(self):
        assert self.v.choice('paid', ['paid', 'unpaid', 'partial'], 'status') == 'paid'
        assert self.v.ok

    def test_choice_invalid(self):
        result = self.v.choice('hacked', ['paid', 'unpaid'], 'status')
        assert not self.v.ok
        assert result == 'paid'  # returns first option as default

    # ── multiple errors ──
    def test_multiple_errors(self):
        self.v.require('', 'name')
        self.v.require('', 'phone')
        assert len(self.v.errors) == 2
        assert len(self.v.all_messages()) == 2

    def test_first_error(self):
        v = Validator()
        assert v.first_error() is None
        v.error('f', 'msg')
        assert v.first_error() == 'msg'
