"""
inference.py — the ONE place your trained models get loaded and called.

Everything else in the backend (ticket generation, severity, tickets table)
talks to the two functions at the bottom of this file — predict_audio() and
predict_image() — and never touches TensorFlow/PyTorch/model files directly.
That's what lets you swap in a better model later by only editing this file.

SETUP:
1. Copy your saved model file into backend/models/
     e.g. backend/models/audio_defect_model.h5
2. Set the paths below.
3. pip install tensorflow  (only needed if your model is Keras/TF, which
   your audio CRNN is, based on the training script we built earlier)
"""
import os
import numpy as np

MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
AUDIO_MODEL_PATH = os.path.join(MODELS_DIR, "audio_defect_model.h5")
IMAGE_MODEL_PATH = os.path.join(MODELS_DIR, "image", "ResNet50_improved_final.keras")

AUDIO_CLASSES = ["normal", "wheel_burnt", "superelevation"]

# From her actual training script (16_single_image_prediction.py) — this
# model has NO "normal" class. It assumes every image it's given shows
# some kind of surface defect, unlike the audio model. Confirm with her
# whether there's a separate defect/no-defect gate upstream of this.
IMAGE_CLASSES = ["Cracks", "Flakings", "Grooves", "Joints", "Shellings", "Spallings", "Squats"]

# ---------------------------------------------------------------------
# Lazy-loaded model singletons — loaded once on first use, not per-request
# ---------------------------------------------------------------------
_audio_model = None
_image_model = None


def _load_audio_model():
    global _audio_model
    if _audio_model is None:
        from tensorflow.keras.models import load_model
        _audio_model = load_model(AUDIO_MODEL_PATH)
    return _audio_model


def _load_image_model():
    global _image_model
    if _image_model is None:
        from tensorflow.keras.models import load_model
        _image_model = load_model(IMAGE_MODEL_PATH)
    return _image_model


# ---------------------------------------------------------------------
# Audio preprocessing — mirrors the training pipeline exactly.
# Whatever you did before feeding X_train_logmel.npy into training,
# do the same here on a single clip.
# ---------------------------------------------------------------------
def _audio_file_to_logmel(filepath, n_mels=128, time_frames=128, sr=22050,
                           n_fft=1024, hop_length=256):
    import librosa
    y, _ = librosa.load(filepath, sr=sr)
    mel = librosa.feature.melspectrogram(y=y, sr=sr, n_fft=n_fft,
                                          hop_length=hop_length, n_mels=n_mels)
    log_mel = librosa.power_to_db(mel, ref=np.max)
    if log_mel.shape[1] < time_frames:
        pad = time_frames - log_mel.shape[1]
        log_mel = np.pad(log_mel, ((0, 0), (0, pad)), mode="constant")
    else:
        log_mel = log_mel[:, :time_frames]
    log_mel = (log_mel - log_mel.mean()) / (log_mel.std() + 1e-8)
    return log_mel.astype(np.float32)


# =======================================================================
# PUBLIC INTERFACE — this is what main.py calls. Keep this shape stable.
# =======================================================================
def predict_audio(filepath: str) -> dict:
    """
    filepath: path to a .wav clip (a test file during simulation, or a
    real upload later).
    Returns: {"predicted_class": str, "confidence": float}
    """
    model = _load_audio_model()
    spec = _audio_file_to_logmel(filepath)
    spec = spec[np.newaxis, ..., np.newaxis]  # -> (1, n_mels, time_frames, 1)
    probs = model.predict(spec, verbose=0)[0]
    idx = int(np.argmax(probs))
    return {
        "predicted_class": AUDIO_CLASSES[idx],
        "confidence": round(float(probs[idx]), 4),
    }


def predict_image(filepath: str) -> dict:
    """
    filepath: path to a track image (.jpg/.png).
    Returns: {"predicted_class": str, "confidence": float}

    Matches her 16_single_image_prediction.py exactly:
    - 224x224, RGB
    - raw pixel values 0-255, NO manual normalization (ResNet50's
      preprocess_input is already a layer inside the saved model —
      normalizing again here would double-apply it, which her own
      comments flag as the bug that caused earlier misclassifications)
    """
    from PIL import Image
    model = _load_image_model()

    img = Image.open(filepath).convert("RGB").resize((224, 224))
    arr = np.array(img, dtype=np.float32)
    arr = np.expand_dims(arr, axis=0)  # -> (1, 224, 224, 3), raw 0-255

    probs = model.predict(arr, verbose=0)[0]
    idx = int(np.argmax(probs))
    return {
        "predicted_class": IMAGE_CLASSES[idx],
        "confidence": round(float(probs[idx]), 4),
    }
