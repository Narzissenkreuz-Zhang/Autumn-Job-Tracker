from __future__ import annotations

import argparse
import json
import mimetypes
import sqlite3
import threading
import webbrowser
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from xlsx_utils import export_workbook, read_rows


APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"
DATA_DIR = APP_DIR / "data"
EXPORT_DIR = APP_DIR / "exports"
DB_PATH = DATA_DIR / "autumn_jobs.db"
# 仅迁移放在项目目录中的旧版 Excel，避免意外读取用户桌面上的同名文件。
DEFAULT_EXCEL = APP_DIR / "秋招投递管理表.xlsx"
SERVER: ThreadingHTTPServer | None = None

NATURE_OPTIONS = ["银行", "央国企", "互联网", "民企", "外企", "证券", "保险", "事业单位", "政府机关", "其他"]
SUBMITTED_OPTIONS = ["未投递", "已投递"]
STAGE_OPTIONS = ["待投递", "已投递", "机筛", "测评", "笔试", "AI面试", "一面", "二面", "三面", "HR面", "体检", "签约", "Offer", "流程结束", "淘汰"]


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(source_excel: Path | None = None) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                nature TEXT NOT NULL DEFAULT '',
                deadline TEXT NOT NULL DEFAULT '',
                submitted TEXT NOT NULL DEFAULT '未投递',
                stage TEXT NOT NULL DEFAULT '待投递',
                exam_time TEXT NOT NULL DEFAULT '',
                interview_time TEXT NOT NULL DEFAULT '',
                website TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS positions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_id INTEGER NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
                base TEXT NOT NULL DEFAULT '',
                department TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                status_override TEXT NOT NULL DEFAULT '',
                exam_override TEXT NOT NULL DEFAULT '',
                interview_override TEXT NOT NULL DEFAULT '',
                link TEXT NOT NULL DEFAULT '',
                notes TEXT NOT NULL DEFAULT '',
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_positions_company ON positions(company_id, sort_order, id);
            """
        )
        position_columns = {row[1] for row in db.execute("PRAGMA table_info(positions)")}
        if "exam_override" not in position_columns:
            db.execute("ALTER TABLE positions ADD COLUMN exam_override TEXT NOT NULL DEFAULT ''")
        if "department" not in position_columns:
            db.execute("ALTER TABLE positions ADD COLUMN department TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE positions SET department=base, base='' WHERE department='' AND base<>''")
        company_count = db.execute("SELECT COUNT(*) FROM companies").fetchone()[0]
        if company_count == 0 and source_excel and source_excel.exists():
            migrate_excel(db, source_excel)


def clean(value: object | None) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:g}"
    return str(value).strip()


def migrate_excel(db: sqlite3.Connection, source_excel: Path) -> None:
    rows = read_rows(source_excel)
    if len(rows) < 5:
        return
    current_company_id: int | None = None
    company_order = 0
    position_order = 0
    for row in rows[4:]:
        padded = list(row[:10]) + [None] * max(0, 10 - len(row))
        company, nature, base, title, deadline, submitted, stage, exam, interview, website = map(clean, padded[:10])
        if company:
            company_order += 1
            position_order = 0
            cursor = db.execute(
                """INSERT INTO companies
                   (name, nature, deadline, submitted, stage, exam_time, interview_time, website, sort_order)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (company, nature, deadline, submitted or "未投递", stage or "待投递", exam, interview, website, company_order),
            )
            current_company_id = cursor.lastrowid
            if title:
                position_order += 1
                db.execute(
                    "INSERT INTO positions (company_id, department, title, sort_order) VALUES (?, ?, ?, ?)",
                    (current_company_id, base, title, position_order),
                )
        elif current_company_id and (base or title):
            position_order += 1
            db.execute(
                "INSERT INTO positions (company_id, department, title, sort_order) VALUES (?, ?, ?, ?)",
                (current_company_id, base, title, position_order),
            )


def company_payloads() -> list[dict]:
    with connect() as db:
        companies = [dict(row) for row in db.execute("SELECT * FROM companies ORDER BY sort_order, id")]
        positions = [dict(row) for row in db.execute("SELECT * FROM positions ORDER BY company_id, sort_order, id")]
    by_company: dict[int, list[dict]] = {}
    for position in positions:
        by_company.setdefault(position["company_id"], []).append(position)
    for company in companies:
        company["positions"] = by_company.get(company["id"], [])
        company["position_count"] = len(company["positions"])
    return companies


