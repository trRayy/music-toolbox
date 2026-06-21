import argparse
import json
import os
import re
import sys
from datetime import date, datetime, time
from decimal import Decimal
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

try:
    import pymysql
except ModuleNotFoundError:  # pragma: no cover - handled at runtime
    pymysql = None

from feishu_oauth_user_token import get_user_access_token_interactively
from config_loader import get_mysql_config


DEFAULT_FETCH_SIZE = 500
DEFAULT_DB_CONFIG = get_mysql_config()
DEFAULT_DB_HOST = DEFAULT_DB_CONFIG["host"]
DEFAULT_DB_PORT = DEFAULT_DB_CONFIG["port"]
DEFAULT_DB_USER = DEFAULT_DB_CONFIG["user"]
DEFAULT_DB_PASSWORD = DEFAULT_DB_CONFIG["password"]
DEFAULT_DB_NAME = DEFAULT_DB_CONFIG["db"]
DEFAULT_DB_CHARSET = DEFAULT_DB_CONFIG["charset"]

SHEETS_QUERY_URL = "https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{spreadsheet_token}/sheets/query"
SHEETS_WRITE_URL = "https://open.feishu.cn/open-apis/sheets/v2/spreadsheets/{spreadsheet_token}/values"


class ImportErrorWithHint(Exception):
    """Raised when import setup or execution fails."""


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import a MySQL table into a Feishu Sheets worksheet.")
    parser.add_argument("--sheet-url", required=True, help="Feishu spreadsheet URL or spreadsheet token.")
    parser.add_argument("--table", required=True, help="MySQL table name to import.")
    parser.add_argument("--worksheet-id", default="", help="Target worksheet id, for example db7efd.")
    parser.add_argument("--worksheet-title", default="", help="Target worksheet title. Used when worksheet-id is not provided.")
    parser.add_argument("--start-cell", default="A1", help="Top-left cell for import, default: A1.")
    parser.add_argument("--fetch-size", type=int, default=DEFAULT_FETCH_SIZE, help="Rows per write batch, default: 500.")
    parser.add_argument("--host", default=os.getenv("MYSQL_HOST", DEFAULT_DB_HOST))
    parser.add_argument("--port", type=int, default=int(os.getenv("MYSQL_PORT", str(DEFAULT_DB_PORT))))
    parser.add_argument("--user", default=os.getenv("MYSQL_USER", DEFAULT_DB_USER))
    parser.add_argument("--password", default=os.getenv("MYSQL_PASSWORD", DEFAULT_DB_PASSWORD))
    parser.add_argument("--db", default=os.getenv("MYSQL_DB", DEFAULT_DB_NAME))
    parser.add_argument("--charset", default=os.getenv("MYSQL_CHARSET", DEFAULT_DB_CHARSET))
    return parser.parse_args(argv)


def require_positive(value: int, name: str) -> int:
    if value <= 0:
        raise ImportErrorWithHint(f"{name} must be greater than 0.")
    return value


