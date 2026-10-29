#!/usr/bin/env python3
"""Fetch only hash-pinned public release wheels; never install into the host."""

from concurrent.futures import ThreadPoolExecutor
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import urllib.parse
import urllib.request


def fetch(row, destination):
    name = row['filename']
    if Path(name).name != name or not name.endswith('.whl'):
        raise ValueError('Invalid wheel name')
    target = destination / name
    def matches(path):
        with path.open('rb') as stream:
            return hashlib.file_digest(stream, 'sha256').hexdigest() == row['sha256']
    if target.is_file() and matches(target):
        return
    parsed = urllib.parse.urlsplit(row['url'])
    if parsed.scheme != 'https' or parsed.hostname != 'files.pythonhosted.org':
        raise ValueError('Expected a public PyPI wheel')
    with tempfile.NamedTemporaryFile(dir=destination, delete=False) as output:
        temporary = Path(output.name)
        try:
            with urllib.request.urlopen(row['url'], timeout=30) as response:
                size = 0
                while chunk := response.read(1024 * 1024):
                    size += len(chunk)
                    if size > 256 * 1024 * 1024:
                        raise ValueError('Oversized wheel')
                    output.write(chunk)
            output.flush()
            if not matches(temporary):
                raise ValueError('Wheel checksum mismatch')
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    args.destination.mkdir(parents=True, exist_ok=True)
    records = json.loads(Path(__file__).with_name('runtime-wheels.json').read_text())
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda row: fetch(row, args.destination), records))
    print('Verified release wheels:', len(records))
