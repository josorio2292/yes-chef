"""
Empirical test: text-embedding-3-small similarity between
recipe ingredient names and Sysco-style catalog descriptions.

Tests the format mismatch between lowercase natural language (ingredient names)
and ALL-CAPS comma-separated catalog descriptions.
"""

import os
import sys
import math


def get_api_config():
    """Detect which API key to use and return (api_key, base_url, model)."""
    openrouter_key = os.environ.get("OPENROUTER_API_KEY")
    openai_key = os.environ.get("OPENAI_API_KEY")

    if openrouter_key:
        print(f"✓ Using OPENROUTER_API_KEY (OpenRouter API)")
        return (
            openrouter_key,
            "https://openrouter.ai/api/v1",
            "openai/text-embedding-3-small",
        )
    elif openai_key:
        print(f"✓ Using OPENAI_API_KEY (OpenAI API directly)")
        return (
            openai_key,
            "https://api.openai.com/v1",
            "text-embedding-3-small",
        )
    else:
        print("✗ ERROR: Neither OPENROUTER_API_KEY nor OPENAI_API_KEY is set.")
        print("  Set one of these environment variables and re-run.")
        sys.exit(1)


# ── Data ──────────────────────────────────────────────────────────────────────

CATALOG_DESCRIPTIONS = [
    "BEEF, TENDERLOIN, FILET, 8OZ, CENTER CUT, 20/CS",
    "SCALLOP, SEA, DIVER, DRY PACK, U-10, 5LB, 2/CS",
    "BACON, SMOKED, APPLEWOOD, THICK CUT, 15/1LB",
    "SALMON, WILD, SOCKEYE, ALASKAN, FILLET, 5OZ, 20/CS",
    "CREAM, HEAVY, WHIPPING, 36%, 1QT, 12/CS",
    "CHEESE, PARMESAN, REGGIANO, AGED 24MO, WEDGE, 5LB, 2/CS",
    "LAMB, RACK, FRENCHED, NEW ZEALAND, 2LB, 8/CS",
    "TUNA, AHI, SASHIMI GRADE, #1, LOIN, 2.5LB, 4/CS",
    "OIL, OLIVE, EXTRA VIRGIN, 1GAL, 4/CS",
    "RICE, ARBORIO, ITALIAN, 12LB, 1/CS",
    "BUTTER, UNSALTED, AA GRADE, 1LB, 36/CS",
    "ASPARAGUS, FRESH, JUMBO, 11LB, 1/CS",
    "MUSHROOM, SHIITAKE, FRESH, 3LB, 2/CS",
    "SALT, KOSHER, 3LB, 12/CS",
    "OIL, CANOLA, PURE, 1GAL, 6/CS",
]

INGREDIENT_NAMES = [
    # Matched ingredients (expected match in catalog)
    "beef tenderloin filet",       # → BEEF, TENDERLOIN, FILET...
    "diver scallops",              # → SCALLOP, SEA, DIVER...
    "applewood smoked bacon",      # → BACON, SMOKED, APPLEWOOD...
    "wild salmon fillet",          # → SALMON, WILD, SOCKEYE...
    "heavy cream",                 # → CREAM, HEAVY, WHIPPING...
    "parmesan cheese",             # → CHEESE, PARMESAN, REGGIANO...
    "rack of lamb",                # → LAMB, RACK, FRENCHED...
    "ahi tuna",                    # → TUNA, AHI, SASHIMI GRADE...
    "olive oil",                   # → OIL, OLIVE, EXTRA VIRGIN...
    "arborio rice",                # → RICE, ARBORIO, ITALIAN...
    "unsalted butter",             # → BUTTER, UNSALTED, AA GRADE...
    "fresh asparagus",             # → ASPARAGUS, FRESH, JUMBO...
    "shiitake mushrooms",          # → MUSHROOM, SHIITAKE, FRESH...
    "kosher salt",                 # → SALT, KOSHER...
    # No-match ingredients (not in catalog — should score poorly)
    "truffle oil",                 # closest: OIL, OLIVE or OIL, CANOLA?
    "saffron",                     # no match
    "A5 wagyu beef",               # closest: BEEF, TENDERLOIN?
    "bourbon",                     # no match
    "yuzu juice",                  # no match
    "salt",                        # closest: SALT, KOSHER
]

