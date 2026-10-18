# VendorLink — demo video script (~3½ min)

Recorded automatically by `scripts/record_demo.py`; cut by `scripts/build_demo_video.py`. Every click is timed against its narration line; agent and model waits are squeezed to 0.6 s in the edit (a *⏩ agents working* tag shows while they happen). Layout: the supplier's phone on the left, FreshCart's console on the right, captions below. Everything on screen is in English; the narration says the apps run in Telugu and Bahasa Indonesia.

## Run it

```bash
make dev                                   # terminal 1, wait for "VendorLink ready"
uv run python scripts/record_demo.py        # terminal 2: ~6 min, headless (--headed to watch)
uv run python scripts/build_demo_video.py   # needs ffmpeg → docs/demo_video/out/VendorLink_demo.mp4 (+ .srt)
```

- Needs `GOOGLE_API_KEY` and `IMAGE_MODEL` for the photo-to-listing beats (AI photos and copy). Slack messages go out for real with `VL_CHANNELS=mcp`.
- Voice: macOS `say` (best installed of Ava/Zoe Premium, Samantha Enhanced, Samantha). `--voice NAME` to choose.
- Edit the words in `narration.json`; the clicks re-time themselves.
- The recorder resets to config A first and leaves config B active with one audit record tampered: run `scripts/demo_reset.py` afterwards.

## Beats

| # | Beat | On screen | Narration |
|---|---|---|---|
| 1 | VendorLink | Title card with the problem statement from the deck | VendorLink. AI agents that let small makers sell to modern retailers: in their own language, within rules they set, with every action provable. |
| 2 | The problem | Problem card: makers can't work retail's systems · retailers can't onboard them one by one · Meet Maya | Both sides want the sale. Home makers make what local shoppers search for, but retail runs on English forms, catalogue standards and fine print. And a maker who supplies forty a day costs a buyer as much work as a big distributor, so the sale never happens. |
| 3 | The idea | Architecture card: Concierge + Guardian · trust gateway · orchestrator + specialists | VendorLink gives every maker an agent, and keeps every agent honest. Maya's Concierge talks with her, and her Guardian, plain code with no model, is the only one that signs. FreshCart's orchestrator hands work to specialist agents. Between them, a trust gateway checks identity, permission and content, and keeps a tamper-evident log. |
| 4 | 1 · Photo to listing | Phone → Chat: **Add a product**, attach `p2_cluttered.jpeg`, type *40 lavender candles at 220 rupees each, 250 gram jar* | Meet Maya. She pours candles at home in Hyderabad. To list a product, she sends a phone photo and a few words: forty lavender candles at two hundred and twenty rupees. |
| 5 | 1 · Product enrichment | Listing card in chat: photo flags, auto-enhanced + two AI photos, title/description; she taps **Use this photo** on an AI photo | The retailer's agents check the photo against catalogue standards: too small, cluttered background. They make an auto-enhanced version and two AI versions of her own photo, and write the title and description from her words, each fact with its source. Her Guardian never approves an AI photo for her, so she picks one. |
| 6 | 1 · A person approves | Console → Inbox → *Review product*: her photo beside the AI photo; **Approve and publish** | FreshCart's policy sends AI photos to a person. The category manager sees her photo beside the AI one, with every flag in words, and publishes it, the AI photo disclosed. |
| 7 | 2 · Her rules | Phone → My shop: rule cards (₹180 floor, 40/day, pickup from 10:00, 15 days) and the signed badge | Maya set her rules once: at least one hundred and eighty rupees a candle, forty a day, pickup after ten, paid within fifteen days. Her Guardian keeps them as her signed standing mandate. |
| 8 | 3 · An order arrives | Console → Orders: store *Corporate gifting desk* → **Place order** | FreshCart's gifting desk orders fifty candles at two hundred and twenty rupees, pickup at eight in the morning, paid in forty-five days. |
| 9 | 3 · Guardian counters | Order card turns **red**; v1 reasons, then the v2 counter row | Her Guardian checks it in plain code, in under a millisecond. Red: too many, too early, paid too late. It counters inside her rules: forty candles, ten a.m., fifteen days. |
| 10 | 4 · People in the loop | Console → Inbox → *Supplier countered*: *Why this needs you*, *Also asked buyer on Slack*; **Accept the counter** | The counter changes the order value by twenty percent, and FreshCart auto-accepts only ten. So the buyer is asked, with the reasons in words, here and on Slack. Whichever answer comes first closes the other. The buyer accepts. |
| 11 | 5 · Her yes | Phone → Deals: amber deal, read-back, language picker (kept in English); **Yes, sign** | The deal is amber: inside her rules, but over her auto-accept limit, so it waits for Maya. Her app and read-backs work in Telugu or Bahasa Indonesia; we show English here. Her own yes is the consent, bound to the hash of this exact version. |
| 12 | 5 · Signed | Phone: *Signed by your Guardian*; Console → Orders: mandate hash | Her Guardian signs, FreshCart countersigns, and the gateway files the order mandate. No model decided or signed anything. |
| 13 | 6 · Observability | Console → Observability: *Who talks to whom*; `vendor-agent:maya` drawer (trust, keys, **Suspend**); **Activities** (time and tokens) | Operators see every agent: who talks to whom, trust scores, every decision, and how long each step takes and how many tokens it used. Any agent can be suspended, quarantined or revoked. |
| 14 | 7 · Under attack | Terminal overlay: `attack_impostor.py --live`, `attack_injected_doc.py --live` → BLOCKED; **Safety** tab | Now an attack: an impostor without Maya's key, and a document with hidden instructions for the model. The gateway blocks both and quarantines the uploader. Maya's real agent is untouched. |
| 15 | 8 · New market, no code | Console → Configuration: **Activate** preset B; specialists and config hash change | A new market is configuration, not code. One click switches FreshCart to packaged food in Indonesia: Bahasa, rupiah, local food-safety permits, and different specialist agents. |
| 16 | 8 · Label check | Phone → Sara's Products; Console → Inbox → cassava chips: P-IRT flag | Sara's cassava chips label has no P-IRT registration number, so the Label Checker flags it before anything is listed. |
| 17 | 9 · Provable | Console → Trust & audit: **Verify log** ✓ → **Tamper** → **Verify log** ✗ broken row | And everything is provable. Verify replays the audit chain: intact. Tamper with one record, and it breaks at exactly that entry. |
| 18 | VendorLink | Closing card | VendorLink. Agents for small makers: in their language, inside their rules, with trust you can verify. Built with Gemini, the Agent Development Kit, A2A and MCP. |
