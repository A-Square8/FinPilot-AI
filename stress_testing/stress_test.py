

import asyncio
import time
import json
import statistics
import argparse
import os
import sys
from dataclasses import dataclass, field, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aiohttp



RENDER_FREE = {
    "ram_mb": 512,
    "vcpu": 0.1,
    "bandwidth_gb_month": 100,
    "cold_start_seconds": 45,
    "spin_down_after_minutes": 15,
}

GEMINI_FREE = {
    "gemini_2_5_flash": {"rpm": 10, "rpd": 250, "tpm": 250_000},
    "gemini_2_5_flash_lite": {"rpm": 15, "rpd": 1000, "tpm": 250_000},
}

SUPABASE_FREE = {
    "max_direct_connections": 60,
    "max_pooler_connections": 200,
    "db_size_mb": 500,
    "egress_gb_month": 5,
    "mau": 50_000,
}

UPSTASH_FREE = {
    "commands_per_day": 10_000,
    "storage_mb": 256,
    "max_concurrent_connections": 10_000,
}

TELEGRAM_BOT_LIMITS = {
    "webhook_max_connections": 100,
    "send_per_second_global": 30,
    "send_per_second_per_chat": 1,
    "send_per_minute_group": 20,
}

# Average token usage per FinPilot request (measured from collector agent prompts)
AVG_INPUT_TOKENS_PER_REQUEST = 350
AVG_OUTPUT_TOKENS_PER_REQUEST = 120
AVG_TOKENS_PER_REQUEST = AVG_INPUT_TOKENS_PER_REQUEST + AVG_OUTPUT_TOKENS_PER_REQUEST

# Average Redis commands per user interaction cycle
REDIS_COMMANDS_PER_INTERACTION = 3



@dataclass
class HttpResult:
    total_requests: int = 0
    successful: int = 0
    failed: int = 0
    latencies_ms: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def rps(self) -> float:
        if self.duration_seconds == 0:
            return 0.0
        return self.successful / self.duration_seconds

    @property
    def p50_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.50)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.95)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def p99_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        sorted_l = sorted(self.latencies_ms)
        idx = int(len(sorted_l) * 0.99)
        return sorted_l[min(idx, len(sorted_l) - 1)]

    @property
    def mean_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        return statistics.mean(self.latencies_ms)


@dataclass
class CapacityResult:
    bottleneck: str = ""
    max_concurrent_users: int = 0
    max_total_users_daily: int = 0
    max_requests_per_minute: int = 0
    max_requests_per_day: int = 0
    breakdown: dict = field(default_factory=dict)



HEALTH_ENDPOINT = "/health"
WEBHOOK_ENDPOINT = "/webhook/{token}"

SAMPLE_TELEGRAM_UPDATE = {
    "update_id": 100000001,
    "message": {
        "message_id": 1,
        "from": {
            "id": 999999999,
            "is_bot": False,
            "first_name": "StressTest",
            "language_code": "en",
        },
        "chat": {
            "id": 999999999,
            "first_name": "StressTest",
            "type": "private",
        },
        "date": int(time.time()),
        "text": "/start",
    },
}


async def _fire_request(
    session: aiohttp.ClientSession,
    url: str,
    method: str = "GET",
    payload: dict | None = None,
) -> tuple[bool, float, str]:
    start = time.perf_counter()
    try:
        if method == "POST":
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                elapsed = (time.perf_counter() - start) * 1000
                if resp.status < 400:
                    return True, elapsed, ""
                body = await resp.text()
                return False, elapsed, f"HTTP {resp.status}: {body[:200]}"
        else:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                elapsed = (time.perf_counter() - start) * 1000
                if resp.status < 400:
                    return True, elapsed, ""
                body = await resp.text()
                return False, elapsed, f"HTTP {resp.status}: {body[:200]}"
    except Exception as e:
        elapsed = (time.perf_counter() - start) * 1000
        return False, elapsed, str(e)


async def run_http_throughput_test(
    base_url: str,
    concurrent: int = 50,
    total_requests: int = 500,
    endpoint: str = "health",
) -> HttpResult:
    result = HttpResult()

    if endpoint == "health":
        url = f"{base_url}{HEALTH_ENDPOINT}"
        method = "GET"
        payload = None
    else:
        token = os.getenv("TELEGRAM_BOT_TOKEN", "test-token")
        url = f"{base_url}/webhook/{token}"
        method = "POST"
        payload = SAMPLE_TELEGRAM_UPDATE

    semaphore = asyncio.Semaphore(concurrent)

    async def bounded_request(sess: aiohttp.ClientSession) -> tuple[bool, float, str]:
        async with semaphore:
            return await _fire_request(sess, url, method, payload)

    start_time = time.perf_counter()
    async with aiohttp.ClientSession() as sess:
        tasks = [bounded_request(sess) for _ in range(total_requests)]
        results = await asyncio.gather(*tasks)
    end_time = time.perf_counter()

    result.duration_seconds = end_time - start_time
    result.total_requests = total_requests

    for ok, latency, err in results:
        if ok:
            result.successful += 1
            result.latencies_ms.append(latency)
        else:
            result.failed += 1
            if err and err not in result.errors:
                result.errors.append(err)

    return result



