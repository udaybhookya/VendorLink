"""Prompts for the Image Enhancer. One `Template` per prompt; fill with `.substitute(...)` so a
missing placeholder fails loudly instead of reaching the model half-filled."""

from string import Template

PLAN_EDITS = Template("""\
You help a small maker get one product photo ready for a retailer's catalogue. Look at the photo.
Return JSON only.

What is wrong with it (machine codes): $problems
The retailer's catalogue: $style. Background: $background.
The retailer wants $count AI versions, each staged in its own look:
$looks

- product: one sentence naming exactly what the product is, as seen.
- keep: the visible details that must not change (shape, colours, label or tag text, container,
  wick, size relative to the frame …). Be specific.
- fixes: up to 3 short corrections to the photo itself that every version needs (sharpness,
  framing, exposure, removing clutter). Not the backdrop or the light: the looks set those.
- scenes: exactly $count sentences, one per look above, in the same order. Each adapts its look
  to this product (which surface or backdrop colour suits it, where the light falls, the camera
  angle) so the versions are clearly different from each other. Never change the product, never
  add text, other products or props that suggest features it may not have.
""")

# Wrapped around every version in code, so the guard does not depend on the model.
FAITHFUL_EDIT = Template("""\
Edit this photo of a small maker's own product for an online catalogue. The product is: $product.
Keep the product exactly as it is: $keep. Do not add, remove or change any text, label, logo,
colour, shape or quantity, and do not add other products or props.
Fix: $fixes.
Restage it in this look ($look): $scene $detail""")
