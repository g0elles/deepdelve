"""Unit count for labelling: the diagnostic's rule (eval/comprehension/units.py) with one fix. A sentence end may be followed by citation markers
("... pigs.[1] Next"), which the old regex missed, so a report that cites after the full stop was counted as one unit per line. Same rule for every arm."""
import re
def units(t: str) -> int:
    t = re.split(r"^#+\s*(?:\d+\.\s*)?Sources\s*$", t, flags=re.M | re.I)[0]
    n = 0
    for line in t.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or set(s) <= set("-| :") or s.startswith("**Title") or re.fullmatch(r"\*\*.*\*\*", s):
            continue
        if s.startswith("|"):
            n += "---" not in s
        else:
            n += max(1, len(re.findall(r"[.!?](?:\[[\d,\s]+\])*(?:\s|$)", s)))
    return n
