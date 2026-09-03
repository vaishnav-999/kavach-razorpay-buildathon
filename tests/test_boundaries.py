"""Tests 21 to 24 — static analysis over the source tree (§17, §3.1).

These are the tests that make the architecture claims checkable rather than
rhetorical. Nothing here runs the application: they read `app/` with `ast` and
assert properties of the code itself, so a future edit that breaks an import
fence fails the suite rather than the demo.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"

# §11.7, invariant 11 — the SDK root packages that may appear only under
# `app/buyer/llm/`.
LLM_SDK_ROOTS = frozenset(
    {"anthropic", "google", "openai", "cohere", "mistralai", "litellm", "langchain"}
)


def source_files() -> list[Path]:
    return sorted(p for p in APP.rglob("*.py"))


def parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def rel(path: Path) -> str:
    return path.relative_to(APP.parent).as_posix()


def dotted(node: ast.expr) -> str | None:
    """`guard.evaluate` from the AST of a call target, or None if not dotted."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def imported_modules(tree: ast.Module) -> list[tuple[str, str | None]]:
    """Every import as `(module, imported_name)`.

    `import a.b` is `("a.b", None)`; `from a import b` is `("a", "b")`, which
    is what catches `from app.platform import razorpay_client`.
    """
    found: list[tuple[str, str | None]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, None) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.extend((module, alias.name) for alias in node.names)
    return found


# -- 21 --------------------------------------------------------------------


