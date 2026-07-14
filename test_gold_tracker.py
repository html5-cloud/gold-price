import unittest

from gold_tracker import _chart_html


class ChartHtmlTest(unittest.TestCase):
    def setUp(self):
        self.html = _chart_html(
            dates=["2026-07-13", "2026-07-14"],
            series=[
                {"name": "周大福", "type": "line", "data": [800, 805]},
                {"name": "老凤祥", "type": "line", "data": [810, 812]},
            ],
            series_prem=[
                {"name": "周大福", "type": "line", "data": [120, 121]},
                {"name": "老凤祥", "type": "line", "data": [130, 128]},
            ],
            default_visible={"周大福"},
            range_months=12,
            minmax={},
            ref_name="上海金交所黄金T+D",
        )

    def test_mobile_legend_has_room_between_items(self):
        self.assertIn("left:sm?8:'center',right:sm?8:null", self.html)
        self.assertIn("itemGap:sm?18:16", self.html)

    def test_mobile_hides_redundant_y_axis_title(self):
        self.assertIn("name:sm?'':axisTitle()", self.html)

    def test_page_has_inline_gold_favicon(self):
        self.assertIn('<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,', self.html)
        self.assertIn("%23d9a441", self.html)

    def test_page_omits_data_source_note(self):
        self.assertNotIn("数据来源:cngold.org", self.html)
        self.assertNotIn('class="note"', self.html)


if __name__ == "__main__":
    unittest.main()
