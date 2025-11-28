#!/usr/bin/env python3
"""
Integration tests for the Task API.

Run with: python api/scripts/test_integration.py

Prerequisites:
    - docker compose up -d
    - All services running
"""

import requests
import time
import sys

BASE_URL = "http://localhost:8000"

# Test users from sql/create_tables.sql
ADMIN_API_KEY = "123e4567-e89b-12d3-a456-426614174000"
USER1_API_KEY = "550e8400-e29b-41d4-a716-446655440000"
USER2_API_KEY = "c56a4180-65aa-42ec-a945-5fd21dec0538"
INVALID_API_KEY = "00000000-0000-0000-0000-000000000000"


class TestResult:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def add_pass(self, name: str):
        self.passed += 1
        print(f"  ✓ {name}")

    def add_fail(self, name: str, reason: str):
        self.failed += 1
        self.errors.append(f"{name}: {reason}")
        print(f"  ✗ {name} - {reason}")

    def summary(self):
        print("\n" + "=" * 60)
        print(f"RESULTS: {self.passed} passed, {self.failed} failed")
        if self.errors:
            print("\nFailures:")
            for error in self.errors:
                print(f"  - {error}")
        print("=" * 60)
        return self.failed == 0


def headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }


def test_health_check(result: TestResult):
    """Test: GET /health returns 200."""
    resp = requests.get(f"{BASE_URL}/health")
    if resp.status_code == 200 and resp.json()["status"] == "healthy":
        result.add_pass("Health check")
    else:
        result.add_fail("Health check", f"Status {resp.status_code}")


def test_invalid_api_key(result: TestResult):
    """Test: Invalid API key returns 401."""
    resp = requests.post(
        f"{BASE_URL}/task",
        json={"a": 1, "b": 2},
        headers=headers(INVALID_API_KEY)
    )
    if resp.status_code == 401:
        result.add_pass("Invalid API key returns 401")
    else:
        result.add_fail("Invalid API key returns 401", f"Got {resp.status_code}")


def test_missing_auth_header(result: TestResult):
    """Test: Missing auth header returns 401."""
    resp = requests.post(
        f"{BASE_URL}/task",
        json={"a": 1, "b": 2}
    )
    if resp.status_code == 401:
        result.add_pass("Missing auth header returns 401")
    else:
        result.add_fail("Missing auth header returns 401", f"Got {resp.status_code}")

def test_task_submission_and_polling(result: TestResult):
    """Test: Submit task and poll until complete."""
    # Submit
    resp = requests.post(
        f"{BASE_URL}/task",
        json={"a": 10, "b": 20},
        headers=headers(USER1_API_KEY)
    )
    if resp.status_code != 202:
        result.add_fail("Task submission", f"Expected 202, got {resp.status_code}")
        return

    task_id = resp.json()["task_id"]
    result.add_pass(f"Task submission (task_id={task_id[:8]}...)")

    # Poll until complete (max 10 seconds)
    for _ in range(10):
        resp = requests.get(
            f"{BASE_URL}/poll/{task_id}",
            headers=headers(USER1_API_KEY)
        )
        if resp.status_code != 200:
            result.add_fail("Task polling", f"Status {resp.status_code}")
            return

        status = resp.json()["status"]
        if status == "complete":
            if resp.json()["result"] == 30:
                result.add_pass("Task completed with correct result (30)")
            else:
                result.add_fail("Task result", f"Expected 30, got {resp.json()['result']}")
            return
        elif status == "failed":
            result.add_fail("Task execution", resp.json().get("error_message", "Unknown"))
            return

        time.sleep(1)

    result.add_fail("Task polling", "Timeout after 10 seconds")


def test_poll_nonexistent_task(result: TestResult):
    """Test: Polling nonexistent task returns 404."""
    resp = requests.get(
        f"{BASE_URL}/poll/00000000-0000-0000-0000-000000000000",
        headers=headers(USER1_API_KEY)
    )
    if resp.status_code == 404:
        result.add_pass("Poll nonexistent task returns 404")
    else:
        result.add_fail("Poll nonexistent task returns 404", f"Got {resp.status_code}")


def test_poll_other_users_task(result: TestResult):
    """Test: User cannot poll another user's task."""
    # Create task as USER1
    resp = requests.post(
        f"{BASE_URL}/task",
        json={"a": 1, "b": 1},
        headers=headers(USER1_API_KEY)
    )
    if resp.status_code != 202:
        result.add_fail("Setup for cross-user test", f"Status {resp.status_code}")
        return

    task_id = resp.json()["task_id"]

    # Try to poll as USER2
    resp = requests.get(
        f"{BASE_URL}/poll/{task_id}",
        headers=headers(USER2_API_KEY)
    )
    if resp.status_code == 404:
        result.add_pass("Cannot poll other user's task (404)")
    else:
        result.add_fail("Cannot poll other user's task", f"Got {resp.status_code}")


def test_admin_update_credits(result: TestResult):
    """Test: Admin can update user credits."""
    # Set credits to 999
    resp = requests.post(
        f"{BASE_URL}/admin/credits",
        json={"user_api_key": USER2_API_KEY, "credits": 999},
        headers=headers(ADMIN_API_KEY)
    )
    if resp.status_code == 200:
        result.add_pass("Admin update credits")
    else:
        result.add_fail("Admin update credits", f"Status {resp.status_code}")


def test_non_admin_cannot_update_credits(result: TestResult):
    """Test: Non-admin cannot update credits."""
    resp = requests.post(
        f"{BASE_URL}/admin/credits",
        json={"user_api_key": USER2_API_KEY, "credits": 999},
        headers=headers(USER1_API_KEY)  # USER1 is not admin
    )
    if resp.status_code == 403:
        result.add_pass("Non-admin cannot update credits (403)")
    else:
        result.add_fail("Non-admin cannot update credits", f"Got {resp.status_code}")


def test_invalid_request_body(result: TestResult):
    """Test: Invalid request body returns 422."""
    resp = requests.post(
        f"{BASE_URL}/task",
        json={"a": "not_an_int", "b": 2},
        headers=headers(USER1_API_KEY)
    )
    if resp.status_code == 422:
        result.add_pass("Invalid request body returns 422")
    else:
        result.add_fail("Invalid request body returns 422", f"Got {resp.status_code}")


def main():
    print("=" * 60)
    print("INTEGRATION TESTS")
    print("=" * 60)
    print()

    result = TestResult()

    # Health
    print("Health:")
    test_health_check(result)
    print()

    # Authentication
    print("Authentication:")
    test_invalid_api_key(result)
    test_missing_auth_header(result)
    print()

    # Task lifecycle
    print("Task Lifecycle:")
    test_task_submission_and_polling(result)
    test_poll_nonexistent_task(result)
    test_poll_other_users_task(result)
    print()

    # Admin
    print("Admin:")
    test_admin_update_credits(result)
    test_non_admin_cannot_update_credits(result)
    print()

    # Validation
    print("Validation:")
    test_invalid_request_body(result)
    print()

    success = result.summary()
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()