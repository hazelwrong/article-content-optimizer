"""Behavioral regression checks for static scan boundaries, not editorial quality."""
import importlib.util
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "preflight_scan.py"
spec = importlib.util.spec_from_file_location("preflight_scan", SCRIPT)
scanner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scanner)


class ScanTests(unittest.TestCase):
    def scan(self, html):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "article.html"
            path.write_text(html, encoding="utf-8")
            return scanner.scan(path)

    def test_general_articles_and_existing_classes_are_read(self):
        for wrapper in ['article', 'article class="dhc-page"', 'div class="sports-article"', 'div class="article-content"', 'main', 'body']:
            with self.subTest(wrapper=wrapper):
                result = self.scan(f'<{wrapper}><h1>Guide</h1><p>Useful content.</p></{wrapper.split()[0]}>')
                self.assertGreater(result['token_count'], 2)
                self.assertEqual(result['title'], 'Guide')

    def test_no_product_count_based_intent_warning(self):
        for links in ['', '<a href="https://brand.example/item">Brand</a>', '<a href="https://www.dhgate.com/product/x">Item</a>']:
            with self.subTest(links=links):
                result = self.scan(f'<article><h1>Best collection 2026</h1><p>Selection criteria.</p>{links}</article>')
                self.assertEqual(result['warnings'], [])

    def test_partial_html_does_not_claim_missing_page_metadata(self):
        result = self.scan('<p>Useful paragraph.</p>')
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['input_scope'], 'local_fragment_or_partial_html')
        self.assertTrue(any('may omit' in item for item in result['limitations']))

    def test_full_html_reports_observed_metadata_candidates(self):
        result = self.scan('<html><head><meta name="googlebot" content="noindex"></head><body><p>Body</p></body></html>')
        self.assertEqual({w['code'] for w in result['warnings']}, {'H1_COUNT', 'CANONICAL_COUNT', 'NOINDEX'})
        self.assertTrue(all(w['category'] == 'technical' for w in result['warnings']))

    def test_observed_noindex_and_empty_canonical_in_partial_input(self):
        result = self.scan('<meta name="robots" content="none"><link rel="canonical" href=""><p>Body</p>')
        self.assertEqual({w['code'] for w in result['warnings']}, {'NOINDEX', 'EMPTY_CANONICAL'})

    def test_visible_production_leak_and_self_undermining_are_candidates(self):
        result = self.scan('<article><p>Commercial disclosure: A future <b>product module</b> may contain marketplace links.</p><p>We have not tested these products.</p><p>本文未进行任何测试/实验。</p></article>')
        self.assertEqual({w['code'] for w in result['warnings']}, {'PRODUCTION_PROCESS_TEXT', 'SELF_UNDERMINING_TEST_STATEMENT'})
        self.assertEqual(len(result['warnings']), 3)
        self.assertTrue(all(w['category'] == 'content' for w in result['warnings']))

    def test_actual_disclosure_and_necessary_limits_are_not_blanket_flags(self):
        result = self.scan('<article><p>We may earn a commission from affiliate links.</p><p>Not suitable for children. Stop use if irritation occurs.</p><p>The manufacturer does not test every color separately.</p></article>')
        self.assertEqual(result['warnings'], [])

    def test_scripts_comments_templates_and_head_are_not_reader_text(self):
        result = self.scan('<html><head><title>future product module</title><style>as an AI language model</style></head><body><article><p>Clean</p><!-- normal implementation comment --><script>future product module</script><template><p>We have not tested</p></template></article></body></html>')
        self.assertFalse(any(w['category'] == 'content' for w in result['warnings']))
        self.assertEqual(result['token_count'], 1)

    def test_legacy_product_slot_comment_is_reported_as_nonvisible_artifact(self):
        result = self.scan('<article><p>Body</p><!-- PRODUCT_MODULE_SLOT: folding chair --></article>')
        self.assertEqual(result['warnings'][0]['code'], 'PRODUCTION_PLACEHOLDER_COMMENT')
        self.assertIn('not visible', result['warnings'][0]['evidence'])

    def test_line_break_does_not_concatenate_words(self):
        result = self.scan('<article><p>future<br>product module</p></article>')
        self.assertEqual(result['warnings'][0]['code'], 'PRODUCTION_PROCESS_TEXT')

    def test_inline_split_word_and_void_tags_preserve_text(self):
        result = self.scan('<article><p>fu<b>ture</b> product module</p><img src="x"><p>Useful detail</p></article>')
        self.assertTrue(any(w['code'] == 'PRODUCTION_PROCESS_TEXT' for w in result['warnings']))
        self.assertEqual(result['token_count'], 5)

    def test_article_preferred_to_page_navigation(self):
        result = self.scan('<body><nav>future product module</nav><main><article><p>Actual body.</p></article></main></body>')
        self.assertEqual(result['warnings'], [])
        self.assertEqual(result['text_scope'], 'article')

    def test_cli_comparison_is_lexical_only_and_read_only(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = [Path(folder) / name for name in ['a.html', 'b.html']]
            html = '<article><h1>Guide</h1><p>Same useful content.</p></article>'
            for path in paths:
                path.write_text(html, encoding='utf-8')
            output = subprocess.check_output([sys.executable, str(SCRIPT), str(paths[0]), '--compare-dir', folder], text=True)
            result = json.loads(output)[0]
            self.assertEqual(result['similar_pages'][0]['meaning'], 'lexical_overlap_only')
            self.assertNotIn('_tokens', result)
            for path in paths:
                self.assertEqual(path.read_text(encoding='utf-8'), html)


if __name__ == '__main__':
    unittest.main()