async def probe_gemini_throughput(num_requests: int = 15) -> dict:
    try:
        import google.generativeai as genai
        from config.settings import settings
        genai.configure(api_key=settings.gemini_api_key)
    except Exception as e:
        return {"error": f"Could not initialize Gemini: {e}", "requests_completed": 0}

    model = genai.GenerativeModel("gemini-2.5-flash")
    prompt = 'Extract transaction: "spent 200 on coffee". Return JSON with amount, type, category, description.'

    latencies = []
    errors = []
    rate_limited_at = None

    for i in range(num_requests):
        start = time.perf_counter()
        try:
            response = await model.generate_content_async(prompt)
            elapsed = (time.perf_counter() - start) * 1000
            latencies.append(elapsed)
            _ = response.text
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            err_str = str(e)
            if "429" in err_str or "RESOURCE_EXHAUSTED" in err_str:
                rate_limited_at = i + 1
                errors.append(f"Rate limited at request {i + 1}: {err_str[:150]}")
                break
            errors.append(f"Request {i + 1}: {err_str[:150]}")

    return {
        "requests_attempted": num_requests,
        "requests_completed": len(latencies),
        "rate_limited_at_request": rate_limited_at,
        "mean_latency_ms": round(statistics.mean(latencies), 1) if latencies else 0,
        "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)] if latencies else 0, 1),
        "min_latency_ms": round(min(latencies), 1) if latencies else 0,
        "max_latency_ms": round(max(latencies), 1) if latencies else 0,
        "errors": errors,
    }



