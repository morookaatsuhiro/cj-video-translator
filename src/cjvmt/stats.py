from math import comb, sqrt

import numpy as np


def mcnemar(b: int, c: int) -> float:
    n = b + c
    return min(1.0, 2 * (sum(comb(n, k) for k in range(min(b, c) + 1)) / 2**n))


def holm(values: list[float]) -> list[float]:
    adjusted = [0.0] * len(values)
    floor = 0.0
    for rank, index in enumerate(sorted(range(len(values)), key=values.__getitem__)):
        floor = max(floor, min(1.0, (len(values) - rank) * values[index]))
        adjusted[index] = floor
    return adjusted


def wilson(success: int, n: int, z: float = 1.95996398454) -> tuple[float, float] | None:
    if not n:
        return None
    ratio = success / n
    denominator = 1 + z * z / n
    center = (ratio + z * z / (2 * n)) / denominator
    half = z * sqrt(ratio * (1 - ratio) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, center - half), min(1.0, center + half)


def bootstrap(differences: list[float], *, iterations: int, seed: int) -> tuple[float, float]:
    values = np.array(differences)
    if not values.size:
        raise ValueError("配对 bootstrap 需要至少一个样本。")
    generator = np.random.default_rng(seed)
    means = np.array([generator.choice(values, len(values), replace=True).mean() for _ in range(iterations)])
    lower, upper = np.quantile(means, [0.025, 0.975])
    return float(lower), float(upper)
