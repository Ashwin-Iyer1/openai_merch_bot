"""Bounded, non-executing catalog extraction and evidence-based comparisons."""
from collections import deque
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import json
import re

MAX_BYTES = 20 * 1024 * 1024


class CatalogError(ValueError):
    pass


class Scripts(HTMLParser):
    def __init__(self):
        super().__init__()
        self.scripts = []
        self.active = False
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'script':
            self.active = True
            self.parts = []

    def handle_data(self, data):
        if self.active:
            self.parts.append(data)

    def handle_endtag(self, tag):
        if tag == 'script' and self.active:
            self.scripts.append(''.join(self.parts))
            self.active = False


def money(value):
    if not isinstance(value, dict):
        return None
    amount, currency = value.get('amount'), value.get('currencyCode')
    if isinstance(amount, bool) or not isinstance(amount, (str, int, float)):
        return None
    if not isinstance(currency, str) or not re.fullmatch(r'[A-Z]{3}', currency):
        return None
    try:
        number = Decimal(str(amount))
        if not number.is_finite() or number < 0 or number > Decimal('1000000000') or number.as_tuple().exponent < -6:
            return None
        return {'amount': format(number, 'f'), 'currency': currency}
    except InvalidOperation:
        return None


def normalize(product):
    if not isinstance(product, dict):
        return None
    handle, title = product.get('handle'), product.get('title')
    if not isinstance(handle, str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,119}', handle):
        return None
    if not isinstance(title, str) or not title.strip():
        return None
    variants = product.get('variants', [])
    if isinstance(variants, dict):
        edges = variants.get('edges')
        variants = variants.get('nodes') or ([row.get('node') for row in edges if isinstance(row, dict)] if isinstance(edges, list) else [])
    variants = variants if isinstance(variants, list) else []
    variant_states = [row.get('availableForSale') for row in variants if isinstance(row, dict)]
    explicit = product.get('availableForSale')
    availability = explicit if type(explicit) is bool else None
    evidence = 'product flag' if availability is not None else 'no explicit availability'
    if availability is None and variant_states:
        if any(value is True for value in variant_states):
            availability, evidence = True, 'at least one variant available'
        elif all(value is False for value in variant_states):
            availability, evidence = False, 'all supplied variants unavailable'
    prices = product.get('priceRange') if isinstance(product.get('priceRange'), dict) else {}
    price = money(prices.get('minVariantPrice'))
    raw_id = product.get('id')
    identifier = raw_id if isinstance(raw_id, str) and re.fullmatch(r'gid://shopify/Product/\d+', raw_id) else 'handle:' + handle
    return {'id': identifier, 'handle': handle, 'title': title.strip()[:300],
            'available': availability, 'evidence': evidence, 'price': price,
            'variantsKnown': len(variant_states), 'variantsAvailable': sum(value is True for value in variant_states)}


def json_fragments(text):
    """Decode JSON values inside script wrappers; never evaluate JavaScript."""
    decoder = json.JSONDecoder()
    opening = re.compile(r'[\[{]')
    position, attempts = 0, 0
    while position < len(text) and attempts < 2000:
        match = opening.search(text, position)
        if not match:
            return
        position = match.start()
        attempts += 1
        try:
            value, end = decoder.raw_decode(text, position)
            yield value
            position = end
        except (json.JSONDecodeError, RecursionError):
            position += 1


