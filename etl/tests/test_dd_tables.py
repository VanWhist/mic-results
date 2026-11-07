"""DD 表の規則ファイルの検査。

    python -m etl.tests.test_dd_tables

1. rulesets.json の各版が参照する dd_ ファイルがあり、読み込めること。
2. どの DD 表も女子 = 男子 + 0.10 であること（FIS の表の作り）。
3. dd_2026-10 の各コードが、FIS の表の作り方（基本値・追加回転・g/G・グラブの回転加算）で計算した値と一致すること。
4. 第2層の照合が g / G を区別すること（7og 0.92 は一致、旧版の 7G 1.01 は 2026-27 では警告）。
5. dd_2026-10 のコードが FIS の表の 58 コードと一致すること。
6. 低いほうの DD の照合（etl/dd_rule.py）が組み合わせごとに正しいこと。
7. 2026-27 だけが新しい DD 表で、規則のほかの値は 2025-26 と同じこと。
"""
import collections
import json
import os
import sys
from decimal import Decimal

sys.stdout.reconfigure(encoding='utf-8')
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from etl import config, dd_rule, verify

RULESETS = json.load(open(os.path.join(config.RULES_DIR, 'rulesets.json'), encoding='utf-8'))


def load_dd(name):
    return json.load(open(os.path.join(config.RULES_DIR, name + '.json'), encoding='utf-8'))


def expected_2026(code):
    """FIS 2026年10月版の作り方で男子の DD を計算する（表の注記と、表の値どうしの関係から）。"""
    D = Decimal
    upright = {'S': D('-0.02'), 'T': D('-0.02'), 'D': D('0.01'), 'X': D('0.01'), 'Y': D('0.01'),
               'M': D('0.01'), 'K': D('0.01'), 'Z': D('0.00')}
    if code in upright:
        return D('0.40') + upright[code]
    base, rot_add, rot_twist = D('0.68'), D('0.17'), D('0.19')
    mod = {'p': D('0.03'), 'g': D('0.05'), 'G': D('0.12'), '': D('0')}
    gfac = D('0.02')
    rotations = {'3': 0, '7': 1, '10': 2, '14': 3}
    for prefix in sorted(rotations, key=len, reverse=True):
        if code.startswith(prefix):
            rest = code[len(prefix):]
            n = rotations[prefix]
            if rest.startswith('o'):
                rest = rest[1:]
            m = rest
            extra = gfac * n if m in ('g', 'G') else D('0')
            return base + rot_add * n + mod[m] + extra
    inverted = {'bP': D('0'), 'bT': D('0'), 'fT': D('0'), 'fP': D('0'), 'bL': D('0.03'),
                'bp': mod['p'], 'bg': mod['g'], 'bG': mod['G'], 'fp': mod['p'], 'fg': mod['g'], 'fG': mod['G'],
                'bF': rot_twist + D('0.03'), 'fF': rot_twist + D('0.03'),
                'bdF': rot_twist * 2 + D('0.03'), 'fdF': rot_twist * 2 + D('0.03'),
                'btF': rot_twist * 3 + D('0.03'), 'ftF': rot_twist * 3 + D('0.03')}
    if code in inverted:
        return base + inverted[code]
    loops = {'l': D('0'), 'lp': mod['p'], 'lg': mod['g'], 'lG': mod['G'],
             'lF': rot_add, 'lpF': rot_add + mod['p'], 'lgF': rot_add + mod['g'] + gfac, 'lGF': rot_add + mod['G'] + gfac}
    if code in loops:
        return base + loops[code]
    raise KeyError(code)


def fake_run(name, jump, dd):
    return {'status': 'OK', 'name': name, 'season': '2026-27', 'series': 'WC', 'q_block': None, 'counting': True, 'best_score': None,
            '_printed': {}, '_recomputed': {'time_points': None, 'air_total': None, 'base_total': None,
                                            'ded_total': None, 'turns_total': None, 'run_score': None},
            'air': [{'jump': jump, 'dd': dd}]}


