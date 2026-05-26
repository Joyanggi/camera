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


PRODUCT_URL = "https://www.saeki.co.kr/item/itemDetail?itemId=PD00041564"
DEFAULT_INTERVAL_SECONDS = 10
MIN_INTERVAL_SECONDS = 5
DEFAULT_BACKOFF_MINUTES = 10
STATE_FILE = "saeki_monitor_state.json"
MACOS_SOUND_NAME = "Glass"
MACOS_SOUND_FILE = f"/System/Library/Sounds/{MACOS_SOUND_NAME}.aiff"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://www.saeki.co.kr/",
}


@dataclass
class CheckResult:
    url: str
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


def meta_content(soup: BeautifulSoup, selector: str) -> Optional[str]:
    tag = soup.select_one(selector)
    if tag and tag.get("content"):
        return clean_text(tag["content"])
    return None


def product_name(soup: BeautifulSoup) -> str:
    for selector in [
        'meta[property="og:title"]',
        'meta[name="twitter:title"]',
        'input#itemNm',
    ]:
        tag = soup.select_one(selector)
        if not tag:
            continue
        value = tag.get("content") or tag.get("value")
        if value:
            return clean_text(value)
    return "RICOH GR IV"


def parse_json_ld_availability(soup: BeautifulSoup) -> Optional[str]:
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            data = json.loads(raw)
        except Exception:
            continue
        availability = find_availability(data)
        if availability:
            return availability
    return None


