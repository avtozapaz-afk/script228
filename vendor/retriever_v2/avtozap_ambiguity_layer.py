# AVTOZAP — слой многозначности (варианты А и Б)
# Ставится ПЕРЕД окончательным выбором part_id.
#
# Б (фундамент): опасное общее слово не может дать single_match, если оно
#    не покрывает почти весь запрос и не подтверждено контекстом или нейронкой.
# А (быстрый слой): для самых частых опасных слов — контекстные правила
#    "слово + уточнитель -> конкретная деталь".

import os
import re, json
from collections import defaultdict

AZ = str.maketrans({
    "ş": "s", "ğ": "g", "ı": "i", "ə": "e", "ç": "c", "ö": "o", "ü": "u",
    "Ş": "s", "Ğ": "g", "İ": "i", "Ə": "e", "Ç": "c", "Ö": "o", "Ü": "u",
})


def na(s):
    """нормализация: нижний регистр, снятие азербайджанской диакритики, только слова"""
    s = str(s or "").replace("İ", "i").replace("I", "ı").lower().translate(AZ)
    return " ".join(re.findall(r"[0-9a-zа-яё]+", s))


# ------------------------------------------------------------------ вариант А
# Контекстные правила для самых частых опасных слов.
# Читается так: если в запросе есть основа слева и любой из уточнителей —
# ответ справа, а общее слово молчит.
# Уточнители — основы слов, сравнение идёт по началу слова.

