#!/usr/bin/env python3
"""
Example client script that submits a task and polls until complete.

This is a DELIVERABLE for the take-home assignment.

Usage:
    pip install requests
    python scripts/submit_task.py
"""

import requests
import time
import sys

# Configuration
BASE_URL = "http://localhost:8000"
API_KEY = "550e8400-e29b-41d4-a716-446655440000"  # test_user1 (500 credits)

def main():
    headers = {
        "Authorization": f"Bearer {API_KEY}",
        "Content-Type": "application/json"
    }
    
    # ============================================
    # Step 1: Submit a task
    # ============================================
    print("=" * 50)
    print("Submitting task: 5 + 3")
    print("=" * 50)
    
    response = requests.post(
        f"{BASE_URL}/task",
        json={"a": 5, "b": 3},
        headers=headers
    )
    
    if response.status_code == 401:
        print(f"Error: Invalid API key")
        sys.exit(1)
    elif response.status_code == 402:
        print(f"Error: Insufficient credits")
        sys.exit(1)
    elif response.status_code != 202:
        print(f"Error: {response.status_code} - {response.text}")
        sys.exit(1)
    
    task_id = response.json()["task_id"]
    print(f"Task ID: {task_id}")
    print()
    
    # ============================================
    # Step 2: Poll until complete
    # ============================================
    print("Polling for result...")
    print("-" * 50)
    
    while True:
        response = requests.get(
            f"{BASE_URL}/poll/{task_id}",
            headers=headers
        )
        
        if response.status_code != 200:
            print(f"Error polling: {response.status_code} - {response.text}")
            sys.exit(1)
        
        data = response.json()
        status = data["status"]
        
        print(f"  Status: {status}")
        
        if status == "complete":
            print("-" * 50)
            print(f"✓ Result: {data['result']}")
            print("=" * 50)
            break
        elif status == "failed":
            print("-" * 50)
            print(f"✗ Error: {data.get('error_message', 'Unknown error')}")
            print("=" * 50)
            sys.exit(1)
        
        time.sleep(1)  # Poll every second

if __name__ == "__main__":
    main()