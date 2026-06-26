"""Exercise the LLM judge in isolation, BEFORE it is wired into the endpoint.

Per planning.md's M3 verification step: call ``score_llm()`` directly on a clearly-AI
paragraph and a public-domain human passage, confirm p_ai separates them, and confirm
malformed model output falls back to 0.5.

Run:  ./.venv/bin/python scripts/try_llm.py
"""

import os
import sys

from dotenv import load_dotenv

# Make the project root importable when run as `python scripts/try_llm.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

load_dotenv()

from signals.llm import _parse, score_llm  # noqa: E402

# Freshly-generated AI-style marketing prose: fluent, evenly hedged, generic.
AI_SAMPLE = (
    "In today's fast-paced digital landscape, leveraging cutting-edge solutions is essential "
    "for businesses seeking to unlock their full potential. By embracing innovation and "
    "fostering a culture of collaboration, organizations can navigate the complexities of the "
    "modern marketplace. Ultimately, a holistic approach to growth empowers teams to deliver "
    "exceptional value and drive sustainable success."
)

# Public-domain human writing (Mark Twain, *Life on the Mississippi*, 1883): idiosyncratic, bursty.
HUMAN_SAMPLE = (
    "After all these years I can picture that old time to myself now, just as it was then: the "
    "white town drowsing in the sunshine of a summer's morning; the streets empty, or pretty "
    "nearly so; one or two clerks sitting in front of the Water Street stores, with their "
    "splint-bottomed chairs tilted back against the wall, chins on breasts, hats slouched over "
    "their faces, asleep with shavings enough around to show what broke them down."
)


def _show(label: str, text: str) -> None:
    result = score_llm(text)
    print(f"\n=== {label} ===")
    print(f"  p_ai      : {result['p_ai']}")
    print(f"  rationale : {result['rationale']}")


def main() -> None:
    _show("AI-style sample (expect HIGH p_ai)", AI_SAMPLE)
    _show("Human sample — Twain 1883 (expect LOW p_ai)", HUMAN_SAMPLE)

    # Parser robustness: malformed model output must yield None (→ fallback in score_llm).
    print("\n=== Parser fallback check ===")
    print(f"  _parse('not json')           -> {_parse('not json')}  (expect None)")
    print(f"  _parse('{{\"oops\": 1}}')        -> {_parse('{\"oops\": 1}')}  (expect None)")
    print("  (score_llm turns a None parse into p_ai=0.5 'parse_failed')")


if __name__ == "__main__":
    main()
