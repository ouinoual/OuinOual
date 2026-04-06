import os
from openai import OpenAI

def get_kimi_client():
    api_key = os.getenv("MOONSHOT_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("MOONSHOT_API_KEY is missing")

    return OpenAI(
        api_key=api_key,
        base_url="https://api.moonshot.cn/v1",
    )

def test_kimi_connection():
    client = get_kimi_client()
    response = client.chat.completions.create(
        model="kimi-k2.5",
        messages=[
            {"role": "system", "content": "You are a concise assistant."},
            {"role": "user", "content": "Reply with only: OK"}
        ],
        temperature=0
    )
    return response.choices[0].message.content.strip()
