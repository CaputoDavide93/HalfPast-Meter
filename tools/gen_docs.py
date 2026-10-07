#!/usr/bin/env python3
"""Regenerate the entity and statistics tables from the integration.

The sources of truth are `SUPPORTED_CLASSIFIERS` in const.py (which streams
exist, which are costs, which fuel prices them) and the entity names in
translations/en.json. sensor.py builds the same set from the same two places,
and tests/test_init.py pins the resulting entity ids, so a table that drifts
from the code fails here first.

  python3 tools/gen_docs.py          # rewrite the AUTOGEN blocks
  python3 tools/gen_docs.py --check  # exit 1 if they are stale (for CI)
"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "bright_meter"
README = ROOT / "README.md"
# Every file carrying AUTOGEN blocks, and the blocks each must have.
TARGETS = {
    README: ("entities", "statistics"),
    ROOT / "docs" / "entities.md": ("entities", "statistics"),
}


def _const():
    """const.py on its own: it imports nothing from Home Assistant."""
    spec = importlib.util.spec_from_file_location(
        "bright_const", COMPONENT / "const.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def entities() -> str:
    const = _const()
    names = json.loads((COMPONENT / "translations" / "en.json").read_text())
    names = {k: v["name"] for k, v in names["entity"]["sensor"].items()}

    rows = []
    for classifier, info in const.SUPPORTED_CLASSIFIERS.items():
        slug = classifier.replace(".", "_")
        unit = "GBP" if info.is_cost else "kWh"
        rows.append((names[f"{slug}_last_day"], unit,
                     "The last whole local day; `day` attribute says which"))
        if info.is_cost:
            continue
        rows.append((names[f"{slug}_data_until"], "timestamp",
                     "End of the newest whole hour Glowmarkt holds (diagnostic)"))
        if info.fuel:
            rows.append((names[f"{info.fuel}_unit_rate"], "GBP/kWh",
                         "Unit rate the meter holds"))
            rows.append((names[f"{info.fuel}_standing_charge"], "GBP/d",
                         "Standing charge the meter holds"))
    out = ["| Sensor | Unit | What it is |", "|---|---|---|"]
    out += [f"| {n} | {u} | {w} |" for n, u, w in rows]
    return "\n".join(out)


def statistics() -> str:
    const = _const()
    out = ["| Statistic | Unit | Stream |", "|---|---|---|"]
    for classifier, info in const.SUPPORTED_CLASSIFIERS.items():
        slug = classifier.replace(".", "_")
        unit = "GBP" if info.is_cost else "kWh"
        out.append(f"| `{const.DOMAIN}:{slug}` | {unit} | `{classifier}` |")
    return "\n".join(out)


BLOCKS = {"entities": entities, "statistics": statistics}


def render(text: str, names: tuple[str, ...], path: pathlib.Path) -> str:
    for name in names:
        fn = BLOCKS[name]
        pattern = re.compile(
            rf"(<!-- AUTOGEN:{name} -->\n)(?:.*?\n)?(<!-- /AUTOGEN:{name} -->)", re.S
        )
        if not pattern.search(text):
            sys.exit(f"{path.name} has no AUTOGEN:{name} block")
        text = pattern.sub(
            lambda m, fn=fn: m.group(1) + fn() + "\n" + m.group(2), text
        )
    return text


def main() -> None:
    stale = []
    for path, names in TARGETS.items():
        current = path.read_text(encoding="utf-8")
        fresh = render(current, names, path)
        if fresh == current:
            continue
        if "--check" in sys.argv[1:]:
            stale.append(str(path.relative_to(ROOT)))
        else:
            path.write_text(fresh, encoding="utf-8")
            print(f"{path.relative_to(ROOT)} AUTOGEN blocks written")
    if stale:
        print("stale AUTOGEN blocks (run python3 tools/gen_docs.py):\n  "
              + "\n  ".join(stale))
        sys.exit(1)
    if "--check" in sys.argv[1:]:
        print("AUTOGEN blocks up to date")


if __name__ == "__main__":
    main()
