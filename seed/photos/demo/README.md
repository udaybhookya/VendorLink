# Demo photos (Scene 2b, config A)

Synthetic product photos for the listing flow: Maya → Chat → **Add a product** → send the photo,
then say *"Lavender candle, 250 gram jar, burns about 30 hours. 40 pieces at 220 rupees each."*
(price, quantity and size in her words; no *handmade / natural / soy wax* in the text).

The gifting retailer's standards (`config/retailer_profiles/gifting.json`): at least 600×600 px,
sharpness ≥ 8, brightness 60–235, uncluttered background. Size, sharpness and brightness are
measured in code; background, framing and light are described by the vision model.

| File | Size | Measured | Expected |
|---|---|---|---|
| `p1_clean_large.jpeg` | 1200×800 | passes | no photo flags → her Guardian approves it on her behalf (the "no question asked" path) |
| `p1_clean.jpeg` | 562×374 | `IMAGE_LOW_RESOLUTION` | too small: her Guardian asks her to pick a photo |
| `p2_cluttered.jpeg` | 860×304 | `IMAGE_LOW_RESOLUTION` | + `IMAGE_CLUTTERED_BACKGROUND` → auto-enhanced + AI versions, she picks one |
| `p3_dark.jpeg` | 1280×468 | `IMAGE_LOW_RESOLUTION`, `IMAGE_TOO_DARK` | + retake tip "near a window in daylight" |
| `p4_blurry.jpeg` | 1280×456 | `IMAGE_LOW_RESOLUTION` | `IMAGE_BLURRY` only if the model sees it out of focus |
| `p5_tiny.jpeg` | 1280×463 | `IMAGE_LOW_RESOLUTION` | + `IMAGE_PRODUCT_TOO_SMALL` |
| `p6_defect.jpeg` | 860×626 | passes | `DEFECT_CRACKS`, `DEFECT_SOOT`, `DEFECT_CHIPPED_CONTAINER` (Craft Quality Checker) |
| `p7_claim_label.jpeg` | 862×618 | passes | `UNSUPPORTED_CLAIM_SOY_WAX` (from the label) |

`p1_clean_large.jpeg` is `p1_clean.jpeg` enlarged; the others keep their original size on purpose.
P2–P7 carry a "P2"…"P7" tag in a corner, which the vision model also sees.
Re-measure after changing a photo:

```bash
uv run python -c "from vl_core.imaging import measure; import sys; print(measure(open(sys.argv[1],'rb').read()))" seed/photos/demo/p1_clean_large.jpeg
```
