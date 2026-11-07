"""印字の DD と DD 表の照合（低いほうの DD の規則を含む。moguls-results の etl/dd_rule.py と同じ内容）。

ICR 4210.2.1・4210.2.2: 同じカテゴリの 2 本で片方だけグラブなら、低いほうの DD を両方に使う。
カテゴリはグラブを除いた形で決める。回転は回転数（3・7・10）と軸（o）ごと、宙返りは向き（b・f）ごと（ひねりのあるものは別）、
ループはひねりの有無ごと。ストレートジャンプ（アップライト）は対象外。
文書の "same category" に定義はない（6204.3.8 と 4210.2.1 は前後を同じ "this category" の中に書き、向きの違いを別のジャンプと認める例外としている）。
ティナさん（JAG）の回答（2026-11-08 に城さんへ、メール）: bG＋bp は bG が bp の DD になる。bG＋fT はカテゴリが違うので、bF＋bG は捻り数が違うので DD の変更なし。
lG＋lF はこの回答に含まれない。城さんの確認（2026-11-07）に合わせて下げない。
2023-24〜2025-26 と ANC Perisher 2026 の印字はこの規則で DD を下げていない（bp＋bG も bG は表のまま）。下げているのは
Hintertux 2026 の FIS Open から（新しい DD 表と同時）。それで照合は新しい DD 表のラウンドだけに使う。
"""
import re
from decimal import Decimal

LOWER_DD_TABLES = ('dd_2026-10',)  # 低いほうの DD の照合を使う DD 表


def category(code):
    """同じカテゴリかを比べる値。低いほうの DD の対象でないコードは None"""
    m = re.fullmatch(r'(\d+)(o?)[pgG]?', code)
    if m:
        return ('rotation', m.group(1), m.group(2))
    m = re.fullmatch(r'([bf])[TPLpgG]', code)
    if m:
        return ('flip', m.group(1))
    m = re.fullmatch(r'l[pgG]?(F?)', code)
    if m:
        return ('loop', m.group(1))
    return None


def has_grab(code):
    return bool(re.search(r'[gG]F?$', code))


def expected_dd(code, others, table):
    """(そのジャンプの DD, 低いほうの適用の相手のコード)。表に無いコードは (None, None)"""
    if code not in table:
        return None, None
    dd, mate = table[code], None
    for other in others:
        if other not in table or has_grab(code) == has_grab(other):
            continue
        if category(code) is not None and category(code) == category(other) and table[other] < dd:
            dd, mate = table[other], other
    return dd, mate


def dd_messages(run, table):
    """1 本の走りの、印字の DD が説明できないジャンプの文（無ければ空）。表は性別ごとの {コード: DD}"""
    out = []
    air = [j for j in run.get('air') or [] if j.get('jump') and j.get('dd') not in (None, '')]
    for i, j in enumerate(air):
        others = [x['jump'] for k, x in enumerate(air) if k != i]
        want, mate = expected_dd(j['jump'], others, table)
        if want is None or Decimal(str(j['dd'])) == Decimal(str(want)):
            continue
        if mate:
            why = f"{mate} と同じカテゴリで片方だけグラブなので低いほうの DD {want} のはず（ICR 4210.2.2）"
        else:
            why = f"DD 表は {want}（低いほうの DD の適用でもない）"
        out.append(f"{run.get('name')} ジャンプ {j['jump']} の DD {j['dd']}: {why}")
    return out
