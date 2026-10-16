"""CLI for reproducible local catalog reports. Network access is explicit."""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from catalog import CatalogError, MAX_BYTES, compare, parse

ROOT = Path(__file__).resolve().parent


def fetch_html(url='https://supply.openai.com/'):
    if url != 'https://supply.openai.com/':
        raise CatalogError('Only the configured public storefront URL is supported.')
    request = Request(url, headers={'User-Agent': 'SupplyObservatory/2.0 (read-only snapshot)', 'Accept': 'text/html'})
    try:
        with urlopen(request, timeout=20) as response:
            if urlparse(response.url).hostname != 'supply.openai.com':
                raise CatalogError('The storefront redirected away. No catalog snapshot was created.')
            data = response.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise CatalogError('Storefront response exceeds 20 MiB.')
            return data.decode('utf-8')
    except (URLError, TimeoutError, UnicodeDecodeError) as exc:
        raise CatalogError('Could not read the storefront. Existing snapshots are unchanged.') from exc


def read(path):
    target = Path(path)
    if target.stat().st_size > MAX_BYTES:
        raise CatalogError('Input exceeds 20 MiB.')
    return target.read_text(encoding='utf-8-sig')


def demo():
    def product(number, title, available, amount=None, currency='USD'):
        data = {'id': f'gid://shopify/Product/{number}', 'handle': title.lower().replace(' ', '-'), 'title': title}
        if available is not None:
            data['availableForSale'] = available
        if amount is not None:
            data['priceRange'] = {'minVariantPrice': {'amount': amount, 'currencyCode': currency}}
        return data
    previous = [product(1, 'Field Notebook', False, '18.00'), product(2, 'Canvas Tote', True, '24.00'), product(3, 'Studio Cap', True, '30.00'), product(4, 'Archive Print', None), product(5, 'Desk Object', False, '45.00')]
    current = [product(1, 'Field Notebook', True, '18.00'), product(2, 'Canvas Tote', True, '28.00'), product(3, 'Studio Cap', False, '30.00'), product(4, 'Archive Print', None), product(6, 'Workshop Mug', True, '22.00'), product(7, 'Pocket Journal', True, '12.00', 'EUR')]
    return (parse(json.dumps(current), source='fictional-demo', captured_at='2026-10-16T12:00:00Z'), parse(json.dumps(previous), source='fictional-demo', captured_at='2026-10-15T12:00:00Z'))


def render(current, previous=None, *, fictional=False):
    payload = {'current': current, 'previous': previous, 'changes': compare(current, previous), 'fictional': fictional}
    encoded = json.dumps(payload, ensure_ascii=False).replace('&', '\\u0026').replace('<', '\\u003c').replace('>', '\\u003e').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    template = (ROOT / 'web/template.html').read_text(encoding='utf-8')
    return template.replace('__STYLES__', (ROOT / 'web/style.css').read_text(encoding='utf-8')).replace('__APP__', (ROOT / 'web/app.js').read_text(encoding='utf-8')).replace('__PAYLOAD__', encoded)


def write_new(path, content, force=False):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('w' if force else 'x', encoding='utf-8', newline='\n') as output:
        output.write(content)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Build a local, dated catalog report from saved product data.')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--demo', action='store_true', help='Use only fictional catalog fixtures')
    group.add_argument('--input', type=Path, help='Saved HTML, product JSON, or normalized snapshot')
    group.add_argument('--fetch', action='store_true', help='Read the configured public storefront once')
    parser.add_argument('--previous', type=Path, help='Earlier normalized snapshot or saved catalog')
    parser.add_argument('--output', type=Path, default=Path('report.local.html'))
    parser.add_argument('--snapshot', type=Path, help='Optional normalized JSON output')
    parser.add_argument('--force', action='store_true', help='Explicitly overwrite output paths')
    args = parser.parse_args(argv)
    try:
        if args.demo and args.previous:
            raise CatalogError('Demo mode includes its own fictional comparison.')
        inputs = [path.resolve() for path in [args.input, args.previous] if path]
        outputs = [path.resolve() for path in [args.output, args.snapshot] if path]
        if len(outputs) != len(set(outputs)) or any(path in inputs for path in outputs):
            raise CatalogError('Output paths must be distinct from each other and from inputs.')
        if not args.force and any(path.exists() for path in outputs):
            raise CatalogError('An output already exists; choose a new path or use --force.')
        if args.demo:
            current, previous = demo()
        else:
            text = fetch_html() if args.fetch else read(args.input)
            current = parse(text, source='https://supply.openai.com/' if args.fetch else args.input.name)
            previous = parse(read(args.previous), source=args.previous.name) if args.previous else None
        html = render(current, previous, fictional=args.demo)
        write_new(args.output, html, args.force)
        if args.snapshot:
            write_new(args.snapshot, json.dumps(current, indent=2) + '\n', args.force)
        print(f'Created {args.output.name}: {len(current["products"])} observed products; {len(compare(current, previous))} changes.')
        return 0
    except (CatalogError, OSError, UnicodeError, ValueError) as exc:
        print(f'Report not created: {exc}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
