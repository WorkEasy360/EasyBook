"""Strict tool input schemas (phase section 23).

Tool arguments come from a language model and are UNTRUSTED. They are
validated with DRF serializers (already the project's validation layer),
which additionally REJECT unknown keys — a model cannot smuggle an
`organization_id` or any other unexpected parameter into a tool. The JSON
Schema advertised to the model is derived from the same serializer, so the
two can never drift apart.
"""

import datetime

from rest_framework import serializers

MAX_PERIOD_DAYS = 366 * 5


class StrictSerializer(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict):
            raise serializers.ValidationError({"non_field_errors": ["Arguments must be an object."]})
        unknown = sorted(set(data) - set(self.fields))
        if unknown:
            raise serializers.ValidationError({key: ["Unknown argument."] for key in unknown})
        return super().to_internal_value(data)


def validate_period(attrs, start="from_date", end="to_date", required=True):
    from_date, to_date = attrs.get(start), attrs.get(end)
    if required and (from_date is None or to_date is None):
        raise serializers.ValidationError({start: ["Both dates are required."]})
    if from_date and to_date:
        if from_date > to_date:
            raise serializers.ValidationError({start: [f"{start} must be on or before {end}."]})
        if (to_date - from_date).days > MAX_PERIOD_DAYS:
            raise serializers.ValidationError({start: ["Period may not exceed five years."]})
    return attrs


class EmptyArgs(StrictSerializer):
    pass


class PeriodArgs(StrictSerializer):
    from_date = serializers.DateField(help_text="Period start, YYYY-MM-DD (inclusive).")
    to_date = serializers.DateField(help_text="Period end, YYYY-MM-DD (inclusive).")

    def validate(self, attrs):
        return validate_period(attrs)


class LimitMixin(serializers.Serializer):
    limit = serializers.IntegerField(
        required=False, min_value=1, max_value=100, help_text="Maximum rows to return (the server caps it further)."
    )


class AsOfArgs(StrictSerializer):
    as_of_date = serializers.DateField(required=False, help_text="As-of date, YYYY-MM-DD. Defaults to today.")


def _field_schema(field: serializers.Field) -> dict:
    schema: dict
    if isinstance(field, serializers.DateField):
        schema = {"type": "string", "format": "date"}
    elif isinstance(field, serializers.UUIDField):
        schema = {"type": "string", "format": "uuid"}
    elif isinstance(field, serializers.ChoiceField):
        schema = {"type": "string", "enum": list(field.choices)}
    elif isinstance(field, serializers.IntegerField):
        schema = {"type": "integer"}
        if field.min_value is not None:
            schema["minimum"] = field.min_value
        if field.max_value is not None:
            schema["maximum"] = field.max_value
    elif isinstance(field, serializers.BooleanField):
        schema = {"type": "boolean"}
    elif isinstance(field, serializers.CharField):
        schema = {"type": "string"}
        if field.max_length is not None:
            schema["maxLength"] = field.max_length
    else:  # pragma: no cover — every tool field type is listed above
        raise TypeError(f"Unsupported tool argument field: {type(field).__name__}")
    if field.help_text:
        schema["description"] = str(field.help_text)
    return schema


def serializer_to_json_schema(serializer_class) -> dict:
    serializer = serializer_class()
    properties = {name: _field_schema(field) for name, field in serializer.fields.items()}
    required = [name for name, field in serializer.fields.items() if field.required]
    schema = {"type": "object", "properties": properties, "additionalProperties": False}
    if required:
        schema["required"] = required
    return schema


def today_or(value: datetime.date | None, ctx) -> datetime.date:
    return value or ctx.today
