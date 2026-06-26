"""Calibration harness for the multi-signal pipeline (milestone M4 steps 2 & 4).

Two passes:
  1) STYLOMETRY ALONE — tests Signal 2 independently (no API), printing its features so we can
     see *why* it scored as it did and tune cutoffs.
  2) FULL FUSION — calls the LLM judge too, then fuses, printing both signal scores beside the
     combined confidence + verdict (so a misbehaving signal is visible).

Run:  ./.venv/bin/python scripts/try_scoring.py
(Pass 1 needs no key; pass 2 needs GROQ_API_KEY in .env.)
"""

import os
import sys

from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv()

from scoring import fuse  # noqa: E402
from signals.stylometry import score_stylometry  # noqa: E402

# (label, expected-direction, text)
CASES = [
    ("clear AI (rubric)", "ai", (
        "Artificial intelligence represents a transformative paradigm shift in modern society. "
        "It is important to note that while the benefits of AI are numerous, it is equally "
        "essential to consider the ethical implications. Furthermore, stakeholders across "
        "various sectors must collaborate to ensure responsible deployment."
    )),
    ("clear AI (long)", "ai", (
        "In today's rapidly evolving digital landscape, organizations must leverage cutting-edge "
        "solutions to unlock their full potential. Moreover, fostering a culture of innovation is "
        "pivotal to navigating the multifaceted challenges of the modern marketplace. It is "
        "important to note that a holistic approach empowers teams to deliver seamless value. "
        "Furthermore, by embracing transformative technologies, stakeholders can drive sustainable "
        "growth. Ultimately, this robust framework underscores the boundless opportunities ahead."
    )),
    ("clear human (rubric)", "human", (
        "ok so i finally tried that new ramen place downtown and honestly? underwhelming. the "
        "broth was fine but they put WAY too much sodium in it and i was thirsty for like three "
        "hours after. my friend got the spicy version and said it was better. probably won't go "
        "back unless someone drags me there"
    )),
    ("borderline: formal human", "?", (
        "The relationship between monetary policy and asset price inflation has been extensively "
        "studied in the literature. Central banks face a fundamental tension between their mandate "
        "for price stability and the unintended consequences of prolonged low interest rates on "
        "equity and real estate valuations."
    )),
    ("borderline: edited AI", "?", (
        "I've been thinking a lot about remote work lately. There are genuine tradeoffs — "
        "flexibility and no commute on one side, isolation and blurred work-life boundaries on the "
        "other. Studies show productivity varies widely by individual and role type."
    )),
    ("clear human (Twain 1883)", "human", (
        "After all these years I can picture that old time to myself now, just as it was then: the "
        "white town drowsing in the sunshine of a summer's morning; the streets empty, or pretty "
        "nearly so; one or two clerks sitting in front of the Water Street stores, with their "
        "splint-bottomed chairs tilted back against the wall, chins on breasts, hats slouched over "
        "their faces, asleep with shavings enough around to show what broke them down."
    )),
]


def pass1_stylometry_only() -> None:
    print("=" * 78)
    print("PASS 1 — STYLOMETRY ALONE (no API). Independent test of Signal 2.")
    print("=" * 78)
    for label, _, text in CASES:
        r = score_stylometry(text)
        f = r["features"]
        print(f"\n{label:28s}  p_stylo={r['p_ai']:.3f}")
        print(
            f"    burst={f['burstiness']:.2f}  ttr={f['lexical_diversity']:.2f}  "
            f"trans={f['transition_density']:.2f}  ai_vocab={f['ai_vocab_hits']}  "
            f"wc={f['word_count']}"
        )


def pass2_full_fusion() -> None:
    from signals.llm import score_llm

    print("\n" + "=" * 78)
    print("PASS 2 — FULL FUSION (LLM + stylometry → confidence + verdict).")
    print("=" * 78)
    print(f"\n{'case':28s} {'p_llm':>6} {'p_stylo':>8} {'p_ai':>6} {'conf':>6}  verdict")
    print("-" * 70)
    for label, _, text in CASES:
        llm = score_llm(text)
        stylo = score_stylometry(text)
        fused = fuse(llm["p_ai"], stylo["p_ai"], stylo["features"]["word_count"])
        print(
            f"{label:28s} {llm['p_ai']:>6.2f} {stylo['p_ai']:>8.2f} "
            f"{fused['p_ai']:>6.2f} {fused['confidence']:>6.2f}  {fused['verdict']}"
        )


if __name__ == "__main__":
    pass1_stylometry_only()
    if os.environ.get("GROQ_API_KEY"):
        pass2_full_fusion()
    else:
        print("\n(skipping PASS 2 — GROQ_API_KEY not set)")