CONTEXT_RULES = [
    # mühərrik
    ("muherrik", ["yasti", "yastq", "podushk", "podusk", "padus", "opor"], "MU-017"),   # подушка двигателя
    ("muherrik", ["alt", "qoruyucu", "zasitnik", "zashitnik"], "MU-053"),  # защита двигателя
    ("muherrik", ["karter"], "MU-009"),
    ("mator",    ["yasti", "yastq", "padus", "podus"], "MU-017"),
    ("mator",    ["alt", "qoruyucu", "zasitnik"], "MU-053"),

    # fara
    ("fara", ["arxa"], "KZ-007"),                       # задний фонарь
    ("fara", ["duman", "dumanli", "tuman"], "KZ-006"),  # противотуманная
    # ПРОВЕРЕНО на живых заявках (217, 298, A17, A66, 42): "fara + lupa" —
    # это фара с линзой, а не линза отдельно. Линза запрашивается только
    # словом "linza" без слова "fara".
    ("fara", ["lupa", "lupali", "lupalı", "lupa ile", "linzali", "qos lupa", "2 lupa"], "KZ-005"),
    ("fara", ["suse", "sise", "steklo"], "KZ-010"),     # стекло фары
    ("fara", ["salask", "salack"], "KZ-002"),           # салазка/кронштейн

    # qapı
    ("qapi", ["kilid", "zamok"], "KZ-017"),
    ("qapi", ["qulp", "rucka"], "KZ-016"),
    ("qapi", ["suse", "sise"], "KZ-013"),
    # Уплотнитель проёма (в кузове) отличать от уплотнителя самой двери:
    # решают слова "yer", "yuva", "proyem" рядом.
    ("qapi", ["yerinin rezin", "yeri rezin", "yuvasinin rezin", "proyem"], "KZ-098"),
    ("qapi", ["rezin", "uplotnit"], "KZ-018"),
    ("qapi", ["petle", "menteşe", "mentese"], "KZ-041"),
    ("qapi", ["dinamik", "kolonk"], "AU-001"),
    ("qapi", ["aktuator", "aktivator"], "EL-004"),

    # baqaj
    ("baqaj", ["kilid", "zamok"], "KZ-050"),
    ("baqaj", ["petle", "mentese"], "KZ-051"),
    ("baqaj", ["amortizator", "upor", "hidrovlik"], "KZ-042"),
    ("baqaj", ["knopka", "duym"], "EL-022"),
    ("baqaj", ["kovr", "ortuy", "podstil"], "AK-005"),
    ("baqaj", ["kant", "xrom"], "KZ-038"),

    # radiator
    ("radiator", ["kondisaner", "kondisioner", "konditsion"], "SO-010"),
    ("radiator", ["barmaqliq", "resetk", "setka"], "KZ-028"),
    ("radiator", ["probk"], "SO-038"),
    ("radiator", ["qapaq", "kryshk"], "SO-039"),
    ("radiator", ["bacok", "backu", "bachok"], "SO-008"),
    ("radiator", ["pec"], "SO-011"),

    # suport / əyləc
    ("suport", ["pulnik", "pilnik", "remkomplekt", "manjet"], "EY-004"),
    ("suport", ["porsen", "porshen"], "EY-004"),

    # Подушка двигателя, а не подушка безопасности: слово yastiq/paduska
    # рядом со словом мотор всегда означает опору двигателя.
    ("mator",     ["yastiq", "yastigi", "yastiqlari", "paduska", "paduskalari", "pasuskalari"], "MU-017"),
    ("motor",     ["yastiq", "yastigi", "yastiqlari", "paduska", "paduskalari"], "MU-017"),
    ("muherrik",  ["yastiq", "yastigi", "yastiqlari", "paduska", "paduskalari"], "MU-017"),
    ("karopka",   ["yastiq", "yastigi", "yastiqlari", "paduska", "paduskalari"], "MU-018"),
    # Мотор замка двери — это EL-041, а не сам замок KZ-017.
    ("qapi", ["kilidi muherrikinin motoru", "kilid motoru", "kilidin motoru"], "EL-041"),
    # Стекло задней двери багажника — это заднее стекло KZ-012, а не сама дверь.
    ("baqaj", ["aynasi", "susesi", "patpres"], "KZ-012"),
    # turbo
    ("turbo", ["datcik", "dacik", "datchik", "dacnik", "dachik", "sensor"], "MU-075"),
    ("turbo", ["aktuator", "aktator", "aktivator"], "MU-038"),
    ("turbo", ["katric", "kartric"], "MU-091"),

    # benzin / qaz
    ("benzin", ["filtr", "filtir"], "SR-010"),
    ("benzin", ["nasos"], "YA-001"),
    ("benzin", ["slank", "slanq", "boru"], "YA-012"),
    ("benzin", ["dacik", "datcik", "sensor"], "YA-006"),
    ("benzin", ["bak", "cen"], "YA-004"),

    # прочие частые
    ("lyuk",   ["mator", "motor"], "EL-034"),
    ("salon",  ["guzgu"], "KZ-021"),
    ("salon",  ["lampa", "plafon", "tavan"], "EL-024"),
    ("salon",  ["nefeslik", "deflektor", "setka"], "KZ-057"),
    ("stop",   ["lampa"], "EL-025"),
    ("stop",   ["suse", "sise"], "KZ-007"),
    ("panel",  ["pribor", "sit", "sitok"], "EL-038"),
    ("kalektor", ["rezin", "praklatka", "proklatka"], "MU-083"),
    ("raspredval", ["datcik", "dacik", "datchik", "dacnik", "dachik", "sensor"], "MU-041"),
    ("katalizator", ["datcik", "dacik", "datchik", "dacnik", "dachik", "sensor"], "EG-002"),
    ("suret", ["dacik", "datcik", "sensor"], "EL-027"),
    ("klapan", ["salnik", "kolpacok"], "MU-016"),
    ("guzgu",  ["donme", "isiq", "povorotnik"], "KZ-069"),
    ("radar",  ["blok", "idareetme"], "EL-014"),
    ("radar",  ["sensor", "sunur", "datcik"], "EL-013"),
    # --- группа 2: общее слово перебивало конкретное, правила не было ---
    ("benzin", ["datcik", "dacik", "datchik", "dacnik", "dachik", "sensor"], "YA-006"),
    ("karopka", ["yag filtr", "filtr", "filtir"], "SR-006"),
    ("karobka", ["yag filtr", "filtr", "filtir"], "SR-006"),
    ("suretler", ["yasti", "yastq", "padus", "podus"], "MU-018"),
    ("qutusu",  ["yasti", "yastq", "padus", "podus"], "MU-018"),
    ("salon",   ["sit", "sito", "shit"], "EL-038"),
    ("abs",     ["datcik", "dacik", "datchik", "dacnik", "dachik", "sensor"], "EY-011"),
    ("suse",    ["knopka", "duym", "qaldirici knopka"], "EL-003"),
    ("sise",    ["knopka", "duym"], "EL-003"),
    ("sotka",   ["mator", "motor"], "EL-072"),
    ("silen",   ["mator", "motor"], "EL-072"),
    ("qranat",  ["pilnik", "plnik", "pulnik", "cexol"], "AS-009"),
    ("bufer",   ["abisofka", "abrisofka", "abulsofka", "abilsofka", "uzluy"], "KZ-003"),
    ("support", ["porsen", "pulnik", "pilnik", "remkomplekt"], "EY-004"),
    ("suport",  ["porsen", "pulnik", "pilnik", "remkomplekt"], "EY-004"),
    ("muherrik",["soyutma radiator"], "SO-001"),
    ("pec",     ["radiator", "radioator"], "SO-011"),
    ("pecin",   ["radiator", "radioator"], "SO-011"),
    ("avtomat", ["karobka", "karopka", "qutu"], "TR-001"),
    ("parktronik", ["blok", "idareetme"], "EL-014"),
    ("rulevoy", ["kalon", "kolon"], "SU-004"),
    # ИСПРАВЛЕНО: "rulavoy pacevnik" по словарю = RG-03 Рулевой подшипник,
    # а не SU-017 (пыльник наконечника). Проверено по выгрузке 31.08.
    ("rulavoy", ["pacevnik", "pacennik", "pacvnik", "podsipnik", "podshipnik"], "RG-03"),
    ("rlovoy", ["pacevnik", "podsipnik"], "RG-03"),
    ("rulavoy", ["pilnik"], "SU-017"),
    # --- перенесено из разбора второго коллеги (владелец -> дочерняя деталь) ---
    ("bufer",   ["salask", "salack", "salacka", "kirse", "kronsteyn"], "KZ-002"),
    ("bamper",  ["salask", "salack", "kronsteyn"], "KZ-002"),
    ("stop",    ["plata", "platas"], "KZ-063"),
    ("fener",   ["plata", "platas"], "KZ-063"),
    ("rulavoy", ["butulka", "bacok", "bachok", "cen"], "SU-014"),
    ("rulevoy", ["butulka", "bacok"], "SU-014"),
    ("dumanli", ["isiq", "fara", "cihaz"], "KZ-006"),
    ("dumanni", ["isiq", "fara"], "KZ-006"),
    ("duman",   ["isiq"], "KZ-006"),
    # --- цикл 3: по оставшимся подтверждённым ошибкам ---
    ("qapi", ["kilid motor", "kilidinin motor", "merkezi qapanma", "kilid actuator", "kilid aktuator"], "EL-004"),
    ("benzin", ["nasos"], "YA-001"),
    ("bezin", ["nasos"], "YA-001"),
    ("nasos", ["zbor"], "YA-001"),
    ("eylec", ["bend", "bendler", "bendleri"], "EY-001"),
    ("eyleci", ["bend", "bendler", "bendleri"], "EY-001"),
    ("salon", ["guzgusu", "guzgu"], "KZ-054"),
    ("radar", ["salon guzgu"], "KZ-054"),
    ("radiator", ["bacokun", "bacoku", "bachok"], "SO-008"),
    ("bacokun", ["qapaq"], "SO-008"),
    ("soyutma", ["radiator"], "SO-001"),
    ("hava", ["xortum", "patrupqa", "patrupka", "patrufka"], "RG-04"),
    ("interkuler", ["patrupka", "paturupka", "patrufka", "slanq"], "SO-027"),
    ("interkulerden", ["paturupka", "patrupka"], "SO-027"),
    ("kompressor", ["klapan", "klapani"], "SO-012"),
    ("kondisaner", ["kompressorun klapan"], "SO-012"),
    ("adsorber", ["klapan", "evap", "purge"], "YA-007"),
    ("filter", ["qapag", "korpus", "korpusu"], "MU-059"),
    ("filtr", ["korpus", "korpusu"], "MU-059"),
    ("baqaj", ["ayna", "aynasinin", "amazator", "amortizator"], "KZ-042"),
    ("baqaj", ["fortucka", "yan suse", "yan sise"], "KZ-013"),
    ("baqaj", ["duqa", "duqanin"], "TY-002"),
    ("ekran", ["morda", "torpedo"], "AK-012"),
    ("bufer", ["spilka", "spilkalari"], "AS-027"),
    ("bufer", ["qaytarici", "katafot", "otrajatel"], "KZ-009"),
    ("isiq", ["qaytarici"], "KZ-009"),
    ("suse", ["qaldiran nabor", "qaldirici nabor", "qaldiran zbor"], "EL-002"),
    ("stupiciya", ["top", "support"], "AS-006"),
    ("stupica", ["top"], "AS-006"),
    ("sukan", ["guclendirici", "guclendiricisinin"], "SU-007"),
    ("elektromexanik", ["sukan", "blok"], "SU-007"),
    ("caska", ["zbor", "caskalar"], "AS-029"),
    ("caskalar", ["zbor", "sag sol"], "AS-029"),
    ("suretler", ["salnik", "praklatka", "komplekt"], "TR-001"),
    ("patpres", ["arxa"], "KZ-012"),
    ("arxa", ["patpres"], "KZ-012"),
    ("dinamo", ["steker", "stekeri", "fisi"], "EL-032"),
    ("park", ["radar sensoru", "radar sunuru"], "EL-013"),
    ("mator", ["qapagi", "qapaginin"], "MU-074"),
    ("muherrik", ["qapagi", "qapaginin"], "MU-074"),
    # --- цикл 4 ---
    ("nasos", ["filtiri ile", "filtri ile"], "YA-001"),
    ("qulp", ["qapi"], "KZ-016"),
    ("lampalar", ["salonda", "banda"], "EL-025"),
    ("lampa", ["banda", "ustunde"], "EL-025"),
    ("radar", ["96890", "sensor", "sunur"], "EL-013"),
    ("baqaj", ["ayna amortizator"], "AS-001"),
    ("guzgu", ["sestirna", "sesterna"], "SU-012"),
    ("val", ["privicniy", "kalenval", "kolenval"], "MU-002"),
    ("privicniy", ["val"], "MU-002"),
    ("kladis", ["dayaq", "uzuk", "uzuyu"], "MU-008"),
    ("suse", ["qaldiran nabor", "qaldiran zbor", "qaldirici zbor"], "EL-002"),
    ("nabor", ["suse qaldiran"], "EL-002"),
    # --- цикл 5 ---
    ("mator", ["ozu", "ozunu", "zbor", "butov"], "MU-001"),
    ("matorun", ["ozu"], "MU-001"),
    ("muherrik", ["ozu", "butov"], "MU-001"),
    # --- цикл 6: точечные защиты по оставшимся истинным ошибкам ---
    # признак "с линзой" (lupalı/linzalı или "lupa ilə") = свойство фары, не отдельная деталь
    ("fara", ["lupali", "lupalı", "linzali", "lupa ile", "linza ile"], "KZ-005"),
    # "arxa" в длинной фразе про фару не делает её задним фонарём,
    # если рядом есть признаки головной оптики
    ("fara", ["qas", "qasdi", "alt ust"], "KZ-005"),
    ("ekran", ["paneli", "panel"], "KZ-032"),
    ("baqaj", ["mirvari", "petle", "mentese"], "KZ-051"),
    ("surucunun", ["altindaki", "alti"], "AS-005"),
]


