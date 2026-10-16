import contextlib
import importlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import catalog
import studio


def item(**extra):
    return {'id': 'gid://shopify/Product/1', 'handle': 'fictional-item', 'title': 'Fictional item', **extra}


class CatalogTests(unittest.TestCase):
    def test_price_and_handle_are_not_availability(self):
        row = catalog.normalize(item(handle='buyable-item', priceRange={'minVariantPrice': {'amount': '50', 'currencyCode': 'EUR'}}))
        self.assertIsNone(row['available'])
        self.assertEqual(row['price']['currency'], 'EUR')

    def test_variant_flags_require_boolean_evidence(self):
        self.assertTrue(catalog.normalize(item(variants={'nodes': [{'availableForSale': True}, {}]}))['available'])
        self.assertFalse(catalog.normalize(item(variants=[{'availableForSale': False}]))['available'])
        self.assertIsNone(catalog.normalize(item(variants=[{'availableForSale': False}, {}]))['available'])
        self.assertIsNone(catalog.normalize(item(availableForSale='true', variants={'edges': None}))['available'])

    def test_money_rejects_nonfinite_negative_or_unsupported_currency(self):
        for value in ['NaN', 'Infinity', '-2', '1e-100000000', True]:
            self.assertIsNone(catalog.money({'amount': value, 'currencyCode': 'USD'}))
        self.assertIsNone(catalog.money({'amount': '1', 'currencyCode': 'not-currency'}))
        self.assertEqual(catalog.money({'amount': '0', 'currencyCode': 'USD'})['amount'], '0')

    def test_next_flight_strings_decode_without_executing_js(self):
        product = item(title='A "quoted" {brace} \\ title', availableForSale=True)
        flight = json.dumps([1, '2:' + json.dumps({'products': [product]})])
        html = '<script>self.__next_f.push(' + flight + ');alert("never");</script>'
        result = catalog.parse(html)
        self.assertEqual(result['products'][0]['title'], product['title'])
        self.assertTrue(result['products'][0]['available'])

    def test_conflicting_duplicates_remain_unknown(self):
        result = catalog.parse(json.dumps([item(availableForSale=True), item(availableForSale=False), item(availableForSale=True)]))
        self.assertIsNone(result['products'][0]['available'])
        self.assertEqual(result['quality']['duplicates'], 2)

    def test_redirect_or_changed_markup_is_not_empty_catalog(self):
        for source in ['<h1>Redirecting...</h1>', '<script>unrelated()</script>', '{}', '[]']:
            with self.assertRaises(catalog.CatalogError):
                catalog.parse(source)

    def test_comparison_baseline_missing_unknown_and_numeric_price(self):
        before = catalog.parse(json.dumps([item(availableForSale=False, priceRange={'minVariantPrice': {'amount': '12.0', 'currencyCode': 'USD'}})]))
        after = catalog.parse(json.dumps([item(availableForSale=True, priceRange={'minVariantPrice': {'amount': '12.00', 'currencyCode': 'USD'}})]))
        self.assertEqual([c['kind'] for c in catalog.compare(after, before)], ['restocked'])
        self.assertEqual(catalog.compare(after), [])
        unknown = catalog.parse(json.dumps([item()]))
        self.assertEqual(catalog.compare(after, unknown)[0]['kind'], 'availability-changed')
        different = catalog.parse(json.dumps([item(id='gid://shopify/Product/2', handle='another')]))
        self.assertEqual({c['kind'] for c in catalog.compare(different, before)}, {'first-seen', 'not-observed'})

    def test_normalized_roundtrip_rejects_invalid_availability_price_and_ids(self):
        current, _ = studio.demo()
        self.assertEqual(catalog.parse(json.dumps(current))['products'], current['products'])
        for field, value in [('available', 'false'), ('price', {'amount': 'NaN', 'currency': 'USD'}), ('id', 'invalid')]:
            damaged = json.loads(json.dumps(current))
            damaged['products'][0][field] = value
            with self.assertRaises(catalog.CatalogError):
                catalog.parse(json.dumps(damaged))

    def test_generated_html_escapes_script_closure(self):
        current = catalog.parse(json.dumps([item(title='</script><img src=x onerror=alert(1)>')]))
        html = studio.render(current)
        self.assertNotIn('</script><img', html)
        self.assertIn('\\u003c/script\\u003e', html)
        markers = catalog.parse(json.dumps([item(title='__APP__ __STYLES__')]))
        self.assertIn('"title": "__APP__ __STYLES__"', studio.render(markers))

    def test_cli_import_is_quiet_and_output_is_reproducible(self):
        with patch('urllib.request.urlopen', side_effect=AssertionError('network')), contextlib.redirect_stdout(io.StringIO()) as output:
            importlib.import_module('openai_supply_all_in_one')
        self.assertEqual(output.getvalue(), '')
        first, previous = studio.demo()
        self.assertEqual(studio.render(first, previous, fictional=True), studio.render(*studio.demo(), fictional=True))

    def test_cli_refuses_overwrite_and_input_output_aliases(self):
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(io.StringIO()):
            output = Path(directory) / 'report.html'
            self.assertEqual(studio.main(['--demo', '--output', str(output)]), 0)
            original = output.read_bytes()
            self.assertEqual(studio.main(['--demo', '--output', str(output)]), 1)
            self.assertEqual(output.read_bytes(), original)
            self.assertEqual(studio.main(['--input', str(output), '--output', str(output), '--force']), 1)
            self.assertEqual(output.read_bytes(), original)

    def test_fetch_redirect_does_not_parse_unrelated_page(self):
        class Response:
            url = 'https://openai.com/'
            def __enter__(self): return self
            def __exit__(self, *args): pass
        with patch('studio.urlopen', return_value=Response()):
            with self.assertRaises(catalog.CatalogError):
                studio.fetch_html()


if __name__ == '__main__':
    unittest.main()
