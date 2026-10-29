r"""Переименование переменных с семантикой рефакторинга Designer — IL-verified.

Источник правил — статический разбор DiagramDesigner.exe 1.4.4.40199
(sha256 564a0e77…, MVID 12d13942…; evidence ev_362fbcd2…, IL-листинги
`rea-l3/il-designer-serialize.txt` в репозитории карты Sherpa RPA):

`ClassRefactoringExpression125::Processed` (дизайнерский рефакторинг выражений):
  * текст обрабатывается ПОСТРОЧНО;
  * директива-строка — `line.TrimStart().ToLower().Trim()` равна
    `#stoprefactoring` / `#startrefactoring` (регистр и внешние пробелы
    не важны); директива переключает флаг обработки;
  * вне зон директив выполняется подстановка `$`-переменных
    (`Parser::NextVariable` → `RefactoringVariables`);
  * машина кавычек отслеживает и `"` (34), и `'` (39): внутри строковых
    литералов подстановка переменных НЕ выполняется (константы в кавычках —
    отдельный проход `RefactoringVariablesTwoQuote`, к переименованию
    переменных отношения не имеет).

Отсюда контракт переименования «как Designer»:
  * `$имя` заменяется только вне кавычек и вне `#stoprefactoring`-зон,
    на границе токена (максимальный захват имени — юникодное `\w`,
    plain-форма не начинается с цифры);
  * `${имя}` — фигурная форма, те же правила;
  * `$$`, `$(`, `$5` и прочие не-имена не трогаются.

Осознанные консервативные отклонения (в сторону МЕНЬШЕГО числа замен):
  * `$имя:` (следующий символ `:`) не переименовывается — в PowerShell это
    квалификатор области видимости (`$env:PATH`); риск испортить `$env:`
    хуже, чем риск недосменить экзотическую коллизию имён;
  * имя — `\w` с юникодом; суррогатные пары/эмодзи не рассматривались;
  * escape-последовательности внутри строк (`` ` `` и `''`) не учитываются:
    кавычка закрывает зону «как есть» — лишних замен это не создаёт.

Три пространства значений (параметр quote_mark):
  * QUOTE_RAW   `"`     — разобранное значение свойства (`process_diff`,
    проверка спеки, IR);
  * QUOTE_XAML  `&quot;`  — значение внутри XAML-атрибута до укладки в файл;
  * QUOTE_FILE  `&amp;quot;` — текст `Value="…"` в самом `.process`
    (PS-кавычка экранирована дважды: XAML + XML-текстовый узел).

Правка в QUOTE_FILE байт-точна: нет unescape→escape-цикла, способного
переставить XML-сущности. Одинарная кавычка `'` нигде не экранируется
и в трёх пространствах одинакова.
"""

from __future__ import annotations

import re

QUOTE_RAW = '"'
QUOTE_XAML = "&quot;"
QUOTE_FILE = "&amp;quot;"

DIRECTIVE_STOP = "#stoprefactoring"
DIRECTIVE_START = "#startrefactoring"

# $имя или ${имя}; максимальный захват имени даёт границу токена бесплатно.
# Имя — юникодные буквы/цифры/подчёркивание (`\w`), plain-форма не начинается
# с цифры (`$5x` — это `$5` + текст, не имя). Кириллица — норма Sherpa.
TOKEN_RE = re.compile(r"\$(?:\{(\w+)\}|([^\d\W]\w*))")


def normalize_var_name(name: str) -> str:
    """`$X` / `${X}` / `X` → `X` (имя без маркеров)."""
    n = name.strip()
    if n.startswith("${") and n.endswith("}"):
        n = n[2:-1]
    elif n.startswith("$"):
        n = n[1:]
    if not re.fullmatch(r"[^\d\W]\w*", n):
        raise ValueError(f"недопустимое имя переменной: {name!r}")
    return n


def _is_directive(line_body: str) -> str | None:
    """Тело строки → имя директивы (сравнение как в IL, см. модуль)."""
    low = line_body.lstrip().lower().strip()
    if low == DIRECTIVE_STOP:
        return DIRECTIVE_STOP
    if low == DIRECTIVE_START:
        return DIRECTIVE_START
    return None


def _rename_line(body: str, mapping: dict[str, str], quote_mark: str,
                 findings: list[dict] | None, line_no: int) -> str:
    """Одна строка: замена вне кавычек; наблюдения — для отчётов."""
    out: list[str] = []
    i, n = 0, len(body)
    qm = len(quote_mark)
    in_double = False
    in_single = False
    while i < n:
        if body.startswith(quote_mark, i):
            in_double = not in_double
            out.append(quote_mark)
            i += qm
            continue
        ch = body[i]
        if ch == "'" and not in_double:
            in_single = not in_single
            out.append(ch)
            i += 1
            continue
        if ch == "$":
            m = TOKEN_RE.match(body, i)
            if m is not None:
                name = m.group(1) or m.group(2)
                if name in mapping:
                    if in_double or in_single:
                        kind = "in_quotes"
                    elif body[m.end():m.end() + 1] == ":":
                        kind = "scope_qualified"
                    else:
                        kind = "replace"
                    if findings is not None:
                        findings.append({"line": line_no, "name": name,
                                         "kind": kind})
                    if kind == "replace":
                        # фигурная форма остаётся фигурной: `${b}2` → `${beta}2`,
                        # иначе `$beta2` — уже другая переменная
                        out.append("${%s}" % mapping[name]
                                   if m.group(1) is not None
                                   else "$" + mapping[name])
                        i = m.end()
                        continue
                out.append(m.group(0))
                i = m.end()
                continue
        out.append(ch)
        i += 1
    return "".join(out)


def rename_in_text(text: str, mapping: dict[str, str], *,
                   quote_mark: str = QUOTE_RAW,
                   findings: list[dict] | None = None) -> str:
    """Переименовать `$`-переменные по mapping {старое: новое}, как Designer.

    quote_mark задаёт пространство текста (см. модуль). findings накапливает
    наблюдения: replace / in_quotes / directive_zone / scope_qualified
    (для радиуса правки и доказательства диффа).
    """
    if not mapping:
        return text
    out: list[str] = []
    refactor_on = True
    for line_no, line in enumerate(text.splitlines(keepends=True), start=1):
        body = line[:-1] if line.endswith("\n") else line
        directive = _is_directive(body)
        if directive is not None:
            refactor_on = directive == DIRECTIVE_START
            out.append(line)
        elif not refactor_on:
            if findings is not None:
                for m in TOKEN_RE.finditer(body):
                    name = m.group(1) or m.group(2)
                    if name in mapping:
                        findings.append({"line": line_no, "name": name,
                                         "kind": "directive_zone"})
            out.append(line)
        else:
            out.append(_rename_line(body, mapping, quote_mark, findings, line_no)
                       + ("\n" if line.endswith("\n") else ""))
    return "".join(out)


def plan_rename(text: str, mapping: dict[str, str], *,
                quote_mark: str = QUOTE_RAW) -> list[dict]:
    """Наблюдения без изменения text: где заменится, где Designer не тронет
    (в кавычках / зоне директивы / квалификатор)."""
    findings: list[dict] = []
    rename_in_text(text, mapping, quote_mark=quote_mark, findings=findings)
    return findings


def directives_unbalanced(text: str) -> bool:
    """True, если `#stoprefactoring` остался без парного `#startrefactoring`:
    рефакторинг выключен до конца текста — обычно это находка для ревью."""
    on = True
    for line in text.splitlines():
        d = _is_directive(line)
        if d == DIRECTIVE_STOP:
            on = False
        elif d == DIRECTIVE_START:
            on = True
    return not on
