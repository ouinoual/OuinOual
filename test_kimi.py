from kimi_client import test_kimi_connection

try:
    result = test_kimi_connection()
    print("KIMI_TEST_OK:", result)
except Exception as e:
    print("KIMI_TEST_ERROR:", str(e))
    raise