def main():
    failures = []

    # 1. referenced tables exist
    tables = {}
    for season, v in RULESETS['versions'].items():
        name = v.get('dd_table')
        if not name:
            continue
        try:
            tables[name] = load_dd(name)
        except FileNotFoundError:
            failures.append(f"{season}: {name}.json が無い")
    if RULESETS['versions'].get('2026-27', {}).get('dd_table') != 'dd_2026-10':
        failures.append("2026-27 が dd_2026-10 を参照していない")

    # 2. women = men + 0.10
    for name, t in tables.items():
        for code, m in t['M'].items():
            w = t['W'].get(code)
            if w is None or Decimal(str(w)) - Decimal(str(m)) != Decimal('0.10'):
                failures.append(f"{name} {code}: 女子 {w} が 男子 {m} + 0.10 でない")
        if set(t['M']) != set(t['W']):
            failures.append(f"{name}: 男子と女子のコードの集合が違う")

    # 3. dd_2026-10 matches FIS construction
    t = tables.get('dd_2026-10')
    if t:
        for code, m in t['M'].items():
            exp = expected_2026(code)
            if Decimal(str(m)) != exp:
                failures.append(f"dd_2026-10 {code}: 表 {m} / 計算 {exp}")
        for must in ('3g', '7g', '10g', '7og', '10og', '14og', 'bg', 'fg', 'lg', 'lgF', '14o', 'fdF', 'ftF'):
            if must not in t['M']:
                failures.append(f"dd_2026-10 に {must} が無い")

        # 4. layer2 distinguishes g and G
        runs = [fake_run('A', '7og', 0.92), fake_run('B', '7oG', 0.99), fake_run('C', '7G', 1.01), fake_run('D', 'bg', 0.73)]
        found = verify.layer2('test-round', runs, t, 'M', collections.defaultdict(set))
        msgs = [f.message if hasattr(f, 'message') else str(f) for f in found]
        flagged = {n for n in 'ABCD' if any(f"{n} ジャンプ" in s for s in msgs)}
        if flagged != {'C'}:
            failures.append(f"第2層の照合: 警告が出た選手 {sorted(flagged)}（期待は C だけ）")

        # 5. FIS の表の 58 コードと一致する（コードが欠けると照合が黙って飛ぶ）
        codes = ('S T D X Y M K Z 3 3p 3g 3G 7 7p 7g 7G 10 10p 10g 10G 3op 3og 3oG 7op 7og 7oG 10op 10og 10oG 14o 14op 14og 14oG '
                 'bP bT bL bp bg bG bF bdF btF fT fP fp fg fG fF fdF ftF l lp lg lG lF lpF lgF lGF').split()
        if set(t['M']) != set(codes):
            failures.append(f"dd_2026-10 のコードが FIS の表と違う: {sorted(set(t['M']) ^ set(codes))}")

        # 6. 低いほうの DD の規則（ICR 4210.2.2）
        def pair_msgs(a, dd_a, b, dd_b):
            return dd_rule.dd_messages({'name': 'A', 'air': [{'jump': a, 'dd': dd_a}, {'jump': b, 'dd': dd_b}]}, t['M'])
        cases = [('bT', 0.68, 'bG', 0.68, 0), ('bp', 0.71, 'bG', 0.71, 0), ('7op', 0.88, '7oG', 0.88, 0), ('bT', 0.68, 'bg', 0.68, 0),
                 ('bF', 0.90, 'bG', 0.80, 0), ('10op', 1.05, '7oG', 0.99, 0), ('S', 0.38, 'bG', 0.80, 0), ('fT', 0.68, 'bG', 0.80, 0),
                 ('bT', 0.68, 'bG', 0.80, 1), ('fT', 0.68, 'bG', 0.68, 1), ('7op', 0.88, 'bG', 0.71, 1), ('7oG', 1.20, 'bp', 0.71, 1),
                 # 宙返りの種類（bL・bP）、軸（o）、回転数、ループ、両方グラブ、グラブ付きのループのフル
                 ('bL', 0.71, 'bG', 0.71, 0), ('bP', 0.68, 'bg', 0.68, 0), ('7p', 0.88, '7oG', 0.99, 0), ('7oG', 0.99, '10op', 1.05, 0),
                 ('lp', 0.71, 'lG', 0.71, 0), ('lp', 0.71, 'lG', 0.80, 1), ('lF', 0.85, 'lGF', 0.85, 0), ('lF', 0.85, 'lgF', 0.92, 1),
                 ('bg', 0.73, 'bG', 0.80, 0), ('bg', 0.73, 'bG', 0.73, 1), ('NJ', 0.0, 'bG', 0.80, 0)]
        for a, dd_a, b, dd_b, want in cases:
            got = pair_msgs(a, dd_a, b, dd_b)
            if len(got) != want:
                failures.append(f"低いほうの DD の照合 {a} {dd_a} + {b} {dd_b}: 警告 {len(got)} 件（期待 {want}）: {got}")
        women = dd_rule.dd_messages({'name': 'W', 'air': [{'jump': 'bT', 'dd': 0.78}, {'jump': 'bG', 'dd': 0.78}]}, t['W'])
        if women:
            failures.append(f"女子の表で低いほうの DD が通らない: {women}")
        if dd_rule.dd_messages({'name': 'X', 'air': [{'jump': None, 'dd': None}, {'jump': 'bG', 'dd': 0.80}]}, t['M']):
            failures.append("空のジャンプと組で警告が出た")

    # 7. 2026-27 だけが新しい DD 表で、規則のほかの値は 2025-26 と同じ
    v26, v27 = RULESETS['versions'].get('2025-26', {}), RULESETS['versions'].get('2026-27', {})
    if v26.get('dd_table') != 'dd_2023-11':
        failures.append(f"2025-26 の DD 表が {v26.get('dd_table')}")
    if v27.get('dd_table') not in dd_rule.LOWER_DD_TABLES:
        failures.append("2026-27 に低いほうの DD の照合が使われない")
    diff = sorted(k for k in set(v26) | set(v27) if v26.get(k) != v27.get(k) and k not in ('dd_table', 'source'))
    if diff:
        failures.append(f"2026-27 と 2025-26 で DD 表のほかに違う値: {diff}")

    print(f"DD 表 {len(tables)} 件（{', '.join(sorted(tables))}）")
    if failures:
        print(f"失敗 {len(failures)} 件")
        for f in failures:
            print('  ' + f)
        return 1
    print("すべて合格")
    return 0


if __name__ == '__main__':
    sys.exit(main())
