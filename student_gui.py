# -*- coding: utf-8 -*-
"""
Моя статистика на informatics.msk.ru — графический интерфейс для студента.

Возможности:
  * вход на сайт по логину/паролю (requests, без браузера);
  * указание ссылки на курс — программа сама находит все списки задач
    (mod/statements) и задания (mod/assign);
  * подробная личная статистика по каждой задаче:
      Зачтено      — принято преподавателем (зелёная клетка),
      ОК           — принято системой,
      Не сдано     — были попытки со штрафом,
      Не сдавал    — попыток не было;
  * по заданиям assign: состояние ответа, оценивания и оценка;
  * экспорт подробной статистики в xlsx.

Запуск:  python3 student_gui.py
"""

import json
import os
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from student_api import (ST_ABSENT, ST_CREDIT, ST_DONE, ST_FAIL, ST_NONE,
                         ST_OK, StudentClient)
from xlsx_export import export_student_stats

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")

DEFAULT_CONFIG = {
    "username": "",
    "password": "",
    "course_url": "https://informatics.msk.ru/course/view.php?id=5602",
    "request_delay_sec": 0.5,
}

STATUS_COLORS = {
    ST_CREDIT: "#1a7f37",   # зачтено преподавателем
    ST_OK: "#0b62c4",       # принято системой
    ST_FAIL: "#b35900",     # не сдано (были попытки)
    ST_NONE: "#888888",     # не сдавал
    ST_ABSENT: "#888888",
}