def test_21_guard_evaluate_has_exactly_one_call_site():
    """§9.4. One door, and the AST says so.

    T-20: a second code path reaching Razorpay without the Guard would need a
    second `evaluate()` call, and this test is what refuses to let one appear.
    """
    call_sites: list[str] = []

    for path in source_files():
        tree = parse(path)
        # Every local name that refers to the guard module, however it was
        # imported, plus `evaluate` itself if it was imported bare. Renaming
        # the import is not a way past this test.
        module_aliases: set[str] = set()
        bare_aliases: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "app.platform.guard":
                        module_aliases.add(alias.asname or alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    if module == "app.platform" and alias.name == "guard":
                        module_aliases.add(alias.asname or alias.name)
                    elif module == "app.platform.guard" and alias.name == "evaluate":
                        bare_aliases.add(alias.asname or alias.name)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = dotted(node.func)
            if name is None:
                continue
            head, _, attr = name.rpartition(".")
            if attr == "evaluate" and (
                head in module_aliases or head.split(".")[-1] == "guard"
            ):
                call_sites.append(f"{rel(path)}:{node.lineno}")
            elif name in bare_aliases:
                call_sites.append(f"{rel(path)}:{node.lineno}")

    assert len(call_sites) == 1, f"guard.evaluate call sites: {call_sites}"
    assert call_sites[0].startswith("app/platform/payments.py:")


# -- 22 --------------------------------------------------------------------


def test_22_razorpay_and_llm_sdks_stay_behind_their_fences():
    """Invariants 1 and 11. T-15: the agent cannot call Razorpay directly."""
    razorpay_offenders: list[str] = []
    sdk_offenders: list[str] = []

    for path in source_files():
        relative = rel(path)
        tree = parse(path)
        inside_platform = relative.startswith("app/platform/")
        inside_llm = relative.startswith("app/buyer/llm/")

        for module, name in imported_modules(tree):
            full = f"{module}.{name}" if name else module

            if not inside_platform and (
                full.startswith("app.platform.razorpay_client")
                or module.startswith("app.platform.razorpay_client")
            ):
                razorpay_offenders.append(f"{relative} -> {full}")

            if not inside_llm and module.split(".")[0] in LLM_SDK_ROOTS:
                sdk_offenders.append(f"{relative} -> {full}")

    assert razorpay_offenders == [], (
        "app/platform/razorpay_client.py may only be imported from within "
        f"app/platform/: {razorpay_offenders}"
    )
    assert sdk_offenders == [], (
        "No LLM SDK may be imported outside app/buyer/llm/: " f"{sdk_offenders}"
    )


# -- 23 --------------------------------------------------------------------


def test_23_the_buyer_plane_never_imports_the_merchant_plane():
    """Invariant 3, T-14. The buyer reaches the merchant only over HTTP.

    An in-process import would let a compromised buyer plane call
    `create_quote()` directly and sign a price nobody quoted.
    """
    offenders: list[str] = []

    for path in sorted((APP / "buyer").rglob("*.py")):
        tree = parse(path)
        for module, name in imported_modules(tree):
            full = f"{module}.{name}" if name else module
            if (
                module == "app.merchant"
                or module.startswith("app.merchant.")
                or full == "app.merchant"
                or full.startswith("app.merchant.")
            ):
                offenders.append(f"{rel(path)} -> {full}")

    assert offenders == [], (
        "No module under app/buyer/ may import from app.merchant. Use "
        f"app/buyer/client.py over HTTP instead: {offenders}"
    )


# -- 24 --------------------------------------------------------------------


def test_24_audit_is_append_only_and_money_is_integer_paise():
    """Invariants 6 and 8, T-16."""
    # (a) No UPDATE or DELETE statement targets audit_events.
    sql_offenders: list[str] = []
    raw_sql = re.compile(r"\b(update|delete)\b[^\n]{0,80}\baudit_events\b", re.I)

    for path in source_files():
        text = path.read_text(encoding="utf-8")
        tree = parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                name = dotted(node.func)
                if name in ("update", "delete", "sqlalchemy.update", "sqlalchemy.delete"):
                    for arg in node.args:
                        if isinstance(arg, ast.Name) and arg.id == "AuditEvent":
                            sql_offenders.append(f"{rel(path)}:{node.lineno} {name}()")
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if raw_sql.search(node.value):
                    sql_offenders.append(f"{rel(path)}:{node.lineno} raw SQL")
        # A comment or a docstring naming the table is fine; a statement is not.
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.split("#", 1)[0]
            if raw_sql.search(stripped):
                sql_offenders.append(f"{rel(path)}:{lineno}")

    assert sql_offenders == [], f"audit_events is append-only: {sql_offenders}"

    # The §15 reset clears the transaction tables. It must not clear this one.
    from app.models import AuditEvent
    from app.platform import demo_reset

    assert AuditEvent not in {model for _, model in demo_reset._CLEARED}
    assert "audit_events" not in {name for name, _ in demo_reset._CLEARED}

    # (b) Every money column in models.py is BigInteger.
    models_tree = parse(APP / "models.py")
    checked = 0
    for node in ast.walk(models_tree):
        if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
            continue
        if "paise" not in node.target.id:
            continue
        assert isinstance(node.value, ast.Call), node.target.id
        assert dotted(node.value.func) == "mapped_column", node.target.id
        first = node.value.args[0]
        assert isinstance(first, ast.Name) and first.id == "BigInteger", (
            f"{node.target.id} is {ast.dump(first)}, not BigInteger"
        )
        checked += 1
    assert checked >= 7, f"only {checked} money columns found in models.py"

    # (c) No float and no Decimal in a money path.
    money_path = [
        APP / "models.py",
        APP / "crypto.py",
        APP / "platform" / "guard.py",
        APP / "platform" / "payments.py",
        APP / "platform" / "mandate.py",
        APP / "platform" / "webhook_dispatch.py",
        APP / "merchant" / "service.py",
        APP / "merchant" / "validator.py",
    ]
    float_offenders: list[str] = []
    for path in money_path:
        for node in ast.walk(parse(path)):
            if isinstance(node, ast.Constant) and isinstance(node.value, float):
                float_offenders.append(f"{rel(path)}:{node.lineno} {node.value!r}")
            if isinstance(node, ast.Call) and dotted(node.func) in (
                "float",
                "Decimal",
                "decimal.Decimal",
            ):
                float_offenders.append(f"{rel(path)}:{node.lineno} float/Decimal call")
    assert float_offenders == [], f"money is integer paise: {float_offenders}"

    decimal_importers = [
        rel(path)
        for path in source_files()
        for module, _ in imported_modules(parse(path))
        if module.split(".")[0] == "decimal"
    ]
    assert decimal_importers == [], f"decimal is imported by: {decimal_importers}"
