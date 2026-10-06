import pytest
import time
from unittest.mock import AsyncMock, patch, MagicMock
from collections import deque


# ============================================================
# Pure function tests (no FastAPI app import needed)
# ============================================================

class SlidingWindowRateLimiter:
    """Copy of the rate limiter for testing."""
    def __init__(self, max_calls: int, window_seconds: float):
        self.max_calls = max_calls
        self.window_seconds = window_seconds
        self._lock = None  # Not needed for sync tests
        self._calls = deque()

    def acquire_sync(self):
        now = time.monotonic()
        while self._calls and (now - self._calls[0]) >= self.window_seconds:
            self._calls.popleft()
        if len(self._calls) < self.max_calls:
            self._calls.append(now)
            return True
        return False


def _mask_key(k: str) -> str:
    """Copy of mask function."""
    if not k:
        return "empty"
    if len(k) <= 10:
        return "***"
    return f"{k[:6]}…{k[-4:]}"


def _detect_provider(model: str) -> str:
    """Copy of provider detection."""
    if isinstance(model, str) and model.startswith("deepseek/"):
        return "deepseek"
    return "openrouter"


def _load_deepseek_keys_from_env(env_vars: dict) -> list:
    """Testable version of key loading."""
    raw = (env_vars.get("VARIAMOS_DEEPSEEK_API_KEYS") or "").strip()
    if raw:
        return [k.strip() for k in raw.split(",") if k.strip()]
    single = (env_vars.get("VARIAMOS_DEEPSEEK_API_KEY") or "").strip()
    return [single] if single else []


def _key_available_now(key_state: dict, api_key: str, now: float) -> bool:
    """Testable version of key availability check."""
    st = key_state.get(api_key, {})
    cooldown_until = float(st.get("cooldown_until", 0.0) or 0.0)
    disabled_until = float(st.get("disabled_until", 0.0) or 0.0)
    return now >= cooldown_until and now >= disabled_until


def _mark_cooldown(key_state: dict, api_key: str, seconds: float):
    """Testable version of cooldown marking."""
    now = time.time()
    st = key_state.setdefault(api_key, {})
    st["cooldown_until"] = max(float(st.get("cooldown_until", 0.0) or 0.0), now + max(0.0, seconds))


def _mark_disabled(key_state: dict, api_key: str, seconds: float):
    """Testable version of disabled marking."""
    now = time.time()
    st = key_state.setdefault(api_key, {})
    st["disabled_until"] = max(float(st.get("disabled_until", 0.0) or 0.0), now + max(0.0, seconds))


def _seconds_until_any_key_available(key_state: dict, keys: list, now: float) -> float:
    """Testable version of wait time calculation."""
    waits = []
    for k in keys:
        st = key_state.get(k, {})
        cd = float(st.get("cooldown_until", 0.0) or 0.0)
        dis = float(st.get("disabled_until", 0.0) or 0.0)
        until = max(cd, dis)
        if until > now:
            waits.append(until - now)
    return min(waits) if waits else 0.0


def _pick_key_round_robin(keys: list, key_state: dict, last_idx: list, now: float):
    """Testable version of round-robin key selection."""
    if not keys:
        return None, last_idx[0]

    n = len(keys)
    start = last_idx[0] % n
    for i in range(n):
        k = keys[(start + i) % n]
        if _key_available_now(key_state, k, now):
            last_idx[0] = (start + i + 1) % n
            return k, last_idx[0]
    return None, last_idx[0]


# ============================================================
# Tests
# ============================================================

class TestProviderDetection:
    """Tests for provider detection logic."""

    def test_detect_deepseek_models(self):
        assert _detect_provider("deepseek/deepseek-chat") == "deepseek"
        assert _detect_provider("deepseek/deepseek-coder") == "deepseek"
        assert _detect_provider("deepseek/deepseek-reasoner") == "deepseek"
        assert _detect_provider("deepseek/deepseek-chat:free") == "deepseek"

    def test_detect_openrouter_models(self):
        assert _detect_provider("anthropic/claude-3.5-sonnet") == "openrouter"
        assert _detect_provider("openai/gpt-4o") == "openrouter"
        assert _detect_provider("google/gemini-pro") == "openrouter"
        assert _detect_provider("meta-llama/llama-3.1-405b") == "openrouter"

    def test_detect_edge_cases(self):
        assert _detect_provider("") == "openrouter"
        assert _detect_provider("deepseek") == "openrouter"  # no slash
        assert _detect_provider("deepseek/") == "deepseek"
        assert _detect_provider(None) == "openrouter"


class TestKeyLoading:
    """Tests for DeepSeek key loading."""

    def test_load_multiple_keys(self):
        env = {"VARIAMOS_DEEPSEEK_API_KEYS": "sk-key1,sk-key2,sk-key3"}
        keys = _load_deepseek_keys_from_env(env)
        assert keys == ["sk-key1", "sk-key2", "sk-key3"]

    def test_load_single_key(self):
        env = {"VARIAMOS_DEEPSEEK_API_KEY": "sk-single"}
        keys = _load_deepseek_keys_from_env(env)
        assert keys == ["sk-single"]

    def test_load_empty(self):
        env = {}
        keys = _load_deepseek_keys_from_env(env)
        assert keys == []

    def test_priority_multi_over_single(self):
        env = {
            "VARIAMOS_DEEPSEEK_API_KEYS": "sk-multi1,sk-multi2",
            "VARIAMOS_DEEPSEEK_API_KEY": "sk-single",
        }
        keys = _load_deepseek_keys_from_env(env)
        assert keys == ["sk-multi1", "sk-multi2"]


