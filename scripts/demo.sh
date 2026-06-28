#!/usr/bin/env bash
# Provenance Guard — end-to-end demo script
# Run with: bash scripts/demo.sh
# Server must be running: python app.py

BASE="http://127.0.0.1:5000"
SEP="────────────────────────────────────────────────────────────────"

echo ""
echo "$SEP"
echo "  Provenance Guard — end-to-end demo"
echo "$SEP"

# ── Health check ──────────────────────────────────────────────────────────────
echo ""
echo "[ 1/6 ] Health check"
curl -s "$BASE/health" | python3 -m json.tool
echo ""

# ── High-confidence AI submission ─────────────────────────────────────────────
echo "$SEP"
echo "[ 2/6 ] POST /submit — high-confidence AI text"
AI_RESULT=$(curl -s -X POST "$BASE/submit" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "In today'\''s rapidly evolving digital landscape, organizations must leverage cutting-edge solutions to unlock their full potential. Moreover, fostering a culture of innovation is pivotal to navigating the multifaceted challenges of the modern marketplace. It is important to note that a holistic approach empowers teams to deliver seamless value. Furthermore, by embracing transformative technologies, stakeholders can drive sustainable growth. Ultimately, this robust framework underscores the boundless opportunities ahead.",
    "content_type": "blog",
    "author_id": "demo-user-1"
  }')
echo "$AI_RESULT" | python3 -m json.tool
AI_ID=$(echo "$AI_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['content_id'])")
echo ""
echo "  → content_id: $AI_ID"
echo ""

# ── Borderline human submission ───────────────────────────────────────────────
echo "$SEP"
echo "[ 3/6 ] POST /submit — borderline formal human text (uncertain expected)"
HUMAN_RESULT=$(curl -s -X POST "$BASE/submit" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "The relationship between monetary policy and asset price inflation has been extensively studied in the literature. Central banks face a fundamental tension between their mandate for price stability and the unintended consequences of prolonged low interest rates on equity and real estate valuations.",
    "content_type": "story",
    "author_id": "demo-user-2"
  }')
echo "$HUMAN_RESULT" | python3 -m json.tool
HUMAN_ID=$(echo "$HUMAN_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['content_id'])")
echo ""
echo "  → content_id: $HUMAN_ID"
echo ""

# ── Appeal the borderline result ──────────────────────────────────────────────
echo "$SEP"
echo "[ 4/6 ] POST /appeal — creator contests the uncertain result"
curl -s -X POST "$BASE/appeal" \
  -H "Content-Type: application/json" \
  -d "{
    \"content_id\": \"$HUMAN_ID\",
    \"creator_reasoning\": \"I wrote this myself. I am an economics PhD student; formal academic prose is my normal register. The formal structure is a product of my training, not an AI tool.\",
    \"author_id\": \"demo-user-2\"
  }" | python3 -m json.tool
echo ""

# ── Third submission (ensures ≥3 log entries even on a clean DB) ──────────────
echo "$SEP"
echo "[ 5a/6 ] POST /submit — casual human text (third entry for log)"
curl -s -X POST "$BASE/submit" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "honestly I have no idea how to even start this essay lol. like I get the prompt but my brain is just not working today. gonna make some coffee and try again in 20 minutes, wish me luck",
    "content_type": "blog",
    "author_id": "demo-user-3"
  }' | python3 -m json.tool
echo ""

# ── Audit log ────────────────────────────────────────────────────────────────
echo "$SEP"
echo "[ 5b/6 ] GET /log?limit=3 — audit log showing 3 entries, appeal on second"
curl -s "$BASE/log?limit=3" | python3 -m json.tool
echo ""

# ── Analytics ────────────────────────────────────────────────────────────────
echo "$SEP"
echo "[ 6/6 ] GET /analytics — detection patterns, appeal rate, signal discord"
curl -s "$BASE/analytics" | python3 -m json.tool

echo ""
echo "$SEP"
echo "  Demo complete."
echo "$SEP"
echo ""
