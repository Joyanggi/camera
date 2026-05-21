import argparse
import datetime as dt
import html
import json
import os
import random
import re
import subprocess
import sys
import time
import warnings
import webbrowser
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Dict, List, Optional

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL.*")

import requests
from bs4 import BeautifulSoup


def configure_console_encoding() -> None:
    if sys.platform == "win32":
        try:
            os.system("chcp 65001 > nul")
        except Exception:
            pass

    for stream in [sys.stdout, sys.stderr]:
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


DEFAULT_INTERVAL_SECONDS = 10
MIN_INTERVAL_SECONDS = 5
DEFAULT_BACKOFF_MINUTES = 10
DEFAULT_NAVER_DELAY_MIN_SECONDS = 3.0
DEFAULT_NAVER_DELAY_MAX_SECONDS = 6.0
MAX_RATE_LIMIT_MULTIPLIER = 3
STATE_FILE = "stock_monitor_state.json"
MACOS_SOUND_NAME = "Glass"
MACOS_SOUND_FILE = f"/System/Library/Sounds/{MACOS_SOUND_NAME}.aiff"

PRODUCT_URLS = [
    # IXUS 제품 우선 확인
    "https://brand.naver.com/canonkorea/products/13104822623",
    "https://brand.naver.com/canonkorea/products/13104765036",
    "https://estore.kr.canon/estore/detailview/41801",
    "https://estore.kr.canon/estore/detailview/41803",
    # 소니 RX100M7
    "https://store.sony.co.kr/product-view/102263765",
]

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}

NAVER_MOBILE_HEADERS = {
    **COMMON_HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
        "Mobile/15E148 Safari/604.1"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Referer": "https://search.shopping.naver.com/",
    "Upgrade-Insecure-Requests": "1",
}

SONY_API_HEADERS = {
    **COMMON_HEADERS,
    "Accept": "application/json, text/plain, */*",
    "Origin": "https://store.sony.co.kr",
    "Referer": "https://store.sony.co.kr/product-view/102263765",
    "platform": "PC",
    "clientId": "jkEJfXWkjf3NDwFlgc37xQ==",
    "Version": "1.0",
    "Content-Type": "application/json; charset=utf-8",
}


@dataclass
class CheckResult:
    url: str
    check_url: str
    name: str
    status: str
    detail: str
    retry_after_seconds: Optional[int] = None

    @property
    def is_buyable(self) -> bool:
        return self.status == "BUYABLE"

    @property
    def is_rate_limited(self) -> bool:
        return self.status == "RATE_LIMITED"

    @property
    def is_network_error(self) -> bool:
        return self.status == "NETWORK_ERROR"


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.readlines()
    except Exception as exc:
        print(f"[경고] .env 파일 읽기 실패: {type(exc).__name__}: {exc}", flush=True)
        return

    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_url(url: str) -> str:
    if "brand.naver.com/" in url:
        return url.replace("https://brand.naver.com/", "https://m.brand.naver.com/")
    return url


def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: Dict[str, Any]) -> None:
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(value or "")).strip()


def page_title(text: str, fallback: str) -> str:
    soup = BeautifulSoup(text, "html.parser")
    if soup.title and soup.title.get_text(strip=True):
        return clean_text(soup.title.get_text(" ", strip=True)).replace(" - 캐논코리아 주식회사", "")

    meta = soup.select_one('meta[property="kakao:commerce:product_name"]')
    if meta and meta.get("content"):
        return clean_text(meta["content"])

    og_title = soup.select_one('meta[property="og:title"]')
    if og_title and og_title.get("content"):
        return clean_text(og_title["content"])

    return fallback


def fetch(url: str, *, naver: bool = False) -> requests.Response:
    headers = NAVER_MOBILE_HEADERS if naver else COMMON_HEADERS
    return requests.get(url, headers=headers, timeout=15)


def parse_retry_after(value: Optional[str]) -> Optional[int]:
    if not value:
        return None

    value = value.strip()
    if value.isdigit():
        return max(int(value), 0)

    try:
        retry_at = parsedate_to_datetime(value)
    except Exception:
        return None

    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=dt.timezone.utc)

    seconds = int((retry_at - dt.datetime.now(dt.timezone.utc)).total_seconds())
    return max(seconds, 0)


