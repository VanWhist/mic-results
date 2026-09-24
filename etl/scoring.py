"""Moguls scoring recomputation with exact decimal arithmetic.

FIS rules verified on every W杯 PDF on file (moguls-results, 2026-09-02):
  ICR 4008   all published scores truncated (not rounded) to 2 decimals
  JH 6203.1  5 turns judges: high and low discarded separately for Base and Deductions
  JH 6204.1  turns minimum 0.1 per judge -> 0.3 floor on the counting total
  JH 6203.1.2 / 6204.3  air: per judge min(cap, trunc2(score x DD)), mean of the 2 judges, sum of jumps, trunc2
  ICR 4206.3 time points = a - b x seconds / pace_time (48/32), clamped to 0..20, trunc2
  ICR 4207.3 tie-break: turns total, then air without DD, then faster time

SAJ 国内大会は規則を大会ごとに確定する（rules dict の値が違うだけで式は同じ）:
  discard_high_low=False（3人制）のときは全員の合計。deductions は常に負の数で持つ。
"""
from decimal import Decimal, ROUND_DOWN

TWO = Decimal('0.01')


def D(x):
    return x if isinstance(x, Decimal) else Decimal(str(x))


def trunc(x, places=2):
    q = Decimal(1).scaleb(-places)
    return D(x).quantize(q, rounding=ROUND_DOWN)


def discard_high_low(values):
    """Return (total, discarded_indices). One highest and one lowest value are removed
    (first occurrence of each when tied); the remaining values are summed."""
    vals = [D(v) for v in values]
    hi = max(range(len(vals)), key=lambda i: vals[i])
    lo = min((i for i in range(len(vals)) if i != hi), key=lambda i: vals[i])
    total = sum(v for i, v in enumerate(vals) if i not in (hi, lo))
    return total, sorted([hi, lo])


def counting_total(values, rules):
    """Sum of the counting judges: high/low discarded when the panel rules say so and there are 5+ judges."""
    if rules.get('discard_high_low', True) and len(values) >= 5:
        return discard_high_low(values)
    return sum((D(v) for v in values), Decimal(0)), []


def air_components(j6, j7, dd, rules):
    cap = D(rules['air_cap_per_judge'])
    v6 = min(cap, trunc(D(j6) * D(dd)))
    v7 = min(cap, trunc(D(j7) * D(dd)))
    return v6, v7, (v6 + v7) / 2


def air_total(jumps, rules):
    """jumps: list of {J6, J7, DD}. Returns (total, per-jump list of (v6, v7, mean))."""
    parts = [air_components(j['J6'], j['J7'], j['DD'], rules) for j in jumps]
    total = trunc(sum((p[2] for p in parts), Decimal(0)))
    return total, parts


def air_without_dd(jumps):
    return sum(((D(j['J6']) + D(j['J7'])) / 2 for j in jumps), Decimal(0))


def time_points(seconds, pace_time, rules):
    a, b = D(rules.get('time_a', 48)), D(rules.get('time_b', 32))
    v = a - b * D(seconds) / D(pace_time)
    v = max(D(rules['time_min']), min(D(rules['time_max']), v))
    return trunc(v)


def turns_total(base_total, ded_total, rules):
    raw = D(base_total) + D(ded_total)
    floor = D(rules['turns_floor'])
    return (floor if raw < floor else raw), raw < floor


def recompute(rec, pace_time, rules):
    """Recompute every derived value of an OK record. Returns dict of Decimals."""
    out = {}
    bt, b_disc = counting_total(rec['base_scores'], rules)
    dt, d_disc = counting_total(rec['ded_scores'], rules)
    out['base_total'], out['base_discard'] = bt, b_disc
    out['ded_total'], out['ded_discard'] = dt, d_disc
    tt, floored = turns_total(bt, dt, rules)
    out['turns_total'], out['turns_floor_applied'] = tt, floored
    at, parts = air_total(rec['air_jumps'], rules)
    out['air_total'], out['air_parts'] = at, parts
    out['air_without_dd'] = air_without_dd(rec['air_jumps'])
    out['time_points'] = time_points(rec['seconds'], pace_time, rules) if pace_time else None
    tp = out['time_points'] if out['time_points'] is not None else D(rec['time_points'])
    out['run_score'] = tp + at + tt
    return out


def rank_order(records, rules):
    """Sort OK records per ICR 4207.3 and assign ranks (ties share the better rank).
    records need: run_score, turns_total, air_without_dd, seconds (Decimals)."""
    tb = rules.get('tie_break')
    if tb is None:
        tb = ['turns_total', 'air_without_dd', 'seconds_asc']

    def key(r):
        k = [-r['run_score']]
        for t in tb:
            if t == 'turns_total':
                k.append(-r['turns_total'])
            elif t == 'air_without_dd':
                k.append(-r['air_without_dd'])
            elif t == 'seconds_asc':
                k.append(r['seconds'])
        return tuple(k)
    ordered = sorted(records, key=key)
    ranks = []
    prev_key, prev_rank = None, 0
    for i, r in enumerate(ordered, start=1):
        k = key(r)
        rank = prev_rank if k == prev_key else i
        ranks.append((r, rank))
        prev_key, prev_rank = k, rank
    return ranks
