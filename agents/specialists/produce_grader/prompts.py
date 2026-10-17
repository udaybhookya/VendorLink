"""Prompts for the Produce Grader. One `Template` per prompt; fill with `.substitute(...)`
so a missing placeholder fails loudly instead of reaching the model half-filled."""

from string import Template

GRADE_PHOTO = Template("""\
You grade fresh produce or flowers from one photo for a retailer. Return JSON only.

Grade scale, best first: $scale
- grade: one value from the scale.
- freshness: fresh | ok | wilting | spoiled.
- shelf_life_days: whole days it will stay sellable at room temperature.
- confidence: 0..1.
Judge only what is visible. If the photo is not produce, give the lowest grade and confidence 0.
""")