def rate_limited_result(url: str, check_url: str, name: str, resp: requests.Response) -> CheckResult:
    retry_after = parse_retry_after(resp.headers.get("Retry-After"))
    if retry_after:
        detail = f"HTTP 429, 서버 요청 대기 {format_duration(retry_after)}"
    else:
        detail = "HTTP 429, 기본 백오프 적용"
    return CheckResult(url, check_url, name, "RATE_LIMITED", detail, retry_after_seconds=retry_after)


def check_canon(url: str) -> CheckResult:
    resp = fetch(url)
    name = page_title(resp.text, url.rsplit("/", 1)[-1])

    if resp.status_code == 429:
        return rate_limited_result(url, url, name, resp)

    if resp.status_code != 200:
        return CheckResult(url, url, name, "UNKNOWN", f"HTTP {resp.status_code}")

    text = resp.text
    sold_out_count = text.upper().count("SOLD OUT")
    if sold_out_count > 0:
        return CheckResult(url, url, name, "SOLD_OUT", f"SOLD OUT {sold_out_count}회")

    body_text = BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    if any(keyword in body_text for keyword in ["구매하기", "장바구니", "총 합계금액"]):
        return CheckResult(url, url, name, "BUYABLE", "SOLD OUT 문구 없음")

    return CheckResult(url, url, name, "UNKNOWN", "상품 페이지는 열렸지만 구매 상태 판정 실패")


def extract_first_status(text: str) -> Optional[str]:
    statuses = re.findall(r'"productStatusType"\s*:\s*"([^"]+)"', text)
    for status in statuses:
        if status and status.lower() != "null":
            return status
    return None


def extract_stock_quantity(text: str) -> Optional[int]:
    quantities = re.findall(r'"stockQuantity"\s*:\s*(\d+)', text)
    for value in quantities:
        try:
            qty = int(value)
        except ValueError:
            continue
        if qty > 0:
            return qty
    if quantities:
        return 0
    return None


def check_naver(url: str) -> CheckResult:
    check_url = normalize_url(url)
    resp = fetch(check_url, naver=True)
    name = page_title(resp.text, url.rsplit("/", 1)[-1])

    if resp.status_code == 429:
        return rate_limited_result(url, check_url, name, resp)

    if resp.status_code != 200:
        return CheckResult(url, check_url, name, "UNKNOWN", f"HTTP {resp.status_code}")

    status = extract_first_status(resp.text)
    stock_qty = extract_stock_quantity(resp.text)

    if status == "OUTOFSTOCK":
        return CheckResult(url, check_url, name, "SOLD_OUT", f"productStatusType={status}, stock={stock_qty}")

    if status and status != "OUTOFSTOCK":
        if stock_qty is None or stock_qty > 0:
            return CheckResult(url, check_url, name, "BUYABLE", f"productStatusType={status}, stock={stock_qty}")
        return CheckResult(url, check_url, name, "UNKNOWN", f"productStatusType={status}, stock={stock_qty}")

    if stock_qty and stock_qty > 0:
        return CheckResult(url, check_url, name, "BUYABLE", f"stock={stock_qty}")

    return CheckResult(url, check_url, name, "UNKNOWN", "네이버 상태 필드 판정 실패")


def sony_product_no(url: str) -> Optional[int]:
    match = re.search(r"/product-view/(\d+)", url)
    if not match:
        return None
    return int(match.group(1))


