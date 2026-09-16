"""Fixed, predefined condition operators (phase section 15). No eval(),
no arbitrary Python/SQL/Jinja expressions — every operator here is matched
against explicit comparison code in the (later-slice) evaluator, never
interpreted as a string."""


class Operator:
    EQUALS = "equals"
    NOT_EQUALS = "not_equals"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    CONTAINS = "contains"
    IN = "in"
    IS_EMPTY = "is_empty"
    IS_NOT_EMPTY = "is_not_empty"


ALL_OPERATORS = frozenset(
    {
        Operator.EQUALS,
        Operator.NOT_EQUALS,
        Operator.GREATER_THAN,
        Operator.GREATER_THAN_OR_EQUAL,
        Operator.LESS_THAN,
        Operator.LESS_THAN_OR_EQUAL,
        Operator.CONTAINS,
        Operator.IN,
        Operator.IS_EMPTY,
        Operator.IS_NOT_EMPTY,
    }
)

# Operators valid per declared field type — e.g. GREATER_THAN on a plain
# string field would silently compare lexicographically, which is never
# what a user configuring "customer_name greater_than X" would mean.
NO_VALUE_OPERATORS = frozenset({Operator.IS_EMPTY, Operator.IS_NOT_EMPTY})
ORDERED_OPERATORS = frozenset(
    {
        Operator.GREATER_THAN,
        Operator.GREATER_THAN_OR_EQUAL,
        Operator.LESS_THAN,
        Operator.LESS_THAN_OR_EQUAL,
    }
)
TEXT_ONLY_OPERATORS = frozenset({Operator.CONTAINS, Operator.IN})
