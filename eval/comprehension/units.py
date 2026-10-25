"""Count claim-bearing units in a report: each table row, bullet or sentence outside headings, rules and the source list (same rule for every report)."""
import re, sys
def units(t: str) -> int:
    t = re.split(r"^#+\s*(?:\d+\.\s*)?Sources\s*$", t, flags=re.M | re.I)[0]
    n = 0
    for line in t.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or set(s) <= set("-| :") or s.startswith("**Title") or re.fullmatch(r"\*\*.*\*\*", s):
            continue
        if s.startswith("|"):
            if "---" in s: continue
            n += 1
        else:
            n += max(1, len(re.findall(r"[.!?](?:\s|$)", s)))
    return n
if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p, units(open(p).read()))
