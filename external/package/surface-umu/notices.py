#!/usr/bin/env python3
"""Copy only known upstream license files from the release Python zipapp."""
from pathlib import Path
import sys
import zipfile

NOTICES = (
    'umu_launcher-1.4.4.dist-info/licenses/LICENSE',
    'python_xlib-0.33.dist-info/LICENSE',
    'six-1.17.0.dist-info/LICENSE',
    'urllib3-2.7.0.dist-info/licenses/LICENSE.txt',
    'truststore-0.10.4.dist-info/licenses/LICENSE',
)

def main():
    destination=Path(sys.argv[2])
    with zipfile.ZipFile(sys.argv[1]) as source:
        for name in NOTICES:
            data=source.read(name)
            if not data or len(data)>100_000:
                raise ValueError('Unexpected upstream notice size')
            target=destination/name
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)

if __name__=='__main__':
    main()