# Expected best-match catalog index for each ingredient (None = no good match)
EXPECTED_CATALOG_IDX = {
    "beef tenderloin filet": 0,
    "diver scallops": 1,
    "applewood smoked bacon": 2,
    "wild salmon fillet": 3,
    "heavy cream": 4,
    "parmesan cheese": 5,
    "rack of lamb": 6,
    "ahi tuna": 7,
    "olive oil": 8,
    "arborio rice": 9,
    "unsalted butter": 10,
    "fresh asparagus": 11,
    "shiitake mushrooms": 12,
    "kosher salt": 13,
    "truffle oil": None,
    "saffron": None,
    "A5 wagyu beef": None,
    "bourbon": None,
    "yuzu juice": None,
    "salt": None,
}


# ── Embedding utilities ───────────────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def embed_batch(client, texts: list[str], model: str) -> list[list[float]]:
    """Embed a batch of texts, returning list of embedding vectors."""
    response = client.embeddings.create(
        model=model,
        input=texts,
    )
    # Sort by index to ensure order matches input
    embeddings = sorted(response.data, key=lambda e: e.index)
    return [e.embedding for e in embeddings]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print("=" * 70)
    print("  Embedding Format Mismatch Test: text-embedding-3-small")
    print("=" * 70)
    print()

    api_key, base_url, model = get_api_config()
    print(f"  Model:    {model}")
    print(f"  Base URL: {base_url}")
    print()

    from openai import OpenAI

    client = OpenAI(api_key=api_key, base_url=base_url)

    # ── Embed catalog descriptions ─────────────────────────────────────────
    print(f"Embedding {len(CATALOG_DESCRIPTIONS)} catalog descriptions...", end=" ", flush=True)
    catalog_embeddings = embed_batch(client, CATALOG_DESCRIPTIONS, model)
    print("done")

    # ── Embed ingredient names ─────────────────────────────────────────────
    print(f"Embedding {len(INGREDIENT_NAMES)} ingredient names...", end=" ", flush=True)
    ingredient_embeddings = embed_batch(client, INGREDIENT_NAMES, model)
    print("done")
    print()

    # ── Build similarity matrix ────────────────────────────────────────────
    # matrix[i][j] = similarity of ingredient i against catalog item j
    sim_matrix = []
    for ing_emb in ingredient_embeddings:
        row = [cosine_similarity(ing_emb, cat_emb) for cat_emb in catalog_embeddings]
        sim_matrix.append(row)

    # ── Per-ingredient results ─────────────────────────────────────────────
    print("=" * 70)
    print("  Per-Ingredient Top-3 Matches")
    print("=" * 70)

    best_scores = []        # score of rank-1 match for summary
    rank1_rank2_gaps = []   # rank1 - rank2 gap
    matched_correctly = 0
    total_with_expected = 0

    for i, ingredient in enumerate(INGREDIENT_NAMES):
        scores = sim_matrix[i]
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
        top3 = ranked[:3]

        expected_idx = EXPECTED_CATALOG_IDX.get(ingredient)
        rank1_score = top3[0][1]
        rank2_score = top3[1][1] if len(top3) > 1 else 0.0
        gap = rank1_score - rank2_score

        best_scores.append(rank1_score)
        rank1_rank2_gaps.append(gap)

        # Check if expected match is in top 3
        top3_indices = [idx for idx, _ in top3]
        in_top3 = expected_idx in top3_indices if expected_idx is not None else None

        if expected_idx is not None:
            total_with_expected += 1
            if in_top3:
                matched_correctly += 1

        # Format output
        status = ""
        if expected_idx is not None:
            status = "✓" if in_top3 else "✗"
        else:
            status = "~"  # no expected match

        print(f"\n  [{status}] Ingredient: \"{ingredient}\"")
        if expected_idx is not None:
            expected_correct = "✓" if in_top3 else "✗"
            print(f"      Expected: [{expected_idx}] \"{CATALOG_DESCRIPTIONS[expected_idx]}\"")

        for rank, (cat_idx, score) in enumerate(top3, 1):
            marker = ""
            if expected_idx is not None and cat_idx == expected_idx:
                marker = " ← EXPECTED"
            print(f"      #{rank}: [{cat_idx:2d}] {score:.4f}  \"{CATALOG_DESCRIPTIONS[cat_idx]}\"{marker}")

        print(f"      Gap (rank1 - rank2): {gap:.4f}")

    # ── Summary ───────────────────────────────────────────────────────────
    print()
    print("=" * 70)
    print("  Summary")
    print("=" * 70)
    print()

    print(f"  Correct top-3 matches: {matched_correctly}/{total_with_expected} ({100*matched_correctly/total_with_expected:.0f}%)")
    print()

    # Score distribution for best matches
    sorted_best = sorted(zip(INGREDIENT_NAMES, best_scores), key=lambda x: x[1], reverse=True)
    print("  Best match scores (rank-1), sorted:")
    for name, score in sorted_best:
        bar = "█" * int(score * 40)
        print(f"    {score:.4f} {bar}  \"{name}\"")

    print()
    print("  Score distribution:")
    scores_only = [s for _, s in sorted_best]
    print(f"    Max:    {max(scores_only):.4f}")
    print(f"    Min:    {min(scores_only):.4f}")
    print(f"    Mean:   {sum(scores_only)/len(scores_only):.4f}")
    print(f"    Median: {sorted(scores_only)[len(scores_only)//2]:.4f}")

    print()
    print("  Rank-1 vs Rank-2 gaps (higher = more confident match):")
    sorted_gaps = sorted(zip(INGREDIENT_NAMES, rank1_rank2_gaps), key=lambda x: x[1], reverse=True)
    for name, gap in sorted_gaps:
        bar = "█" * int(gap * 200)
        print(f"    {gap:.4f} {bar}  \"{name}\"")

    print()
    print("  Worst matches (lowest rank-1 score):")
    for name, score in sorted_best[-5:]:
        expected_idx = EXPECTED_CATALOG_IDX.get(name)
        label = "(no expected match)" if expected_idx is None else f"(expected: [{expected_idx}])"
        print(f"    {score:.4f}  \"{name}\" {label}")

    print()
    print("  Assessment:")
    avg_matched = sum(
        best_scores[i] for i, n in enumerate(INGREDIENT_NAMES)
        if EXPECTED_CATALOG_IDX.get(n) is not None
    ) / total_with_expected
    avg_unmatched = sum(
        best_scores[i] for i, n in enumerate(INGREDIENT_NAMES)
        if EXPECTED_CATALOG_IDX.get(n) is None
    ) / (len(INGREDIENT_NAMES) - total_with_expected)

    print(f"    Avg best-match score for ingredients WITH catalog entry:    {avg_matched:.4f}")
    print(f"    Avg best-match score for ingredients WITHOUT catalog entry: {avg_unmatched:.4f}")
    separation = avg_matched - avg_unmatched
    print(f"    Separation (matched - unmatched):                           {separation:.4f}")
    if separation > 0.05:
        print("    → GOOD: Embeddings clearly distinguish matched from unmatched items.")
    elif separation > 0.02:
        print("    → OK:   Some separation but may need a similarity threshold.")
    else:
        print("    → POOR: Little separation — embeddings may not handle this format gap well.")

    print()
    print("=" * 70)


if __name__ == "__main__":
    main()
