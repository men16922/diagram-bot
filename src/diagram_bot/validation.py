"""Formats of AWS identifiers checked at the edges (HTTP models, Identity Center sign-in)."""
REGION = r"^[a-z]{2}(?:-[a-z0-9]+)+-\d+$"
ACCOUNT = r"^\d{12}$"
ROLE_NAME = r"^[\w+=,.@-]+$"
