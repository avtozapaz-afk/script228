#!/usr/bin/env python3
"""AVTOZAP — прогон эталона 469 и результат одной командой.

    python3 PROGON_ETALON_469.py

Скрипт сам:

1. проверит, что всё на месте (зависимости, словарь, эталон);
2. спросит ключ OpenAI, если его нет в переменной окружения;
3. прогонит все 469 размеченных заявок через конвейер;
4. сверит с эталоном и напечатает результат.

Прогон **возобновляемый**: результат каждой заявки сразу пишется на диск.
Прервали на середине — запустите ту же команду, продолжит с места остановки.

Ключ нигде не сохраняется: он живёт только в памяти процесса и не попадает
ни в один файл результатов.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

ETALON = os.path.join("data", "etalon_469.jsonl")
ETALON_SRC = os.path.join("data", "reference", "AVTOZAP_ETALON_GOTOVYY.csv")
OUT_DIR = os.path.join("out", "etalon469")
RESULTS = os.path.join(OUT_DIR, "results.jsonl")

LINE = "─" * 72


def say(text: str = "") -> None:
    print(text, flush=True)


def fail(text: str) -> int:
    say()
    say(f"НЕ МОГУ ПРОДОЛЖИТЬ: {text}")
    return 1


def check_dependencies() -> str | None:
    """Вернуть текст ошибки, если чего-то не хватает."""
    missing = []
    for module, package in (("openai", "openai"), ("rapidfuzz", "rapidfuzz"),
                            ("openpyxl", "openpyxl")):
        try:
            __import__(module)
        except ImportError:
            missing.append(package)
    if missing:
        return ("не установлены пакеты: " + ", ".join(missing) + "\n"
                "        Установите одной командой:\n"
                "            pip install " + " ".join(missing))
    return None


def ensure_etalon() -> str | None:
    """Собрать вход из размеченного CSV, если его ещё нет."""
    if os.path.exists(ETALON):
        return None
    if not os.path.exists(ETALON_SRC):
        return (f"нет ни {ETALON}, ни исходной разметки {ETALON_SRC}.\n"
                "        Положите файл разметки на место и запустите снова.")
    say("Собираю вход из разметки…")
    result = subprocess.run(
        [sys.executable, os.path.join("scripts", "build_etalon_input.py")],
        capture_output=True, text=True)
    if result.returncode != 0:
        return "не удалось собрать вход:\n" + (result.stderr or result.stdout)
    say(result.stdout.strip())
    return None


def get_api_key() -> str | None:
    """Ключ из окружения, иначе — скрытый ввод. Обратно НЕ печатаем."""
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if key:
        say("Ключ API: взят из переменной окружения OPENAI_API_KEY.")
        return key
    if not sys.stdin.isatty():
        say("OPENAI_API_KEY не задан, а ввод неинтерактивный.")
        say("Задайте переменную окружения и запустите снова:")
        say('    export OPENAI_API_KEY="sk-..."      # Linux / macOS')
        say('    set OPENAI_API_KEY=sk-...           # Windows cmd')
        return None
    say()
    say("Вставьте ключ OpenAI и нажмите Enter.")
    say("Ввод скрыт — символы не отображаются, это нормально.")
    key = getpass.getpass("Ключ: ").strip()
    return key or None


def count_done() -> int:
    if not os.path.exists(RESULTS):
        return 0
    with open(RESULTS, encoding="utf-8") as fh:
        return sum(1 for line in fh if line.strip())


def main() -> int:
    say(LINE)
    say("AVTOZAP — прогон эталона 469")
    say(LINE)

    problem = check_dependencies()
    if problem:
        return fail(problem)

    problem = ensure_etalon()
    if problem:
        return fail(problem)

    with open(ETALON, encoding="utf-8") as fh:
        total = sum(1 for line in fh if line.strip())
    already = count_done()

    say(f"Заявок в эталоне : {total}")
    if already:
        say(f"Уже посчитано    : {already} — продолжу с этого места")
    else:
        say("Займёт примерно 15–25 минут: на каждую заявку два обращения к "
            "модели.")
    say(f"Результаты лягут : {OUT_DIR}/")
    say()

    key = get_api_key()
    if not key:
        return fail("ключ не введён")
    os.environ["OPENAI_API_KEY"] = key

    say()
    say(LINE)
    say("ПРОГОН")
    say(LINE)

    import run_test

    code = run_test.main(["--input", ETALON, "--out-dir", OUT_DIR])
    if code != 0:
        return fail("прогон завершился с ошибкой, смотрите сообщения выше")

    say()
    say(LINE)
    say("РЕЗУЛЬТАТ")
    say(LINE)

    from scripts.score_etalon import main as score
    score(["--etalon", ETALON, "--results", RESULTS])

    say()
    say(LINE)
    say("Файлы результата:")
    for name in ("results.csv", "results.jsonl", "summary.md",
                 "failure_analysis.md"):
        path = os.path.join(OUT_DIR, name)
        if os.path.exists(path):
            say(f"    {path}")
    say()
    say("Отдельно можно проверить правило отказов и переспроса — только в")
    say("наборе 200 есть заявки, где верный ответ «кода быть не должно», и")
    say("точка отсчёта «боевая система права в 128 из 200»:")
    say()
    say("    python3 run_test.py --input data/etalon_200.jsonl "
        "--out-dir out/etalon200")
    say("    python3 scripts/score_etalon.py --etalon data/etalon_200.jsonl \\")
    say("            --results out/etalon200/results.jsonl")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n\nПрервано. Посчитанное сохранено — запустите ту же команду, "
              "продолжит с места остановки.")
        sys.exit(130)
