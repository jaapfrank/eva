#!/usr/bin/env python3
"""Vergelijk twee agenda-exporten zonder de invoer te wijzigen."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile


def reject_constant(value):
    raise ValueError(f"ongeldige JSON-constante: {value}")


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"dubbele objectsleutel: {key!r}")
        result[key] = value
    return result


def load_export(path):
    try:
        with path.open(encoding="utf-8") as stream:
            items = json.load(stream, parse_constant=reject_constant,
                              object_pairs_hook=unique_keys)
        if not isinstance(items, list):
            raise ValueError("de export moet een JSON-lijst zijn")
        indexed = {}
        for number, item in enumerate(items, 1):
            if not isinstance(item, dict):
                raise ValueError(f"afspraak {number} is geen object")
            for field in ("id", "title", "start"):
                if not isinstance(item.get(field), str) or not item[field].strip():
                    raise ValueError(f"afspraak {number}: {field} moet een nietlege string zijn")
            if item["id"] in indexed:
                raise ValueError(f"dubbel id: {item['id']!r}")
            indexed[item["id"]] = item
        return indexed
    except (ValueError, UnicodeError) as exc:
        raise ValueError(f"{path}: {exc}") from exc


def equal(a, b):
    # Booleans mogen niet gelijk worden gesteld aan JSON-getallen.
    if type(a) is not type(b):
        return False
    if isinstance(a, dict):
        return a.keys() == b.keys() and all(equal(a[k], b[k]) for k in a)
    if isinstance(a, list):
        return len(a) == len(b) and all(equal(x, y) for x, y in zip(a, b))
    return a == b


def compare(before, after):
    common = sorted(before.keys() & after.keys())
    return {
        "added": [after[k] for k in sorted(after.keys() - before.keys())],
        "removed": [before[k] for k in sorted(before.keys() - after.keys())],
        "changed": [{"id": k, "before": before[k], "after": after[k]}
                    for k in common if not equal(before[k], after[k])],
        "unchangedIds": [k for k in common if equal(before[k], after[k])],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", metavar="BEFORE", type=Path)
    parser.add_argument("after", metavar="AFTER", type=Path)
    parser.add_argument("output", metavar="OUTPUT", type=Path)
    args = parser.parse_args()
    temporary = None
    try:
        for source in (args.before, args.after):
            if (args.output.resolve() == source.resolve() or
                    (args.output.exists() and os.path.samefile(args.output, source))):
                raise ValueError("OUTPUT mag niet hetzelfde bestand zijn als een invoerbestand")
        result = compare(load_export(args.before), load_export(args.after))
        text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=args.output.parent,
                                         delete=False) as stream:
            temporary = stream.name
            stream.write(text)
        os.replace(temporary, args.output)
        temporary = None
    except (OSError, ValueError, RecursionError) as exc:
        print(f"Fout: {exc}", file=sys.stderr)
        return 1
    finally:
        if temporary is not None:
            os.unlink(temporary)
    print(f"Toegevoegd: {len(result['added'])}; verwijderd: {len(result['removed'])}; "
          f"gewijzigd: {len(result['changed'])}; ongewijzigd: {len(result['unchangedIds'])}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
