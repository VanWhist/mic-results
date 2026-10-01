"""文字の対応表（ToUnicode）が無い・壊れている PDF を、フォントファイルの対応表で文字に戻す。

2022 全日本ジュニア・2022 五箇山・2014 NASPA の SAJ PDF は、HG丸ゴシックM-PRO の文字が字形の番号のまま入っていて、
pdfplumber では '(cid:4580)'、pdfium では chr(4580) のように読める。番号は同じフォント（Windows の HGRSMP.TTF）の
字形の番号と一致するので、フォントの cmap を逆に引けば文字に戻る（2026-09-30 に 5 大会の全ページで確認）。

どの PDF に使うかは推測しない。registry の pdfs の項目に "glyph_font": "HGRSMP" と書いたものだけに使う。
"""
import functools, os, re

FONT_FILES = {'HGRSMP': os.path.join(os.environ.get('WINDIR', r'C:\Windows'), 'Fonts', 'HGRSMP.TTF')}
CID = re.compile(r'^\(cid:(\d+)\)$')


@functools.lru_cache(maxsize=None)
def glyph_map(key):
    """字形の番号 → 文字。同じ字形に複数の文字が対応するときは番号の小さい文字"""
    from fontTools.ttLib import TTFont
    path = FONT_FILES[key]
    if not os.path.exists(path):
        raise FileNotFoundError(f"文字に戻すためのフォント {path} が無い（registry の glyph_font: {key}）")
    font = TTFont(path)
    gid = {g: i for i, g in enumerate(font.getGlyphOrder())}
    out = {}
    for u, g in sorted(font.getBestCmap().items()):
        out.setdefault(gid[g], chr(u))
    return out


def is_target_font(fontname):
    """HG丸ゴシックM-PRO（'PPFNGI+HGMaruGothicMPRO-90ms-RKSJ-H'、NASPA は Shift_JIS の名前 'HG丸ｺﾞｼｯｸM-PRO' が化けたもの）"""
    name = str(fontname)
    return 'MaruGothicMPRO' in name or ('HG' in name and 'M-PRO' in name)


def fix_page(page, key):
    """pdfplumber のページの文字を、その場で文字に戻す（dedupe_chars・extract_text より前に呼ぶ）。戻した文字数を返す"""
    if not key:
        return 0
    table = glyph_map(key)
    n = 0
    for c in page.chars:
        m = CID.match(c['text'])
        if m and is_target_font(c.get('fontname')) and int(m.group(1)) in table:
            c['text'] = table[int(m.group(1))]
            n += 1
    return n
