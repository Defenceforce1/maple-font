"""Personal Chinese/Latin profile: no coding ligatures or Japanese/Nerd glyphs."""
import unicodedata
from fontTools.subset import Options, Subsetter

CODING_FEATURES = {
    "calt", "liga", "dlig", "hlig", "rlig",
    "ss01", "ss02", "ss03", "ss04", "ss07", "ss08", "ss09", "ss10", "ss11",
}

def excluded_codepoint(cp):
    if (
        0x3040 <= cp <= 0x30FF
        or 0x31F0 <= cp <= 0x31FF
        or 0x3190 <= cp <= 0x319F
        or 0x1AFF0 <= cp <= 0x1AFFF
        or 0x1B000 <= cp <= 0x1B16F
        or 0xFF65 <= cp <= 0xFF9F
        or 0x3031 <= cp <= 0x3035
        or cp == 0x303B
        or 0xE000 <= cp <= 0xF8FF
        or 0xF0000 <= cp <= 0xFFFFD
        or 0x100000 <= cp <= 0x10FFFD
        or 0xFB00 <= cp <= 0xFB06
    ):
        return True
    name = unicodedata.name(chr(cp), "")
    return any(term in name for term in ("HIRAGANA", "KATAKANA", "HANGUL"))

def ligature_subtables(font):
    if "GSUB" not in font:
        return
    for lookup in font["GSUB"].table.LookupList.Lookup:
        for sub in lookup.SubTable:
            if lookup.LookupType == 4:
                yield sub
            elif lookup.LookupType == 7 and sub.ExtensionLookupType == 4:
                yield sub.ExtSubTable

def is_mark_composition(font, first, ligature):
    cmap = font.getBestCmap()
    mark_glyphs = {
        glyph for cp, glyph in cmap.items()
        if unicodedata.category(chr(cp)).startswith("M")
    }
    # Glyph names may change after saving a format-3 post table. Identify
    # unencoded mark variants by GDEF/GSUB relationships as well as names.
    mark_glyphs |= {g + ".case" for g in mark_glyphs}
    if "GDEF" in font and font["GDEF"].table.GlyphClassDef:
        mark_glyphs |= {
            g for g, cls in font["GDEF"].table.GlyphClassDef.classDefs.items()
            if cls == 3
        }
    if "GSUB" in font:
        mappings = []
        for lookup in font["GSUB"].table.LookupList.Lookup:
            for sub in lookup.SubTable:
                typ = lookup.LookupType
                if typ == 7:
                    typ, sub = sub.ExtensionLookupType, sub.ExtSubTable
                if typ == 1:
                    mappings.append(sub.mapping)
        while True:
            extra = {
                target for mapping in mappings for source, target in mapping.items()
                if source in mark_glyphs
            }
            if extra <= mark_glyphs:
                break
            mark_glyphs |= extra
    return all(g in mark_glyphs for g in [first, *ligature.Component])

def audit_font(font):
    cmap = font.getBestCmap()
    kana = sum(
        0x3040 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF
        or 0x1AFF0 <= cp <= 0x1B16F or 0xFF65 <= cp <= 0xFF9F
        for cp in cmap
    )
    private = sum(
        0xE000 <= cp <= 0xF8FF or 0xF0000 <= cp <= 0xFFFFD
        or 0x100000 <= cp <= 0x10FFFD for cp in cmap
    )
    nonmark_ligatures = sum(
        not is_mark_composition(font, first, lig)
        for sub in ligature_subtables(font)
        for first, ligatures in sub.ligatures.items() for lig in ligatures
    )
    active = {}
    if "GSUB" in font:
        for record in font["GSUB"].table.FeatureList.FeatureRecord:
            if record.FeatureTag in CODING_FEATURES and record.Feature.LookupCount:
                active[record.FeatureTag] = record.Feature.LookupCount
    return dict(kana=kana, private_use=private,
                nonmark_ligatures=nonmark_ligatures, coding_features=active)

def apply_chinese_only_profile(font):
    # The requested cv/ss05/ss06 shapes have already been frozen into outlines.
    # Keep normal accent/mark processing; remove letter/symbol ligatures.
    for sub in ligature_subtables(font):
        sub.ligatures = {
            first: [lig for lig in ligs if is_mark_composition(font, first, lig)]
            for first, ligs in sub.ligatures.items()
        }
        sub.ligatures = {first: ligs for first, ligs in sub.ligatures.items() if ligs}
    for tag in ("GSUB", "GPOS"):
        if tag not in font or not font[tag].table.ScriptList:
            continue
        scripts = font[tag].table.ScriptList
        scripts.ScriptRecord = [
            rec for rec in scripts.ScriptRecord if rec.ScriptTag not in ("kana", "hang")
        ]
        scripts.ScriptCount = len(scripts.ScriptRecord)
        for rec in scripts.ScriptRecord:
            rec.Script.LangSysRecord = [
                lang for lang in rec.Script.LangSysRecord
                if lang.LangSysTag.strip() not in ("JAN", "KOR")
            ]
            rec.Script.LangSysCount = len(rec.Script.LangSysRecord)
    opts = Options()
    opts.glyph_names = True
    opts.no_subset_tables += ["meta"]
    opts.name_IDs = ["*"]
    opts.name_languages = ["*"]
    opts.name_legacy = True
    opts.notdef_outline = True
    opts.recalc_bounds = True
    opts.recalc_average_width = False
    opts.layout_features = sorted({
        rec.FeatureTag for rec in font["GSUB"].table.FeatureList.FeatureRecord
        if rec.FeatureTag not in CODING_FEATURES
    }) if "GSUB" in font else []
    keep = [cp for cp in font.getBestCmap() if not excluded_codepoint(cp)]
    subset = Subsetter(options=opts)
    subset.populate(unicodes=keep)
    subset.subset(font)
    if "meta" in font:
        for key in ("dlng", "slng"):
            font["meta"].data[key] = "Latn, Hans, Hant"
    if "OS/2" in font:
        font["OS/2"].ulCodePageRange1 &= ~((1 << 17) | (1 << 19) | (1 << 21))
    # Keep the 550-unit Latin metric, because recalculation over many CJK
    # glyphs would otherwise change the family metric.
    font["OS/2"].xAvgCharWidth = 550
    result = audit_font(font)
    assert result["kana"] == 0 and result["private_use"] == 0, result
    assert result["nonmark_ligatures"] == 0 and not result["coding_features"], result
    print("Chinese-only profile:", result)
