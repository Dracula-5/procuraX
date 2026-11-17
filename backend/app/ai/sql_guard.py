"""Guard for model-written SQL: one read-only SELECT over allow-listed tables, columns and functions.

The analytics assistant may let a language model draft SQL for a question the fixed intents do not cover.
Before anything runs, the text is parsed with sqlglot (PostgreSQL dialect) and refused unless:

* it is exactly one statement, a SELECT (CTEs and sub-queries allowed, all of them SELECTs);
* every table is on the allow-list, unqualified (no schema, no catalogs such as pg_catalog);
* every column is an allowed column of an allowed table, or an alias the query itself defines (bank
  details, MFA secrets, settings and other tenants' identifiers are simply not on the list);
* every function is on the allow-list (aggregates, date and text helpers; never set_config,
  current_setting, pg_sleep, dblink, lo_* or anything else);
* no `*`, no SELECT … INTO, no row locks, no comments, no VALUES lists.

The rewritten statement gets LIMIT ≤ MAX_ROWS. The caller runs it in a read-only transaction with a
statement timeout under the user's tenant (RLS), so a gap in this guard still cannot write, wait forever,
or read another tenant's rows. Pure function, no I/O.
"""

from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

MAX_ROWS = 200


def _columns(names: str) -> frozenset[str]:
    return frozenset(names.split())


ALLOWED: dict[str, frozenset[str]] = {
    "purchase_requests": _columns(
        "id number title status category currency estimated_total base_amount requester_id department_id "
        "cost_center_id preferred_vendor_id is_emergency required_by fiscal_year submitted_at decided_at "
        "created_at contract_id"
    ),
    "purchase_request_items": _columns(
        "id purchase_request_id line_no description quantity unit_price uom line_total"
    ),
    "vendors": _columns("id name status risk_level contract_status categories created_at"),
    "departments": _columns("id name code"),
    "cost_centers": _columns("id code name department_id is_active"),
    "budgets": _columns("id cost_center_id fiscal_year amount currency"),
    "purchase_orders": _columns("id number purchase_request_id vendor_id status currency total created_at"),
    "invoices": _columns(
        "id purchase_order_id vendor_id invoice_number invoice_date subtotal tax total currency "
        "match_status created_at"
    ),
    "payments": _columns("id invoice_id amount currency status created_at decided_at"),
    "contracts": _columns("id number vendor_id title status starts_on ends_on value_cap currency"),
}
ALLOWED_FUNCTIONS = frozenset(
    {
        "count", "sum", "avg", "min", "max", "coalesce", "round", "abs", "nullif", "greatest", "least",
        "lower", "upper", "length", "date_trunc", "timestamp_trunc", "extract", "to_char", "cast",
        "current_date", "count_if", "date_part",
    }
)  # fmt: skip
_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command,
    exp.Set, exp.Transaction, exp.Commit, exp.Rollback, exp.Into, exp.Lock, exp.Values, exp.Copy,
    exp.Use, exp.Pragma, exp.Grant, exp.TruncateTable,
)  # fmt: skip


class UnsafeQuery(ValueError):
    """Refused, with a reason code the API returns as details.reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(frozen=True)
class GuardedQuery:
    sql: str
    tables: tuple[str, ...]


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return node.sql_name().lower()


def guard(sql: str, *, max_rows: int = MAX_ROWS) -> GuardedQuery:
    text = sql.strip().rstrip(";").strip()
    if not text:
        raise UnsafeQuery("sql_empty", "No query")
    if "--" in text or "/*" in text:
        raise UnsafeQuery("sql_comment", "Comments are not allowed")
    try:
        statements = sqlglot.parse(text, read="postgres")
    except ParseError as exc:
        raise UnsafeQuery("sql_parse", "The query could not be parsed") from exc
    if len(statements) != 1 or statements[0] is None:
        raise UnsafeQuery("sql_multiple_statements", "Exactly one statement is allowed")
    tree = statements[0]
    if not isinstance(tree, exp.Select | exp.Union | exp.Intersect | exp.Except):
        raise UnsafeQuery("sql_not_select", "Only SELECT queries are allowed")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN):
            raise UnsafeQuery("sql_forbidden", f"{type(node).__name__} is not allowed")
        if isinstance(node, exp.Star):
            raise UnsafeQuery("sql_star", "Name the columns instead of *")
        if isinstance(node, exp.Func) and _function_name(node) not in ALLOWED_FUNCTIONS:
            raise UnsafeQuery("sql_function", f"Function {_function_name(node)} is not allowed")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    aliases: dict[str, str] = {}
    tables: set[str] = set()
    for table in tree.find_all(exp.Table):
        name = table.name.lower()
        if table.args.get("db") or table.args.get("catalog"):
            raise UnsafeQuery("sql_table", f"Qualified table {table.sql()} is not allowed")
        if name in cte_names:
            continue
        if name not in ALLOWED:
            raise UnsafeQuery("sql_table", f"Table {name} is not allowed")
        tables.add(name)
        aliases[(table.alias or name).lower()] = name
    defined = {a.alias.lower() for a in tree.find_all(exp.Alias) if a.alias}
    defined |= {col.lower() for cte in tree.find_all(exp.CTE) for col in cte.this.named_selects}
    allowed_columns = set().union(*(ALLOWED[t] for t in tables)) if tables else set()
    for column in tree.find_all(exp.Column):
        name = column.name.lower()
        qualifier = column.table.lower()
        if qualifier and qualifier in aliases:
            if name not in ALLOWED[aliases[qualifier]]:
                raise UnsafeQuery("sql_column", f"Column {qualifier}.{name} is not allowed")
        elif qualifier and qualifier not in cte_names:
            raise UnsafeQuery("sql_column", f"Unknown table alias {qualifier}")
        elif not qualifier and name not in allowed_columns and name not in defined:
            raise UnsafeQuery("sql_column", f"Column {name} is not allowed")

    limited = tree
    if isinstance(tree, exp.Select):
        current = tree.args.get("limit")
        value = current.expression if current is not None else None
        too_many = not (isinstance(value, exp.Literal) and value.is_int and int(value.this) <= max_rows)
        if too_many:
            limited = tree.limit(max_rows, copy=True)
    else:
        limited = exp.select("*").from_(tree.subquery("q")).limit(max_rows)
    return GuardedQuery(sql=limited.sql(dialect="postgres"), tables=tuple(sorted(tables)))
