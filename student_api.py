# -*- coding: utf-8 -*-
"""
Студенческий клиент informatics.msk.ru (requests, без браузера).

Приложение для студента: вход на сайт, указание ссылки на курс,
автосбор всех списков задач (mod/statements) и заданий (mod/assign)
курса и подробной ЛИЧНОЙ статистики по ним.

Как устроено обращение к сайту (выявлено разбором страниц курса):

  * GET  /login/index.php            — форма входа, содержит logintoken
  * POST /login/index.php            — вход (username, password, logintoken)
  * GET  /course/view.php?id=NNN     — главная курса; из неё собираются
    ссылки mod/statements/view.php?id=NNN (списки задач) и
    mod/assign/view.php?id=NNN (задания на проверку)
  * GET  /py/monitor?contest_id=NNN  — таблица результатов контрольной
    (contest_id = id статьи со «Условиями задач»). Ячейки строки студента:
      '+' / '+N'          — задача решена (N — штрафные баллы);
      bgcolor #ffff66     — принято системой (ОК);
      bgcolor #e1f2e1     — зачтено/принято преподавателем вручную;
      bgcolor #ff6666 / '-N' — попытки были, задача не сдана (штраф N);
      пусто (#fff)        — попыток не было.
  * GET  /mod/assign/view.php?id=NNN — страница задания; блок
    div.submissionstatustable содержит «Состояние ответа», «Состояние
    оценивания» и оценку студента.
"""

import re
import time

import requests
from bs4 import BeautifulSoup

BASE = "https://informatics.msk.ru"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# ---------------------------------------------------------------- statuses
ST_OK = "ОК"                  # принято системой
ST_CREDIT = "Зачтено"         # принято (зачтено) преподавателем
ST_FAIL = "Не сдано"          # были попытки со штрафом, не зачтено
ST_NONE = "Не сдавал"         # попыток не было
ST_ABSENT = "Нет в таблице"   # студент не найден в таблице контрольной

GREEN_BG = {"#e1f2e1", "#99ff99", "#ccffcc", "#d5f5d5"}
RED_BG = {"#ff6666", "#ff9999", "#ffcccc"}

ST_DONE = (ST_OK, ST_CREDIT)  # «сдано» в широком смысле


class TaskStat:
    """Статус одной задачи контрольной глазами конкретного студента."""

    def __init__(self, letter, title, chapter_id=None):
        self.letter = letter            # литерa задачи (A, B, C ...)
        self.title = title              # название задачи
        self.chapter_id = chapter_id
        self.status = ST_NONE           # ST_* выше
        self.penalty = 0                # штрафные баллы (из '+N' / '-N')
        self.raw = ""                   # исходный текст ячейки

    def as_row(self):
        return [self.letter, self.title, self.status, self.penalty]


class ContestStat:
    """Статистика студента по одному списку задач (contest)."""

    def __init__(self, contest_id, name=""):
        self.contest_id = contest_id
        self.name = name                # название со страницы курса
        self.title = ""                 # название контеста из таблицы
        self.tasks = []                 # list[TaskStat]
        self.sum = 0                    # Sum из строки студента
        self.found = False              # найдена ли строка студента

    @property
    def done(self):
        return sum(1 for t in self.tasks if t.status in ST_DONE)

    @property
    def total(self):
        return len(self.tasks)


class AssignStat:
    """Статус студента по одному заданию mod/assign."""

    def __init__(self, cmid, name=""):
        self.cmid = cmid
        self.name = name                # название со страницы курса
        self.title = ""                 # название со страницы задания
        self.response_state = ""        # «Состояние ответа на задание»
        self.grading_state = ""         # «Состояние оценивания»
        self.grade = ""                 # выставленная оценка, если есть
        self.status = "Нет данных"      # сводный статус


