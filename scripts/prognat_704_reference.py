"""Прогон контрольного набора: 704 заявки с подтверждением покупателя.
Порядок: сначала матчер, если он молчит — предложение нейронки.
Запускать после любой правки правил."""
import json
from avtozap_matcher_engine import match, PARTS, IDX
from avtozap_ambiguity_layer import na

rows = json.load(open('dannye/zayavki_6188.json'))
ru2 = {}
for c, p in PARTS.items():
    ru2.setdefault((p['name_ru'] or '').strip().lower(), c)
gt = [(t, ru2.get((n or '').strip().lower())) for _, t, n, _ in rows
      if ru2.get((n or '').strip().lower())]

ok = wrong = 0
errors = []
for txt, exp in gt:
    got, how = match(txt)
    if got == exp:
        ok += 1
    elif got:
        wrong += 1
        errors.append((txt, got, exp, how))
print(f'верно {ok} | неверно {wrong} | не найдено {len(gt)-ok-wrong} | всего {len(gt)}')
print('\nошибки:')
for txt, got, exp, how in errors:
    print(f'  [{how:12}] {txt[:44]:46} дало {got} нужно {exp}')
