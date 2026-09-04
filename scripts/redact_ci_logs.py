"""Remove test credentials and sensitive common patterns before artifact upload."""
import re
import sys
from pathlib import Path


def redact(text, values=()):
    for value in sorted(values, key=len, reverse=True):
        if len(value) >= 8:
            text = text.replace(value, '[REDACTED]')
    text = re.sub(r'(?i)(bearer\s+)[\w.\-]+', r'\1[REDACTED]', text)
    text = re.sub(r'(?i)((?:password|access_token|refresh_token|secret|authorization|pu_session|pu_csrf)\s*[=:]\s*)[^\s,;]+', r'\1[REDACTED]', text)
    text = re.sub(r'https?://[^\s]+', '[URL]', text)
    return re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[EMAIL]', text)


if __name__ == '__main__':
    path = Path('.env.ci')
    values = [line.split('=', 1)[1] for line in path.read_text().splitlines() if '=' in line] if path.exists() else []
    print(redact(sys.stdin.read(), values), end='')
