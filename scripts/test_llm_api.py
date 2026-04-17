"""Quick test of the LLM API endpoint — for debugging only."""
import httpx

token = "7h8obmJN8U7ytdGB0pB9ae1EgWTyHqY72TeF4ZOZg2o"
url = "https://vio.automotive-wan.com:446/chat/completions"
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {token}",
    "useLegacyCompletionsEndpoint": "false",
    "X-Tenant-ID": "default_tenant",
}

# Test streaming
body = {
    "model": "VIO:Claude 4.6 Sonnet",
    "messages": [{"role": "user", "content": "Say hello"}],
    "stream": True,
    "max_tokens": 50,
}

print(f"POST {url} (stream=true)")
with httpx.stream("POST", url, headers=headers, json=body, verify=False, timeout=30) as r:
    print(f"Status: {r.status_code}")
    print(f"Content-Type: {r.headers.get('content-type')}")
    for line in r.iter_lines():
        print(f"  {line}")
        if "[DONE]" in line:
            break
