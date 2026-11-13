# -*- coding: utf-8 -*-
"""
CLI-версия: собрать статистику и напечатать/сохранить в xlsx.

Запуск:
    python3 main.py --login ЛОГИН --password ПАРОЛЬ \
        --course https://informatics.msk.ru/course/view.php?id=5602 \
        [--out статистика.xlsx]

Без аргументов берутся логин/пароль/курс из config.json.
"""

import argparse
import json
import os
import sys

from student_api import ST_DONE, StudentClient
from xlsx_export import export_student_stats

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--login")
    ap.add_argument("--password")
    ap.add_argument("--course")
    ap.add_argument("--delay", type=float, default=None)
    ap.add_argument("--out", help="путь к xlsx; без него — только печать")
    args = ap.parse_args(argv)

    cfg = {}
    cfg_path = os.path.join(HERE, "config.json")
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)

    login = args.login or cfg.get("username")
    password = args.password or cfg.get("password")
    course = args.course or cfg.get("course_url")
    if not login or not password or not course:
        ap.error("Нужны --login/--password/--course (или config.json)")

    client = StudentClient(login, password,
                           delay=cfg.get("request_delay_sec", 0.5)
                           if args.delay is None else args.delay)
    client.login()
    print(f"Вход выполнен: {client.my_name} (id {client.my_user_id})")

    stats = client.collect_course_stats(
        course,
        progress=lambda d, t, msg: print(f"[{d}/{t}] {msg}",
                                         file=sys.stderr))

    course_info = stats["course"]
    print(f"\nКурс: {course_info['title']}")
    for st in stats["contests"]:
        print(f"\n=== {st.name}  ({st.done}/{st.total} сдано, Sum={st.sum})")
        for t in st.tasks:
            mark = {"ОК": "+", "Зачтено": "✓", "Не сдано": "!",
                    "Не сдавал": "·"}.get(t.status, "?")
            print(f"  {mark} [{t.letter}] {t.title:<45} {t.status}"
                  + (f" (штраф {t.penalty})" if t.penalty else ""))
    for a in stats["assigns"]:
        ok = "✓" if a.status in ST_DONE else "·"
        print(f"\n=== {a.name}\n  {ok} Ответ: {a.response_state or '—'}; "
              f"Оценивание: {a.grading_state or '—'}; "
              f"Оценка: {a.grade or '—'} → {a.status}")

    if args.out:
        export_student_stats(stats, args.out)
        print(f"\nСохранено: {args.out}")


if __name__ == "__main__":
    main()