def compute_capacity_model(
    avg_messages_per_user_per_day: int = 15,
    avg_messages_per_user_per_minute_peak: float = 2.0,
    gemini_calls_per_message: float = 1.0,
    redis_commands_per_message: int = REDIS_COMMANDS_PER_INTERACTION,
) -> CapacityResult:
    result = CapacityResult()
    breakdown = {}

    # -- Gemini API capacity (combining both flash models via fallback) --
    total_rpm = (
        GEMINI_FREE["gemini_2_5_flash"]["rpm"]
        + GEMINI_FREE["gemini_2_5_flash_lite"]["rpm"]
    )
    total_rpd = (
        GEMINI_FREE["gemini_2_5_flash"]["rpd"]
        + GEMINI_FREE["gemini_2_5_flash_lite"]["rpd"]
    )
    total_tpm = (
        GEMINI_FREE["gemini_2_5_flash"]["tpm"]
        + GEMINI_FREE["gemini_2_5_flash_lite"]["tpm"]
    )

    effective_rpm_from_requests = total_rpm / gemini_calls_per_message
    effective_rpm_from_tokens = total_tpm / AVG_TOKENS_PER_REQUEST
    gemini_effective_rpm = min(effective_rpm_from_requests, effective_rpm_from_tokens)
    gemini_daily = total_rpd / gemini_calls_per_message
    gemini_total_users_daily = gemini_daily / avg_messages_per_user_per_day
    gemini_concurrent_peak = gemini_effective_rpm / avg_messages_per_user_per_minute_peak

    breakdown["gemini_api"] = {
        "combined_rpm": total_rpm,
        "combined_rpd": total_rpd,
        "combined_tpm": total_tpm,
        "effective_rpm_by_requests": round(effective_rpm_from_requests, 1),
        "effective_rpm_by_tokens": round(effective_rpm_from_tokens, 1),
        "effective_rpm_final": round(gemini_effective_rpm, 1),
        "max_daily_requests": round(gemini_daily),
        "max_total_users_daily": round(gemini_total_users_daily),
        "max_concurrent_users": round(gemini_concurrent_peak),
    }

    # -- Supabase database capacity --
    db_concurrent = SUPABASE_FREE["max_pooler_connections"]
    breakdown["supabase_db"] = {
        "max_pooler_connections": db_concurrent,
        "max_direct_connections": SUPABASE_FREE["max_direct_connections"],
        "storage_mb": SUPABASE_FREE["db_size_mb"],
        "max_concurrent_users": db_concurrent,
    }

    # -- Upstash Redis capacity --
    redis_daily_interactions = UPSTASH_FREE["commands_per_day"] / redis_commands_per_message
    redis_total_users_daily = redis_daily_interactions / avg_messages_per_user_per_day
    breakdown["upstash_redis"] = {
        "commands_per_day": UPSTASH_FREE["commands_per_day"],
        "commands_per_interaction": redis_commands_per_message,
        "max_daily_interactions": round(redis_daily_interactions),
        "max_total_users_daily": round(redis_total_users_daily),
        "max_concurrent_connections": UPSTASH_FREE["max_concurrent_connections"],
    }

    # -- Telegram Bot API capacity --
    tg_concurrent = TELEGRAM_BOT_LIMITS["webhook_max_connections"]
    tg_rpm = TELEGRAM_BOT_LIMITS["send_per_second_global"] * 60
    breakdown["telegram_bot_api"] = {
        "max_webhook_connections": tg_concurrent,
        "max_outbound_msg_per_minute": tg_rpm,
        "max_concurrent_users": tg_concurrent,
    }

    # -- Render compute capacity --
    # 0.1 vCPU handles roughly 50-80 lightweight async requests per second
    # FastAPI async with 512MB RAM can handle ~200 concurrent connections in theory
    # but CPU-bound Gemini response parsing caps real throughput
    render_estimated_rps = 60
    render_concurrent = 100
    breakdown["render_compute"] = {
        "ram_mb": RENDER_FREE["ram_mb"],
        "vcpu": RENDER_FREE["vcpu"],
        "estimated_rps_lightweight": render_estimated_rps,
        "estimated_max_concurrent_connections": render_concurrent,
        "cold_start_seconds": RENDER_FREE["cold_start_seconds"],
    }

    # -- Determine the bottleneck (minimum across all services) --
    service_concurrent = {
        "gemini_api": round(gemini_concurrent_peak),
        "supabase_db": db_concurrent,
        "upstash_redis": UPSTASH_FREE["max_concurrent_connections"],
        "telegram_bot_api": tg_concurrent,
        "render_compute": render_concurrent,
    }

    service_daily_users = {
        "gemini_api": round(gemini_total_users_daily),
        "upstash_redis": round(redis_total_users_daily),
    }

    bottleneck_concurrent = min(service_concurrent, key=service_concurrent.get)
    bottleneck_daily = min(service_daily_users, key=service_daily_users.get)

    # The tightest constraint is the system limit
    result.bottleneck = bottleneck_concurrent
    result.max_concurrent_users = service_concurrent[bottleneck_concurrent]
    result.max_total_users_daily = service_daily_users[bottleneck_daily]
    result.max_requests_per_minute = round(gemini_effective_rpm)
    result.max_requests_per_day = round(gemini_daily)
    result.breakdown = breakdown

    return result