def check_sony(url: str) -> CheckResult:
    product_no = sony_product_no(url)
    if not product_no:
        return CheckResult(url, url, url, "UNKNOWN", "소니 상품 번호를 찾지 못함")

    resp = requests.post(
        "https://shop-api.e-ncp.com/products/search-by-nos",
        headers={**SONY_API_HEADERS, "Referer": url},
        json={"productNos": [product_no], "hasOptionValues": True},
        timeout=15,
    )
    fallback_name = str(product_no)

    if resp.status_code == 429:
        return rate_limited_result(url, url, fallback_name, resp)

    if resp.status_code != 200:
        return CheckResult(url, url, fallback_name, "UNKNOWN", f"HTTP {resp.status_code}")

    data = resp.json()
    products = data.get("products") or []
    if not products:
        return CheckResult(url, url, fallback_name, "UNKNOWN", "소니 API에서 상품 정보 없음")

    product = products[0]
    base_info = product.get("baseInfo") or {}
    status_info = product.get("status") or {}
    option_values = product.get("optionValues") or []

    name = clean_text(base_info.get("productName") or fallback_name)
    sale_status = status_info.get("saleStatusType")
    soldout = status_info.get("soldout")
    display = status_info.get("display")
    stock_values = [
        value
        for value in [
            base_info.get("stockCnt"),
            base_info.get("mainStockCnt"),
            *(option.get("stockCnt") for option in option_values),
        ]
        if isinstance(value, int)
    ]
    max_stock = max(stock_values) if stock_values else None

    detail = f"saleStatusType={sale_status}, soldout={soldout}, stock={max_stock}"

    if display is False:
        return CheckResult(url, url, name, "SOLD_OUT", f"{detail}, display=false")

    if sale_status in {"STOP", "PROHIBITION", "READY", "FINISHED"}:
        return CheckResult(url, url, name, "SOLD_OUT", detail)

    if soldout is True:
        return CheckResult(url, url, name, "SOLD_OUT", detail)

    if sale_status == "ONSALE" and soldout is False:
        return CheckResult(url, url, name, "BUYABLE", detail)

    if max_stock is not None and max_stock > 0:
        return CheckResult(url, url, name, "BUYABLE", detail)

    return CheckResult(url, url, name, "UNKNOWN", detail)


def check_product(url: str) -> CheckResult:
    if "store.sony.co.kr/" in url:
        return check_sony(url)
    if "naver.com/" in url:
        return check_naver(url)
    if "estore.kr.canon/" in url:
        return check_canon(url)
    return CheckResult(url, normalize_url(url), url, "UNKNOWN", "지원하지 않는 URL")


def source_label(url: str) -> str:
    if "store.sony.co.kr/" in url:
        return "소니"
    if "estore.kr.canon/" in url:
        return "공홈"
    if "naver.com/" in url:
        return "네이버"
    return "기타"


