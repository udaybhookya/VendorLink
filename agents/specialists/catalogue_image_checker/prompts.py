"""Prompts for the Catalogue Image Checker. One `Template` per prompt; fill with `.substitute(...)`
so a missing placeholder fails loudly instead of reaching the model half-filled."""

from string import Template

REVIEW_PHOTO = Template("""\
You review one product photo for a retailer's online catalogue. Describe only what is visible.
Return JSON only.

The retailer wants: background $background; style: $style.

- background: plain (one even colour, or a single surface against a plain or softly blurred
  backdrop), busy (patterns, furniture, a sharp room behind) or cluttered (other objects, mess).
- other_objects: true if hands, people, unrelated items or packaging clutter are in the frame.
- product_size: small if the product takes up less than about a third of the photo's height
  and width; otherwise good.
- cut_off: true if part of the product is outside the photo (a close-up that crops it).
- lighting: even, harsh (hard shadows, glare) or dim.
- blur: none, slight, or strong (the product itself is visibly out of focus or smeared; ignore
  any text or numbers overlaid on the photo).
- noise: low, medium or high (grain, compression blocks, smudges on the lens).
- catalogue_ready: true only if this photo could go on the retailer's site as it is.
- confidence: 0..1.
""")
