# Technical Reference

python-pptx technical reference for slide generation.

## Slide Dimensions

**16:9 (presentations):**
- Width: 13.333 inches
- Height: 7.5 inches
- Safe margins: 0.5 inches

**1:1 (carousels):**
- Width: 7.5 inches
- Height: 7.5 inches

**Always use:**
- Blank layout: `prs.slide_layouts[6]`
- python-pptx version: 1.0.2

## Common Imports

```python
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.util import Inches, Pt
```

## Chart Types

- `XL_CHART_TYPE.PIE` - Pie chart
- `XL_CHART_TYPE.DOUGHNUT` - Doughnut chart
- `XL_CHART_TYPE.BAR_CLUSTERED` - Horizontal bars
- `XL_CHART_TYPE.COLUMN_CLUSTERED` - Vertical columns
- `XL_CHART_TYPE.LINE` - Line chart

## Adding Charts

```python
chart_data = CategoryChartData()
chart_data.categories = ["A", "B", "C"]
chart_data.add_series("Values", [10, 20, 30])

slide.shapes.add_chart(
    XL_CHART_TYPE.DOUGHNUT,
    Inches(x), Inches(y),
    Inches(width), Inches(height),
    chart_data
)
```

## Adding Images

```python
slide.shapes.add_picture(
    "path/to/image.png",
    Inches(x), Inches(y),
    width=Inches(w)  # Height auto-calculated
)
```

## Editing Existing PPTX Files

When user provides an existing PPTX:

1. **Read the file:**
   ```python
   from pptx import Presentation
   prs = Presentation("path/to/existing.pptx")
   ```

2. **Analyze:** Number of slides, styling, content structure

3. **Apply changes:** Add/remove slides, update content, modify styling

4. **Save to output directory** (don't overwrite original unless requested)

## Preview All Layouts

To see all available layouts:
```bash
uv run .claude/skills/pptx-generator/generate-cookbook-preview.py
```

This generates `cookbook-preview.pptx` with every layout.
