"""pdfminer の CMap 'UniJIS-UCS2-HW-H'（-V）に、本来の親 'UniJIS-UCS2-H'（-V）を下敷きとして足す。

Adobe の CMap 'UniJIS-UCS2-HW-H' は半角にする文字だけを定義し、残りは usecmap で 'UniJIS-UCS2-H' を引き継ぐ。
pdfminer の同梱データ（cmap/UniJIS-UCS2-HW-H.json.gz）は半角の部分だけで親を引き継がないので、この CMap の和文フォント
（MS 明朝・MS ゴシック）の漢字・かなは対応する CID が無いとして黙って落ちる。2012 埼玉県松之山（2051-0001・0002）は
氏名・クラブ名・見出しが全部消え、表を 1 つも読めなかった（pdfium では読める）。

同梱の CMap にこの名前を使う PDF は 8 本（2012 松之山 4・2012 松之山国体記念 2・2013 FIS 2033-0018 2。2026-10-08 に
registry の全 PDF 1,223 本のフォントの Encoding を調べた）。import するだけで効く（CMapDB のキャッシュの CMap を差し替える）。
"""
from pdfminer.cmapdb import CMapDB

USECMAP = {'UniJIS-UCS2-HW-H': 'UniJIS-UCS2-H', 'UniJIS-UCS2-HW-V': 'UniJIS-UCS2-V'}


def _merged(base, over):
    """親の code2cid（入れ子の dict）に、子の定義を上から重ねた新しい dict"""
    out = {k: (_merged(v, {}) if isinstance(v, dict) else v) for k, v in base.items()}
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merged(out[k], v)
        else:
            out[k] = v
    return out


def install():
    for child, parent in USECMAP.items():
        cmap = CMapDB.get_cmap(child)
        if not getattr(cmap, 'usecmap_installed', False):
            cmap.code2cid = _merged(CMapDB.get_cmap(parent).code2cid, cmap.code2cid)
            cmap.usecmap_installed = True


install()