def generate_report(
    capacity: CapacityResult,
    http_health: HttpResult | None = None,
    http_webhook: HttpResult | None = None,
    gemini_probe: dict | None = None,
) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("FINPILOT AI -- STRESS TEST REPORT")
    lines.append("=" * 72)
    lines.append("")

    lines.append("-" * 72)
    lines.append("SECTION 1: MATHEMATICAL CAPACITY MODEL")
    lines.append("-" * 72)
    lines.append("")
    lines.append(f"  System bottleneck:            {capacity.bottleneck}")
    lines.append(f"  Max concurrent users:         {capacity.max_concurrent_users}")
    lines.append(f"  Max total users per day:      {capacity.max_total_users_daily}")
    lines.append(f"  Max requests per minute:      {capacity.max_requests_per_minute}")
    lines.append(f"  Max requests per day:         {capacity.max_requests_per_day}")
    lines.append("")

    for svc, data in capacity.breakdown.items():
        lines.append(f"  [{svc}]")
        for k, v in data.items():
            lines.append(f"    {k}: {v}")
        lines.append("")

    if http_health:
        lines.append("-" * 72)
        lines.append("SECTION 2: HTTP THROUGHPUT -- /health ENDPOINT")
        lines.append("-" * 72)
        lines.append("")
        lines.append(f"  Total requests:     {http_health.total_requests}")
        lines.append(f"  Successful:         {http_health.successful}")
        lines.append(f"  Failed:             {http_health.failed}")
        lines.append(f"  Duration:           {http_health.duration_seconds:.2f}s")
        lines.append(f"  Requests/sec:       {http_health.rps:.1f}")
        lines.append(f"  Mean latency:       {http_health.mean_ms:.1f}ms")
        lines.append(f"  P50 latency:        {http_health.p50_ms:.1f}ms")
        lines.append(f"  P95 latency:        {http_health.p95_ms:.1f}ms")
        lines.append(f"  P99 latency:        {http_health.p99_ms:.1f}ms")
        if http_health.errors:
            lines.append(f"  Errors:             {http_health.errors[:5]}")
        lines.append("")

    if http_webhook:
        lines.append("-" * 72)
        lines.append("SECTION 3: HTTP THROUGHPUT -- /webhook ENDPOINT")
        lines.append("-" * 72)
        lines.append("")
        lines.append(f"  Total requests:     {http_webhook.total_requests}")
        lines.append(f"  Successful:         {http_webhook.successful}")
        lines.append(f"  Failed:             {http_webhook.failed}")
        lines.append(f"  Duration:           {http_webhook.duration_seconds:.2f}s")
        lines.append(f"  Requests/sec:       {http_webhook.rps:.1f}")
        lines.append(f"  Mean latency:       {http_webhook.mean_ms:.1f}ms")
        lines.append(f"  P50 latency:        {http_webhook.p50_ms:.1f}ms")
        lines.append(f"  P95 latency:        {http_webhook.p95_ms:.1f}ms")
        lines.append(f"  P99 latency:        {http_webhook.p99_ms:.1f}ms")
        if http_webhook.errors:
            lines.append(f"  Errors:             {http_webhook.errors[:5]}")
        lines.append("")

    if gemini_probe:
        lines.append("-" * 72)
        lines.append("SECTION 4: GEMINI API THROUGHPUT PROBE")
        lines.append("-" * 72)
        lines.append("")
        for k, v in gemini_probe.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    lines.append("=" * 72)
    lines.append("END OF REPORT")
    lines.append("=" * 72)
    return "\n".join(lines)



async def main() -> None:
    parser = argparse.ArgumentParser(description="FinPilot AI Stress Testing Suite")
    parser.add_argument("--host", default="http://127.0.0.1:8000", help="Base URL of the running FinPilot instance")
    parser.add_argument("--concurrent", type=int, default=50, help="Concurrent connections for HTTP tests")
    parser.add_argument("--requests", type=int, default=500, help="Total requests for HTTP tests")
    parser.add_argument("--skip-http", action="store_true", help="Skip HTTP throughput tests")
    parser.add_argument("--skip-gemini", action="store_true", help="Skip Gemini API probe")
    parser.add_argument("--gemini-requests", type=int, default=15, help="Number of Gemini probe requests")
    parser.add_argument("--avg-messages", type=int, default=15, help="Average messages per user per day")
    parser.add_argument("--output", default=None, help="Output file for JSON results")
    args = parser.parse_args()

    print("FinPilot AI Stress Testing Suite")
    print(f"Target: {args.host}")
    print(f"Concurrent: {args.concurrent} | Total HTTP requests: {args.requests}")
    print()

    # Layer 3 always runs (no external dependency)
    print("[1/4] Computing mathematical capacity model...")
    capacity = compute_capacity_model(avg_messages_per_user_per_day=args.avg_messages)

    http_health = None
    http_webhook = None
    gemini_probe_result = None

    if not args.skip_http:
        print(f"[2/4] Running HTTP throughput test on /health ({args.requests} requests, {args.concurrent} concurrent)...")
        http_health = await run_http_throughput_test(
            args.host, args.concurrent, args.requests, endpoint="health"
        )

        print(f"[3/4] Running HTTP throughput test on /webhook ({args.requests} requests, {args.concurrent} concurrent)...")
        http_webhook = await run_http_throughput_test(
            args.host, args.concurrent, args.requests, endpoint="webhook"
        )
    else:
        print("[2/4] Skipped HTTP /health test")
        print("[3/4] Skipped HTTP /webhook test")

    if not args.skip_gemini:
        print(f"[4/4] Probing Gemini API throughput ({args.gemini_requests} requests)...")
        gemini_probe_result = await probe_gemini_throughput(args.gemini_requests)
    else:
        print("[4/4] Skipped Gemini API probe")

    report = generate_report(capacity, http_health, http_webhook, gemini_probe_result)
    print()
    print(report)

    if args.output:
        output_data = {
            "capacity_model": asdict(capacity),
            "http_health": asdict(http_health) if http_health else None,
            "http_webhook": asdict(http_webhook) if http_webhook else None,
            "gemini_probe": gemini_probe_result,
        }
        out_path = Path(args.output)
        out_path.write_text(json.dumps(output_data, indent=2, default=str))
        print(f"\nJSON results written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