def load_config():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except (OSError, ValueError):
        cfg = {}
    merged = dict(DEFAULT_CONFIG)
    merged.update(cfg)
    return merged


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Моя статистика — informatics.msk.ru")
        self.geometry("1000x640")
        self.minsize(760, 480)

        self.cfg = load_config()
        self.client = None
        self.stats = None               # результат collect_course_stats
        self.busy = False
        self.q = queue.Queue()

        self._build_ui()
        self.after(100, self._poll_queue)

    # ---------------------------- UI ---------------------------------- #
    def _build_ui(self):
        try:
            style = ttk.Style(self)
            if style.theme_use() == "default" \
                    and "clam" in style.theme_names():
                style.theme_use("clam")
        except tk.TclError:
            pass

        top = ttk.Frame(self, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="Логин:").pack(side="left")
        self.login_var = tk.StringVar(value=self.cfg["username"])
        ttk.Entry(top, textvariable=self.login_var, width=20).pack(
            side="left", padx=(4, 10))
        ttk.Label(top, text="Пароль:").pack(side="left")
        self.pass_var = tk.StringVar(value=self.cfg["password"])
        ttk.Entry(top, textvariable=self.pass_var, width=16,
                  show="*").pack(side="left", padx=(4, 10))
        self.btn_login = ttk.Button(top, text="Войти", command=self.on_login)
        self.btn_login.pack(side="left")
        self.status_var = tk.StringVar(value="Не авторизован")
        ttk.Label(top, textvariable=self.status_var,
                  foreground="#666").pack(side="left", padx=10)

        row2 = ttk.Frame(self, padding=(8, 2))
        row2.pack(fill="x")
        ttk.Label(row2, text="Ссылка на курс:").pack(side="left")
        self.course_var = tk.StringVar(value=self.cfg["course_url"])
        ttk.Entry(row2, textvariable=self.course_var).pack(
            side="left", fill="x", expand=True, padx=(4, 10))
        self.btn_collect = ttk.Button(row2, text="Собрать статистику",
                                      command=self.on_collect, state="disabled")
        self.btn_collect.pack(side="left")
        self.btn_export = ttk.Button(row2, text="Экспорт в xlsx…",
                                     command=self.on_export, state="disabled")
        self.btn_export.pack(side="left", padx=(6, 0))

        self.progress = ttk.Progressbar(self, mode="determinate")
        self.progress.pack(fill="x", padx=8)
        self.progress_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.progress_var,
                  foreground="#666").pack(anchor="w", padx=10)

        # сводка + подробности
        self.paned = ttk.PanedWindow(self, orient="vertical")
        self.paned.pack(fill="both", expand=True, padx=8, pady=6)

        sum_frame = ttk.LabelFrame(self.paned, text="Сводка", padding=4)
        self.summary = ttk.Treeview(
            sum_frame, columns=("kind", "done", "total", "percent"),
            height=8)
        self.summary.heading("#0", text="Работа")
        self.summary.column("#0", width=360)
        for col, head, w, anchor in (
                ("kind", "Тип", 110, "w"),
                ("done", "Сдано", 70, "center"),
                ("total", "Всего", 70, "center"),
                ("percent", "%", 60, "center")):
            self.summary.heading(col, text=head)
            self.summary.column(col, width=w, anchor=anchor)
        vsb = ttk.Scrollbar(sum_frame, orient="vertical",
                            command=self.summary.yview)
        self.summary.configure(yscrollcommand=vsb.set)
        self.summary.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.paned.add(sum_frame, weight=2)

        det_frame = ttk.LabelFrame(self.paned, text="Подробности", padding=4)
        self.detail = ttk.Treeview(
            det_frame, columns=("letter", "status", "penalty"), height=10)
        self.detail.heading("#0", text="Задача")
        self.detail.column("#0", width=420)
        self.detail.heading("letter", text="Литера")
        self.detail.column("letter", width=70, anchor="center")
        self.detail.heading("status", text="Статус")
        self.detail.column("status", width=150, anchor="center")
        self.detail.heading("penalty", text="Штраф")
        self.detail.column("penalty", width=70, anchor="center")
        for st, color in STATUS_COLORS.items():
            self.detail.tag_configure(st, foreground=color)
        vsb2 = ttk.Scrollbar(det_frame, orient="vertical",
                             command=self.detail.yview)
        self.detail.configure(yscrollcommand=vsb2.set)
        self.detail.pack(side="left", fill="both", expand=True)
        vsb2.pack(side="right", fill="y")
        self.paned.add(det_frame, weight=3)

        self.summary.bind("<<TreeviewSelect>>", self._show_detail)

        legend = ("Зачтено — принято преподавателем · ОК — принято системой · "
                  "Не сдано — были попытки (штраф) · Не сдавал — попыток не было")
        ttk.Label(self, text=legend, foreground="#666").pack(
            anchor="w", padx=10, pady=(0, 4))

    # --------------------------- события ------------------------------ #
    def _set_busy(self, busy, status=""):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.btn_login.configure(state="disabled" if busy else
                                 ("normal" if self.client else "normal"))
        self.btn_collect.configure(
            state="normal" if (self.client and not busy) else "disabled")
        self.btn_export.configure(
            state="normal" if (self.stats and not busy) else "disabled")
        if status:
            self.status_var.set(status)

    def on_login(self):
        if self.busy:
            return
        cfg = self._current_cfg()
        if not cfg["username"] or not cfg["password"]:
            messagebox.showwarning("Вход", "Введите логин и пароль.")
            return
        save_config(cfg)
        self._set_busy(True, "Вход на informatics.msk.ru…")
        threading.Thread(target=self._worker_login, args=(cfg,),
                         daemon=True).start()

    def on_collect(self):
        if self.busy or not self.client:
            return
        cfg = self._current_cfg()
        save_config(cfg)
        total = 0
        try:
            course = self.client.get_course(cfg["course_url"])
            total = len(course["statements"]) + len(course["assigns"])
        except Exception as exc:
            self._set_busy(False)
            messagebox.showerror("Курс", f"Не удалось открыть курс:\n{exc}")
            return
        if total == 0:
            messagebox.showwarning(
                "Курс", "На странице курса не найдено ни списков задач, "
                        "ни заданий. Проверьте ссылку и доступ к курсу.")
            return
        self.progress.configure(maximum=total, value=0)
        self._set_busy(True, "Сбор статистики…")
        threading.Thread(target=self._worker_collect, args=(cfg,),
                         daemon=True).start()

    def on_export(self):
        if not self.stats:
            return
        path = filedialog.asksaveasfilename(
            title="Экспорт статистики", defaultextension=".xlsx",
            initialfile="моя_статистика.xlsx",
            filetypes=[("Excel", "*.xlsx")])
        if not path:
            return
        try:
            export_student_stats(self.stats, path)
        except Exception as exc:
            messagebox.showerror("Экспорт", f"Не удалось сохранить:\n{exc}")
            return
        messagebox.showinfo("Экспорт", f"Статистика сохранена:\n{path}")

    def _current_cfg(self):
        return {"username": self.login_var.get().strip(),
                "password": self.pass_var.get(),
                "course_url": self.course_var.get().strip(),
                "request_delay_sec": self.cfg.get("request_delay_sec", 0.5)}

    # ---------------------------- потоки ------------------------------ #
    def _worker_login(self, cfg):
        try:
            client = StudentClient(cfg["username"], cfg["password"],
                                   delay=cfg.get("request_delay_sec", 0.5))
            client.login()
            self.q.put(("login_ok", client))
        except Exception as exc:
            self.q.put(("error", str(exc)))

    def _worker_collect(self, cfg):
        try:
            stats = self.client.collect_course_stats(
                cfg["course_url"],
                progress=lambda d, t, msg: self.q.put(("progress", d, msg)))
            self.q.put(("collect_ok", stats))
        except Exception as exc:
            self.q.put(("error", str(exc)))

    def _poll_queue(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "login_ok":
                    self.client = msg[1]
                    self._set_busy(
                        False,
                        f"Вход выполнен: {self.client.my_name} "
                        f"(id {self.client.my_user_id})")
                elif kind == "progress":
                    self.progress["value"] = msg[1]
                    self.progress_var.set(msg[2])
                elif kind == "collect_ok":
                    self.stats = msg[1]
                    self._fill_summary()
                    self._set_busy(False, "Статистика собрана")
                elif kind == "error":
                    self._set_busy(False, "Ошибка")
                    messagebox.showerror("Ошибка", msg[1])
        except queue.Empty:
            pass
        self.after(100, self._poll_queue)

    # -------------------------- заполнение ---------------------------- #
    def _fill_summary(self):
        self.summary.delete(*self.summary.get_children())
        course = self.stats["course"]
        if course["title"]:
            self.title(f"Моя статистика — {course['title']}")
        for st in self.stats["contests"]:
            pct = f"{st.done * 100 // st.total}%" if st.total else "—"
            self.summary.insert(
                "", "end", iid=f"c{st.contest_id}",
                text=st.name or st.title or f"Контрольная {st.contest_id}",
                values=("Контрольная", st.done, st.total, pct))
        for a in self.stats["assigns"]:
            done = 1 if a.status in ST_DONE else 0
            self.summary.insert(
                "", "end", iid=f"a{a.cmid}",
                text=a.name or a.title or f"Задание {a.cmid}",
                values=("Задание (assign)", done, 1,
                        "100%" if done else "0%"))
        if self.stats["contests"]:
            self.summary.selection_set(self.summary.get_children()[0])

    def _show_detail(self, _event=None):
        sel = self.summary.selection()
        if not sel:
            return
        self.detail.delete(*self.detail.get_children())
        iid = sel[0]
        if iid.startswith("c"):
            st = next(s for s in self.stats["contests"]
                      if f"c{s.contest_id}" == iid)
            if not st.found:
                self.detail.insert("", "end",
                                   text="Студент не найден в таблице "
                                        "результатов этой контрольной",
                                   tags=(ST_NONE,))
            for t in st.tasks:
                self.detail.insert(
                    "", "end", text=t.title or "(без названия)",
                    values=(t.letter, t.status,
                            t.penalty if t.penalty else ""),
                    tags=(t.status,))
        else:
            a = next(x for x in self.stats["assigns"] if f"a{x.cmid}" == iid)
            self.detail.insert("", "end", text="Состояние ответа",
                               values=("", a.response_state or "—", ""))
            self.detail.insert("", "end", text="Состояние оценивания",
                               values=("", a.grading_state or "—", ""))
            self.detail.insert("", "end", text="Оценка",
                               values=("", a.grade or "—", ""))
            color = ST_CREDIT if a.status in ST_DONE else ST_FAIL
            self.detail.insert("", "end", text="Итог",
                               values=("", a.status, ""), tags=(color,))


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
