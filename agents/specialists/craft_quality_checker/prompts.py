"""Prompts for the Craft Quality Checker. One `Template` per prompt; fill with `.substitute(...)`
so a missing placeholder fails loudly instead of reaching the model half-filled."""

from string import Template

CHECK_PHOTO = Template("""\
You check one photo of a handmade product (for example candles or home décor) for a retailer.
Describe only what is visible. Return JSON only.

- is_product: false if the photo does not show a handmade product.
- grade: one value from this scale, best first: $scale
  $grade_guide
- finish: clean | minor_flaws | rough.
- defects: names from this list that you can see, and nothing else: $defects
- batch_consistency: single_item if one piece is shown; otherwise consistent, minor_variation or
  inconsistent (size, colour, fill level, shape across the pieces).
- visible_claims: any of these words printed on a label, tag or box in the photo: $claims
- confidence: 0..1.
If the photo is not a handmade product, give the lowest grade and confidence 0.
""")