def validate_table_name(table_name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", table_name):
        raise ImportErrorWithHint(
            f"Invalid table name {table_name!r}. Only letters, numbers, and underscores are supported."
        )
    return table_name


def extract_spreadsheet_token(sheet_url: str) -> str:
    value = sheet_url.strip()
    if not value:
        raise ImportErrorWithHint("sheet_url cannot be empty.")

    if value.startswith("http://") or value.startswith("https://"):
        parsed = urlparse(value)
        match = re.search(r"/sheets/([A-Za-z0-9]+)", parsed.path)
        if not match:
            raise ImportErrorWithHint(f"Unable to extract spreadsheet token from URL: {value}")
        return match.group(1)

    if not re.fullmatch(r"[A-Za-z0-9]+", value):
        raise ImportErrorWithHint(f"Invalid spreadsheet token: {value}")
    return value


def parse_cell_reference(cell_ref: str) -> tuple[int, int]:
    match = re.fullmatch(r"([A-Za-z]+)([1-9][0-9]*)", cell_ref.strip())
    if not match:
        raise ImportErrorWithHint(f"Invalid start cell: {cell_ref!r}. Example: A1")

    column_letters = match.group(1).upper()
    row_index = int(match.group(2))

    column_index = 0
    for char in column_letters:
        column_index = column_index * 26 + (ord(char) - ord("A") + 1)

    return row_index, column_index


def column_number_to_letters(column_number: int) -> str:
    if column_number <= 0:
        raise ValueError("column_number must be greater than 0")

    letters: list[str] = []
    number = column_number
    while number > 0:
        number, remainder = divmod(number - 1, 26)
        letters.append(chr(ord("A") + remainder))
    return "".join(reversed(letters))


def build_range(sheet_id: str, start_row: int, start_col: int, row_count: int, col_count: int) -> str:
    end_row = start_row + row_count - 1
    end_col = start_col + col_count - 1
    start_ref = f"{column_number_to_letters(start_col)}{start_row}"
    end_ref = f"{column_number_to_letters(end_col)}{end_row}"
    return f"{sheet_id}!{start_ref}:{end_ref}"


def make_mysql_config(args: argparse.Namespace) -> dict:
    return {
        "host": args.host,
        "port": args.port,
        "user": args.user,
        "password": args.password,
        "db": args.db,
        "charset": args.charset,
        "connect_timeout": 5,
        "read_timeout": 30,
        "write_timeout": 30,
    }


def normalize_cell_value(value):
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, time):
        return value.strftime("%H:%M:%S")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def http_request_json(url: str, *, method: str = "GET", headers: dict | None = None, payload: dict | None = None) -> dict:
    request_headers = {"Content-Type": "application/json; charset=utf-8"}
    if headers:
        request_headers.update(headers)

    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    request = Request(url=url, data=data, headers=request_headers, method=method)

    try:
        with urlopen(request, timeout=30) as response:
            charset = response.headers.get_content_charset("utf-8")
            body = response.read().decode(charset)
    except HTTPError as exc:
        error_body = exc.read().decode("utf-8", errors="replace")
        raise ImportErrorWithHint(f"HTTP {exc.code} calling {url}: {error_body}") from exc
    except URLError as exc:
        raise ImportErrorWithHint(f"Network error calling {url}: {exc.reason}") from exc

    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ImportErrorWithHint(f"Invalid JSON returned by {url}: {body}") from exc


def get_user_access_token() -> str:
    token = os.getenv("FEISHU_USER_ACCESS_TOKEN", "").strip()
    if token:
        print("Using FEISHU_USER_ACCESS_TOKEN from environment.")
        return token

    print("FEISHU_USER_ACCESS_TOKEN is not set. Starting interactive OAuth flow...")
    response = get_user_access_token_interactively()
    data = response.get("data") or {}
    token = (data.get("access_token") or data.get("user_access_token") or "").strip()
    if not token:
        raise ImportErrorWithHint("OAuth completed but user_access_token was missing.")
    print("OAuth completed. user_access_token acquired.")
    return token


def get_sheets(spreadsheet_token: str, user_access_token: str) -> list[dict]:
    url = SHEETS_QUERY_URL.format(spreadsheet_token=spreadsheet_token)
    response = http_request_json(
        url,
        headers={"Authorization": f"Bearer {user_access_token}"},
    )
    if response.get("code") != 0:
        raise ImportErrorWithHint(f"Query sheets failed: {json.dumps(response, ensure_ascii=False)}")
    sheets = (response.get("data") or {}).get("sheets") or []
    if not sheets:
        raise ImportErrorWithHint("No worksheet found in the target spreadsheet.")
    return sheets


def choose_sheet(sheets: list[dict], worksheet_id: str, worksheet_title: str) -> dict:
    if worksheet_id:
        for sheet in sheets:
            if sheet.get("sheet_id") == worksheet_id:
                return sheet
        raise ImportErrorWithHint(f"Worksheet id not found: {worksheet_id}")

    if worksheet_title:
        for sheet in sheets:
            if sheet.get("title") == worksheet_title:
                return sheet
        raise ImportErrorWithHint(f"Worksheet title not found: {worksheet_title}")

    return sheets[0]


def ensure_sheet_capacity(sheet: dict, start_row: int, start_col: int, row_count: int, col_count: int) -> None:
    grid = sheet.get("grid_properties") or {}
    current_rows = int(grid.get("row_count") or 0)
    current_cols = int(grid.get("column_count") or 0)
    required_rows = start_row + row_count - 1
    required_cols = start_col + col_count - 1

    if required_rows > current_rows or required_cols > current_cols:
        raise ImportErrorWithHint(
            "Target worksheet is too small for this import. "
            f"Current size: {current_rows} rows x {current_cols} cols; "
            f"required: {required_rows} rows x {required_cols} cols."
        )


