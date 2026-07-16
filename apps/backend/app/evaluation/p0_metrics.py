from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable


CITATION_PATTERN = re.compile(r"\[(\d+)]")
TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]", re.IGNORECASE)
MATCH_TOKEN_PATTERN = re.compile(r"[a-z0-9]+|[\u3400-\u9fff]+", re.IGNORECASE)
MONTH_NUMBERS = {
    "january": 1,
    "jan": 1,
    "february": 2,
    "feb": 2,
    "march": 3,
    "mar": 3,
    "april": 4,
    "apr": 4,
    "may": 5,
    "june": 6,
    "jun": 6,
    "july": 7,
    "jul": 7,
    "august": 8,
    "aug": 8,
    "september": 9,
    "sept": 9,
    "sep": 9,
    "october": 10,
    "oct": 10,
    "november": 11,
    "nov": 11,
    "december": 12,
    "dec": 12,
}
SPELLING_EQUIVALENTS = {
    "amphitheatre": "amphitheater",
    "theatre": "theater",
}


def answer_groups_match(prediction: str, ground_truth: object) -> bool:
    groups = ground_truth if isinstance(ground_truth, list) else [ground_truth]
    for group in groups:
        alternatives = group if isinstance(group, list) else [group]
        if not any(answer_value_matches(prediction, str(value)) for value in alternatives):
            return False
    return True


def official_rgb_answer_match(prediction: str, ground_truth: object) -> bool:
    normalized_prediction = prediction.casefold()
    groups = ground_truth if isinstance(ground_truth, list) else [ground_truth]
    for group in groups:
        alternatives = group if isinstance(group, list) else [group]
        if not any(str(value).casefold() in normalized_prediction for value in alternatives):
            return False
    return True


def answer_value_matches(prediction: str, reference: str) -> bool:
    prediction_tokens = normalized_match_tokens(prediction)
    reference_tokens = normalized_match_tokens(reference)
    if not reference_tokens:
        return not prediction_tokens
    if contains_token_sequence(prediction_tokens, reference_tokens):
        return True
    if contains_sequence_with_middle_initials(prediction_tokens, reference_tokens):
        return True
    reference_dates = extract_dates(reference_tokens)
    prediction_dates = extract_dates(prediction_tokens)
    return bool(reference_dates) and any(
        dates_equivalent(reference_date, prediction_date)
        for reference_date in reference_dates
        for prediction_date in prediction_dates
    )


def extract_citation_indices(text: str) -> list[int]:
    return dedupe_preserve_order(int(match.group(1)) for match in CITATION_PATTERN.finditer(text))


def citation_scores(
    prediction: str,
    positive_indices: set[int],
    document_count: int,
    positive_groups: list[set[int]] | None = None,
) -> dict[str, float | int]:
    citations = extract_citation_indices(prediction)
    valid = [index for index in citations if 1 <= index <= document_count]
    supported = [index for index in valid if index in positive_indices]
    required_groups = positive_groups if positive_groups is not None else ([positive_indices] if positive_indices else [])
    cited_indices = set(valid)
    covered_groups = sum(bool(group & cited_indices) for group in required_groups)
    return {
        "citation_count": len(citations),
        "invalid_citation_count": len(citations) - len(valid),
        "citation_precision": len(supported) / len(valid) if valid else 0.0,
        "citation_coverage": covered_groups / len(required_groups) if required_groups else 0.0,
        "cited_positive_group_count": covered_groups,
        "required_positive_group_count": len(required_groups),
    }


def normalized_token_f1(prediction: str, reference: str) -> float:
    predicted_tokens = normalized_tokens(prediction)
    reference_tokens = normalized_tokens(reference)
    if not predicted_tokens or not reference_tokens:
        return 1.0 if predicted_tokens == reference_tokens else 0.0
    remaining = list(reference_tokens)
    overlap = 0
    for token in predicted_tokens:
        if token in remaining:
            remaining.remove(token)
            overlap += 1
    if overlap == 0:
        return 0.0
    precision = overlap / len(predicted_tokens)
    recall = overlap / len(reference_tokens)
    return 2 * precision * recall / (precision + recall)


def retrieval_recall(selected_ids: Iterable[str], relevant_ids: Iterable[str]) -> float:
    selected = set(selected_ids)
    relevant = set(relevant_ids)
    if not relevant:
        return 0.0
    return len(selected & relevant) / len(relevant)