class AmbiguityLayer:
    def __init__(self, danger_path=None, min_rivals=3, coverage_floor=0.60):
        # ЕДИНСТВЕННАЯ правка вендорного кода: danger.json ищется рядом с
        # модулем, а не в текущем каталоге. Логика слоя не менялась.
        danger_path = danger_path or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "danger.json")
        """
        danger_path   — файл со списком опасных слов, посчитанным по словарю
        min_rivals    — со скольких конкурентов слово считается опасным
        coverage_floor— какую долю запроса должно покрыть слово, чтобы ему верили
        """
        d = json.load(open(danger_path))
        self.danger = {w: v for w, v in d.items() if len(v["rivals"]) >= min_rivals}
        self.floor = coverage_floor
        self.rules = defaultdict(list)
        for stem, quals, code in CONTEXT_RULES:
            self.rules[stem].append((quals, code))

    # ---------------------------------------------------------- вариант А
    def context_rule(self, text):
        n = na(text)
        toks = n.split()
        best = None
        for stem, variants in self.rules.items():
            if not any(t.startswith(stem) for t in toks):
                continue
            for quals, code in variants:
                for q in quals:
                    if " " in q:
                        # многословный уточнитель ищем по всей фразе
                        hit = q in n
                        pos = n.find(q) if hit else -1
                    else:
                        hit = any(t.startswith(q) for t in toks)
                        pos = next((i for i, t in enumerate(toks) if t.startswith(q)), -1)
                        pos = len(" ".join(toks[:pos])) if pos >= 0 else -1
                    if not hit:
                        continue
                    # приоритет: длиннее уточнитель, при равенстве — тот, что раньше в тексте
                    key = (len(q), -pos)
                    if best is None or key > best[1]:
                        best = (code, key, f"{stem}+{q}")
        if best:
            return best[0], best[2]
        return None, None

    # ---------------------------------------------------------- вариант Б
    def is_dangerous(self, term):
        return na(term) in self.danger

    def allow(self, term, query, llm_code=None, winner_code=None):
        """
        Можно ли опасному слову дать окончательный ответ.
        Разрешаем, только если оно покрывает почти весь запрос
        или его подтвердила нейронка.
        """
        t = na(term)
        if t not in self.danger:
            return True, "не опасное слово"
        qt = na(query).split()
        if not qt:
            return False, "пустой запрос"
        coverage = len(t.split()) / len(qt)
        if coverage >= self.floor:
            return True, f"покрывает {coverage:.0%} запроса"
        if llm_code and winner_code and llm_code == winner_code:
            return True, "подтверждено нейронкой"
        return False, f"общее слово покрывает лишь {coverage:.0%} запроса"