# ---------------------------------------------------------------------- #
class StudentClient:
    def __init__(self, username, password, delay=0.5, timeout=60):
        self.username = username
        self.password = password
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": UA})
        self.my_user_id = None
        self.my_name = None

    # ------------------------------------------------------------------ #
    def _get(self, url, **params):
        time.sleep(self.delay)
        return self.session.get(url, params=params, timeout=self.timeout)

    def login(self):
        """Вход на сайт; запоминает id и отображаемое имя студента."""
        page = self._get(BASE + "/login/index.php")
        page.raise_for_status()
        m = re.search(r'name="logintoken" value="([^"]+)"', page.text)
        data = {"username": self.username, "password": self.password}
        if m:
            data["logintoken"] = m.group(1)
        resp = self.session.post(BASE + "/login/index.php", data=data,
                                 timeout=self.timeout, allow_redirects=True)
        resp.raise_for_status()
        home = self._get(BASE + "/")
        m = re.search(r'user/profile\.php\?id=(\d+)', home.text)
        if not m:
            raise RuntimeError("Не удалось войти на informatics.msk.ru "
                               "(проверьте логин и пароль)")
        self.my_user_id = int(m.group(1))
        self.my_name = ""
        for mm in re.finditer(r'user/profile\.php\?id=\d+[^>]*>([^<]+)',
                              home.text):
            name = _norm(mm.group(1))
            if name:
                self.my_name = name
                break
        return self.my_user_id

    def is_logged_in(self):
        if not self.my_user_id:
            return False
        try:
            home = self._get(BASE + "/")
            return f'user/profile.php?id={self.my_user_id}' in home.text
        except requests.RequestException:
            return False

    # ------------------------------- курс ----------------------------- #
    def parse_course_url(self, link):
        """По ссылке на курс вернуть course id; принимает и просто число."""
        s = str(link).strip()
        m = re.search(r"course/view\.php\?id=(\d+)", s)
        if m:
            return int(m.group(1))
        m = re.search(r"[?&]id=(\d+)", s)
        if m:
            return int(m.group(1))
        if s.isdigit():
            return int(s)
        return None

    def get_course(self, course_link):
        """Вернуть {id, title, statements:[(cmid,name)], assigns:[(cmid,name)]}."""
        course_id = self.parse_course_url(course_link)
        if not course_id:
            raise RuntimeError("Не удалось разобрать ссылку на курс: "
                               f"{course_link!r}. Нужна ссылка вида "
                               "https://informatics.msk.ru/course/view.php"
                               "?id=NNNN")
        page = self._get(BASE + "/course/view.php", id=course_id)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "lxml")
        title = ""
        if soup.title:
            title = _norm(soup.title.get_text())
            title = re.sub(r"^Курс:\s*", "", title)

        statements, assigns = [], []
        seen_s, seen_a = set(), set()
        for a in soup.find_all("a", href=True):
            href = a["href"].split("#")[0]
            m = re.search(r"(?:^|https?://[^/]*)/mod/statements/view\.php"
                          r"\?id=(\d+)$", href)
            if m:
                cmid = int(m.group(1))
                if cmid in seen_s:
                    continue
                seen_s.add(cmid)
                name = _norm(a.get_text(" ", strip=True))
                name = re.sub(r"\s*Условия задач\s*$", "", name)
                statements.append((cmid, name or f"Контрольная {cmid}"))
                continue
            m = re.search(r"(?:^|https?://[^/]*)/mod/assign/view\.php"
                          r"\?id=(\d+)$", href)
            if m:
                cmid = int(m.group(1))
                if cmid in seen_a:
                    continue
                seen_a.add(cmid)
                name = _norm(a.get_text(" ", strip=True))
                assigns.append((cmid, name or f"Задание {cmid}"))

        return {"id": course_id, "title": title,
                "statements": statements, "assigns": assigns}

    # --------------------------- задачи (statements) ------------------ #
    def find_my_monitor_row(self, table_soup):
        """Найти <tr> студента в таблице /py/monitor: сначала по user_id,
        затем по совпадению ФИО (на informatics у человека и бота могут
        быть разные учётные записи с одинаковым отображаемым именем)."""
        if not table_soup:
            return None
        by_name = None
        for tr in table_soup.find_all("tr"):
            a = tr.find("a", href=re.compile(r"user_id=\d+"))
            if not a:
                continue
            m = re.search(r"user_id=(\d+)", a["href"])
            uid = int(m.group(1)) if m else None
            if uid == self.my_user_id:
                return tr, uid
            if self.my_name and by_name is None and \
                    _norm(a.get_text(" ", strip=True)) == self.my_name:
                by_name = (tr, uid)
        return by_name

    def get_contest_stat(self, contest_id, name=""):
        """Собрать статистику студента по одному списку задач."""
        stat = ContestStat(contest_id, name)
        resp = self._get(BASE + "/py/monitor", contest_id=contest_id)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        html_table = soup.find("table")
        if html_table is None:
            raise RuntimeError(
                f"Нет таблицы результатов (contest_id={contest_id}). "
                "Проверьте, что курс доступен и id верный.")

        # --- заголовок: N | Name | Sum | A | B | ...
        header = None
        for tr in html_table.find_all("tr"):
            tds = tr.find_all("td")
            if tds and tds[0].get_text(strip=True).upper() == "N" \
                    and len(tds) > 3:
                header = tds
                break
        if header is None:
            raise RuntimeError(
                f"Не найден заголовок таблицы (contest_id={contest_id}).")
        for td in header[3:]:
            letter = td.get_text(strip=True)
            chapter = None
            a = td.find("a")
            if a and a.get("href"):
                m = re.search(r"chapterid=(\d+)", a["href"])
                if m:
                    chapter = int(m.group(1))
            stat.tasks.append(TaskStat(letter, "", chapter))

        # --- легende: Letter | Название задачи [chapterid]
        titles = {}
        for t2 in soup.find_all("table")[1:]:
            for tr in t2.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) == 2 and tds[0].get_text(strip=True) == "Контест":
                    stat.title = _norm(re.sub(r"^\d+\s*", "",
                                               tds[1].get_text(" ",
                                                               strip=True)))
                    break
            for tr in t2.find_all("tr"):
                tds = tr.find_all("td")
                if len(tds) != 2:
                    continue
                mm = re.match(r"^(.*?)\s*\[(\d+)\]\s*$",
                              tds[1].get_text(" ", strip=True))
                if mm:
                    titles[int(mm.group(2))] = (tds[0].get_text(strip=True),
                                                mm.group(1).strip())
        for task in stat.tasks:
            if task.chapter_id in titles:
                letter2, title2 = titles[task.chapter_id]
                if letter2:
                    task.letter = letter2
                task.title = title2

        # --- строка студента
        row = self.find_my_monitor_row(html_table)
        if not row:
            for task in stat.tasks:
                task.status = ST_ABSENT
            return stat
        tr, _uid = row
        stat.found = True
        tds = tr.find_all("td")
        try:
            stat.sum = int(tds[2].get_text(strip=True))
        except (IndexError, ValueError):
            stat.sum = 0
        cells = tds[3:]
        for i, task in enumerate(stat.tasks):
            td = cells[i] if i < len(cells) else None
            if td is None:
                task.status = ST_NONE
                continue
            text = td.get_text(" ", strip=True)
            bg = (td.get("bgcolor") or "").lower()
            task.raw = text
            task.status, task.penalty = classify_cell(text, bg)
        return stat

    # ---------------------------- задания (assign) -------------------- #
    def get_assign_stat(self, cmid, name=""):
        """Собрать статус студента по одному заданию mod/assign."""
        stat = AssignStat(cmid, name)
        resp = self._get(BASE + "/mod/assign/view.php", id=cmid)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        if soup.title:
            stat.title = _norm(re.sub(r"\s*[—-]\s*МАИ.*$", "",
                                      soup.title.get_text()))
        box = soup.find("div", class_="submissionstatustable")
        table = None
        if box:
            table = box.find("table", class_="generaltable") or \
                box.find("table")
        if table is None:
            # страница недоступна или блок статуса отсутствует
            page_text = soup.get_text(" ", strip=True)
            if "Не разрешено" in page_text or "Error" in page_text[:2000]:
                stat.status = "Нет доступа"
            return stat

        for tr in table.find_all("tr"):
            th = tr.find("th")
            td = tr.find("td")
            if not th or not td:
                continue
            label = _norm(th.get_text(" ", strip=True))
            value = _norm(td.get_text(" ", strip=True))
            if "состояние ответа" in label.lower():
                stat.response_state = value
            elif "оценивания" in label.lower():
                stat.grading_state = value
            elif label.lower().startswith("оценка"):
                stat.grade = "" if value == "-" else value

        stat.status = classify_assign(stat.response_state,
                                      stat.grading_state, stat.grade)
        return stat

    # ------------------------------ всё сразу ------------------------- #
    def collect_course_stats(self, course_link, progress=None):
        """Собрать полную статистику по курсу: contest'ы и assign'ы."""
        course = self.get_course(course_link)
        result = {"course": course, "contests": [], "assigns": []}
        items = course["statements"] + course["assigns"]
        total = len(items) or 1
        done = 0
        for cmid, name in course["statements"]:
            result["contests"].append(self.get_contest_stat(cmid, name))
            done += 1
            if progress:
                progress(done, total, f"Контрольная «{name}»")
        for cmid, name in course["assigns"]:
            try:
                result["assigns"].append(self.get_assign_stat(cmid, name))
            except RuntimeError:
                stat = AssignStat(cmid, name)
                stat.status = "Нет доступа"
                result["assigns"].append(stat)
            done += 1
            if progress:
                progress(done, total, f"Задание «{name}»")
        return result


