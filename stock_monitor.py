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
from dataclasses import dataclass
from typing import Optional

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL.*")

import requests
from bs4 import BeautifulSoup


DEFAULT_INTERVAL_SECONDS = 35
STATE_FILE = "stock_monitor_state.json"
MACOS_SOUND_NAME = "Glass"
MACOS_SOUND_FILE = f"/System/Library/Sounds/{MACOS_SOUND_NAME}.aiff"

PRODUCT_URLS = [
    "https://estore.kr.canon/estore/detailview/41801",
    "https://estore.kr.canon/estore/detailview/41803",
    "https://estore.kr.canon/estore/detailview/42895",
    "https://estore.kr.canon/estore/detailview/40262",
    "https://estore.kr.canon/estore/detailview/40260",
    "https://brand.naver.com/canonkorea/products/13104822623",
    "https://brand.naver.com/canonkorea/products/13104765036",
    "https://brand.naver.com/canonkorea/products/10366295455",
    "https://brand.naver.com/canonkorea/products/10366295456",
]

COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
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
    "Referer": "https://search.shopping.naver.com/",
}


@dataclass
class CheckResult:
    url: str
    check_url: str
    name: str
    status: str
    detail: str

    @property
    def is_buyable(self) -> bool:
        return self.status == "BUYABLE"


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_url(url: str) -> str:
    if "brand.naver.com/" in url:
        return url.replace("https://brand.naver.com/", "https://m.brand.naver.com/")
    return url


def load_state() -> dict:
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state: dict) -> None:
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


def check_canon(url: str) -> CheckResult:
    resp = fetch(url)
    name = page_title(resp.text, url.rsplit("/", 1)[-1])

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


def check_product(url: str) -> CheckResult:
    if "naver.com/" in url:
        return check_naver(url)
    if "estore.kr.canon/" in url:
        return check_canon(url)
    return CheckResult(url, normalize_url(url), url, "UNKNOWN", "지원하지 않는 URL")


def source_label(url: str) -> str:
    if "estore.kr.canon/" in url:
        return "공홈"
    if "naver.com/" in url:
        return "네이버"
    return "기타"


def safe_osascript_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def run_system_command(args: list[str], label: str) -> bool:
    result = subprocess.run(args, check=False, capture_output=True, text=True)
    if result.returncode == 0:
        return True

    reason = (result.stderr or result.stdout or f"exit code {result.returncode}").strip()
    print(f"[경고] {label} 실패: {reason}", flush=True)
    return False


def play_alert_sound(sound_repeats: int = 2) -> None:
    if sys.platform == "darwin":
        for _ in range(max(sound_repeats, 0)):
            run_system_command(["afplay", MACOS_SOUND_FILE], "알림음 재생")
    elif sys.platform == "win32":
        try:
            import winsound

            for _ in range(max(sound_repeats, 1)):
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        except Exception:
            pass


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

    if sys.platform == "darwin":
        script = (
            f'display notification "{safe_osascript_text(message[:180])}" '
            f'with title "{safe_osascript_text(title[:80])}" '
            f'sound name "{MACOS_SOUND_NAME}"'
        )
        run_system_command(["osascript", "-e", script], "macOS 알림")
        play_alert_sound(sound_repeats)
        if open_url and url:
            run_system_command(["open", url], "상품 페이지 열기")
    elif sys.platform == "win32":
        try:
            from plyer import notification

            notification.notify(title=title, message=message[:200], app_name="카메라 재고 모니터", timeout=10)
        except Exception:
            pass
        play_alert_sound(sound_repeats)


def print_result(index: int, total: int, result: CheckResult) -> None:
    label = {"BUYABLE": "구매가능", "SOLD_OUT": "품절", "UNKNOWN": "확인필요"}.get(result.status, result.status)
    source = source_label(result.url)
    print(f"  [{index}/{total}] [{source}] {label} {result.name} | {result.detail}", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="캐논/네이버 카메라 재고 모니터")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SECONDS, help="확인 주기(초). 권장: 30초 이상")
    parser.add_argument("--once", action="store_true", help="한 번만 확인하고 종료")
    parser.add_argument("--open", action="store_true", help="구매 가능 감지 시 상품 페이지 열기")
    parser.add_argument("--test-alert", action="store_true", help="재고 확인 없이 알림과 소리만 테스트")
    parser.add_argument("--sound-repeats", type=int, default=2, help="재고 감지 시 추가 알림음 반복 횟수")
    args = parser.parse_args()

    interval = max(args.interval, 30)
    state = load_state()
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
    print("=" * 72, flush=True)

    while True:
        cycle += 1
        print(f"\n[{now()}] #{cycle} 확인 중", flush=True)
        buyable_results = []

        for idx, url in enumerate(PRODUCT_URLS, start=1):
            try:
                result = check_product(url)
            except Exception as exc:
                result = CheckResult(url, normalize_url(url), url, "UNKNOWN", f"{type(exc).__name__}: {exc}")

            print_result(idx, len(PRODUCT_URLS), result)

            previous_status = state.get(url, {}).get("status")
            state[url] = {
                "status": result.status,
                "name": result.name,
                "detail": result.detail,
                "checked_at": now(),
            }

            if result.is_buyable and previous_status != "BUYABLE":
                buyable_results.append(result)

            time.sleep(random.uniform(0.8, 1.8))

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
