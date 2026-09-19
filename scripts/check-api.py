"""Every claim the docs and the website make about this API has to be true of the API.

Three kinds of claim, all unambiguous enough to check mechanically:

    from ekbatan.locks import PostgresKeyedLockProvider      the name has to exist there
    `ActionPlan.add`                                         the attribute has to exist on it
    ```python ...```                                         it has to parse as Python

Prose is not checked and cannot be: `plan.add(...)` names a variable, not a type. What is
checked is what a reader would copy and expect to import, call, or paste.

Both trees, because both are read and they drift apart. The snippet check is the one that has
caught the most: a leading-dot method chain with no enclosing parentheses reads fine and is a
`SyntaxError` the moment anyone pastes it.

    uv run python scripts/check-api.py
"""

import ast
import importlib
import inspect
import pathlib
import pkgutil
import re
import sys
import textwrap

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: Both trees, with the extension each uses.
TREES = ((ROOT / "docs", "*.md"), (ROOT / "website" / "src" / "pages", "*.mdx"))

IMPORT = re.compile(r"^\s*from\s+(ekbatan[\w.]*)\s+import\s+\(?([^()\n]*)\)?", re.M)
MODULE = re.compile(r"^\s*import\s+(ekbatan[\w.]*)", re.M)
DOTTED = re.compile(r"`([A-Z][A-Za-z0-9]*)\.([a-z_][a-z0-9_]*)(\(\))?`")
FENCE = re.compile(r"```python\n(.*?)```", re.S)

#: `ActionEvent.avsc` is the Avro schema file, not an attribute of the class it is named after,
#: and `JobRegistry.java` is the Java source this one was ported from.
FILE_SUFFIXES = {
    "avsc",
    "proto",
    "desc",
    "py",
    "java",
    "kt",
    "sql",
    "json",
    "yml",
    "yaml",
    "toml",
    "mdx",
    "md",
}

#: Ledgers record history, and history contains names that were renamed away. `RetryConfig.retries
#: -> max_retries` is a true sentence about a name that no longer exists, which is the point of it.
HISTORY = {"JAVA-PARITY-LEDGER.md", "BEYOND-CODE-LEDGER.md", "DECISIONS-TO-REVIEW.md"}


def framework_classes() -> dict[str, type]:
    import ekbatan

    found: dict[str, type] = {}
    for mod in pkgutil.walk_packages(ekbatan.__path__, "ekbatan."):
        try:
            module = importlib.import_module(mod.name)
        except Exception:  # an optional extra that is not installed
            continue
        for name, obj in vars(module).items():
            if (
                not name.startswith("_")
                and inspect.isclass(obj)
                and getattr(obj, "__module__", "").startswith("ekbatan")
            ):
                found.setdefault(name, obj)
    return found


def main() -> int:
    classes = framework_classes()
    bad: list[tuple[str, int, str, str]] = []
    imports = attrs = 0

    snippets = 0
    pages = [(f, f.relative_to(ROOT)) for tree, glob in TREES for f in sorted(tree.rglob(glob))]
    for page, where in pages:
        text = page.read_text()

        def line_of(pos: int, source: str = text) -> int:
            return source[:pos].count("\n") + 1

        for m in IMPORT.finditer(text):
            module_name, names = m.group(1), m.group(2)
            try:
                module = importlib.import_module(module_name)
            except Exception as error:
                bad.append((str(where), line_of(m.start()), module_name, type(error).__name__))
                continue
            for name in (n.strip().split(" as ")[0] for n in names.split(",")):
                if not name or name == "*":
                    continue
                imports += 1
                if not hasattr(module, name):
                    bad.append(
                        (str(where), line_of(m.start()), f"{module_name}.{name}", "not exported")
                    )

        for m in MODULE.finditer(text):
            imports += 1
            try:
                importlib.import_module(m.group(1))
            except Exception as error:
                bad.append((str(where), line_of(m.start()), m.group(1), type(error).__name__))

        # A fragment may legitimately start mid-class or mid-function, so it is retried
        # wrapped in one. What is refused is a snippet nothing can parse at all.
        for m in FENCE.finditer(text):
            snippets += 1
            src = m.group(1)
            error: SyntaxError | None = None
            for attempt in (
                src,
                "class _X:\n" + textwrap.indent(src, "    "),
                "def _f():\n" + textwrap.indent(src, "    "),
            ):
                try:
                    ast.parse(attempt)
                    error = None
                    break
                except SyntaxError as raised:
                    error = raised
            if error is not None:
                bad.append(
                    (
                        str(where),
                        line_of(m.start()) + 1,
                        str(error).split("(")[0].strip(),
                        "does not parse",
                    )
                )

        for m in DOTTED.finditer(text):
            cls, attr = m.group(1), m.group(2)
            if cls not in classes or (attr in FILE_SUFFIXES and not m.group(3)):
                continue
            if page.name in HISTORY:
                continue
            attrs += 1
            # A dataclass field with no default is an annotation, not a class attribute, so
            # `hasattr` alone reported `Persistable.id` missing from the class that declares it.
            declared = any(
                attr in vars(base).get("__annotations__", {}) for base in classes[cls].__mro__
            )
            if not hasattr(classes[cls], attr) and not declared:
                bad.append((str(where), line_of(m.start()), f"{cls}.{attr}", "no such attribute"))

    print(
        f"{len(pages)} pages: {imports} imported names, {attrs} attribute claims and "
        f"{snippets} snippets checked against {len(classes)} framework classes, {len(bad)} wrong"
    )
    for page, line, what, why in bad:
        print(f"  {page}:{line}  {what}  ({why})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
