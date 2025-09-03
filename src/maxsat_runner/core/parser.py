import re

O_LINE = re.compile(r'^\s*o\s+([+-]?\d+)\s*$', re.ASCII)
S_LINE = re.compile(r'^\s*s\b.*OPTIMUM FOUND\b', re.IGNORECASE | re.ASCII)

def parse_o(line: str):
    m = O_LINE.match(line)
    return int(m.group(1)) if m else None

def normalize_spaces(s: str) -> str:
    # supprime les espaces multiples
    return " ".join(s.split())

def is_optimum(line: str) -> bool:
    norm = normalize_spaces(line.strip().upper())
    return norm == "S OPTIMUM FOUND"