def normalized_company(data: dict) -> dict:
    return {
        "name": clean(data.get("name")),
        "nature": clean(data.get("nature")),
        "deadline": clean(data.get("deadline")),
        "submitted": clean(data.get("submitted")) or "未投递",
        "stage": clean(data.get("stage")) or "待投递",
        "exam_time": clean(data.get("exam_time")),
        "interview_time": clean(data.get("interview_time")),
        "website": clean(data.get("website")),
        "notes": clean(data.get("notes")),
    }


def normalized_position(data: dict) -> dict:
    return {
        "base": clean(data.get("base")),
        "department": clean(data.get("department")),
        "title": clean(data.get("title")),
        "status_override": clean(data.get("status_override")),
        "exam_override": clean(data.get("exam_override")),
        "interview_override": clean(data.get("interview_override")),
        "link": clean(data.get("link")),
        "notes": clean(data.get("notes")),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "AutumnJobs/1.0"

    def log_message(self, fmt: str, *args) -> None:
        if args and str(args[1]).startswith("4"):
            super().log_message(fmt, *args)

    def send_json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict:
        length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def do_GET(self) -> None:
        route = urlparse(self.path).path
        if route == "/api/companies":
            self.send_json({"companies": company_payloads(), "options": {"natures": NATURE_OPTIONS, "submitted": SUBMITTED_OPTIONS, "stages": STAGE_OPTIONS}})
            return
        if route == "/api/health":
            self.send_json({"ok": True})
            return
        if route == "/api/export.xlsx":
            companies = company_payloads()
            flat_positions = []
            for company in companies:
                for position in company["positions"]:
                    flat_positions.append({**position, "company_name": company["name"]})
            export_path = EXPORT_DIR / f"秋招投递备份_{datetime.now():%Y%m%d_%H%M}.xlsx"
            export_workbook(export_path, companies, flat_positions)
            body = export_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            encoded_name = quote(export_path.name)
            self.send_header("Content-Disposition", f"attachment; filename=autumn_jobs_backup.xlsx; filename*=UTF-8''{encoded_name}")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.serve_static(route)

    def serve_static(self, route: str) -> None:
        relative = "index.html" if route in {"", "/"} else unquote(route.lstrip("/"))
        target = (STATIC_DIR / relative).resolve()
        if STATIC_DIR.resolve() not in target.parents and target != STATIC_DIR.resolve():
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not target.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        body = target.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(target.name)[0] or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        try:
            if route == "/api/companies":
                data = normalized_company(self.read_json())
                if not data["name"]:
                    self.send_json({"error": "公司名称不能为空"}, 400)
                    return
                with connect() as db:
                    order = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM companies").fetchone()[0]
                    cursor = db.execute(
                        """INSERT INTO companies
                           (name, nature, deadline, submitted, stage, exam_time, interview_time, website, notes, sort_order)
                           VALUES (:name, :nature, :deadline, :submitted, :stage, :exam_time, :interview_time, :website, :notes, :sort_order)""",
                        {**data, "sort_order": order},
                    )
                self.send_json({"id": cursor.lastrowid}, 201)
                return
            match = __import__("re").fullmatch(r"/api/companies/(\d+)/positions", route)
            if match:
                company_id = int(match.group(1))
                data = normalized_position(self.read_json())
                if not data["base"] and not data["department"] and not data["title"]:
                    self.send_json({"error": "Base、部门和岗位至少填写一个"}, 400)
                    return
                with connect() as db:
                    order = db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM positions WHERE company_id = ?", (company_id,)).fetchone()[0]
                    cursor = db.execute(
                        """INSERT INTO positions
                           (company_id, base, department, title, status_override, exam_override, interview_override, link, notes, sort_order)
                           VALUES (:company_id, :base, :department, :title, :status_override, :exam_override, :interview_override, :link, :notes, :sort_order)""",
                        {**data, "company_id": company_id, "sort_order": order},
                    )
                self.send_json({"id": cursor.lastrowid}, 201)
                return
            if route == "/api/shutdown":
                self.send_json({"ok": True})
                if SERVER:
                    threading.Thread(target=SERVER.shutdown, daemon=True).start()
                return
            self.send_json({"error": "接口不存在"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_PUT(self) -> None:
        route = urlparse(self.path).path
        try:
            if route == "/api/companies/reorder":
                ids = self.read_json().get("ids", [])
                if not isinstance(ids, list) or not all(isinstance(item, int) for item in ids):
                    self.send_json({"error": "排序数据无效"}, 400)
                    return
                with connect() as db:
                    current_ids = [row[0] for row in db.execute("SELECT id FROM companies")]
                    if len(ids) != len(current_ids) or set(ids) != set(current_ids):
                        self.send_json({"error": "公司列表已变化，请刷新后重试"}, 409)
                        return
                    for order, company_id in enumerate(ids, 1):
                        db.execute("UPDATE companies SET sort_order=? WHERE id=?", (order, company_id))
                self.send_json({"ok": True})
                return
            positions_reorder_match = __import__("re").fullmatch(r"/api/companies/(\d+)/positions/reorder", route)
            if positions_reorder_match:
                company_id = int(positions_reorder_match.group(1))
                ids = self.read_json().get("ids", [])
                if not isinstance(ids, list) or not all(isinstance(item, int) for item in ids):
                    self.send_json({"error": "排序数据无效"}, 400)
                    return
                with connect() as db:
                    current_ids = [row[0] for row in db.execute("SELECT id FROM positions WHERE company_id=?", (company_id,))]
                    if len(ids) != len(current_ids) or set(ids) != set(current_ids):
                        self.send_json({"error": "岗位列表已变化，请刷新后重试"}, 409)
                        return
                    for order, position_id in enumerate(ids, 1):
                        db.execute("UPDATE positions SET sort_order=? WHERE id=? AND company_id=?", (order, position_id, company_id))
                self.send_json({"ok": True})
                return
            company_match = __import__("re").fullmatch(r"/api/companies/(\d+)", route)
            position_match = __import__("re").fullmatch(r"/api/positions/(\d+)", route)
            if company_match:
                data = normalized_company(self.read_json())
                if not data["name"]:
                    self.send_json({"error": "公司名称不能为空"}, 400)
                    return
                with connect() as db:
                    db.execute(
                        """UPDATE companies SET name=:name, nature=:nature, deadline=:deadline,
                           submitted=:submitted, stage=:stage, exam_time=:exam_time,
                           interview_time=:interview_time, website=:website, notes=:notes,
                           updated_at=CURRENT_TIMESTAMP WHERE id=:id""",
                        {**data, "id": int(company_match.group(1))},
                    )
                self.send_json({"ok": True})
                return
            if position_match:
                data = normalized_position(self.read_json())
                with connect() as db:
                    db.execute(
                        """UPDATE positions SET base=:base, department=:department, title=:title, status_override=:status_override,
                           exam_override=:exam_override, interview_override=:interview_override, link=:link, notes=:notes,
                           updated_at=CURRENT_TIMESTAMP WHERE id=:id""",
                        {**data, "id": int(position_match.group(1))},
                    )
                self.send_json({"ok": True})
                return
            self.send_json({"error": "接口不存在"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def do_DELETE(self) -> None:
        route = urlparse(self.path).path
        company_match = __import__("re").fullmatch(r"/api/companies/(\d+)", route)
        position_match = __import__("re").fullmatch(r"/api/positions/(\d+)", route)
        with connect() as db:
            if company_match:
                db.execute("DELETE FROM companies WHERE id = ?", (int(company_match.group(1)),))
                self.send_json({"ok": True})
                return
            if position_match:
                db.execute("DELETE FROM positions WHERE id = ?", (int(position_match.group(1)),))
                self.send_json({"ok": True})
                return
        self.send_json({"error": "接口不存在"}, 404)


def main() -> None:
    parser = argparse.ArgumentParser(description="秋招投递管理助手")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--init-only", action="store_true")
    parser.add_argument("--source", type=Path, default=DEFAULT_EXCEL)
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    initialize_database(args.source)
    if args.init_only:
        print(json.dumps({"companies": len(company_payloads()), "database": str(DB_PATH)}, ensure_ascii=False))
        return

    global SERVER
    port = args.port
    while True:
        try:
            SERVER = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
            if port > args.port + 10:
                raise
    url = f"http://127.0.0.1:{port}"
    print(f"秋招投递管理助手已启动：{url}")
    print("关闭此窗口即可停止程序。")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        SERVER.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SERVER.server_close()


if __name__ == "__main__":
    main()
