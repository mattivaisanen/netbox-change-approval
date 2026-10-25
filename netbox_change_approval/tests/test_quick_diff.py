from django.template.loader import render_to_string
from django.test import SimpleTestCase


class QuickDiffTableTests(SimpleTestCase):
    """The small change table on batch pages, the inbox and the dashboard."""

    def _render(self, rows):
        return render_to_string('netbox_change_approval/inc/quick_diff.html', {'rows': rows})

    def test_fixed_column_widths(self):
        html = self._render([{'label': 'name', 'old': 'a', 'new': 'b'}])
        self.assertIn('table-layout: fixed', html)
        self.assertIn('width: 30%', html)

    def test_new_object_has_no_old_column(self):
        html = self._render([{'label': 'name', 'old': None, 'new': 'Rack 1'}])
        self.assertNotIn('>Old<', html)
        self.assertIn('>Value<', html)

    def test_empty_values_look_the_same(self):
        html = self._render([
            {'label': 'a', 'old': '', 'new': 'x'},
            {'label': 'b', 'old': None, 'new': ''},
            {'label': 'c', 'old': 'y', 'new': 'z'},
        ])
        self.assertEqual(html.count('<span class="text-muted">&mdash;</span>'), 3)
        self.assertNotIn('text-danger">&mdash;', html)
        self.assertIn('<span class="text-danger">y</span>', html)


class QuickToggleIncludedTests(SimpleTestCase):
    def test_widget_includes_row_click_and_arrow(self):
        html = render_to_string('netbox_change_approval/inc/quick_toggle.html')
        self.assertIn('nbcaQuickRows', html)
        # Transforms don't apply to inline elements; without this the arrow never turned.
        self.assertIn('display: inline-block', html)


class ShortAgeTests(SimpleTestCase):
    def test_largest_unit_only(self):
        from datetime import timedelta

        from django.utils import timezone

        from ..templatetags.nbca_diff import short_age
        self.assertEqual(short_age(timezone.now() - timedelta(hours=10, minutes=47)), '10\xa0hours ago')
        self.assertEqual(short_age(None), '')
