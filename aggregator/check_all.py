"""모든 모니터 폴더의 재고 체크를 한 번에 실행해 status.json으로 출력.

- 각 모니터 폴더의 stock_monitor.py를 import하여 check_product / check_product()를 호출
- 이전 status.json과 비교해 SOLD_OUT→BUYABLE 전환 시 Slack 알림 발사
- 결과는 repo root의 status.json에 저장
"""

import datetime as dt
import importlib.util
import json
import os
import sys
import time
import warnings
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL.*")

import requests

ROOT = Path(__file__).resolve().parent.parent
STATUS_FILE = ROOT / "status.json"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec or not spec.loader:
        raise RuntimeError(f"failed to load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mac = load_module("mac_monitor", ROOT / "mac-camera-stock-monitor" / "stock_monitor.py")
saeki = load_module("saeki_monitor", ROOT / "saeki-gr4-stock-monitor" / "stock_monitor.py")
plthink = load_module("plthink_monitor", ROOT / "plthink-ricoh-stock-monitor" / "stock_monitor.py")


SITE_LABEL_BY_URL_PART = [
    ("plthink.com", "유쾌한생각"),
    ("compuzone.co.kr", "컴퓨존"),
    ("dkc.kr", "DKC"),
    ("saeki.co.kr", "세기몰"),
    ("store.sony.co.kr", "소니공홈"),
    ("estore.kr.canon", "캐논공홈"),
    ("brand.naver.com", "네이버스토어"),
]


def site_label(url: str) -> str:
    for marker, label in SITE_LABEL_BY_URL_PART:
        if marker in url:
            return label
    return "기타"


def normalize_result(url: str, result, source: str) -> Dict[str, Any]:
    return {
        "site": site_label(url),
        "source_module": source,
        "url": url,
        "name": getattr(result, "name", url),
        "status": getattr(result, "status", "UNKNOWN"),
        "detail": getattr(result, "detail", ""),
    }


NETWORK_ERROR_MARKERS = (
    "nameresolutionerror",
    "failed to resolve",
    "connecttimeout",
    "read timed out",
    "connection timed out",
    "newconnectionerror",
    "failed to establish a new connection",
    "max retries exceeded",
    "temporary failure in name resolution",
)


def is_transient_network_error(exc: Exception) -> bool:
    if isinstance(exc, (requests.exceptions.ConnectionError, requests.exceptions.Timeout)):
        return True
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in NETWORK_ERROR_MARKERS)


def short_network_detail(exc: Exception) -> str:
    text = str(exc)
    if "Failed to resolve" in text or "NameResolutionError" in text or "Temporary failure in name resolution" in text:
        return "DNS 조회 실패 (러너 일시 장애 가능)"
    if "ConnectTimeout" in type(exc).__name__ or "timed out" in text.lower():
        return "연결 타임아웃 (사이트 응답 지연 또는 IP 차단 가능)"
    if "ConnectionError" in type(exc).__name__:
        return "연결 실패"
    return f"{type(exc).__name__}"


def run_with_retry(fn, *args, retries: int = 1, retry_delay: float = 2.0):
    """일시 네트워크 오류면 retries 회 재시도. 그래도 실패하면 마지막 예외 그대로 raise."""
    last_exc: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            return fn(*args)
        except Exception as exc:
            last_exc = exc
            if attempt < retries and is_transient_network_error(exc):
                time.sleep(retry_delay)
                continue
            raise
    if last_exc:
        raise last_exc


def call_with_fallback(source: str, url: str, fallback_name: str, fn, *args) -> Dict[str, Any]:
    try:
        result = run_with_retry(fn, *args)
        return normalize_result(url, result, source)
    except Exception as exc:
        if is_transient_network_error(exc):
            status = "NETWORK_ERROR"
            detail = short_network_detail(exc)
        else:
            status = "ERROR"
            detail = f"{type(exc).__name__}: {str(exc)[:200]}"
        return {
            "site": site_label(url),
            "source_module": source,
            "url": url,
            "name": fallback_name,
            "status": status,
            "detail": detail,
        }


