#!/usr/bin/env python3
"""Build tests/fixtures/match_calibration.json from a resolver cache.

Positives are verified matches. Negatives are the most similar store names of
OTHER verified products plus candidates the resolver rejected for assets it then
resolved correctly. The set guards against regressions; it is not an independent
accuracy measurement.

Usage: python3 scripts/build-match-calibration.py [cache.json] [out.json]
"""

import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "bin"))

import resolve_store as rs  # noqa: E402

NEIGHBOURS = 3


def store_name(rec):
    return rs.unescape((rec.get("legacy") or {}).get("name")
                       or (rec.get("detail") or {}).get("name") or "")


def publisher(rec):
    return ((rec.get("legacy") or {}).get("publisher")
            or (rec.get("detail") or {}).get("author") or None)


def build(cache):
    records = cache.get("resolved", {})
    positives, negatives, unverified = [], [], []
    for rec in records.values():
        if rec.get("status") == "resolved" and rec.get("verified_via") != "matches.json":
            name = store_name(rec)
            if name and rec.get("title"):
                positives.append({"local": rec["title"], "store": name,
                                  "publisher": publisher(rec), "id": str(rec["id"])})
            for cand in rec.get("candidates_rejected") or []:
                if cand.get("store_name") and str(cand.get("id")) != str(rec.get("id")):
                    negatives.append({"local": rec["title"], "store": cand["store_name"],
                                      "source": "rejected-candidate"})
        elif rec.get("status") == "unverified":
            for cand in rec.get("candidates_rejected") or []:
                if cand.get("store_name"):
                    unverified.append({"local": rec.get("title") or "",
                                       "store": cand["store_name"], "id": str(cand.get("id"))})

    for pos in positives:
        own = rs.normalize(pos["store"])
        ranked = sorted(
            (other for other in positives
             if other["id"] != pos["id"] and rs.normalize(other["store"]) != own),
            key=lambda other: rs.similarity(pos["local"], other["store"]), reverse=True)
        for other in ranked[:NEIGHBOURS]:
            negatives.append({"local": pos["local"], "store": other["store"],
                              "source": "confusable"})

    def key(row):
        return (row["local"], row["store"])

    def unique(rows):
        seen, out = set(), []
        for row in sorted(rows, key=key):
            if key(row) not in seen:
                seen.add(key(row))
                out.append(row)
        return out

    return {"positives": unique(positives), "negatives": unique(negatives),
            "unverified": unique(unverified)}


def main(argv):
    src = argv[1] if len(argv) > 1 else os.path.join(REPO, "state", "cache.json")
    out = argv[2] if len(argv) > 2 else os.path.join(REPO, "tests", "fixtures", "match_calibration.json")
    with open(src, encoding="utf-8") as fh:
        data = build(json.load(fh))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
    print(f"positives={len(data['positives'])} negatives={len(data['negatives'])} "
          f"unverified={len(data['unverified'])} -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
