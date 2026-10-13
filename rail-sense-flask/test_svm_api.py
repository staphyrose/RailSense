import requests
import os

# ============================================================
# PUT THE PATH OF ONE TEST WAV FILE HERE
# ============================================================

AUDIO_PATH = r"C:\Railsense_audio\testing_data\Normal\normal_4.wav"


# ============================================================
# CHECK FILE EXISTS
# ============================================================

if not os.path.exists(AUDIO_PATH):

    print("ERROR: Audio file not found!")
    print(AUDIO_PATH)
    exit()


print("Audio file found:")
print(AUDIO_PATH)
print()


# ============================================================
# SEND AUDIO TO FLASK SVM API
# ============================================================

url = "http://127.0.0.1:5000/api/ai/audio/predict"


with open(AUDIO_PATH, "rb") as audio:

    files = {
        "audio": (
            os.path.basename(AUDIO_PATH),
            audio,
            "audio/wav"
        )
    }

    response = requests.post(
        url,
        files=files
    )


# ============================================================
# DISPLAY RESULT
# ============================================================

print("============================================")
print("        RAILSENSE SVM API TEST")
print("============================================")

print("HTTP Status:", response.status_code)
print()

print("Response:")

try:

    result = response.json()

    print(result)

except Exception:

    print(response.text)


print("============================================")