def parse(text, *, source='local input', captured_at=None):
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise CatalogError('Input exceeds 20 MiB.')
    try:
        root = json.loads(text)
    except (json.JSONDecodeError, RecursionError):
        parser = Scripts()
        parser.feed(text)
        if not parser.scripts:
            raise CatalogError('No supported product JSON found. A redirect or changed page is not an empty catalog.')
        root = parser.scripts
    if isinstance(root, dict) and root.get('schema') == 'supply-snapshot/v1':
        return validate_snapshot(root)
    queue = deque([(root, 0)])
    products = {}
    duplicates = 0
    visited = 0
    while queue:
        value, depth = queue.popleft()
        visited += 1
        if visited > 50000 or depth > 12:
            raise CatalogError('Input exceeds the extraction complexity limit.')
        if isinstance(value, dict):
            product = normalize(value)
            if product:
                if product['id'] in products:
                    duplicates += 1
                    old = products[product['id']]
                    if old['available'] is not None and product['available'] is not None and old['available'] != product['available']:
                        product['available'] = None
                        product['evidence'] = 'conflicting duplicate flags'
                    if old['evidence'] == 'conflicting duplicate flags':
                        product['available'], product['evidence'] = None, old['evidence']
                    product['price'] = product['price'] or old['price']
                products[product['id']] = product
            queue.extend((child, depth + 1) for child in value.values() if isinstance(child, (dict, list)))
        elif isinstance(value, list):
            queue.extend((child, depth + 1) for child in value if isinstance(child, (dict, list, str)))
        elif isinstance(value, str) and ('{' in value or '[' in value):
            queue.extend((child, depth + 1) for child in json_fragments(value))
    if len(products) > 5000:
        raise CatalogError('Catalog exceeds 5,000 products.')
    if not products:
        raise CatalogError('No supported products found. Existing snapshots must not be replaced by a failed extraction.')
    return {'schema': 'supply-snapshot/v1', 'source': str(source)[:200],
            'capturedAt': captured_at or datetime.now(timezone.utc).isoformat(),
            'products': sorted(products.values(), key=lambda p: p['title'].casefold()),
            'quality': {'duplicates': duplicates, 'products': len(products)}}


def validate_snapshot(snapshot):
    rows = snapshot.get('products')
    if not isinstance(rows, list) or not rows or len(rows) > 5000:
        raise CatalogError('A normalized snapshot needs 1–5,000 products.')
    normalized = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or row['id'] in seen:
            raise CatalogError('Invalid or duplicate product identifier.')
        if type(row.get('available')) is not bool and row.get('available') is not None:
            raise CatalogError('Availability must be true, false, or null.')
        price = row.get('price')
        converted = {'amount': price.get('amount'), 'currencyCode': price.get('currency')} if isinstance(price, dict) else None
        canonical = normalize({'id': row['id'], 'handle': row.get('handle'), 'title': row.get('title'), 'availableForSale': row.get('available'), 'priceRange': {'minVariantPrice': converted}})
        if not canonical or canonical['id'] != row['id'] or (price is not None and canonical['price'] is None):
            raise CatalogError('Invalid normalized product.')
        for key in ['variantsKnown', 'variantsAvailable']:
            val = row.get(key, 0)
            if type(val) is not int or not 0 <= val <= 50000:
                raise CatalogError('Invalid variant count.')
            canonical[key] = val
        if canonical['variantsAvailable'] > canonical['variantsKnown']:
            raise CatalogError('Available variant count exceeds supplied variant count.')
        canonical['evidence'] = str(row.get('evidence', canonical['evidence']))[:100]
        seen.add(row['id'])
        normalized.append(canonical)
    return {'schema': 'supply-snapshot/v1', 'source': str(snapshot.get('source', 'local snapshot'))[:200], 'capturedAt': str(snapshot.get('capturedAt', 'unknown'))[:40], 'products': normalized, 'quality': {'products': len(normalized), 'duplicates': 0}}


def compare(current, previous=None):
    old = {product['id']: product for product in previous['products']} if previous else {}
    now = {product['id']: product for product in current['products']}
    changes = []
    for key, product in now.items():
        before = old.get(key)
        if before is None:
            if previous:
                changes.append({'kind': 'first-seen', 'id': key, 'title': product['title'], 'before': None, 'after': product['available']})
            continue
        if before['available'] != product['available']:
            kind = 'restocked' if before['available'] is False and product['available'] is True else 'availability-changed'
            changes.append({'kind': kind, 'id': key, 'title': product['title'], 'before': before['available'], 'after': product['available']})
        before_price, after_price = before['price'], product['price']
        if before_price and after_price and (before_price['currency'] != after_price['currency'] or Decimal(before_price['amount']) != Decimal(after_price['amount'])):
            changes.append({'kind': 'price-changed', 'id': key, 'title': product['title'], 'before': before_price, 'after': after_price})
    for key in old.keys() - now.keys():
        changes.append({'kind': 'not-observed', 'id': key, 'title': old[key]['title'], 'before': old[key]['available'], 'after': None})
    return changes