def find_availability(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        availability = value.get("availability")
        if availability:
            return str(availability)
        offers = value.get("offers")
        if offers:
            found = find_availability(offers)
            if found:
                return found
        for item in value.values():
            found = find_availability(item)
            if found:
                return found
    elif isinstance(value, list):
        for item in value:
            found = find_availability(item)
            if found:
                return found
    return None


def page_signals(text: str) -> Dict[str, Optional[str]]:
    soup = BeautifulSoup(text, "html.parser")
    schema_availability = parse_json_ld_availability(soup)
    product_availability = meta_content(soup, 'meta[property="product:availability"]')
    recopick_availability = meta_content(soup, 'meta[property="recopick:availability"]')
    order_group = soup.select_one('#orderItemArea .ui-price-group[data-id="PD00041564"]')
    sale_status = order_group.get("data-status") if order_group else None
    has_notice_button = soup.select_one("#btnWhrcvNotice, #btnBtmWhrcvNotice") is not None

    return {
        "name": product_name(soup),
        "schema_availability": schema_availability,
        "product_availability": product_availability,
        "recopick_availability": recopick_availability,
        "sale_status": sale_status,
        "has_notice_button": "Y" if has_notice_button else "N",
    }


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


def format_duration(seconds: int) -> str:
    seconds = max(int(seconds), 0)
    minutes, sec = divmod(seconds, 60)
    if minutes:
        return f"{minutes}분 {sec}초"
    return f"{sec}초"


def check_product() -> CheckResult:
    resp = requests.get(PRODUCT_URL, headers=HEADERS, timeout=15)
    fallback_name = "RICOH GR IV"

    if resp.status_code == 429:
        retry_after = parse_retry_after(resp.headers.get("Retry-After"))
        detail = "HTTP 429, 기본 백오프 적용"
        if retry_after:
            detail = f"HTTP 429, 서버 요청 대기 {format_duration(retry_after)}"
        return CheckResult(PRODUCT_URL, fallback_name, "RATE_LIMITED", detail, retry_after)

    if resp.status_code != 200:
        return CheckResult(PRODUCT_URL, fallback_name, "UNKNOWN", f"HTTP {resp.status_code}")

    signals = page_signals(resp.text)
    name = signals["name"] or fallback_name
    availability_values = [
        (signals.get("schema_availability") or "").lower(),
        (signals.get("product_availability") or "").lower(),
        (signals.get("recopick_availability") or "").lower(),
    ]
    detail = (
        f"schema={signals.get('schema_availability')}, "
        f"product={signals.get('product_availability')}, "
        f"recopick={signals.get('recopick_availability')}, "
        f"saleStatus={signals.get('sale_status')}, "
        f"noticeButton={signals.get('has_notice_button')}"
    )

    if any(value.endswith("/outofstock") or value in {"out of stock", "oos", "outofstock"} for value in availability_values):
        return CheckResult(PRODUCT_URL, name, "SOLD_OUT", detail)

    if signals.get("has_notice_button") == "Y":
        return CheckResult(PRODUCT_URL, name, "SOLD_OUT", detail)

    if any(value.endswith("/instock") or value in {"in stock", "instock", "available"} for value in availability_values):
        return CheckResult(PRODUCT_URL, name, "BUYABLE", detail)

    return CheckResult(PRODUCT_URL, name, "UNKNOWN", detail)


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


def run_system_command(args: List[str], label: str) -> bool:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        return True
    reason = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
    print(f"[경고] {label} 실패: {reason}", flush=True)
    return False


def try_system_command(args: List[str]) -> bool:
    try:
        result = subprocess.run(args, check=False, capture_output=True, text=True)
    except Exception:
        return False
    return result.returncode == 0


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


def osascript_text(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def open_product_page(url: str) -> None:
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
            f"display notification {osascript_text(message[:180])} "
            f"with title {osascript_text(title[:80])} "
            f"sound name {osascript_text(MACOS_SOUND_NAME)}"
        )
        try_system_command(["osascript", "-e", script])
    elif sys.platform == "win32":
        try:
            from plyer import notification
            notification.notify(title=title, message=message[:200], app_name="세기몰 재고 모니터", timeout=10)
        except Exception:
            pass

    play_alert_sound(sound_repeats)

    if open_url and url:
        open_product_page(url)


def print_result(result: CheckResult) -> None:
    label = {
        "BUYABLE": "구매가능",
        "SOLD_OUT": "품절",
        "UNKNOWN": "확인필요",
        "NETWORK_ERROR": "네트워크오류",
        "RATE_LIMITED": "차단대기",
        "SKIPPED": "건너뜀",
    }.get(result.status, result.status)
    print(f"  [세기몰] {label} {result.name} | {result.detail}", flush=True)


def main() -> None:
    configure_console_encoding()
    load_env_file()

    parser = argparse.ArgumentParser(description="세기몰 RICOH GR IV 재고 모니터")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="확인 주기(초)")
    parser.add_argument("--once", action="store_true", help="한 번만 확인하고 종료")
    parser.add_argument("--open", action="store_true", help="구매 가능 감지 시 상품 페이지 열기")
    parser.add_argument("--test-alert", action="store_true", help="재고 확인 없이 알림과 소리만 테스트")
    parser.add_argument("--sound-repeats", type=int, default=2, help="재고 감지 시 추가 알림음 반복 횟수")
    parser.add_argument("--backoff-minutes", type=int, default=DEFAULT_BACKOFF_MINUTES, help="HTTP 429 감지 시 쉬는 시간")
    args = parser.parse_args()

    interval = max(args.interval, MIN_INTERVAL_SECONDS)
    backoff_seconds = max(args.backoff_minutes, 1) * 60
    state = load_state()
    backoff_until: Optional[dt.datetime] = None
    cycle = 0

    if args.test_alert:
        send_notification(
            "세기몰 재고 모니터 테스트",
            "이 알림과 소리가 들리면 재고 풀림 감지 알림도 받을 수 있습니다.",
            PRODUCT_URL,
            open_url=False,
            sound_repeats=args.sound_repeats,
        )
        return

    print("=" * 72, flush=True)
    print("세기몰 RICOH GR IV 재고 모니터링 시작 | Ctrl+C 로 종료", flush=True)
    print(f"확인 상품: {PRODUCT_URL}", flush=True)
    print(f"확인 주기: {interval}초 이상", flush=True)
    print(f"429 백오프: {format_duration(backoff_seconds)} 이상", flush=True)
    targets = enabled_external_targets()
    print(f"외부 알림: {', '.join(targets) if targets else '없음'}", flush=True)
    print("=" * 72, flush=True)

    while True:
        cycle += 1
        print(f"\n[{now()}] #{cycle} 확인 중", flush=True)

        if backoff_until and dt.datetime.now() < backoff_until:
            remaining = int((backoff_until - dt.datetime.now()).total_seconds())
            result = CheckResult(PRODUCT_URL, state.get("name") or "RICOH GR IV", "SKIPPED", f"{format_duration(remaining)} 후 재개")
            print_result(result)
        else:
            try:
                result = check_product()
            except Exception as exc:
                if is_network_error_exception(exc):
                    result = CheckResult(PRODUCT_URL, "RICOH GR IV", "NETWORK_ERROR", network_error_detail(exc))
                else:
                    result = CheckResult(PRODUCT_URL, "RICOH GR IV", "UNKNOWN", f"{type(exc).__name__}: {exc}")

            print_result(result)

            if result.is_rate_limited:
                retry_seconds = result.retry_after_seconds or 0
                wait_seconds = max(backoff_seconds, retry_seconds) + random.randint(30, 120)
                backoff_until = dt.datetime.now() + dt.timedelta(seconds=wait_seconds)
                print(f"  [세기몰] 429 감지: {format_duration(wait_seconds)} 동안 요청을 건너뜁니다.", flush=True)
            elif not result.is_network_error:
                previous_status = state.get("status")
                state = {
                    "status": result.status,
                    "name": result.name,
                    "detail": result.detail,
                    "checked_at": now(),
                }
                save_state(state)

                if result.is_buyable and previous_status != "BUYABLE":
                    send_notification(
                        "세기몰 RICOH GR IV 재고 풀림 감지!",
                        f"{result.name} 구매 가능 신호: {result.detail}",
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
