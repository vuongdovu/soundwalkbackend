"""
Custom validators for domain-specific input.

This module provides validators for:
- Phone numbers (PII validation)

These validators are specific to user-facing applications that handle
personally identifiable information (PII).

Usage:
    from toolkit.validators import validate_phone_number

    class MyModel(models.Model):
        phone = models.CharField(validators=[validate_phone_number])

Note:
    - For generic infrastructure validators (file size, file extension, URL domain,
      JSON schema, XSS prevention, slug format), see core.validators
"""

from __future__ import annotations

import re

from django.core.exceptions import ValidationError


def validate_phone_number(value: str):
    """
    Validate phone number format.

    Accepts formats:
    - +1234567890
    - +1 234 567 890
    - +1-234-567-890
    - 1234567890 (10+ digits)

    Args:
        value: Phone number string to validate

    Raises:
        ValidationError: If format is invalid
    """
    # Remove spaces, dashes, parentheses
    cleaned = re.sub(r"[\s\-\(\)]", "", value)

    # Check for valid format
    if not re.match(r"^\+?[1-9]\d{6,14}$", cleaned):
        raise ValidationError(
            "Enter a valid phone number. "
            "Format: +1234567890 or 10-15 digits."
        )
