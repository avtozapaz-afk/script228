#!/usr/bin/env python3
"""AVTOZAP — сравнить Arbiter V3 и V4 на обоих эталонах одной командой.

    python3 SRAVNENIE_V3_V4.py

Скрипт делает четыре прогона (две версии арбитра × два эталона) и печатает
таблицу с двумя главными цифрами:

* **правильные ANSWER** — сколько заявок закрыто верно;
* **опасные ложные ANSWER** — сколько раз система уверенно ответила там, где
  нужен был переспрос. Это худший вид ошибки, и рост здесь важнее прироста
  точности.

Прогон **возобновляемый**: каждая заявка пишется на диск сразу. Прервали —
запустите ту же команду, продолжит с места остановки и не оплатит посчитанное
дважды.

Займёт примерно 45–70 минут: 669 заявок × 2 версии, на каждую по два обращения
к модели. Хотите быстрее — прогоните только набор 200:

    python3 SRAVNENIE_V3_V4.py --only 200

Ключ нигде не сохраняется: живёт только в памяти процесса.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

if hasattr(sys.stdout, "reconfigure"):        # консоль Windows не в UTF-8
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

LINE = "─" * 72

SETS = {
    "469": os.path.join("data", "etalon_469.jsonl"),
    "200": os.path.join("data", "etalon_200.jsonl"),
}


def say(text: str = "") -> None:
    print(text, flush=True)


def get_api_key() -> str | None:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        say("Ключ API: взят из переменной окружения OPENAI_API_KEY.")
        return key
    if not sys.stdin.isatty():
        say("OPENAI_API_KEY не задан, а ввод неинтерактивный.")
        say('    set OPENAI_API_KEY=sk-...           # Windows cmd')
        say('    export OPENAI_API_KEY="sk-..."      # Linux / macOS')
        return None
    say()
    say("Вставьте ключ OpenAI и нажмите Enter. Ввод скрыт — это нормально.")
    return getpass.getpass("Ключ: ").strip() or None


def done(path: str) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", choices=("469", "200"), default=None,
                        help="прогнать только один эталон")
    args = parser.parse_args(argv)

    say(LINE)
    say("AVTOZAP — сравнение Arbiter V3 и V4")
    say(LINE)

    wanted = [args.only] if args.only else ["469", "200"]
    for name in wanted:
        if not os.path.exists(SETS[name]):
            say(f"НЕ МОГУ ПРОДОЛЖИТЬ: нет входа {SETS[name]}")
            say("Соберите его: python3 scripts/build_etalon_input.py")
            return 1

    key = get_api_key()
    if not key:
        return 1
    os.environ["OPENAI_API_KEY"] = key

    import run_test
    from scripts.compare_arbiters import main as compare

    for name in wanted:
        for version in ("v3", "v4"):
            out_dir = os.path.join("out", f"etalon{name}_{version}")
            already = done(os.path.join(out_dir, "results.jsonl"))
            say()
            say(LINE)
            say(f"ПРОГОН: эталон {name} · арбитр {version.upper()}"
                + (f" · продолжаю с {already}" if already else ""))
            say(LINE)
            code = run_test.main(["--input", SETS[name], "--out-dir", out_dir,
                                  "--arbiter", version])
            if code != 0:
                say(f"\nПрогон {name}/{version} завершился с ошибкой.")
                return 1

    for name in wanted:
        say()
        say(LINE)
        say(f"СРАВНЕНИЕ НА ЭТАЛОНЕ {name}")
        say(LINE)
        compare(["--etalon", SETS[name],
                 "--a", os.path.join("out", f"etalon{name}_v3", "results.jsonl"),
                 "--b", os.path.join("out", f"etalon{name}_v4", "results.jsonl"),
                 "--details"])

    say()
    say(LINE)
    say("Что смотреть в первую очередь:")
    say("  1. «ОПАСНЫЕ ложные ANSWER» — если у V4 их больше, V4 не принимаем,")
    say("     каким бы ни был прирост точности.")
    say("  2. «правильные ANSWER» — ради чего всё и делалось.")
    say("  3. Строка вердикта под таблицей — она считает ровно по этим двум.")
    say()
    say("Заявки, где система переспросила или ошиблась, лежат в каждой папке")
    say("прогона как pilot_queue.jsonl — это будущий эталон, заполните в нём")
    say("поле correct_external_code.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nПрервано. Посчитанное сохранено — запустите ту же команду.")
        sys.exit(130)
