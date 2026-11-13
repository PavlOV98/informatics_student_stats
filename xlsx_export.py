# -*- coding: utf-8 -*-
"""Экспорт студенческой статистики в xlsx (openpyxl).

Лист «Сводка» — по одной строке на работу; лист «Задачи» — подробная
таблица: работа, литера, задача, статус, штраф.
"""

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from student_api import (ST_ABSENT, ST_CREDIT, ST_DONE, ST_FAIL, ST_NONE,
                         ST_OK)

FILLS = {
    ST_CREDIT: PatternFill("solid", fgColor="D6F5D6"),   # зачтено
    ST_OK: PatternFill("solid", fgColor="D9E9FF"),       # принято системой
    ST_FAIL: PatternFill("solid", fgColor="FFE3CC"),     # не сдано
    ST_NONE: PatternFill("solid", fgColor="F2F2F2"),     # не сдавал
}
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")
BOLD = Font(bold=True)
CENTER = Alignment(horizontal="center")


def _style_header(ws, ncols):
    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.font = BOLD
        cell.fill = HEADER_FILL
        cell.alignment = CENTER


def export_student_stats(stats, path):
    """stats — результат StudentClient.collect_course_stats."""
    wb = Workbook()

    # ------------------------------------------------------- Сводка ---- #
    ws = wb.active
    ws.title = "Сводка"
    ws.append(["Работа", "Тип", "Сдано", "Всего", "Процент"])
    course = stats["course"]
    for st in stats["contests"]:
        pct = round(st.done / st.total * 100) if st.total else None
        ws.append([st.name or st.title or f"Контрольная {st.contest_id}",
                   "Контрольная", st.done, st.total, pct])
        if pct is not None:
            ws.cell(row=ws.max_row, column=5).number_format = "0\"%\""
    for a in stats["assigns"]:
        done = 1 if a.status in ST_DONE else 0
        ws.append([a.name or a.title or f"Задание {a.cmid}",
                   "Задание (assign)", done, 1, 100 if done else 0])
        ws.cell(row=ws.max_row, column=5).number_format = "0\"%\""
        ws.cell(row=ws.max_row, column=3).fill = \
            FILLS[ST_CREDIT] if done else FILLS[ST_NONE]
    _style_header(ws, 5)
    for col, width in zip("ABCDE", (42, 16, 8, 8, 9)):
        ws.column_dimensions[col].width = width

    # ------------------------------------------------------- Задачи ---- #
    ws2 = wb.create_sheet("Задачи")
    ws2.append(["Работа", "Литера", "Задача", "Статус", "Штраф"])
    for st in stats["contests"]:
        name = st.name or st.title or f"Контрольная {st.contest_id}"
        for t in st.tasks:
            ws2.append([name, t.letter, t.title, t.status,
                        t.penalty or None])
            fill = FILLS.get(t.status)
            if fill:
                for col in range(1, 6):
                    ws2.cell(row=ws2.max_row, column=col).fill = fill
    for a in stats["assigns"]:
        name = a.name or a.title or f"Задание {a.cmid}"
        fill = FILLS[ST_CREDIT] if a.status in ST_DONE else FILLS[ST_NONE]
        for label, value in (("Состояние ответа", a.response_state),
                             ("Состояние оценивания", a.grading_state),
                             ("Оценка", a.grade),
                             ("Итог", a.status)):
            ws2.append([name, "", label, value or "—", None])
            for col in range(1, 6):
                ws2.cell(row=ws2.max_row, column=col).fill = fill
    _style_header(ws2, 5)
    for col, width in zip("ABCDE", (30, 8, 44, 18, 7)):
        ws2.column_dimensions[col].width = width
    ws2.freeze_panes = "A2"

    wb.save(path)
    return path