def run_all_checks() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    for url in mac.PRODUCT_URLS:
        results.append(call_with_fallback("mac", url, url, mac.check_product, url))
        time.sleep(1.0)

    results.append(call_with_fallback(
        "saeki", saeki.PRODUCT_URL, "RICOH GR IV (세기몰)", saeki.check_product,
    ))
    time.sleep(1.0)

    for url in plthink.PRODUCT_URLS:
        results.append(call_with_fallback("plthink", url, url, plthink.check_product, url))
        time.sleep(1.0)

    return results


def load_previous() -> Dict[str, Any]:
    if not STATUS_FILE.exists():
        return {}
    try:
        with STATUS_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def previous_status_by_url(previous: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for item in previous.get("products", []):
        url = item.get("url")
        status = item.get("status")
        if url and status:
            out[url] = status
    return out


def previous_full_by_url(previous: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for item in previous.get("products", []):
        url = item.get("url")
        if url:
            out[url] = item
    return out


def carry_forward_transient(results: List[Dict[str, Any]], previous: Dict[str, Any]) -> List[Dict[str, Any]]:
    """현재가 NETWORK_ERROR/ERROR고 이전이 BUYABLE/SOLD_OUT/UNKNOWN였으면 이전 값을 들고 가고
    detail에 stale 마커를 붙임. 전환 감지에 영향 없도록 'last_good_status'도 별도 보존."""
    prev_full = previous_full_by_url(previous)
    prev_checked_at = previous.get("checked_at")
    out = []
    for item in results:
        url = item.get("url")
        if item.get("status") in {"NETWORK_ERROR", "ERROR"} and url in prev_full:
            prev = prev_full[url]
            prev_status = prev.get("status")
            if prev_status in {"BUYABLE", "SOLD_OUT", "UNKNOWN"}:
                merged = dict(prev)
                merged["detail"] = (
                    f"{prev.get('detail', '')} (stale: {item.get('detail', '')}, "
                    f"last good {prev_checked_at})"
                )
                merged["stale"] = True
                merged["stale_reason"] = item.get("detail", "")
                out.append(merged)
                continue
        out.append(item)
    return out


def send_slack(text: str) -> None:
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook:
        return
    try:
        resp = requests.post(webhook, json={"text": text}, timeout=10)
        if resp.status_code >= 400:
            print(f"[경고] Slack 알림 실패: HTTP {resp.status_code}", flush=True)
    except Exception as exc:
        print(f"[경고] Slack 알림 실패: {type(exc).__name__}: {exc}", flush=True)


def detect_transitions(previous: Dict[str, str], current: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    transitions = []
    for item in current:
        if item.get("status") != "BUYABLE":
            continue
        prev = previous.get(item.get("url", ""))
        if prev == "BUYABLE":
            continue
        transitions.append(item)
    return transitions


def main() -> int:
    print(f"[{dt.datetime.now().isoformat()}] check_all 시작", flush=True)

    if os.environ.get("TEST_SLACK", "").lower() in {"true", "1", "yes"}:
        now_str = dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat()
        text = (
            "[🧪 슬랙 테스트] 이 메시지가 보이면 GitHub Actions → Slack 웹훅 경로가 정상 동작합니다.\n"
            f"시간: {now_str}"
        )
        send_slack(text)
        print(f"[{now_str}] TEST_SLACK 모드: 테스트 메시지 전송 후 종료", flush=True)
        return 0

    previous = load_previous()
    previous_status = previous_status_by_url(previous)

    raw_results = run_all_checks()
    results = carry_forward_transient(raw_results, previous)

    for item in results:
        marker = " (stale)" if item.get("stale") else ""
        print(f"  [{item['site']}] {item['status']}{marker} {item['name']} | {item['detail']}", flush=True)

    transitions = detect_transitions(previous_status, results)
    for item in transitions:
        text = (
            f"[재고 풀림 감지!] [{item['site']}] {item['name']}\n"
            f"신호: {item['detail']}\n"
            f"{item['url']}"
        )
        send_slack(text)
        print(f"[알림] Slack 전송: {item['name']}", flush=True)

    payload = {
        "checked_at": dt.datetime.now(dt.timezone(dt.timedelta(hours=9))).isoformat(),
        "products": results,
    }
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[{dt.datetime.now().isoformat()}] {STATUS_FILE} 작성 완료 ({len(results)}개 항목)", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
