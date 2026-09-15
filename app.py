from nicegui import app, ui

@app.get("/ir_frame")
def ir_frame():
    from fastapi.responses import Response
    try:
        return Response(FRAME_Q.queue[-1], media_type="image/jpeg")
    except IndexError:
        return Response(status_code=204)

img = ui.interactive_image("/ir_frame")
ui.timer(1 / 15, lambda: img.set_source(f"/ir_frame?t={time.time()}"))
