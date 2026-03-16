import queue
import threading
import time

# GLOBAL QUEUES
frame_queue = queue.Queue(maxsize=6)
result_queue = queue.Queue()

def generate_frame_result():
    frame = frame_queue.get()import queue
from deepface import DeepFace

# GLOBAL QUEUES
frame_queue = queue.Queue(maxsize=6)
result_queue = queue.Queue()


def generate_frame_result():
    while True:
        frame = frame_queue.get()

        try:
            analysis = DeepFace.analyze(frame, actions=['emotion'], enforce_detection=False)

            emotion = analysis[0]["dominant_emotion"]

        except Exception:
            emotion = "unknown"

        result = {
            "emotion": emotion
        }

        result_queue.put(result)

        yield result
    yeild(type(frame))