def retrieval_any(selected_ids: Iterable[str], relevant_ids: Iterable[str]) -> bool:
    return bool(set(selected_ids) & set(relevant_ids))


def retrieval_all(selected_ids: Iterable[str], relevant_ids: Iterable[str]) -> bool:
    selected = set(selected_ids)
    relevant = set(relevant_ids)
    return bool(relevant) and relevant.issubset(selected)


def binary_ndcg_at_k(ranked_ids: Iterable[str], relevant_ids: Iterable[str], k: int) -> float:
    ranked = list(ranked_ids)[: max(0, k)]
    relevant = set(relevant_ids)
    if not relevant or not ranked:
        return 0.0
    gains = [1.0 if item in relevant else 0.0 for item in ranked]
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal_size = min(len(relevant), len(ranked))
    ideal_dcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_size))
    return dcg / ideal_dcg if ideal_dcg else 0.0


def official_longmemeval_ndcg_at_k(ranked_ids: Iterable[str], relevant_ids: Iterable[str], k: int) -> float:
    ranked = list(ranked_ids)[: max(0, k)]
    relevant = set(relevant_ids)
    if not relevant or not ranked:
        return 0.0
    gains = [1.0 if item in relevant else 0.0 for item in ranked]
    ideal = [1.0] * min(len(relevant), len(ranked)) + [0.0] * max(0, len(ranked) - len(relevant))
    actual_dcg = longmemeval_discounted_gain(gains)
    ideal_dcg = longmemeval_discounted_gain(ideal)
    return actual_dcg / ideal_dcg if ideal_dcg else 0.0


def longmemeval_discounted_gain(gains: list[float]) -> float:
    if not gains:
        return 0.0
    return gains[0] + sum(
        gain / math.log2(index + 1)
        for index, gain in enumerate(gains[1:], start=1)
    )


def normalize_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def normalized_match_tokens(value: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", value).casefold()
    without_marks = "".join(character for character in decomposed if not unicodedata.combining(character))
    return [SPELLING_EQUIVALENTS.get(token, token) for token in MATCH_TOKEN_PATTERN.findall(without_marks)]


def contains_token_sequence(tokens: list[str], expected: list[str]) -> bool:
    size = len(expected)
    return any(tokens[index : index + size] == expected for index in range(len(tokens) - size + 1))


def contains_sequence_with_middle_initials(tokens: list[str], expected: list[str]) -> bool:
    if len(expected) < 2:
        return False
    for start, token in enumerate(tokens):
        if token != expected[0]:
            continue
        token_index = start + 1
        expected_index = 1
        while token_index < len(tokens) and expected_index < len(expected):
            if tokens[token_index] == expected[expected_index]:
                expected_index += 1
            elif len(tokens[token_index]) != 1:
                break
            token_index += 1
        if expected_index == len(expected):
            return True
    return False


def extract_dates(tokens: list[str]) -> list[tuple[int, int, int | None]]:
    dates: list[tuple[int, int, int | None]] = []
    for index, token in enumerate(tokens):
        month = MONTH_NUMBERS.get(token)
        if month is not None and index + 1 < len(tokens) and tokens[index + 1].isdigit():
            day = int(tokens[index + 1])
            year = date_year(tokens, index + 2)
            if 1 <= day <= 31:
                dates.append((month, day, year))
        if token.isdigit() and index + 1 < len(tokens):
            month = MONTH_NUMBERS.get(tokens[index + 1])
            day = int(token)
            year = date_year(tokens, index + 2)
            if month is not None and 1 <= day <= 31:
                dates.append((month, day, year))
    return dates


def date_year(tokens: list[str], index: int) -> int | None:
    if index < len(tokens) and tokens[index].isdigit() and len(tokens[index]) == 4:
        return int(tokens[index])
    return None


def dates_equivalent(left: tuple[int, int, int | None], right: tuple[int, int, int | None]) -> bool:
    if left[:2] != right[:2]:
        return False
    return left[2] is None or right[2] is None or left[2] == right[2]


def normalized_tokens(value: str) -> list[str]:
    return TOKEN_PATTERN.findall(normalize_text(value))


def dedupe_preserve_order(values: Iterable[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