# ---------------------------------------------------------------------- #
def classify_cell(text, bg):
    """Текст+цвет ячейки таблицы результатов -> (статус, штраф)."""
    text = (text or "").strip()
    bg = (bg or "").lower()
    m = re.match(r"^\+(\d*)$", text)
    if m:
        penalty = int(m.group(1)) if m.group(1) else 0
        if bg in GREEN_BG:
            return ST_CREDIT, penalty
        return ST_OK, penalty
    m = re.match(r"^-(\d+)$", text)
    if m or bg in RED_BG:
        return ST_FAIL, int(m.group(1)) if m else 0
    return ST_NONE, 0


def classify_assign(response_state, grading_state, grade):
    """Состояния страницы assign -> сводный статус студента."""
    rs = (response_state or "").lower()
    gs = (grading_state or "").lower()
    grade = (grade or "").strip()
    if "зачт" in gs or "зачт" in grade.lower():
        return ST_CREDIT
    if grade:
        return "Оценено"
    if "оценено" in gs and "не оценено" not in gs:
        return "Оценено"
    if "ни одной попытки" in rs or "не отправлено" in rs:
        return ST_NONE
    if "черновик" in rs:
        return "Черновик"
    if "отправлен" in rs or "сдан" in rs or "разблокирован" in rs:
        return "Сдано на проверку"
    return response_state or "Нет данных"


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()
