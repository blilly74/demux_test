#!/usr/bin/env python3
"""Run directly on the Pi against the live stream: python3 pi_ts_diag.py udp://0.0.0.0:11025"""
import subprocess, sys, threading, time, json

import av
import psutil

DURATION = 5  # seconds per test section


def section(title):
    print(f"\n=== {title} ===")


def run_ffprobe(url):
    section("Stream identification (ffprobe)")
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", "-timeout", "3000000", url],
        capture_output=True, text=True,
    )
    if out.returncode != 0 or not out.stdout:
        print("ffprobe failed to open source:", out.stderr.strip()[:500])
        return
    data = json.loads(out.stdout)
    for s in data.get("streams", []):
        print(f"  index={s['index']} codec_type={s['codec_type']} codec_name={s.get('codec_name')} "
              f"id={s.get('id')} tags={s.get('tags', {})}")
    print("Use the index whose codec_type is NOT 'video'/'audio' as your KLV stream.")


def run_continuity_check(url):
    section(f"Wire-level packet integrity ({DURATION}s, via ffmpeg)")
    out = subprocess.run(
        ["ffmpeg", "-i", url, "-t", str(DURATION), "-f", "null", "-"],
        capture_output=True, text=True,
    )
    lines = [l for l in out.stderr.splitlines()
             if any(k in l.lower() for k in ("continuity", "discontinu", "corrupt", "missing"))]
    if lines:
        print(f"  {len(lines)} integrity warning(s) found, e.g.:")
        for l in lines[:5]:
            print("   ", l.strip())
        print("  -> loss is happening upstream of Python. Fix the network path before touching decode code.")
    else:
        print("  No continuity/corruption warnings from ffmpeg. Upstream delivery looks clean.")


def build_decoder(video_stream, decoder_name):
    """Standalone CodecContext, independent of the stream's own decoder.
    Raises on first decode if the backing hardware device isn't actually usable
    (av.CodecContext.create() alone does not open the device, so we must probe it)."""
    dec = av.CodecContext.create(decoder_name, "r")
    dec.extradata = video_stream.codec_context.extradata
    return dec


def demux_test(url, decoder_name=None):
    label = decoder_name or "default (software)"
    section(f"Live demux/decode test using {label}, {DURATION}s")

    container = av.open(url, options={"fflags": "nobuffer", "flags": "low_delay"})
    video = next((s for s in container.streams if s.type == "video"), None)
    if video is None:
        print("  No video stream found by PyAV — stopping.")
        container.close()
        return

    dec = None
    if decoder_name:
        try:
            dec = build_decoder(video, decoder_name)
        except Exception as e:
            print(f"  Could not create {decoder_name}: {e}")
            container.close()
            return

    proc = psutil.Process()
    cpu_result = {}

    def sample_cpu():
        cpu_result["system_pct"] = psutil.cpu_percent(interval=DURATION)

    t_cpu = threading.Thread(target=sample_cpu)
    t0_proc = proc.cpu_times()
    t0_wall = time.time()
    t_cpu.start()

    counts, decoded, corrupt, decode_errors = {}, 0, 0, 0
    for packet in container.demux():
        counts[packet.stream_index] = counts.get(packet.stream_index, 0) + 1
        if packet.is_corrupt:
            corrupt += 1
        if packet.stream_index == video.index:
            try:
                frames = dec.decode(packet) if dec else packet.decode()
                for _ in frames:
                    decoded += 1
            except Exception as e:
                decode_errors += 1
                if dec and decode_errors == 1:
                    print(f"  {decoder_name} rejected the stream on first use: {e}")
                    print("  -> device likely not present/usable on this board. Skipping rest of this section.")
                    break
        if time.time() - t0_wall > DURATION:
            break

    t_cpu.join()
    t1_proc = proc.cpu_times()
    wall = time.time() - t0_wall
    proc_pct = ((t1_proc.user + t1_proc.system) - (t0_proc.user + t0_proc.system)) / wall * 100

    print(f"  packets/stream: { {container.streams[i].type + f'({i})': c for i, c in counts.items()} }")
    print(f"  corrupt packets: {corrupt}")
    print(f"  frames decoded: {decoded}  ({decoded / wall:.1f} fps)")
    print(f"  this process CPU: {proc_pct:.0f}% of one core")
    print(f"  system-wide CPU during test: {cpu_result.get('system_pct', 'n/a')}%")
    container.close()


def udp_queue_check(url):
    if not url.startswith("udp://"):
        return
    section("UDP receive queue / drops")
    port = url.rsplit(":", 1)[-1].split("?")[0]
    try:
        ss = subprocess.run(["ss", "-u", "-a", "-n"], capture_output=True, text=True).stdout
        for line in ss.splitlines():
            if f":{port}" in line:
                print("  ", line)
    except FileNotFoundError:
        print("  'ss' not installed (apt install iproute2) — skipping socket queue check.")
    try:
        netstat = subprocess.run(["netstat", "-su"], capture_output=True, text=True)
        for l in netstat.stdout.splitlines():
            if "error" in l.lower() or "buffer" in l.lower():
                print("  ", l.strip())
    except FileNotFoundError:
        print("  'netstat' not installed (apt install net-tools) — skipping UDP error counters.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 pi_ts_diag.py <url, e.g. udp://0.0.0.0:11025>")
        sys.exit(1)
    url = sys.argv[1]

    run_ffprobe(url)
    run_continuity_check(url)
    udp_queue_check(url)

    section("Hardware H.264 decoder listed in ffmpeg build")
    listed = "h264_v4l2m2m" in subprocess.run(
        ["ffmpeg", "-hide_banner", "-decoders"], capture_output=True, text=True
    ).stdout
    print("  h264_v4l2m2m:", listed, "(listing alone doesn't mean the device is usable — see next section)")

    demux_test(url)
    if listed:
        demux_test(url, "h264_v4l2m2m")

    section("Done")
    print("Compare fps and CPU% between the software and hardware decode sections above,")
    print("and check whether integrity warnings appeared before blaming the decoder.")
