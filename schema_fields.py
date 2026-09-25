"""The marshmallow field types moz/schemas.py builds its request schemas from,
plus load_query() which validates a query dict:

    load_query(SchemaCls, {"domain": "tesla.com"}) -> (data, None) | (None, error body)
"""
from marshmallow import RAISE, Schema, ValidationError, fields, validate


class BaseSchema(Schema):
    """Unknown query params are rejected (a typo must not silently no-op)."""

    class Meta:
        unknown = RAISE


def load_query(schema_cls, query):
    try:
        return schema_cls().load(query), None
    except ValidationError as e:
        messages = e.normalized_messages()
        flat = "; ".join(f"{k}: {' '.join(v) if isinstance(v, list) else v}" for k, v in messages.items())
        return None, {"error": f"Invalid parameters: {flat}", "errors": messages}


class StrippedString(fields.String):
    """String with whitespace collapsed; empty -> error when required, else None."""

    def _deserialize(self, value, attr, data, **kwargs):
        value = " ".join(super()._deserialize(value, attr, data, **kwargs).split())
        if not value:
            if self.required:
                raise ValidationError("Must not be empty.")
            return None
        return value


class RefField(StrippedString):
    """ONE param that accepts several forms; `resolver` normalizes it (and
    raises ValueError with a user-facing message)."""
    resolver = None

    def __init__(self, resolver=None, **kwargs):
        kwargs.setdefault("required", True)
        super().__init__(**kwargs)
        self._resolver = resolver if resolver is not None else type(self).resolver

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None or self._resolver is None:
            return value
        try:
            return self._resolver(value)
        except ValueError as e:
            raise ValidationError(str(e))


class PageField(fields.Integer):
    """1-based page, default 1, capped at max_page."""

    def __init__(self, max_page=100, **kwargs):
        kwargs.setdefault("load_default", 1)
        kwargs.setdefault("validate", validate.Range(min=1, max=max_page))
        super().__init__(strict=False, **kwargs)


class ChoiceField(StrippedString):
    """Case-insensitive choice from a list; optional by default."""

    def __init__(self, choices, **kwargs):
        kwargs.setdefault("required", False)
        kwargs.setdefault("load_default", None)
        super().__init__(**kwargs)
        self.choices = [str(c).lower() for c in choices]

    def _deserialize(self, value, attr, data, **kwargs):
        value = super()._deserialize(value, attr, data, **kwargs)
        if value is None:
            return None
        if value.lower() not in self.choices:
            raise ValidationError(f"Must be one of: {', '.join(self.choices)}.")
        return value.lower()
