#!/usr/bin/env python3
"""Stream a lean4export closure and report exact metrics for one theorem graph."""

from __future__ import annotations

import argparse
import json
from array import array
from pathlib import Path


TARGET = "KoalaBear.sexticPoly_irreducible"
WATCHED = (
    "Eq.mpr",
    "Eq.ndrec",
    "Eq.rec",
    "congrArg",
    "Fintype.card",
    "Fintype.elems",
    "ZMod.fintype",
    "ZMod.card",
    "KoalaBear.fieldSize",
)
NONE = 0xFFFFFFFF
MAX_U64 = 0xFFFFFFFFFFFFFFFF


def sat_add(*values: int) -> int:
    total = sum(values)
    return min(total, MAX_U64)


def add_name(record: dict, names: dict[int, str]) -> None:
    name_id = record["in"]
    if "str" in record:
        node = record["str"]
        segment = node["str"]
    else:
        node = record["num"]
        segment = str(node["i"])
    prefix = names[node["pre"]]
    names[name_id] = f"{prefix}.{segment}" if prefix else segment


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("export", type=Path)
    args = parser.parse_args()

    names = {0: ""}
    depth = array("I")
    tree_size = array("Q")
    child1 = array("I")
    child2 = array("I")
    child3 = array("I")
    flags = array("H")
    kind_codes = array("B")
    head_names = array("I")
    watched_counts = [array("Q") for _ in WATCHED]
    roots = None
    line_count = 0

    with args.export.open(encoding="utf-8") as stream:
        for line in stream:
            line_count += 1
            if line.startswith('{"in":'):
                add_name(json.loads(line), names)
                continue
            if '"ie"' in line:
                record = json.loads(line)
                expr_id = record["ie"]
                if expr_id != len(depth):
                    raise RuntimeError(
                        f"non-sequential expression id {expr_id}, expected {len(depth)}"
                    )
                kind = next(key for key in record if key != "ie")
                node = record[kind]
                children: list[int]
                node_flags = 0
                if kind == "app":
                    children = [node["fn"], node["arg"]]
                elif kind in ("lam", "forallE"):
                    children = [node["type"], node["body"]]
                elif kind == "letE":
                    children = [node["type"], node["value"], node["body"]]
                elif kind == "proj":
                    children = [node["struct"]]
                elif kind == "mdata":
                    children = [node["expr"]]
                else:
                    children = []
                if kind == "const":
                    const_name = names[node["name"]]
                    for index, watched in enumerate(WATCHED):
                        if const_name == watched:
                            node_flags |= 1 << index

                kind_codes.append(1 if kind == "app" else 0)
                if kind == "const":
                    head_names.append(node["name"])
                elif kind == "app":
                    head_names.append(head_names[node["fn"]])
                else:
                    head_names.append(NONE)

                child_depths = [depth[child] for child in children]
                depth.append(1 + max(child_depths, default=0))
                tree_size.append(
                    sat_add(1, *(tree_size[child] for child in children))
                )
                padded = children + [NONE] * (3 - len(children))
                child1.append(padded[0])
                child2.append(padded[1])
                child3.append(padded[2])
                flags.append(node_flags)
                for index, counts in enumerate(watched_counts):
                    counts.append(
                        sat_add(
                            1 if node_flags & (1 << index) else 0,
                            *(counts[child] for child in children),
                        )
                    )
                continue
            if line.startswith('{"thm":'):
                record = json.loads(line)["thm"]
                if names[record["name"]] == TARGET:
                    roots = (record["type"], record["value"])

    if roots is None:
        raise RuntimeError(f"missing theorem {TARGET}")

    print(f"file={args.export}")
    print(f"lines={line_count} expressions={len(depth)}")
    for label, root in zip(("type", "value"), roots):
        print(
            f"{label}_root={root} depth={depth[root]} "
            f"tree_size_capped={tree_size[root]}"
        )
        for watched, counts in zip(WATCHED, watched_counts):
            print(f"{label}_tree_occurrences[{watched}]={counts[root]}")

        seen = bytearray(len(depth))
        stack = [root]
        unique = 0
        unique_watched = [0] * len(WATCHED)
        while stack:
            expr_id = stack.pop()
            if seen[expr_id]:
                continue
            seen[expr_id] = 1
            unique += 1
            node_flags = flags[expr_id]
            for index in range(len(WATCHED)):
                if node_flags & (1 << index):
                    unique_watched[index] += 1
            for child in (child1[expr_id], child2[expr_id], child3[expr_id]):
                if child != NONE:
                    stack.append(child)
        print(f"{label}_unique_nodes={unique}")
        for watched, count in zip(WATCHED, unique_watched):
            print(f"{label}_unique_constants[{watched}]={count}")

        if label == "value":
            eq_mpr_name_id = next(
                name_id for name_id, name in names.items() if name == "Eq.mpr"
            )
            partial_spines = bytearray(len(depth))
            for expr_id, is_seen in enumerate(seen):
                if not is_seen or kind_codes[expr_id] != 1:
                    continue
                fn = child1[expr_id]
                if head_names[expr_id] == eq_mpr_name_id:
                    partial_spines[fn] = 1
            transports = [
                expr_id
                for expr_id, is_seen in enumerate(seen)
                if is_seen
                and kind_codes[expr_id] == 1
                and head_names[expr_id] == eq_mpr_name_id
                and not partial_spines[expr_id]
            ]
            print(f"value_maximal_Eq.mpr_applications={len(transports)}")
            for transport_index, transport in enumerate(transports, 1):
                args_reversed = []
                cursor = transport
                while kind_codes[cursor] == 1:
                    args_reversed.append(child2[cursor])
                    cursor = child1[cursor]
                arguments = list(reversed(args_reversed))
                print(
                    f"transport[{transport_index}] root={transport} "
                    f"arguments={len(arguments)} depth={depth[transport]} "
                    f"tree_size={tree_size[transport]}"
                )
                for arg_index, argument in enumerate(arguments):
                    head_id = head_names[argument]
                    head = names[head_id] if head_id != NONE else "<non-application>"
                    watched_summary = ",".join(
                        f"{watched}={counts[argument]}"
                        for watched, counts in zip(WATCHED, watched_counts)
                        if counts[argument]
                    )
                    print(
                        f"transport[{transport_index}].arg[{arg_index}]="
                        f"root:{argument},head:{head},depth:{depth[argument]},"
                        f"tree:{tree_size[argument]},watched:{watched_summary or '-'}"
                    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