def is_network_error_exception(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".lower()
    network_markers = [
        "nameresolutionerror",
        "failed to resolve",
        "nodename nor servname",
        "no route to host",
        "newconnectionerror",
        "failed to establish a new connection",
        "temporary failure in name resolution",
        "network is unreachable",
    ]
    return isinstance(exc, requests.exceptions.ConnectionError) or any(marker in text for marker in network_markers)


def network_error_detail(exc: Exception) -> str:
    text = str(exc)
    if "Failed to resolve" in text or "nodename nor servname" in text or "NameResolutionError" in text:
        return "DNS 조회 실패"
    if "No route to host" in text:
        return "네트워크 경로 없음"
    if "Failed to establish a new connection" in text:
        return "연결 생성 실패"
    return f"{type(exc).__name__}: {text[:160]}"


def format_duration(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    minutes, sec = divmod(seconds, 60)
    if minutes:
        return f"{minutes}분 {sec}초"
    return f"{sec}초"


def request_delay_seconds(source: str, args: argparse.Namespace) -> float:
    if source == "네이버":
        delay_min = max(float(args.naver_delay_min), 0.0)
        delay_max = max(float(args.naver_delay_max), delay_min)
        return random.uniform(delay_min, delay_max)

    return random.uniform(0.8, 1.8)


def source_backoff_seconds(source: str, args: argparse.Namespace) -> int:
    if source == "네이버":
        return max(args.naver_backoff_minutes, 1) * 60
    return max(args.backoff_minutes, 1) * 60


def safe_osascript_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def run_system_command(args: List[str], label: str) -> bool:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        return True

    reason = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
    print(f"[경고] {label} 실패: {reason}", flush=True)
    return False


def enabled_external_targets() -> List[str]:
    targets = []
    if os.environ.get("SLACK_WEBHOOK_URL"):
        targets.append("Slack")
    return targets


def send_slack_notification(text: str) -> None:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return

    try:
        resp = requests.post(webhook_url, json={"text": text}, timeout=10)
        if resp.status_code >= 400:
            print(f"[경고] Slack 알림 실패: HTTP {resp.status_code} {resp.text[:200]}", flush=True)
    except Exception as exc:
        print(f"[경고] Slack 알림 실패: {type(exc).__name__}: {exc}", flush=True)


def send_external_notifications(title: str, message: str, url: str = "") -> None:
    text = f"[{title}]\n{message}"
    if url:
        text = f"{text}\n{url}"

    send_slack_notification(text)


def play_alert_sound(sound_repeats: int = 2) -> None:
    if sys.platform == "darwin":
        for _ in range(max(sound_repeats, 0)):
            run_system_command(["afplay", MACOS_SOUND_FILE], "알림음 재생")
    elif sys.platform == "win32":
        try:
            import winsound

            for _ in range(max(sound_repeats, 1)):
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
                time.sleep(0.25)
        except Exception:
            pass
    else:
        print("\a", end="", flush=True)


def open_product_page(url: str) -> None:
    if not url:
        return

    try:
        opened = webbrowser.open(url)
    except Exception as exc:
        print(f"[경고] 상품 페이지 열기 실패: {type(exc).__name__}: {exc}", flush=True)
        return

    if not opened:
        print("[경고] 상품 페이지 열기 실패: 기본 브라우저를 찾지 못했습니다.", flush=True)


def send_notification(
    title: str,
    message: str,
    url: str = "",
    open_url: bool = False,
    sound_repeats: int = 2,
) -> None:
    print(f"\n{'=' * 72}", flush=True)
    print(f"[알림] {title}", flush=True)
    print(message, flush=True)
    if url:
        print(f"링크: {url}", flush=True)
    print(f"{'=' * 72}\n", flush=True)

    send_external_notifications(title, message, url)

    if sys.platform == "darwin":
        script = (
            f'display notification "{safe_osascript_text(message[:180])}" '
            f'with title "{safe_osascript_text(title[:80])}" '
            f'sound name "{MACOS_SOUND_NAME}"'
        )
        run_system_command(["osascript", "-e", script], "macOS 알림")
    elif sys.platform == "win32":
        try:
            from plyer import notification

            notification.notify(title=title, message=message[:200], app_name="카메라 재고 모니터", timeout=10)
        except Exception:
            pass

    play_alert_sound(sound_repeats)

    if open_url and url:
        open_product_page(url)


def print_result(index: int, total: int, result: CheckResult) -> None:
    label = {
        "BUYABLE": "구매가능",
        "SOLD_OUT": "품절",
        "UNKNOWN": "확인필요",
        "NETWORK_ERROR": "네트워크오류",
        "RATE_LIMITED": "차단대기",
        "SKIPPED": "건너뜀",
    }.get(result.status, result.status)
    source = source_label(result.url)
    print(f"  [{index}/{total}] [{source}] {label} {result.name} | {result.detail}", flush=True)


def main() -> None:
    configure_console_encoding()
    load_env_file()

    parser = argparse.ArgumentParser(description="카메라 재고 모니터")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="확인 주기(초). 너무 짧으면 429가 날 수 있음")
    parser.add_argument("--once", action="store_true", help="한 번만 확인하고 종료")
    parser.add_argument("--open", action="store_true", help="구매 가능 감지 시 상품 페이지 열기")
    parser.add_argument("--test-alert", action="store_true", help="재고 확인 없이 알림과 소리만 테스트")
    parser.add_argument("--sound-repeats", type=int, default=2, help="재고 감지 시 추가 알림음 반복 횟수")
    parser.add_argument("--backoff-minutes", type=int, default=DEFAULT_BACKOFF_MINUTES, help="HTTP 429 감지 시 해당 사이트를 쉬는 시간")
    parser.add_argument("--naver-backoff-minutes", type=int, default=DEFAULT_BACKOFF_MINUTES, help="네이버 HTTP 429 감지 시 네이버만 쉬는 시간")
    parser.add_argument("--naver-delay-min", type=float, default=DEFAULT_NAVER_DELAY_MIN_SECONDS, help="네이버 상품 요청 후 최소 대기 시간(초)")
    parser.add_argument("--naver-delay-max", type=float, default=DEFAULT_NAVER_DELAY_MAX_SECONDS, help="네이버 상품 요청 후 최대 대기 시간(초)")
    args = parser.parse_args()

    interval = max(args.interval, MIN_INTERVAL_SECONDS)
    backoff_seconds = max(args.backoff_minutes, 1) * 60
    state = load_state()
    backoff_until: Dict[str, dt.datetime] = {}
    rate_limit_counts: Dict[str, int] = {}
    cycle = 0

    if args.test_alert:
        send_notification(
            "카메라 재고 모니터 테스트",
            "이 알림과 소리가 들리면 재고 풀림 감지 알림도 받을 수 있습니다.",
            sound_repeats=args.sound_repeats,
        )
        return

    print("=" * 72, flush=True)
    print("카메라 재고 모니터링 시작 | Ctrl+C 로 종료", flush=True)
    print(f"확인 상품: {len(PRODUCT_URLS)}개", flush=True)
    print(f"확인 주기: {interval}초 이상", flush=True)
    print(f"429 백오프: 사이트별 {format_duration(backoff_seconds)} 이상", flush=True)
    print(
        f"네이버 요청 간격: {args.naver_delay_min:.1f}~{args.naver_delay_max:.1f}초",
        flush=True,
    )
    targets = enabled_external_targets()
    print(f"외부 알림: {', '.join(targets) if targets else '없음'}", flush=True)
    print("=" * 72, flush=True)

    while True:
        cycle += 1
        print(f"\n[{now()}] #{cycle} 확인 중", flush=True)
        buyable_results: List[CheckResult] = []

        for idx, url in enumerate(PRODUCT_URLS, start=1):
            source = source_label(url)
            source_backoff_until = backoff_until.get(source)
            if source_backoff_until and dt.datetime.now() < source_backoff_until:
                remaining = int((source_backoff_until - dt.datetime.now()).total_seconds())
                fallback_name = state.get(url, {}).get("name") or url.rsplit("/", 1)[-1]
                result = CheckResult(url, normalize_url(url), fallback_name, "SKIPPED", f"{format_duration(remaining)} 후 재개")
                print_result(idx, len(PRODUCT_URLS), result)
                continue

            try:
                result = check_product(url)
            except Exception as exc:
                if is_network_error_exception(exc):
                    result = CheckResult(url, normalize_url(url), url, "NETWORK_ERROR", network_error_detail(exc))
                else:
                    result = CheckResult(url, normalize_url(url), url, "UNKNOWN", f"{type(exc).__name__}: {exc}")

            print_result(idx, len(PRODUCT_URLS), result)

            if result.is_rate_limited:
                retry_seconds = result.retry_after_seconds or 0
                rate_limit_counts[source] = min(
                    rate_limit_counts.get(source, 0) + 1,
                    MAX_RATE_LIMIT_MULTIPLIER,
                )
                source_backoff = source_backoff_seconds(source, args) * rate_limit_counts[source]
                wait_seconds = max(source_backoff, retry_seconds) + random.randint(30, 120)
                backoff_until[source] = dt.datetime.now() + dt.timedelta(seconds=wait_seconds)
                print(
                    f"  [{source}] 429 감지: {format_duration(wait_seconds)} 동안 {source} 요청을 건너뜁니다. "
                    f"(연속 {rate_limit_counts[source]}회)",
                    flush=True,
                )
                continue

            if result.is_network_error:
                continue

            rate_limit_counts[source] = 0

            previous_status = state.get(url, {}).get("status")
            state[url] = {
                "status": result.status,
                "name": result.name,
                "detail": result.detail,
                "checked_at": now(),
            }

            if result.is_buyable and previous_status != "BUYABLE":
                buyable_results.append(result)

            time.sleep(request_delay_seconds(source, args))

        save_state(state)

        for result in buyable_results:
            send_notification(
                "카메라 재고 풀림 감지!",
                f"[{source_label(result.url)}] {result.name} 구매 가능 신호: {result.detail}",
                result.url,
                open_url=args.open,
                sound_repeats=args.sound_repeats,
            )

        if args.once:
            break

        sleep_seconds = interval + random.uniform(0, 5)
        print(f"[{now()}] 다음 확인까지 약 {sleep_seconds:.1f}초 대기", flush=True)
        try:
            time.sleep(sleep_seconds)
        except KeyboardInterrupt:
            print("\n종료.", flush=True)
            break


if __name__ == "__main__":
    main()
