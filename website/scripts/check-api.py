"""Every claim the site makes about this library's API has to be true of the library.

Two kinds of claim, both unambiguous enough to check mechanically:

    from ekbatan.locks import PostgresKeyedLockProvider      the name has to exist there
    `ActionPlan.add`                                         the attribute has to exist on it

Prose is not checked and cannot be: `plan.add(...)` names a variable, not a type. What is
checked is what a reader would copy and expect to import or call.

    cd website && uv run --project .. python scripts/check-api.py
"""

import importlib
import inspect
import pathlib
import pkgutil
import re
import sys

PAGES = pathlib.Path(__file__).resolve().parent.parent / "src" / "pages"

IMPORT = re.compile(r"^\s*from\s+(ekbatan[\w.]*)\s+import\s+\(?([^()\n]*)\)?", re.M)
MODULE = re.compile(r"^\s*import\s+(ekbatan[\w.]*)", re.M)
DOTTED = re.compile(r"`([A-Z][A-Za-z0-9]*)\.([a-z_][a-z0-9_]*)(\(\))?`")

#: `ActionEvent.avsc` is the Avro schema file, not an attribute of the class it is named after.
FILE_SUFFIXES = {"avsc", "proto", "desc", "py", "sql", "json", "yml", "yaml", "toml", "mdx"}


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

    for page in sorted(PAGES.rglob("*.mdx")):
        text = page.read_text()
        where = page.relative_to(PAGES)

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

        for m in DOTTED.finditer(text):
            cls, attr = m.group(1), m.group(2)
            if cls not in classes or (attr in FILE_SUFFIXES and not m.group(3)):
                continue
            attrs += 1
            if not hasattr(classes[cls], attr):
                bad.append((str(where), line_of(m.start()), f"{cls}.{attr}", "no such attribute"))

    print(
        f"{imports} imported names and {attrs} attribute claims checked against "
        f"{len(classes)} framework classes, {len(bad)} wrong"
    )
    for page, line, what, why in bad:
        print(f"  {page}:{line}  {what}  ({why})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
