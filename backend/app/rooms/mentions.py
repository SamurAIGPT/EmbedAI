"""Mention matching shared by room dispatch and tools.

Code and quotes cannot address agents. Longest names win; punctuation may end
names, while letters, combining marks, digits and underscores continue them.
"""
import re
import unicodedata


def continuation(char):
    return char == '_' or unicodedata.category(char)[0] in ('L', 'M', 'N')


def parse(text, members):
    masked = re.sub(r'```[\s\S]*?(?:```|$)|~~~[\s\S]*?(?:~~~|$)|`[^`\n]*`|(?m:^\s*>[^\n]*)', lambda m: ' ' * len(m[0]), text)
    candidates = sorted([(str(name), identifier) for identifier, name in members] + [('everyone', None)], key=lambda p: -len(p[0]))
    found = []
    for match in re.finditer('@', masked):
        start = match.start()
        if start and not (masked[start-1].isspace() or masked[start-1] in '([{"\'_*~'):
            continue
        for name, identifier in candidates:
            end = start + 1 + len(name)
            if masked[start+1:end].casefold() != name.casefold():
                continue
            if end < len(masked) and continuation(masked[end]):
                continue
            if identifier not in found:
                found.append(identifier)
            break
    return found
