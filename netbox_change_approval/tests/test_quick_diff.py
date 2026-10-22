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