class TestKeyState:
    """Tests for key state management."""

    def test_key_available_no_state(self):
        key_state = {}
        now = time.time()
        assert _key_available_now(key_state, "sk-test", now) is True

    def test_key_available_with_cooldown(self):
        key_state = {}
        now = time.time()
        key_state["sk-test"] = {"cooldown_until": now + 10, "disabled_until": 0}
        assert _key_available_now(key_state, "sk-test", now) is False
        assert _key_available_now(key_state, "sk-test", now + 11) is True

    def test_key_available_disabled(self):
        key_state = {}
        now = time.time()
        key_state["sk-test"] = {"cooldown_until": 0, "disabled_until": now + 10}
        assert _key_available_now(key_state, "sk-test", now) is False

    def test_mark_cooldown(self):
        key_state = {}
        now = time.time()
        _mark_cooldown(key_state, "sk-test", 5.0)
        assert key_state["sk-test"]["cooldown_until"] >= now + 5.0

    def test_mark_disabled(self):
        key_state = {}
        now = time.time()
        _mark_disabled(key_state, "sk-test", 3600.0)
        assert key_state["sk-test"]["disabled_until"] >= now + 3600.0

    def test_cooldown_max_behavior(self):
        key_state = {}
        now = time.time()
        _mark_cooldown(key_state, "sk-test", 5.0)
        first = key_state["sk-test"]["cooldown_until"]
        _mark_cooldown(key_state, "sk-test", 10.0)
        second = key_state["sk-test"]["cooldown_until"]
        assert second >= first  # max of existing and new


class TestWaitTimeCalculation:
    """Tests for seconds until any key available."""

    def test_no_keys(self):
        wait = _seconds_until_any_key_available({}, [], time.time())
        assert wait == 0.0

    def test_all_keys_available(self):
        key_state = {}
        keys = ["sk-1", "sk-2"]
        wait = _seconds_until_any_key_available(key_state, keys, time.time())
        assert wait == 0.0

    def test_some_keys_cooldown(self):
        now = time.time()
        key_state = {
            "sk-1": {"cooldown_until": now + 5, "disabled_until": 0},
            "sk-2": {"cooldown_until": now + 10, "disabled_until": 0},
        }
        wait = _seconds_until_any_key_available(key_state, ["sk-1", "sk-2"], now)
        assert wait == 5.0  # min of 5 and 10

    def test_disabled_longer_than_cooldown(self):
        now = time.time()
        key_state = {
            "sk-1": {"cooldown_until": now + 5, "disabled_until": now + 20},
        }
        wait = _seconds_until_any_key_available(key_state, ["sk-1"], now)
        assert wait == 20.0  # max of cooldown and disabled


class TestRoundRobin:
    """Tests for round-robin key selection."""

    def test_basic_rotation(self):
        keys = ["sk-1", "sk-2", "sk-3"]
        key_state = {}
        last_idx = [0]

        k1, _ = _pick_key_round_robin(keys, key_state, last_idx, time.time())
        k2, _ = _pick_key_round_robin(keys, key_state, last_idx, time.time())
        k3, _ = _pick_key_round_robin(keys, key_state, last_idx, time.time())
        k4, _ = _pick_key_round_robin(keys, key_state, last_idx, time.time())

        assert {k1, k2, k3} == {"sk-1", "sk-2", "sk-3"}
        assert k4 == k1  # wraps around

    def test_skips_cooled_down_key(self):
        keys = ["sk-1", "sk-2", "sk-3"]
        key_state = {"sk-1": {"cooldown_until": time.time() + 10, "disabled_until": 0}}
        last_idx = [0]

        k, _ = _pick_key_round_robin(keys, key_state, last_idx, time.time())
        assert k in ["sk-2", "sk-3"]

    def test_returns_none_when_all_cooldown(self):
        keys = ["sk-1", "sk-2"]
        now = time.time()
        key_state = {
            "sk-1": {"cooldown_until": now + 10, "disabled_until": 0},
            "sk-2": {"cooldown_until": now + 10, "disabled_until": 0},
        }
        last_idx = [0]

        k, _ = _pick_key_round_robin(keys, key_state, last_idx, now)
        assert k is None

    def test_empty_keys(self):
        key_state = {}
        last_idx = [0]
        k, _ = _pick_key_round_robin([], key_state, last_idx, time.time())
        assert k is None


class TestRateLimiter:
    """Tests for sliding window rate limiter."""

    def test_allows_up_to_limit(self):
        limiter = SlidingWindowRateLimiter(3, 60.0)
        assert limiter.acquire_sync() is True
        assert limiter.acquire_sync() is True
        assert limiter.acquire_sync() is True
        assert limiter.acquire_sync() is False  # 4th should fail

    def test_window_reset(self):
        limiter = SlidingWindowRateLimiter(2, 0.1)  # 100ms window
        assert limiter.acquire_sync() is True
        assert limiter.acquire_sync() is True
        assert limiter.acquire_sync() is False

        # Wait for window to pass
        time.sleep(0.15)
        assert limiter.acquire_sync() is True  # window reset

    def test_different_keys_independent(self):
        limiter1 = SlidingWindowRateLimiter(2, 60.0)
        limiter2 = SlidingWindowRateLimiter(2, 60.0)

        assert limiter1.acquire_sync() is True
        assert limiter1.acquire_sync() is True
        assert limiter1.acquire_sync() is False

        assert limiter2.acquire_sync() is True
        assert limiter2.acquire_sync() is True
        assert limiter2.acquire_sync() is False


class TestMaskKey:
    """Tests for key masking."""

    def test_mask_full_key(self):
        assert _mask_key("sk-abcdef123456") == "sk-abc…3456"

    def test_mask_short_key(self):
        assert _mask_key("sk-short") == "***"

    def test_mask_empty(self):
        assert _mask_key("") == "empty"
        assert _mask_key(None) == "empty"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])