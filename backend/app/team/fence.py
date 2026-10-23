"""Text one agent or person wrote that the harness shows to another agent.

Untrusted text always travels inside a JSON string. This makes it harder for
that text to pass as harness wording inside the string: it is normalised,
invisible characters go, lines that imitate a harness marker are defused, and
credential-looking values are redacted."""
import re
import unicodedata

from app.services.agent_memory import redact_secrets

# Words the harness uses to speak to an agent. Compared after look-alike folding.
MARKERS = ('system', 'assistant', 'user', 'human', 'developer', 'tool', 'harness', 'approval', 'approved',
           'instruction', 'instructions', 'note', 'important', 'override', 'permission', 'team', 'room')
_INVISIBLE = {'Cf', 'Cc', 'Zl', 'Zp'}
_BRACKETS = '[]<>{}()|#*_`~:=-'
_LEAD = re.compile(r'^[\s' + re.escape(_BRACKETS) + r'>]*([^\W_]+)')


def _clean(value) -> str:
    value = unicodedata.normalize('NFKC', value if isinstance(value, str) else str(value or ''))
    return ''.join(c for c in value if c in '\n\t' or unicodedata.category(c) not in _INVISIBLE)


def _fold(word: str) -> str:
    # Mixed-script look-alikes (Cyrillic a, Greek o) are not caught by NFKC.
    table = str.maketrans({'а': 'a', 'е': 'e', 'о': 'o', 'р': 'p', 'с': 'c', 'х': 'x', 'у': 'y', 'і': 'i', 'ѕ': 's',
                           'ο': 'o', 'α': 'a', 'ε': 'e', 'ι': 'i', 'υ': 'u', '0': 'o', '1': 'i', '3': 'e', '5': 's', '$': 's'})
    return word.lower().translate(table)


def text(value, limit: int = 12000) -> str:
    """Multi-line text: marker-like line starts are replaced with '(', secrets redacted."""
    cleaned, _ = redact_secrets(_clean(value)[:limit])
    lines = []
    for line in cleaned.split('\n'):
        match = _LEAD.match(line)
        if match and _fold(match.group(1)) in MARKERS:
            line = '(' + line.lstrip(' \t' + _BRACKETS + '>')
        lines.append(line)
    return '\n'.join(lines)


def line(value, limit: int = 120) -> str:
    """One-line field (name, title, label): no newlines or brackets, bounded."""
    cleaned = re.sub(r'[\n\r\t]+', ' ', _clean(value))
    cleaned = re.sub(r'[\[\]<>{}]', '', cleaned).strip()
    cleaned, _ = redact_secrets(cleaned)
    return cleaned[:limit]