def get_table_columns_and_count(mysql_config: dict, table_name: str) -> tuple[list[str], int]:
    conn = pymysql.connect(**mysql_config)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM `{table_name}` LIMIT 0")
            columns = [desc[0] for desc in cur.description or []]
            cur.execute(f"SELECT COUNT(*) FROM `{table_name}`")
            row_count = int(cur.fetchone()[0])
    finally:
        conn.close()

    if not columns:
        raise ImportErrorWithHint(f"Table {table_name!r} has no columns.")
    return columns, row_count


def iter_table_rows(mysql_config: dict, table_name: str, fetch_size: int):
    conn = pymysql.connect(cursorclass=pymysql.cursors.SSCursor, **mysql_config)
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM `{table_name}`")
            while True:
                rows = cur.fetchmany(fetch_size)
                if not rows:
                    break
                yield [[normalize_cell_value(value) for value in row] for row in rows]
    finally:
        conn.close()


def write_values(
    spreadsheet_token: str,
    sheet_id: str,
    start_row: int,
    start_col: int,
    rows: list[list],
    user_access_token: str,
) -> dict:
    if not rows:
        return {"code": 0, "msg": "nothing to write"}

    col_count = max(len(row) for row in rows)
    normalized_rows = [row + [""] * (col_count - len(row)) for row in rows]
    value_range = build_range(sheet_id, start_row, start_col, len(normalized_rows), col_count)
    payload = {
        "valueRange": {
            "range": value_range,
            "majorDimension": "ROWS",
            "values": normalized_rows,
        }
    }
    url = SHEETS_WRITE_URL.format(spreadsheet_token=spreadsheet_token)
    response = http_request_json(
        url,
        method="PUT",
        headers={"Authorization": f"Bearer {user_access_token}"},
        payload=payload,
    )
    if response.get("code") != 0:
        raise ImportErrorWithHint(f"Write values failed: {json.dumps(response, ensure_ascii=False)}")
    return response


def import_table_to_feishu(args: argparse.Namespace) -> None:
    if pymysql is None:
        raise ImportErrorWithHint("Missing dependency: pymysql. Install it with `pip install pymysql` first.")

    fetch_size = require_positive(args.fetch_size, "fetch_size")
    table_name = validate_table_name(args.table)
    start_row, start_col = parse_cell_reference(args.start_cell)
    spreadsheet_token = extract_spreadsheet_token(args.sheet_url)
    mysql_config = make_mysql_config(args)
    user_access_token = get_user_access_token()

    print(f"Reading metadata for table `{table_name}` from database `{args.db}`...")
    columns, row_count = get_table_columns_and_count(mysql_config, table_name)
    print(f"Table columns: {len(columns)}, table rows: {row_count}")

    print("Querying Feishu worksheet list...")
    sheets = get_sheets(spreadsheet_token, user_access_token)
    target_sheet = choose_sheet(sheets, args.worksheet_id.strip(), args.worksheet_title.strip())
    sheet_id = str(target_sheet.get("sheet_id") or "")
    sheet_title = str(target_sheet.get("title") or "")

    total_rows_to_write = row_count + 1
    ensure_sheet_capacity(target_sheet, start_row, start_col, total_rows_to_write, len(columns))
    print(f"Target worksheet: {sheet_title} ({sheet_id})")
    print(f"Import start cell: {args.start_cell.upper()}")

    print("Writing header row...")
    write_values(
        spreadsheet_token,
        sheet_id,
        start_row,
        start_col,
        [columns],
        user_access_token,
    )

    next_row = start_row + 1
    written_rows = 0
    for batch_index, rows in enumerate(iter_table_rows(mysql_config, table_name, fetch_size), start=1):
        print(f"Writing batch {batch_index}: {len(rows)} rows...")
        write_values(
            spreadsheet_token,
            sheet_id,
            next_row,
            start_col,
            rows,
            user_access_token,
        )
        next_row += len(rows)
        written_rows += len(rows)

    print(
        f"Import completed. Wrote {written_rows} data rows and 1 header row to "
        f"{sheet_title} ({sheet_id})."
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        import_table_to_feishu(args)
        return 0
    except KeyboardInterrupt:
        print("Interrupted by user.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
