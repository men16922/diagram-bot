"""Exercise the installed CLI and compare all synthetic report artifacts offline."""
import csv
import json
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree


def main():
    with TemporaryDirectory(prefix='diagram-bot-smoke-') as directory:
        base = Path(directory)
        generated, rendered = base / 'generated', base / 'rendered'
        for arguments in (
            ['demo', '--output', str(generated)],
            ['render', str(generated / 'snapshot.json'), '--output', str(rendered)],
        ):
            subprocess.run([sys.executable, '-m', 'diagram_bot.cli', *arguments], check=True)
        for name in ('index.html', 'snapshot.json', 'inventory.csv', 'review.md', 'topology.drawio'):
            first, second = (generated / name).read_bytes(), (rendered / name).read_bytes()
            if not first or first != second:
                raise RuntimeError(f'Empty or inconsistent report artifact: {name}')
        snapshot = json.loads((generated / 'snapshot.json').read_text(encoding='utf-8'))
        if snapshot['metadata'].get('demo') is not True:
            raise RuntimeError('Smoke output must contain synthetic data only')
        with (generated / 'inventory.csv').open(encoding='utf-8-sig', newline='') as stream:
            inventory = list(csv.DictReader(stream))
        if len(inventory) != len(snapshot['graph']['nodes']):
            raise RuntimeError('Inventory and graph disagree')
        document = ElementTree.parse(generated / 'topology.drawio')
        if document.find('diagram/mxGraphModel/root') is None:
            raise RuntimeError('Editable draw.io graph is missing')
    print('Offline CLI smoke: 5 report artifacts match; inventory and native draw.io validated.')


if __name__ == '__main__':
    main()
