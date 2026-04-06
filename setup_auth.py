from dotenv import load_dotenv
load_dotenv()

import requests
from ali_api import AliExpress, build_timestamp_ms, build_signature, BASE_REST_URL, API_AUTH_CREATE

# تهيئة الكائن المساعد (يقرأ APP_KEY و APP_SECRET من .env)
ali = AliExpress()

API_NAME = API_AUTH_CREATE
BASE_URL = BASE_REST_URL + API_NAME


def get_auth_code():
    """
    يطبع رابط التفويض، ثم يطلب منك لصق الـ CODE (فقط الجزء بعد code= من عنوان المتصفح).
    """
    redirect_uri = "https://replit.com/@oualidmsirdi/AffiliateExpress"

    url = (
        "https://api-sg.aliexpress.com/oauth/authorize"
        f"?response_type=code"
        f"&client_id={ali.app_key}"
        f"&redirect_uri={redirect_uri}"
        f"&view=web"
        f"&sp=ae"
    )

    print("---------------------------------------------------")
    print("STEP 1: CLICK THIS LINK TO LOGIN:")
    print(url)
    print("---------------------------------------------------")

    code = input("Paste the CODE (just the random letters/numbers) here: ")
    return code.strip()


def get_access_token(code: str):
    """
    يرسل الكود إلى /auth/token/create بنمط REST + توقيع HMAC-SHA256
    ويحفظ الاستجابة الكاملة في token.json عبر ali.save_token().
    """

    # 1) البارامترات المطلوبة كما في مثال NodeJS الذي اعتمدناه
    params = {
        "app_key": ali.app_key,
        "timestamp": build_timestamp_ms(),
        "sign_method": "sha256",
        "code": code,
    }

    # 2) حساب sign بنفس خوارزمية auth/token/create
    params["sign"] = build_signature(params, API_NAME, ali.app_secret)

    print(f"\n🔄 Connecting to: {BASE_URL} ...")
    print("🧩 Request params (for debugging):")
    for k, v in params.items():
        print(f"  {k}: {v}")

    # 3) إرسال الطلب GET (مطابق لـ axios.get في مثال JS)
    resp = requests.get(
        BASE_URL,
        params=params,
        timeout=30,
    )

    print("\n---------------------------------------------------")
    print(f"📡 Status Code: {resp.status_code}")
    print("📄 Raw Response Content:")
    print(resp.text)
    print("---------------------------------------------------\n")

    try:
        data = resp.json()

        # أخطاء نظامية مثل InvalidCode
        if isinstance(data, dict) and data.get("code") not in (None, "0") and "access_token" not in data:
            print("❌ FAILED. System error:")
            print(data)
            return

        # حالة النجاح: يوجد access_token في الاستجابة
        if "access_token" in data:
            ali.save_token(data)
            print("✅ SUCCESS! Token saved to token.json")
            print("⚠️ Full token response (keep it secret):")
            print(data)
        else:
            print("❌ FAILED. Unknown JSON structure:")
            print(data)

    except Exception as e:
        print("⚠️ Could not parse JSON. The request may have failed silently.")
        print("Exception:", e)


if __name__ == "__main__":
    code = get_auth_code()
    if code:
        get_access_token(code)
    else:
        print("⚠️ No code provided. Exiting.")
