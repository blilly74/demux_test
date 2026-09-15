import asyncio
import queue
import threading

import av
import cv2

FRAME_Q: queue.Queue = queue.Queue(maxsize=1)  # keep only the newest frame

def demux_worker(url: str, klv_callback):
    container = av.open(url, options={"fflags": "nobuffer", "flags": "low_delay"})

    video_stream = next(s for s in container.streams if s.type == "video")
    data_stream  = next(s for s in container.streams if s.type == "data")

    # Try hardware decode on the Pi; falls back if unavailable.
    try:
        video_stream.codec_context = av.CodecContext.create("h264_v4l2m2m", "r")
    except Exception:
        pass

    for packet in container.demux(video_stream, data_stream):
        if packet.stream.index == data_stream.index:
            klv_callback(bytes(packet))          # -> your existing KLV parser
            continue

        for frame in packet.decode():
            img = frame.to_ndarray(format="bgr24")
            ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                continue
            if FRAME_Q.full():
                FRAME_Q.get_nowait()              # drop stale frame, never block
            FRAME_Q.put_nowait(jpg.tobytes())

def start(url: str, klv_callback):
    threading.Thread(target=demux_worker, args=(url, klv_callback), daemon=True).start()
