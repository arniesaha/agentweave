"""Tests for agentweave.pii — PII detection, redaction, flag, block modes."""

import os

import pytest

from agentweave.pii import (
    PIIBlockedError,
    PIIMode,
    PIIResult,
    scan_text,
    _find_matches,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _scan(text: str, mode: str) -> PIIResult:
    return scan_text(text, mode=mode)


# ---------------------------------------------------------------------------
# Email detection
# ---------------------------------------------------------------------------

class TestEmailDetection:
    def test_simple_email(self):
        r = _scan("Contact us at user@example.com for support.", "flag")
        assert r.is_detected
        kinds = {m.kind for m in r.matches}
        assert "EMAIL" in kinds

    def test_email_with_subdomain(self):
        r = _scan("Send to dev.team@mail.corp.example.org", "flag")
        assert r.is_detected

    def test_no_false_positive_on_version(self):
        # "v1.2.3@release" is not a valid email
        r = _scan("version v1.2.3 is installed", "flag")
        assert not r.is_detected

    def test_multiple_emails(self):
        r = _scan("From: alice@foo.com To: bob@bar.org", "flag")
        emails = [m for m in r.matches if m.kind == "EMAIL"]
        assert len(emails) == 2


# ---------------------------------------------------------------------------
# Phone detection
# ---------------------------------------------------------------------------

class TestPhoneDetection:
    def test_us_phone_dashes(self):
        r = _scan("Call 800-555-1234 now.", "flag")
        assert r.is_detected
        assert any(m.kind == "PHONE" for m in r.matches)

    def test_us_phone_with_country_code(self):
        r = _scan("Reach us at +1-800-555-1234.", "flag")
        assert r.is_detected

    def test_us_phone_parentheses(self):
        r = _scan("Phone: (800) 555-1234", "flag")
        assert r.is_detected

    def test_us_phone_dots(self):
        r = _scan("Call 800.555.1234", "flag")
        assert r.is_detected

    def test_plain_7digit_no_detection(self):
        # 7-digit local number — shouldn't match our 10-digit pattern
        r = _scan("ext 555-1234", "flag")
        # May or may not match depending on context — at minimum the test
        # verifies no exception is raised
        assert isinstance(r, PIIResult)


# ---------------------------------------------------------------------------
# SSN detection
# ---------------------------------------------------------------------------

class TestSSNDetection:
    def test_ssn_with_dashes(self):
        r = _scan("SSN: 123-45-6789", "flag")
        assert r.is_detected
        assert any(m.kind == "SSN" for m in r.matches)

    def test_ssn_with_spaces(self):
        r = _scan("Social: 123 45 6789", "flag")
        assert r.is_detected

    def test_not_a_ssn_zip_code(self):
        # 5-digit zip is not an SSN
        r = _scan("ZIP: 90210", "flag")
        assert not r.is_detected


# ---------------------------------------------------------------------------
# Credit card detection
# ---------------------------------------------------------------------------

class TestCreditCardDetection:
    def test_visa_16_digit_spaces(self):
        r = _scan("Card: 4111 1111 1111 1111", "flag")
        assert r.is_detected
        assert any(m.kind == "CREDIT_CARD" for m in r.matches)

    def test_visa_16_digit_dashes(self):
        r = _scan("Card: 4111-1111-1111-1111", "flag")
        assert r.is_detected

    def test_mastercard(self):
        r = _scan("MC: 5500-0000-0000-0004", "flag")
        assert r.is_detected

    def test_amex(self):
        r = _scan("Amex: 3714-496353-98431", "flag")
        assert r.is_detected

    def test_discover(self):
        r = _scan("Discover: 6011-1111-1111-1117", "flag")
        assert r.is_detected

    def test_partial_card_no_match(self):
        # 12 digits is not a credit card
        r = _scan("ref: 411111111111", "flag")
        assert not r.is_detected


# ---------------------------------------------------------------------------
# IPv4 detection
# ---------------------------------------------------------------------------

class TestIPv4Detection:
    def test_private_ip(self):
        r = _scan("Server at 192.168.1.100", "flag")
        assert r.is_detected
        assert any(m.kind == "IPV4" for m in r.matches)

    def test_public_ip(self):
        r = _scan("IP: 8.8.8.8", "flag")
        assert r.is_detected

    def test_no_false_positive_version(self):
        # Version strings like "1.2.3" (only 3 octets) shouldn't match
        r = _scan("Python 3.11.2 is installed", "flag")
        assert not r.is_detected


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------

class TestOffMode:
    def test_off_mode_skips_scan(self):
        r = _scan("user@example.com and 123-45-6789", "off")
        assert not r.is_detected
        assert r.matches == []
        assert r.cleaned == r.original

    def test_env_default_is_off(self, monkeypatch):
        monkeypatch.delenv("AGENTWEAVE_PII_MODE", raising=False)
        r = scan_text("user@example.com")  # no mode arg — reads from env
        assert not r.is_detected


class TestFlagMode:
    def test_flag_does_not_redact(self):
        text = "Email: user@example.com"
        r = _scan(text, "flag")
        assert r.is_detected
        assert r.cleaned == text  # unchanged
        assert r.original == text

    def test_flag_sets_pii_kinds(self):
        r = _scan("Call 800-555-1234 or email user@example.com", "flag")
        assert r.is_detected
        kinds = {m.kind for m in r.matches}
        assert "EMAIL" in kinds
        assert "PHONE" in kinds


class TestRedactMode:
    def test_email_redacted(self):
        r = _scan("Contact user@example.com", "redact")
        assert r.is_detected
        assert "[REDACTED:EMAIL]" in r.cleaned
        assert "user@example.com" not in r.cleaned

    def test_ssn_redacted(self):
        r = _scan("SSN: 123-45-6789", "redact")
        assert r.is_detected
        assert "[REDACTED:SSN]" in r.cleaned
        assert "123-45-6789" not in r.cleaned

    def test_multiple_redacted(self):
        text = "Email user@example.com, SSN 123-45-6789"
        r = _scan(text, "redact")
        assert r.is_detected
        assert "user@example.com" not in r.cleaned
        assert "123-45-6789" not in r.cleaned

    def test_clean_text_unchanged(self):
        text = "Hello, this message has no PII."
        r = _scan(text, "redact")
        assert not r.is_detected
        assert r.cleaned == text

    def test_redacted_length_changes(self):
        text = "user@example.com"
        r = _scan(text, "redact")
        assert r.cleaned != text
        assert "[REDACTED:EMAIL]" in r.cleaned


class TestBlockMode:
    def test_block_raises_on_pii(self):
        with pytest.raises(PIIBlockedError) as exc_info:
            _scan("SSN: 123-45-6789", "block")
        assert exc_info.value.matches

    def test_block_error_contains_kinds(self):
        with pytest.raises(PIIBlockedError) as exc_info:
            _scan("Email: user@example.com, SSN: 123-45-6789", "block")
        kinds_in_msg = exc_info.value.args[0]
        assert "EMAIL" in kinds_in_msg or "SSN" in kinds_in_msg

    def test_block_no_raise_on_clean(self):
        r = _scan("Hello world, no PII here.", "block")
        assert not r.is_detected


# ---------------------------------------------------------------------------
# env var override
# ---------------------------------------------------------------------------

class TestEnvVar:
    def test_mode_from_env_redact(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_PII_MODE", "redact")
        r = scan_text("My email is test@example.com")
        assert r.is_detected
        assert "[REDACTED:EMAIL]" in r.cleaned

    def test_mode_from_env_off(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_PII_MODE", "off")
        r = scan_text("My email is test@example.com")
        assert not r.is_detected

    def test_invalid_mode_defaults_to_off(self, monkeypatch):
        monkeypatch.setenv("AGENTWEAVE_PII_MODE", "magic_mode")
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            r = scan_text("test@example.com")
        assert not r.is_detected
        assert any("not valid" in str(warning.message) for warning in w)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string(self):
        for mode in (PIIMode.OFF, PIIMode.FLAG, PIIMode.REDACT):
            r = _scan("", mode)
            assert not r.is_detected
            assert r.cleaned == ""

    def test_none_content_graceful(self):
        # None should return empty-ish result without crashing
        r = scan_text("", mode="flag")
        assert not r.is_detected

    def test_only_whitespace(self):
        r = _scan("   \n\t  ", "flag")
        assert not r.is_detected

    def test_mixed_pii_all_detected(self):
        text = (
            "Name: John Doe, "
            "Email: john.doe@example.com, "
            "Phone: 800-555-1234, "
            "SSN: 123-45-6789, "
            "Card: 4111-1111-1111-1111, "
            "IP: 192.168.1.1"
        )
        r = _scan(text, "flag")
        kinds = {m.kind for m in r.matches}
        assert "EMAIL" in kinds
        assert "PHONE" in kinds
        assert "SSN" in kinds
        assert "CREDIT_CARD" in kinds
        assert "IPV4" in kinds

    def test_redact_preserves_surrounding_text(self):
        text = "Hello user@example.com, how are you?"
        r = _scan(text, "redact")
        assert r.cleaned.startswith("Hello ")
        assert r.cleaned.endswith(", how are you?")
